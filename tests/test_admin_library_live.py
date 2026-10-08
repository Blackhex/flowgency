from __future__ import annotations

import json
import re
import shutil
from urllib.parse import quote

import pytest

from flowgency import app as app_mod
from flowgency.configuration import ConfigStore
from flowgency.configuration.models import MemorySelector
from flowgency.memory import resolve_memory_selector
from tests.test_agent_library_routes import _seed_library_app, _write_blueprint
from tests.test_memory_channel_routes import _seed_memory_app

_LIVE_INITIAL = re.compile(r'<script type="application/json" id="live-initial">(.*?)</script>', re.S)
_NAVIGATION = ["navigation-teams"]
_LIBRARY = "/admin/agent-library"
_ADVISOR = f"{_LIBRARY}/blueprints/advisor"
_SKILL_FILE = ".agents/skills/daily-review/SKILL.md"
_PROMPT_FILE = ".agents/prompts/daily-review.prompt.md"
_HOSTILE = "</textarea><script>alert('channel')</script><img src=x onerror=alert(1)>"


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
    registration = _registration(page.text)
    assert registration["url"] == url
    snapshot = client.get(url)
    regions = _regions(snapshot)
    assert list(regions) == expected_regions
    _assert_snapshot_matches_page(page.text, regions)
    return regions


@pytest.fixture
def library(monkeypatch, tmp_path, raw_config):
    client, config_path, library_root, cache_root = _seed_library_app(monkeypatch, tmp_path, raw_config)

    class Library:
        pass

    env = Library()
    env.client, env.config_path, env.root, env.cache_root, env.tmp = client, config_path, library_root, cache_root, tmp_path
    return env


@pytest.fixture
def channels(monkeypatch, tmp_path, raw_config):
    client, config_path, resolved = _seed_memory_app(monkeypatch, tmp_path, raw_config)

    class Channels:
        pass

    env = Channels()
    env.client, env.config_path, env.resolved, env.tmp = client, config_path, resolved, tmp_path
    env.store = app_mod.app.state.services.memory_store
    return env


def _digest(client) -> str:
    page = client.get(_ADVISOR).text
    return re.search(r'name="expected_digest" value="([0-9a-f]+)"', page).group(1)


def _marker(html: str, attribute: str) -> str:
    return re.search(rf'{attribute}="([^"]*)"', html).group(1)


class TestLibraryListLive:
    def test_snapshot_matches_the_page_and_keys_rows_by_blueprint(self, library):
        regions = _assert_page_and_snapshot(
            library.client, _LIBRARY, f"{_LIBRARY}?__live=1", [*_NAVIGATION, "library-blueprints"]
        )
        snapshot = library.client.get(f"{_LIBRARY}?__live=1").json()

        assert snapshot["binding"] == {"page": "agent-library", "team": None, "entity": None, "tab": None, "query": {}}
        assert snapshot["structure"] == "agent-library:1"
        assert 'data-live-key="blueprint:advisor"' in regions["library-blueprints"]

    def test_added_and_removed_blueprints_follow_remote_changes(self, library):
        _write_blueprint(library.root, "designer", "Designer")
        added = _regions(library.client.get(f"{_LIBRARY}?__live=1"))["library-blueprints"]
        assert 'data-live-key="blueprint:designer"' in added
        assert "Designer" in added

        shutil.rmtree(library.root / "designer")
        removed = _regions(library.client.get(f"{_LIBRARY}?__live=1"))["library-blueprints"]
        assert "blueprint:designer" not in removed

    def test_a_missing_library_root_is_a_failed_read_not_a_snapshot(self, library):
        shutil.rmtree(library.root)
        response = library.client.get(f"{_LIBRARY}?__live=1")

        assert response.status_code == 409
        assert response.headers["cache-control"] == "no-store"

    def test_the_snapshot_answers_not_modified_for_a_matching_etag(self, library):
        first = library.client.get(f"{_LIBRARY}?__live=1")
        second = library.client.get(f"{_LIBRARY}?__live=1", headers={"If-None-Match": first.headers["etag"]})

        assert second.status_code == 304


