from __future__ import annotations

import json
import re

import pytest
import yaml
from fastapi.testclient import TestClient

import flowgency.app as app_mod
import flowgency.integrations as integrations_mod
from flowgency.configuration.store import ConfigStore
from tests._team_helpers import apply_team_paths, create_team_environment
from tests.test_admin_dispatch import _status, _write_blueprint

_LIVE_INITIAL = re.compile(r'<script type="application/json" id="live-initial">(.*?)</script>', re.S)
_NAVIGATION_REGIONS = ["navigation-teams"]
_SCHEDULER_INTERVAL = 15


def _team(tmp_path, key: str, name: str, *, dispatch: bool, agents: tuple[str, ...] = ()) -> dict:
    paths = create_team_environment(tmp_path, key, create_state=True)
    return apply_team_paths(
        {
            "name": name,
            "default_integration": "copilot",
            "runtime": {"timeout": 1800},
            "permissions": {"mode": "restricted", "rules": [{"path": str(paths.workspace_root), "tools": ["shell"]}]},
            "agents": [{"name": agent, "blueprint": "advisor", "integration": "copilot"} for agent in agents],
            "dispatch": {"enabled": dispatch},
            "workspaces": [],
        },
        paths,
    )


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    library_root = tmp_path / "agent-library"
    _write_blueprint(library_root, "advisor", "Advisor")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "flowgency": {
                    "title": "Flowgency",
                    "default_team": "test",
                    "ai_backend": "copilot",
                    "agent_library": str(library_root),
                    "compilation_cache": str(tmp_path / "compiled-agents"),
                    "memory_store": str(tmp_path / "memory-store"),
                    "prompt_store": str(tmp_path / "prompts"),
                    "dispatch": {"interval": _SCHEDULER_INTERVAL},
                },
                "memory": {"channels": {}},
                "teams": {
                    "test": _team(tmp_path, "test", "Test Agents", dispatch=True, agents=("product",)),
                    "other": _team(tmp_path, "other", "Other Team", dispatch=False),
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    monkeypatch.setattr(app_mod, "_get_timer_status", lambda path, interval: _status())
    monkeypatch.setattr(integrations_mod, "scan_available", lambda: [])
    app_mod.refresh_services()
    store = ConfigStore(config_path)

    class Environment:
        client = TestClient(app_mod.app)
        root = tmp_path

        @staticmethod
        def revision() -> str:
            return store.load().revision

        @staticmethod
        def patch(change) -> None:
            store.patch(store.load().revision, change)
            app_mod.refresh_services()

    return Environment


def _regions(response) -> dict[str, str]:
    assert response.status_code == 200, response.text
    return {region["key"]: region["html"] for region in response.json()["regions"]}


def _registration(page: str) -> dict:
    return json.loads(_LIVE_INITIAL.search(page).group(1))


def _page_region_keys(page: str) -> list[str]:
    return re.findall(r'data-live-region="([^"]+)"', page)


def _assert_snapshot_matches_page(page: str, regions: dict[str, str]) -> None:
    assert sorted(regions) == sorted(_page_region_keys(page))
    for key, html in regions.items():
        assert html in page, key


def _live_keys(html: str) -> list[str]:
    return re.findall(r'data-live-key="([^"]+)"', html)


class TestAdminSettingsLive:
    PAGE = "/admin/"
    URL = "/admin/?__live=1"

    def test_snapshot_matches_the_page_and_registers_the_canonical_read(self, admin_env):
        page = admin_env.client.get(self.PAGE)
        snapshot = admin_env.client.get(self.URL)
        regions = _regions(snapshot)

        assert snapshot.json()["binding"] == {
            "page": "admin-settings", "team": None, "entity": None, "tab": None, "query": {}
        }
        assert snapshot.json()["structure"] == "admin-settings:1"
        assert list(regions) == [*_NAVIGATION_REGIONS, "settings-status"]
        _assert_snapshot_matches_page(page.text, regions)
        assert _registration(page.text)["url"] == self.URL

    def test_status_reports_the_saved_revision_and_follows_a_remote_change(self, admin_env):
        loaded = admin_env.revision()
        before = _regions(admin_env.client.get(self.URL))["settings-status"]
        assert f'data-live-revision="{loaded}"' in before
        assert 'data-live-baseline-input="revision"' in before

        admin_env.patch(lambda raw: raw["flowgency"].update(title="Renamed elsewhere"))
        after = _regions(admin_env.client.get(self.URL))["settings-status"]

        assert f'data-live-revision="{admin_env.revision()}"' in after
        assert after != before

    def test_the_form_revision_and_drafts_never_travel_in_a_snapshot(self, admin_env):
        page = admin_env.client.get(self.PAGE).text
        assert f'name="revision" value="{admin_env.revision()}"' in page

        snapshot = admin_env.client.get(self.URL).text

        assert 'name="revision"' not in snapshot
        assert "<form" not in snapshot and "<input" not in snapshot and "<select" not in snapshot

    def test_a_revision_conflict_page_embeds_the_canonical_get_not_the_post_action(self, admin_env):
        response = admin_env.client.post(
            "/admin/settings",
            data={"revision": "0" * 64, "title": "Draft title", "default_team": "test", "ai_backend": "copilot"},
        )

        assert response.status_code == 409
        assert response.headers["cache-control"] == "no-store"
        assert _registration(response.text)["url"] == self.URL
        assert 'data-live-region="settings-status"' in response.text


class TestAdminIntegrationsLive:
    PAGE = "/admin/integrations"
    URL = "/admin/integrations?__live=1"
    AVAILABLE = [{"module_path": "acme.widget", "author": "acme", "filename": "widget.py"}]

    def test_snapshot_matches_the_page_and_keys_rows_by_integration(self, admin_env):
        page = admin_env.client.get(self.PAGE)
        regions = _regions(admin_env.client.get(self.URL))

        assert list(regions) == [
            *_NAVIGATION_REGIONS, "integrations-status", "integrations-installed", "integrations-available"
        ]
        _assert_snapshot_matches_page(page.text, regions)
        installed = _live_keys(regions["integrations-installed"])
        assert installed and all(key.startswith(("integration:", "integration-action:")) for key in installed)
        assert "integration:copilot" in installed
        assert _registration(page.text)["url"] == self.URL

    def test_the_restart_banner_is_local_and_never_part_of_the_snapshot_url(self, admin_env):
        page = admin_env.client.get(self.PAGE + "?restart=1")

        assert "Integration changes require a service restart" in page.text
        assert _registration(page.text)["url"] == self.URL
        assert "restart" not in " ".join(_regions(admin_env.client.get(self.URL)).values()).lower().replace(
            "restart service", ""
        )

    def test_available_rows_come_and_go_with_the_scan(self, admin_env, monkeypatch):
        empty = _regions(admin_env.client.get(self.URL))["integrations-available"]
        assert _live_keys(empty) == ["available:empty"]

        monkeypatch.setattr(integrations_mod, "scan_available", lambda: self.AVAILABLE)
        listed = _regions(admin_env.client.get(self.URL))
        page = admin_env.client.get(self.PAGE)

        assert _live_keys(listed["integrations-available"]) == ["available:list", "available:acme.widget", "available-action:acme.widget"]
        assert "1 available to register" in listed["integrations-status"]
        _assert_snapshot_matches_page(page.text, listed)

    def test_row_actions_are_delegated_disposable_forms_without_inline_handlers(self, admin_env, monkeypatch):
        monkeypatch.setattr(integrations_mod, "scan_available", lambda: self.AVAILABLE)
        page = admin_env.client.get(self.PAGE).text.split("<main", 1)[1]
        regions = _regions(admin_env.client.get(self.URL))

        assert not re.search(r"\son\w+=", page)
        for key in ("integrations-installed", "integrations-available"):
            for form in re.findall(r"<form\b[^>]*>", regions[key]):
                assert "data-live-disposable" in form and "data-live-key=" in form
        assert 'data-confirm="Unregister copilot?"' in regions["integrations-installed"]


class TestAdminDispatchLive:
    PAGE = "/admin/dispatch"
    URL = "/admin/dispatch?__live=1"

    def test_snapshot_matches_the_page_with_distinct_team_keys(self, admin_env):
        page = admin_env.client.get(self.PAGE)
        regions = _regions(admin_env.client.get(self.URL))

        assert list(regions) == [*_NAVIGATION_REGIONS, "dispatch-status", "dispatch-action", "dispatch-teams"]
        _assert_snapshot_matches_page(page.text, regions)
        assert _live_keys(regions["dispatch-teams"]) == ["dispatch-teams:list", "dispatch-team:test", "dispatch-team:other"]
        assert _registration(page.text)["url"] == self.URL

    def test_status_follows_the_scheduler_and_the_action_form_is_keyed_by_what_it_does(self, admin_env, monkeypatch):
        inactive = _regions(admin_env.client.get(self.URL))
        assert "Dispatcher inactive" in inactive["dispatch-status"]
        assert _live_keys(inactive["dispatch-action"]) == ["dispatch-action:install"]
        assert "Set Up Dispatcher" in inactive["dispatch-action"]

        conflict = _status("misconfigured", installed=True, conflict=True, mismatches=["config_path"])
        monkeypatch.setattr(app_mod, "_get_timer_status", lambda path, interval: conflict)
        repair = _regions(admin_env.client.get(self.URL))
        assert "Dispatcher misconfigured" in repair["dispatch-status"]
        assert _live_keys(repair["dispatch-action"]) == ["dispatch-action:repair-replace"]
        assert 'data-confirm="Replace the existing dispatcher with this dashboard config?"' in repair["dispatch-action"]
        assert 'name="replace" value="true"' in repair["dispatch-action"]

        monkeypatch.setattr(app_mod, "_get_timer_status", lambda path, interval: _status("active", installed=True))
        active = _regions(admin_env.client.get(self.URL))
        assert "Dispatcher active" in active["dispatch-status"]
        assert "<form" not in active["dispatch-action"]

    def test_team_status_and_interval_follow_a_remote_change(self, admin_env, monkeypatch):
        monkeypatch.setattr(app_mod, "_get_timer_status", lambda path, interval: {**_status(), "expected_interval": interval})
        before = _regions(admin_env.client.get(self.URL))
        assert 'data-dispatch-enabled="true"' in before["dispatch-teams"].split("dispatch-team:other")[0]
        assert 'data-dispatch-enabled="false"' in before["dispatch-teams"].split("dispatch-team:other")[1]
        assert 'data-live-revision="15"' in before["dispatch-status"]

        def change(raw: dict) -> None:
            raw["teams"]["other"]["dispatch"] = {"enabled": True}
            raw["flowgency"]["dispatch"] = {"interval": 30}

        admin_env.patch(change)
        after = _regions(admin_env.client.get(self.URL))

        assert after["dispatch-teams"].count('data-dispatch-enabled="true"') == 2
        assert 'data-live-revision="30"' in after["dispatch-status"]

    def test_the_interval_form_stays_outside_every_region(self, admin_env):
        page = admin_env.client.get(self.PAGE).text
        regions = _regions(admin_env.client.get(self.URL))

        assert 'name="dispatch_interval"' in page
        assert all('name="dispatch_interval"' not in html for html in regions.values())

    def test_an_install_error_page_embeds_the_canonical_get_and_keeps_its_banner_local(self, admin_env, monkeypatch):
        monkeypatch.setattr(app_mod, "install_dispatch", lambda replace=False: "Scheduler refused <b>this</b>")

        response = admin_env.client.post("/admin/dispatch/install", data={})

        assert response.status_code == 409
        assert _registration(response.text)["url"] == self.URL
        assert "Scheduler refused &lt;b&gt;this&lt;/b&gt;" in response.text
        assert "Scheduler refused" not in " ".join(_regions(admin_env.client.get(self.URL)).values())


class TestAdminTeamsLive:
    PAGE = "/admin/teams"
    URL = "/admin/teams?__live=1"

    def test_snapshot_matches_the_page_and_keys_cards_by_team(self, admin_env):
        page = admin_env.client.get(self.PAGE)
        regions = _regions(admin_env.client.get(self.URL))

        assert list(regions) == [*_NAVIGATION_REGIONS, "teams-status", "teams-list"]
        _assert_snapshot_matches_page(page.text, regions)
        assert _live_keys(regions["teams-list"]) == [
            "teams:cards", "admin-team:test", "team-action:test:delete", "admin-team:other", "team-action:other:delete",
        ]
        assert "2 teams configured" in regions["teams-status"]
        assert _registration(page.text)["url"] == self.URL

    def test_remote_create_and_remove_change_the_cards_and_the_count(self, admin_env, tmp_path):
        def add(raw: dict) -> None:
            raw["teams"]["third"] = _team(tmp_path, "third", "Third Team", dispatch=False)

        admin_env.patch(add)
        created = _regions(admin_env.client.get(self.URL))
        assert "admin-team:third" in _live_keys(created["teams-list"])
        assert "3 teams configured" in created["teams-status"]

        admin_env.patch(lambda raw: [raw["teams"].pop(key) for key in ("third", "other")])
        removed = _regions(admin_env.client.get(self.URL))
        assert _live_keys(removed["teams-list"]) == ["teams:cards", "admin-team:test", "team-action:test:delete"]

    def test_delete_forms_are_disposable_delegated_and_escape_the_team_name(self, admin_env):
        admin_env.patch(lambda raw: raw["teams"]["other"].update(name="O'Brien <b>Team</b>"))
        page = admin_env.client.get(self.PAGE).text
        html = _regions(admin_env.client.get(self.URL))["teams-list"]

        assert not re.search(r"\sonsubmit=", page)
        for form in re.findall(r"<form\b[^>]*>", html):
            assert "data-live-disposable" in form and "data-confirm=" in form
        assert "O&#39;Brien &lt;b&gt;Team&lt;/b&gt;" in html
        assert "<b>Team</b>" not in html

    def test_empty_list_renders_the_empty_state_as_a_keyed_item(self, admin_env):
        admin_env.patch(lambda raw: (raw["teams"].clear(), raw["flowgency"].pop("default_team")))

        regions = _regions(admin_env.client.get(self.URL))

        assert _live_keys(regions["teams-list"]) == ["teams:empty"]
        assert "0 teams configured" in regions["teams-status"]

    def test_a_delete_conflict_page_embeds_the_canonical_get_and_the_same_summary(self, admin_env):
        response = admin_env.client.post("/admin/teams/other/delete", data={"revision": "0" * 64})

        assert response.status_code == 409
        assert _registration(response.text)["url"] == self.URL
        assert "Configuration changed. Reload before deleting." in response.text
        _assert_snapshot_matches_page(response.text, _regions(admin_env.client.get(self.URL)))


class TestAdminTeamFormsLive:
    def test_edit_snapshot_matches_the_page_and_binds_the_team(self, admin_env):
        page = admin_env.client.get("/admin/teams/test/edit")
        snapshot = admin_env.client.get("/admin/teams/test/edit?__live=1")
        regions = _regions(snapshot)

        assert snapshot.json()["binding"] == {
            "page": "admin-team-edit", "team": None, "entity": "test", "tab": None, "query": {}
        }
        assert snapshot.json()["structure"] == "admin-team-edit:1"
        assert list(regions) == [*_NAVIGATION_REGIONS, "team-edit-status", "team-edit-agents"]
        _assert_snapshot_matches_page(page.text, regions)
        assert _registration(page.text)["url"] == "/admin/teams/test/edit?__live=1"
        assert "Manage agents (1)" in regions["team-edit-agents"]
        assert 'href="/test/agents"' in regions["team-edit-agents"]

    def test_edit_status_marks_the_saved_revision_against_the_forms_loaded_one(self, admin_env):
        loaded = admin_env.revision()
        page = admin_env.client.get("/admin/teams/test/edit").text
        assert f'name="revision" value="{loaded}"' in page

        admin_env.patch(lambda raw: raw["teams"]["test"]["agents"].append(
            {"name": "second", "blueprint": "advisor", "integration": "copilot"}
        ))
        regions = _regions(admin_env.client.get("/admin/teams/test/edit?__live=1"))

        status = regions["team-edit-status"]
        assert f'data-live-revision="{admin_env.revision()}"' in status
        assert 'data-live-baseline-input="revision"' in status
        assert "Manage agents (2)" in regions["team-edit-agents"]
        assert 'name="revision"' not in " ".join(regions.values())
        assert all(token not in " ".join(regions.values()) for token in ("<form", "<input", "<textarea", "<select"))

    def test_a_removed_team_answers_404_to_its_snapshot(self, admin_env):
        admin_env.patch(lambda raw: raw["teams"].pop("other"))

        assert admin_env.client.get("/admin/teams/other/edit?__live=1").status_code == 404

    def test_a_validation_error_keeps_the_submitted_form_and_the_canonical_get(self, admin_env):
        loaded = admin_env.revision()
        response = admin_env.client.post(
            "/admin/teams/test/save",
            data={
                "revision": loaded,
                "name": "Draft name",
                "workspace_path": str(admin_env.root / "missing-workspace"),
                "path": str(admin_env.root / "teams" / "test"),
                "default_integration": "copilot",
                "runtime_timeout": "1800",
                "workspaces_json": "[]",
            },
        )

        assert response.status_code == 422
        assert _registration(response.text)["url"] == "/admin/teams/test/edit?__live=1"
        assert "Configuration is invalid." in response.text
        assert 'value="Draft name"' in response.text
        assert f'name="revision" value="{loaded}"' in response.text

    def test_a_stale_save_shows_the_current_status_while_the_loaded_revision_is_retained(self, admin_env):
        loaded = admin_env.revision()
        admin_env.patch(lambda raw: raw["teams"]["test"].update(name="Renamed elsewhere"))
        response = admin_env.client.post(
            "/admin/teams/test/save",
            data={
                "revision": loaded,
                "name": "Draft name",
                "workspace_path": str(admin_env.root / "workspaces" / "test"),
                "path": str(admin_env.root / "teams" / "test"),
                "default_integration": "copilot",
                "runtime_timeout": "1800",
                "workspaces_json": "[]",
            },
        )

        assert response.status_code == 409
        assert "Configuration changed. Reload before saving." in response.text
        assert _registration(response.text)["url"] == "/admin/teams/test/edit?__live=1"
        assert f'data-live-revision="{admin_env.revision()}"' in response.text

    def test_new_snapshot_matches_the_page(self, admin_env):
        page = admin_env.client.get("/admin/teams/new")
        snapshot = admin_env.client.get("/admin/teams/new?__live=1")
        regions = _regions(snapshot)

        assert snapshot.json()["binding"] == {
            "page": "admin-team-new", "team": None, "entity": None, "tab": None, "query": {}
        }
        assert snapshot.json()["structure"] == "admin-team-new:1"
        assert list(regions) == [*_NAVIGATION_REGIONS, "team-new-status"]
        _assert_snapshot_matches_page(page.text, regions)
        assert _registration(page.text)["url"] == "/admin/teams/new?__live=1"
        assert "2 teams configured" in regions["team-new-status"]
        assert 'data-live-baseline-input="revision"' in regions["team-new-status"]

    def test_a_create_error_page_retains_the_draft_and_the_canonical_get(self, admin_env):
        response = admin_env.client.post(
            "/admin/teams/create",
            data={"revision": admin_env.revision(), "key": "draft", "name": "", "workspace_path": "", "path": ""},
        )

        assert response.status_code == 200
        assert "Key, name, workspace path, and path are required." in response.text
        assert 'value="draft"' in response.text
        assert _registration(response.text)["url"] == "/admin/teams/new?__live=1"
        assert 'data-live-region="team-new-status"' in response.text

    def test_a_create_conflict_page_embeds_the_canonical_get(self, admin_env, tmp_path):
        paths = create_team_environment(tmp_path, "fresh")
        response = admin_env.client.post(
            "/admin/teams/create",
            data={
                "revision": "0" * 64,
                "key": "fresh",
                "name": "Fresh",
                "workspace_path": str(paths.workspace_root),
                "path": str(paths.state_root),
                "default_integration": "copilot",
                "workspaces_json": "[]",
            },
        )

        assert response.status_code == 409
        assert _registration(response.text)["url"] == "/admin/teams/new?__live=1"
        assert "Configuration changed. Reload before saving." in response.text
