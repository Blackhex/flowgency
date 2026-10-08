from __future__ import annotations

import json
import re
import shutil

from flowgency.configuration import ConfigStore
from flowgency.web.live import _SHARED_NAVIGATION_REGIONS
from tests._ticket_helpers import delivery_definition

_LIVE_INITIAL = re.compile(r'<script type="application/json" id="live-initial">(.*?)</script>', re.S)
_ADMIN_NAVIGATION = ["navigation-teams"]
_TEAM_NAVIGATION = list(_SHARED_NAVIGATION_REGIONS)
_LIBRARY = "/admin/workflow-library"
_DELIVERY = f"{_LIBRARY}/blueprints/delivery"
_NEW_BLUEPRINT = f"{_LIBRARY}/blueprints/new"
_SETTINGS = "/newsletter/workflows/board-a/settings"
_CREATE = "/newsletter/workflows/new"
_CONFIG_NOTICE = "Configuration changed since this form loaded. Reload to see the latest."
_SOURCE_NOTICE = "Workflow source changed since this editor loaded. Reload to see the latest."


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


def _assert_page_and_snapshot(client, path: str, url: str, expected_regions: list[str]) -> dict[str, str]:
    page = client.get(path)
    assert page.status_code == 200, page.text
    assert _registration(page.text)["url"] == url
    regions = _regions(client.get(url))
    assert list(regions) == expected_regions
    _assert_snapshot_matches_page(page.text, regions)
    return regions


def _marker(html: str, attribute: str) -> str:
    return re.search(rf'{attribute}="([^"]*)"', html).group(1)


def _blueprint_definition(blueprint_id: str, name: str) -> dict:
    return {**delivery_definition(), "id": blueprint_id, "name": name}


class TestWorkflowLibraryLive:
    URL = f"{_LIBRARY}?__live=1"

    def test_snapshot_matches_the_page_and_keys_cards_by_blueprint(self, workflow_web_env):
        regions = _assert_page_and_snapshot(
            workflow_web_env.client, _LIBRARY, self.URL, [*_ADMIN_NAVIGATION, "workflow-library-blueprints"]
        )
        snapshot = workflow_web_env.client.get(self.URL).json()

        assert snapshot["binding"] == {"page": "workflow-library", "team": None, "entity": None, "tab": None, "query": {}}
        assert snapshot["structure"] == "workflow-library:1"
        assert 'data-live-key="workflow-blueprint:delivery"' in regions["workflow-library-blueprints"]

    def test_references_are_keyed_by_team_and_workflow_id_not_display_text(self, workflow_web_env):
        html = _regions(workflow_web_env.client.get(self.URL))["workflow-library-blueprints"]

        assert 'data-live-key="workflow-reference:delivery:newsletter:board-a"' in html

    def test_added_and_removed_blueprints_follow_remote_changes(self, workflow_web_env):
        env = workflow_web_env
        env.write_blueprint("triage", _blueprint_definition("triage", "Triage"))
        added = _regions(env.client.get(self.URL))["workflow-library-blueprints"]
        assert 'data-live-key="workflow-blueprint:triage"' in added
        assert "Triage" in added

        shutil.rmtree(env.library.root / "triage")
        removed = _regions(env.client.get(self.URL))["workflow-library-blueprints"]
        assert "workflow-blueprint:triage" not in removed

    def test_a_remote_source_change_moves_the_card_title(self, workflow_web_env):
        env = workflow_web_env
        env.write_blueprint("delivery", {**delivery_definition(), "name": "Delivery renamed elsewhere"})

        assert "Delivery renamed elsewhere" in _regions(env.client.get(self.URL))["workflow-library-blueprints"]

    def test_the_snapshot_answers_not_modified_for_a_matching_etag(self, workflow_web_env):
        first = workflow_web_env.client.get(self.URL)
        second = workflow_web_env.client.get(self.URL, headers={"If-None-Match": first.headers["etag"]})

        assert second.status_code == 304

    def test_a_snapshot_read_writes_nothing(self, workflow_web_env):
        env = workflow_web_env
        before = sorted(str(path) for path in env.library.root.parent.rglob("*"))
        config = env.store.path.read_bytes()

        for _ in range(2):
            env.client.get(self.URL)

        assert sorted(str(path) for path in env.library.root.parent.rglob("*")) == before
        assert env.store.path.read_bytes() == config