class TestBlueprintDetailLive:
    URL = f"{_ADVISOR}?__live=1"
    REGIONS = [*_NAVIGATION, "blueprint-header", "blueprint-files", "blueprint-users", "blueprint-runtimes"]

    def test_snapshot_matches_the_page_and_registers_the_canonical_read(self, library):
        _assert_page_and_snapshot(library.client, _ADVISOR, self.URL, self.REGIONS)
        snapshot = library.client.get(self.URL).json()

        assert snapshot["binding"] == {
            "page": "blueprint-detail", "team": None, "entity": "advisor", "tab": None, "query": {}
        }
        assert snapshot["structure"] == "blueprint-detail:1"

    def test_source_revision_is_a_read_only_marker_apart_from_the_editor_baseline(self, library):
        loaded = _digest(library.client)
        header = _regions(library.client.get(self.URL))["blueprint-header"]

        assert f'data-live-revision="{loaded}"' in header
        assert 'data-live-scope="source"' in header
        assert 'data-live-baseline-input="expected_digest"' in header

        (library.root / "advisor" / "AGENTS.md").write_text("# Advisor\n\nEdited elsewhere.\n", encoding="utf-8")
        regions = _regions(library.client.get(self.URL))

        assert f'data-live-revision="{loaded}"' not in regions["blueprint-header"]
        assert _marker(regions["blueprint-header"], "data-live-revision") != loaded
        assert library.client.get(_ADVISOR).text.count(f'value="{loaded}"') == 0

    def test_no_editor_field_or_draft_travels_in_a_snapshot(self, library):
        snapshot = library.client.get(self.URL).text

        assert "<textarea" not in snapshot
        assert "expected_digest" in snapshot  # only the data-live-baseline-input marker names it
        assert 'name="expected_digest"' not in snapshot
        assert "<form" not in snapshot

    def test_instance_users_and_files_follow_remote_changes(self, library):
        (library.root / "advisor" / ".agents" / "skills" / "daily-review" / "notes.md").write_text("notes\n", encoding="utf-8")
        regions = _regions(library.client.get(self.URL))

        assert "notes.md" in regions["blueprint-files"]
        assert "Newsletter / Advisor" in regions["blueprint-users"]

    def test_the_editor_form_stays_a_controller_owned_form_outside_the_regions(self, library):
        page = library.client.get(_ADVISOR).text
        form = re.search(r'<form method="post" action="[^"]*/source"[^>]*>', page).group(0)

        assert "data-live-owned" in form
        for key, html in _regions(library.client.get(self.URL)).items():
            assert "/source" not in html, key

    @pytest.mark.parametrize("key", ["missing", "Bad_Key", "-bad"])
    def test_an_unknown_or_invalid_blueprint_is_unavailable(self, library, key):
        assert library.client.get(f"{_LIBRARY}/blueprints/{key}?__live=1").status_code == 404

    def test_removing_the_blueprint_reports_unavailable_without_another_record(self, library):
        shutil.rmtree(library.root / "advisor")
        response = library.client.get(self.URL)

        assert response.status_code == 404
        assert "regions" not in response.json()

    def test_a_snapshot_never_creates_library_infrastructure(self, library):
        library.client.get(self.URL)

        assert not (library.root.parent / ".flowgency-agent-library").exists()

    def test_a_rejected_save_keeps_the_draft_and_registers_the_canonical_read(self, library):
        response = library.client.post(
            f"{_ADVISOR}/source",
            data={"path": "AGENTS.md", "expected_digest": "stale", "content": "# My local draft\n"},
        )

        assert response.status_code == 409
        assert "Blueprint source changed; reload before saving" in response.text
        assert "# My local draft" in response.text
        assert _registration(response.text)["url"] == self.URL
        assert "/source" not in _registration(response.text)["url"]


class TestBlueprintSkillLive:
    URL = f"{_ADVISOR}/skills/daily-review?path={quote(_SKILL_FILE, safe='')}&__live=1"
    REGIONS = [*_NAVIGATION, "skill-header", "skill-files", "skill-source"]

    @pytest.mark.parametrize("path", [f"{_ADVISOR}/skills", f"{_ADVISOR}/skills/daily-review"])
    def test_snapshot_matches_the_page_and_registers_the_resolved_selection(self, library, path):
        _assert_page_and_snapshot(library.client, path, self.URL, self.REGIONS)
        snapshot = library.client.get(self.URL).json()

        assert snapshot["binding"] == {
            "page": "blueprint-skill", "team": None, "entity": "advisor", "tab": "daily-review",
            "query": {"path": _SKILL_FILE},
        }
        assert snapshot["structure"] == "blueprint-skill:1"

    def test_a_file_selection_registers_that_file(self, library):
        page = library.client.get(f"{_ADVISOR}/skills/daily-review?path={quote('.agents/skills/daily-review/checklist.md', safe='')}")

        assert _registration(page.text)["binding"]["query"] == {"path": ".agents/skills/daily-review/checklist.md"}

    def test_source_marker_follows_the_blueprint_digest_and_new_files_appear(self, library):
        loaded = _digest(library.client)
        before = _regions(library.client.get(self.URL))["skill-source"]
        assert f'data-live-revision="{loaded}"' in before
        assert 'data-live-baseline-input="expected_digest"' in before

        (library.root / "advisor" / ".agents" / "skills" / "daily-review" / "extra.md").write_text("extra\n", encoding="utf-8")
        regions = _regions(library.client.get(self.URL))

        assert f'data-live-revision="{loaded}"' not in regions["skill-source"]
        assert "extra.md" in regions["skill-files"]

    def test_the_editor_draft_and_baseline_never_travel_in_a_snapshot(self, library):
        snapshot = library.client.get(self.URL).text

        assert "<textarea" not in snapshot
        assert 'name="expected_digest"' not in snapshot
        assert "<form" not in snapshot

    def test_a_removed_file_or_skill_is_unavailable_rather_than_another_file(self, library):
        (library.root / "advisor" / ".agents" / "skills" / "daily-review" / "checklist.md").unlink()
        stale = f"{_ADVISOR}/skills/daily-review?path={quote('.agents/skills/daily-review/checklist.md', safe='')}&__live=1"

        assert library.client.get(stale).status_code == 404
        assert library.client.get(f"{_ADVISOR}/skills/gone?path={quote(_SKILL_FILE, safe='')}&__live=1").status_code == 404

    def test_a_stale_selection_still_renders_the_page_with_the_first_file(self, library):
        page = library.client.get(f"{_ADVISOR}/skills/daily-review?path=missing.md")

        assert page.status_code == 200
        assert _registration(page.text)["binding"]["query"] == {"path": _SKILL_FILE}

    def test_a_rejected_save_registers_the_canonical_read(self, library):
        response = library.client.post(
            f"{_ADVISOR}/source",
            data={"path": _SKILL_FILE, "expected_digest": "stale", "content": "local skill draft"},
        )

        assert response.status_code == 409
        assert "local skill draft" in response.text
        assert _registration(response.text)["url"] == self.URL