class TestBlueprintEditorLive:
    URL = f"{_DELIVERY}?__live=1"
    REGIONS = [*_ADMIN_NAVIGATION, "workflow-blueprint-source"]

    def test_snapshot_matches_the_page_and_registers_the_canonical_read(self, workflow_web_env):
        _assert_page_and_snapshot(workflow_web_env.client, _DELIVERY, self.URL, self.REGIONS)
        snapshot = workflow_web_env.client.get(self.URL).json()

        assert snapshot["binding"] == {
            "page": "workflow-blueprint", "team": None, "entity": "delivery", "tab": None, "query": {}
        }
        assert snapshot["structure"] == "workflow-blueprint:1"

    def test_the_editor_stays_outside_every_region_and_its_baselines_are_separate_inputs(self, workflow_web_env):
        env = workflow_web_env
        page = env.client.get(_DELIVERY).text
        regions = _regions(env.client.get(self.URL))
        digest = env.library.inspect("delivery").digest

        for html in regions.values():
            assert "workflow-editor-data" not in html
            assert "data-editor-form" not in html
            assert "<input" not in html and "<form" not in html
        assert f'name="expected_digest" value="{digest}"' in page
        assert f'name="expected_revision" value="{env.store.load().revision}"' in page
        assert page.count("data-live-owned") >= 1

    def test_markers_expose_the_current_source_and_config_revisions(self, workflow_web_env):
        env = workflow_web_env
        html = _regions(env.client.get(self.URL))["workflow-blueprint-source"]

        assert f'data-live-revision="{env.library.inspect("delivery").digest}"' in html
        assert f'data-live-revision="{env.store.load().revision}"' in html
        assert 'data-live-baseline-input="expected_digest"' in html
        assert 'data-live-baseline-input="expected_revision"' in html
        assert _SOURCE_NOTICE in html

    def test_a_remote_source_edit_moves_the_marker_but_not_the_loaded_baseline(self, workflow_web_env):
        env = workflow_web_env
        loaded = re.search(r'name="expected_digest" value="([0-9a-f]+)"', env.client.get(_DELIVERY).text).group(1)

        env.write_blueprint("delivery", {**delivery_definition(), "description": "Edited elsewhere"})

        marker = _regions(env.client.get(self.URL))["workflow-blueprint-source"]
        assert f'data-live-revision="{loaded}"' not in marker
        assert f'data-live-revision="{env.library.inspect("delivery").digest}"' in marker

    def test_a_missing_blueprint_is_unavailable_and_never_another_record(self, workflow_web_env):
        response = workflow_web_env.client.get(f"{_LIBRARY}/blueprints/ghost?__live=1")

        assert response.status_code == 404
        assert "delivery" not in response.text

    def test_a_broken_source_is_a_failed_read_not_a_snapshot(self, workflow_web_env):
        env = workflow_web_env
        (env.library.root / "delivery" / "workflow.yaml").write_text("not: [valid", encoding="utf-8")

        response = env.client.get(self.URL)

        assert response.status_code == 409
        assert response.headers["cache-control"] == "no-store"

    def test_the_snapshot_answers_not_modified_for_a_matching_etag(self, workflow_web_env):
        first = workflow_web_env.client.get(self.URL)
        second = workflow_web_env.client.get(self.URL, headers={"If-None-Match": first.headers["etag"]})

        assert second.status_code == 304

    def test_a_post_rendered_error_page_keeps_its_issues_and_registers_the_canonical_read(self, workflow_web_env):
        env = workflow_web_env
        response = env.client.post(_DELIVERY, data={"payload": "not json"})

        assert response.status_code == 422
        assert "Payload must be valid JSON." in response.text
        assert _registration(response.text)["url"] == self.URL

    def test_a_snapshot_read_writes_nothing(self, workflow_web_env):
        env = workflow_web_env
        before = sorted(str(path) for path in env.library.root.parent.rglob("*"))
        config = env.store.path.read_bytes()
        source = (env.library.root / "delivery" / "workflow.yaml").read_bytes()

        for _ in range(2):
            env.client.get(self.URL)

        assert sorted(str(path) for path in env.library.root.parent.rglob("*")) == before
        assert env.store.path.read_bytes() == config
        assert (env.library.root / "delivery" / "workflow.yaml").read_bytes() == source