class TestBlueprintPromptsLive:
    URL = f"{_ADVISOR}/prompts?path={quote(_PROMPT_FILE, safe='')}&__live=1"
    REGIONS = [*_NAVIGATION, "prompts-header", "prompts-list"]

    def test_snapshot_matches_the_page_and_registers_the_resolved_selection(self, library):
        _assert_page_and_snapshot(library.client, f"{_ADVISOR}/prompts", self.URL, self.REGIONS)
        snapshot = library.client.get(self.URL).json()

        assert snapshot["binding"] == {
            "page": "blueprint-prompts", "team": None, "entity": "advisor", "tab": None,
            "query": {"path": _PROMPT_FILE},
        }
        assert snapshot["structure"] == "blueprint-prompts:1"

    def test_the_list_is_keyed_by_prompt_name_and_follows_remote_prompts(self, library):
        before = _regions(library.client.get(self.URL))["prompts-list"]
        assert 'data-live-key="prompt:daily-review"' in before
        assert 'data-live-key="prompt:pr-review"' in before

        (library.root / "advisor" / ".agents" / "prompts" / "weekly.prompt.md").write_text(
            "---\nname: weekly\ndescription: Weekly\n---\n\nRun weekly.\n", encoding="utf-8"
        )
        after = _regions(library.client.get(self.URL))["prompts-list"]

        assert 'data-live-key="prompt:weekly"' in after

    def test_source_marker_is_the_blueprint_digest_with_the_editor_baseline_named(self, library):
        loaded = _digest(library.client)
        header = _regions(library.client.get(self.URL))["prompts-header"]

        assert f'data-live-revision="{loaded}"' in header
        assert 'data-live-baseline-input="expected_digest"' in header

    def test_editors_and_drafts_stay_outside_the_snapshot(self, library):
        snapshot = library.client.get(self.URL).text

        assert "<textarea" not in snapshot
        assert "<form" not in snapshot
        assert 'name="slug"' not in snapshot

    def test_every_prompt_form_is_controller_owned(self, library):
        page = library.client.get(f"{_ADVISOR}/prompts").text
        forms = re.findall(r"<form [^>]*>", page)

        assert len(forms) == 3
        assert all("data-live-owned" in form for form in forms)

    def test_a_removed_prompt_is_unavailable_rather_than_the_next_prompt(self, library):
        (library.root / "advisor" / ".agents" / "prompts" / "daily-review.prompt.md").unlink()

        assert library.client.get(self.URL).status_code == 404

    def test_a_blueprint_without_prompts_registers_no_selection(self, library):
        for prompt in (library.root / "advisor" / ".agents" / "prompts").glob("*.prompt.md"):
            prompt.unlink()
        page = library.client.get(f"{_ADVISOR}/prompts")

        assert _registration(page.text)["url"] == f"{_ADVISOR}/prompts?__live=1"
        assert _regions(library.client.get(f"{_ADVISOR}/prompts?__live=1"))["prompts-list"]

    def test_a_failed_create_keeps_the_draft_and_registers_the_canonical_read(self, library):
        response = library.client.post(
            f"{_ADVISOR}/source",
            data={"slug": "new-one", "expected_digest": "stale", "content": "local prompt draft"},
        )

        assert response.status_code == 409
        assert "local prompt draft" in response.text
        assert _registration(response.text)["url"].startswith(f"{_ADVISOR}/prompts?")
        assert _registration(response.text)["url"].endswith("__live=1")