class TestNewBlueprintLive:
    URL = f"{_NEW_BLUEPRINT}?__live=1"

    def test_snapshot_matches_the_page_and_carries_only_the_config_marker(self, workflow_web_env):
        regions = _assert_page_and_snapshot(
            workflow_web_env.client, _NEW_BLUEPRINT, self.URL, [*_ADMIN_NAVIGATION, "workflow-blueprint-source"]
        )
        snapshot = workflow_web_env.client.get(self.URL).json()

        assert snapshot["binding"] == {
            "page": "workflow-blueprint-new", "team": None, "entity": None, "tab": None, "query": {}
        }
        assert snapshot["structure"] == "workflow-blueprint-new:1"
        marker = regions["workflow-blueprint-source"]
        assert 'data-live-baseline-input="expected_revision"' in marker
        assert "expected_digest" not in marker

    def test_a_config_change_moves_the_marker_and_the_snapshot_never_creates_a_blueprint(self, workflow_web_env):
        env = workflow_web_env
        before = _regions(env.client.get(self.URL))["workflow-blueprint-source"]
        listing = sorted(path.name for path in env.library.root.iterdir())

        store = ConfigStore(env.store.path)
        store.patch(store.load().revision, lambda raw: raw["flowgency"].update(title="Renamed elsewhere"))

        assert _regions(env.client.get(self.URL))["workflow-blueprint-source"] != before
        assert sorted(path.name for path in env.library.root.iterdir()) == listing

    def test_a_post_rendered_error_page_registers_the_canonical_read(self, workflow_web_env):
        response = workflow_web_env.client.post(_NEW_BLUEPRINT, data={"payload": "not json"})

        assert response.status_code == 422
        assert _registration(response.text)["url"] == self.URL