class TestMemoryChannelListLive:
    URL = "/admin/memory-channels?__live=1"
    PAGE = "/admin/memory-channels"

    def test_snapshot_matches_the_page_and_keys_rows_by_channel(self, channels):
        regions = _assert_page_and_snapshot(
            channels.client, self.PAGE, self.URL, [*_NAVIGATION, "channels-status", "channels-table"]
        )
        snapshot = channels.client.get(self.URL).json()

        assert snapshot["binding"] == {"page": "memory-channels", "team": None, "entity": None, "tab": None, "query": {}}
        assert snapshot["structure"] == "memory-channels:1"
        assert 'data-live-key="channel:brand-strategy"' in regions["channels-table"]
        assert 'data-live-key="channel:support"' in regions["channels-table"]

    def test_config_revision_is_a_marker_apart_from_the_create_form_baseline(self, channels):
        loaded = ConfigStore(channels.config_path).load().revision
        status = _regions(channels.client.get(self.URL))["channels-status"]

        assert f'data-live-revision="{loaded}"' in status
        assert 'data-live-baseline-input="revision"' in status
        assert 'data-live-scope="config"' in status

    def test_remote_channel_changes_update_rows_and_the_marker(self, channels):
        store = ConfigStore(channels.config_path)
        loaded = store.load().revision
        store.patch(loaded, lambda raw: raw["memory"]["channels"].update({"launch-notes": {"display_name": "Launch Notes"}}))
        app_mod.refresh_services()
        regions = _regions(channels.client.get(self.URL))

        assert 'data-live-key="channel:launch-notes"' in regions["channels-table"]
        assert f'data-live-revision="{loaded}"' not in regions["channels-status"]

    def test_the_create_form_and_its_draft_stay_outside_the_snapshot(self, channels):
        snapshot = channels.client.get(self.URL).text

        assert "<form" not in snapshot
        assert 'name="revision"' not in snapshot

    def test_the_create_form_is_controller_owned(self, channels):
        page = channels.client.get(self.PAGE).text
        form = re.search(r'<form method="post" action="/admin/memory-channels/create"[^>]*>', page).group(0)

        assert "data-live-owned" in form

    def test_a_rejected_create_keeps_its_errors_and_registers_the_canonical_read(self, channels):
        response = channels.client.post(
            "/admin/memory-channels/create",
            data={"revision": ConfigStore(channels.config_path).load().revision, "channel_key": "Bad Key", "display_name": "Bad"},
        )

        assert response.status_code == 409
        assert "Channel keys must be lowercase" in response.text
        assert _registration(response.text)["url"] == self.URL


class TestMemoryChannelDetailLive:
    PAGE = "/admin/memory-channels/brand-strategy"
    URL = f"{PAGE}?__live=1"
    REGIONS = [*_NAVIGATION, "channel-header", "channel-status", "channel-references", "channel-files"]

    def test_snapshot_matches_the_page_and_registers_the_canonical_read(self, channels):
        _assert_page_and_snapshot(channels.client, self.PAGE, self.URL, self.REGIONS)
        snapshot = channels.client.get(self.URL).json()

        assert snapshot["binding"] == {
            "page": "memory-channel", "team": None, "entity": "brand-strategy", "tab": None, "query": {}
        }
        assert snapshot["structure"] == "memory-channel:1"

    def test_config_and_content_revisions_are_separate_markers_apart_from_the_editor_baselines(self, channels):
        config = ConfigStore(channels.config_path).load().revision
        content = channels.store.read(channels.resolved).revision
        status = _regions(channels.client.get(self.URL))["channel-status"]

        assert f'data-live-revision="{config}"' in status
        assert 'data-live-baseline-input="revision"' in status
        assert f'data-live-revision="{content}"' in status
        assert 'data-live-baseline-input="content_revision"' in status
        assert 'data-live-scope="config"' in status
        assert 'data-live-scope="content"' in status

    def test_a_remote_memory_edit_moves_only_the_content_marker(self, channels):
        loaded = channels.store.read(channels.resolved).revision
        assert f'name="content_revision" value="{loaded}"' in channels.client.get(self.PAGE).text

        channels.store.try_update(
            channels.resolved, loaded, lambda current: {**current.files, "memory.md": b"# Brand\n\nEdited elsewhere.\n", "extra.md": b"more\n"}
        )
        status = _regions(channels.client.get(self.URL))["channel-status"]
        files = _regions(channels.client.get(self.URL))["channel-files"]

        assert f'data-live-revision="{loaded}"' not in status
        assert "2 files" in files

    def test_the_editor_baselines_and_content_never_travel_in_a_snapshot(self, channels):
        snapshot = channels.client.get(self.URL).text

        assert "<textarea" not in snapshot
        assert "<form" not in snapshot
        assert 'value="memory.md"' not in snapshot
        assert 'name="content_revision"' not in snapshot
        assert "# Brand" not in snapshot

    def test_every_channel_form_is_controller_owned(self, channels):
        forms = re.findall(r"<form [^>]*>", channels.client.get(self.PAGE).text)

        assert len(forms) == 3
        assert all("data-live-owned" in form for form in forms)

    def test_hostile_memory_content_is_escaped_on_the_page_and_absent_from_snapshots(self, channels):
        current = channels.store.read(channels.resolved)
        channels.store.try_update(channels.resolved, current.revision, lambda snapshot: {**snapshot.files, "memory.md": _HOSTILE.encode()})
        page = channels.client.get(self.PAGE).text
        snapshot = channels.client.get(self.URL)

        assert snapshot.status_code == 200
        assert "alert('channel')" not in snapshot.text
        assert "<script>alert" not in page
        assert "&lt;/textarea&gt;&lt;script&gt;" in page

    def test_hostile_display_names_are_escaped_in_every_region(self, channels):
        store = ConfigStore(channels.config_path)
        store.patch(
            store.load().revision,
            lambda raw: raw["memory"]["channels"]["brand-strategy"].update(display_name="<img src=x onerror=alert(1)>"),
        )
        app_mod.refresh_services()
        snapshot = channels.client.get(self.URL)

        assert snapshot.status_code == 200
        assert "<img" not in snapshot.text
        assert "&lt;img" in _regions(snapshot)["channel-header"]

    def test_references_follow_remote_config_changes(self, channels):
        before = _regions(channels.client.get(self.URL))["channel-references"]
        assert "Newsletter / Advisor" in before

        store = ConfigStore(channels.config_path)

        def drop_reference(raw: dict) -> None:
            for team in raw["teams"].values():
                for agent in team["agents"]:
                    agent.pop("default_memory", None)

        store.patch(store.load().revision, drop_reference)
        app_mod.refresh_services()
        after = _regions(channels.client.get(self.URL))["channel-references"]

        assert "Newsletter / Advisor" not in after
        assert "No config references currently target this channel." in after

    def test_a_removed_channel_is_unavailable_without_another_record(self, channels):
        store = ConfigStore(channels.config_path)

        def drop(raw: dict) -> None:
            for team in raw["teams"].values():
                for agent in team["agents"]:
                    agent.pop("default_memory", None)
            del raw["memory"]["channels"]["brand-strategy"]

        store.patch(store.load().revision, drop)
        app_mod.refresh_services()
        response = channels.client.get(self.URL)

        assert response.status_code == 404
        assert "regions" not in response.json()

    def test_a_snapshot_never_creates_channel_memory(self, channels):
        # `support` is declared but its memory directory was never created.
        resolved = resolve_memory_selector(
            MemorySelector(scope="channel", channel="support"),
            job_id="probe", team_key="admin", agent_name="channel", routine_id=None,
            channels=ConfigStore(channels.config_path).load().config.memory.channels,
            store_root=channels.store.root,
        )
        assert not resolved.directory.exists()
        locks = channels.store.root / ".locks"
        before = sorted(path.name for path in locks.iterdir()) if locks.exists() else []

        snapshot = channels.client.get("/admin/memory-channels/support?__live=1")

        assert snapshot.status_code == 200
        assert not resolved.directory.exists()
        assert (sorted(path.name for path in locks.iterdir()) if locks.exists() else []) == before

    def test_an_unknown_channel_is_unavailable(self, channels):
        assert channels.client.get("/admin/memory-channels/nope?__live=1").status_code == 404

    def test_a_rejected_rekey_keeps_the_page_and_registers_the_canonical_read(self, channels):
        response = channels.client.post(
            self.PAGE,
            data={
                "revision": ConfigStore(channels.config_path).load().revision,
                "display_name": "Local name draft",
                "new_key": "renamed",
            },
        )

        assert response.status_code == 409
        assert "Cannot rekey a referenced channel" in response.text
        assert _registration(response.text)["url"] == self.URL

    def test_a_rejected_content_save_keeps_the_draft_and_registers_the_canonical_read(self, channels):
        response = channels.client.post(
            f"{self.PAGE}/content",
            data={"filename": "memory.md", "content_revision": "stale", "content": "my unsaved memory draft"},
        )

        assert response.status_code == 409
        assert "my unsaved memory draft" in response.text
        assert _registration(response.text)["url"] == self.URL