class TestWorkflowSettingsLive:
    URL = f"{_SETTINGS}?__live=1"
    REGIONS = [*_TEAM_NAVIGATION, "workflow-settings-header", "workflow-settings-source"]

    def test_snapshot_matches_the_page_and_registers_the_canonical_read(self, workflow_web_env):
        _assert_page_and_snapshot(workflow_web_env.client, _SETTINGS, self.URL, self.REGIONS)
        snapshot = workflow_web_env.client.get(self.URL).json()

        assert snapshot["binding"] == {
            "page": "workflow-settings", "team": "newsletter", "entity": "board-a", "tab": None, "query": {}
        }
        assert snapshot["structure"] == "workflow-settings:1"

    def test_the_form_stays_outside_every_region(self, workflow_web_env):
        regions = _regions(workflow_web_env.client.get(self.URL))

        for key in ("workflow-settings-header", "workflow-settings-source"):
            assert "<form" not in regions[key] and "<input" not in regions[key] and "<select" not in regions[key]
            assert "button" not in regions[key]

    def test_the_header_follows_a_remote_rename_by_workflow_id(self, workflow_web_env):
        env = workflow_web_env
        header = _regions(env.client.get(self.URL))["workflow-settings-header"]
        assert 'data-live-key="workflow-settings:newsletter:board-a:title"' in header

        store = ConfigStore(env.store.path)
        store.patch(
            store.load().revision,
            lambda raw: raw["teams"]["newsletter"]["workflows"]["board-a"].update(name="Board A renamed"),
        )

        changed = _regions(env.client.get(self.URL))["workflow-settings-header"]
        assert "Board A renamed settings" in changed
        assert changed != header

    def test_the_config_revision_is_a_read_only_marker_apart_from_the_loaded_baseline(self, workflow_web_env):
        env = workflow_web_env
        loaded = env.store.load().revision
        assert f'name="expected_revision" value="{loaded}"' in env.client.get(_SETTINGS).text

        store = ConfigStore(env.store.path)
        store.patch(loaded, lambda raw: raw["flowgency"].update(title="Renamed elsewhere"))

        marker = _regions(env.client.get(self.URL))["workflow-settings-source"]
        assert f'data-live-revision="{loaded}"' not in marker
        assert f'data-live-revision="{env.store.load().revision}"' in marker
        assert 'data-live-baseline-input="expected_revision"' in marker
        assert _CONFIG_NOTICE in marker

    def test_a_removed_workflow_or_team_is_unavailable_and_never_another_record(self, workflow_web_env):
        env = workflow_web_env
        assert env.client.get("/newsletter/workflows/ghost/settings?__live=1").status_code == 404
        assert env.client.get("/ghost/workflows/board-a/settings?__live=1").status_code == 404

    def test_the_snapshot_answers_not_modified_for_a_matching_etag(self, workflow_web_env):
        first = workflow_web_env.client.get(self.URL)
        second = workflow_web_env.client.get(self.URL, headers={"If-None-Match": first.headers["etag"]})

        assert second.status_code == 304

    def test_a_post_rendered_error_page_keeps_its_issues_and_registers_the_canonical_read(self, workflow_web_env):
        env = workflow_web_env
        response = env.save_settings(name="", root=env.root_a)
        assert response.status_code in {422, 409}
        stale = env.save_settings(name="Stale name", expected_revision="0" * 64)

        assert stale.status_code == 409
        assert "Stale name" in stale.text
        assert _registration(stale.text)["url"] == self.URL

    def test_check_storage_stays_a_local_action_and_not_a_snapshot(self, workflow_web_env):
        env = workflow_web_env
        response = env.client.post(
            f"{_SETTINGS}/check-storage?__live=1",
            data={
                "name": "Board A", "blueprint": "delivery", "integration": "local",
                "integration_config.root": str(env.root_a), "expected_revision": env.store.load().revision,
            },
        )

        assert response.status_code == 200
        assert "regions" not in response.json()
        assert response.json()["status"] == "ok"

    def test_a_snapshot_read_writes_nothing(self, workflow_web_env):
        env = workflow_web_env
        config = env.store.path.read_bytes()
        before = sorted(str(path) for path in env.root_a.parent.rglob("*"))

        for _ in range(2):
            env.client.get(self.URL)

        assert env.store.path.read_bytes() == config
        assert sorted(str(path) for path in env.root_a.parent.rglob("*")) == before


class TestWorkflowCreateLive:
    URL = f"{_CREATE}?__live=1"
    REGIONS = [*_TEAM_NAVIGATION, "workflow-settings-header", "workflow-settings-source"]

    def test_snapshot_matches_the_page_and_registers_the_canonical_read(self, workflow_web_env):
        _assert_page_and_snapshot(workflow_web_env.client, _CREATE, self.URL, self.REGIONS)
        snapshot = workflow_web_env.client.get(self.URL).json()

        assert snapshot["binding"] == {
            "page": "workflow-create", "team": "newsletter", "entity": None, "tab": None, "query": {}
        }
        assert snapshot["structure"] == "workflow-create:1"

    def test_the_random_workflow_id_never_reaches_a_region_so_polls_stay_quiet(self, workflow_web_env):
        first = workflow_web_env.client.get(self.URL)
        second = workflow_web_env.client.get(self.URL, headers={"If-None-Match": first.headers["etag"]})

        assert second.status_code == 304
        assert "wf-" not in first.text

    def test_the_header_names_the_new_workflow_by_team(self, workflow_web_env):
        header = _regions(workflow_web_env.client.get(self.URL))["workflow-settings-header"]

        assert 'data-live-key="workflow-create:newsletter:title"' in header
        assert "New workflow" in header

    def test_an_unknown_team_is_unavailable(self, workflow_web_env):
        assert workflow_web_env.client.get("/ghost/workflows/new?__live=1").status_code == 404

    def test_a_post_rendered_error_page_keeps_its_warning_and_registers_the_canonical_read(self, workflow_web_env):
        env = workflow_web_env
        response = env.client.post(
            _CREATE,
            data={
                "name": "", "blueprint": "delivery", "integration": "local",
                "integration_config.root": "", "expected_revision": env.store.load().revision,
            },
        )

        assert response.status_code == 422
        assert 'role="alert"' in response.text
        assert _registration(response.text)["url"] == self.URL
