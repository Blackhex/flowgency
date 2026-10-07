"""Tests for workspace plugin system."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import yaml
from starlette.testclient import TestClient


def test_registry_is_populated():
    """Shipped workspaces are auto-registered."""
    from flowgency.workspaces import REGISTRY
    assert "tmux" in REGISTRY
    assert "cursor" in REGISTRY
    assert "superset" in REGISTRY
    assert "ide" in REGISTRY
    assert "chat" in REGISTRY
    assert "custom" in REGISTRY


def test_get_workspace():
    """Can retrieve a workspace by name."""
    from flowgency.workspaces import get_workspace
    ws = get_workspace("tmux")
    assert ws.name == "tmux"
    assert ws.display_name == "tmux"


def test_get_workspace_unknown_raises():
    """Unknown workspace name raises KeyError."""
    from flowgency.workspaces import get_workspace
    with pytest.raises(KeyError):
        get_workspace("nonexistent")


def test_base_workspace_interface():
    """BaseWorkspace defines the expected interface."""
    from flowgency.workspaces import BaseWorkspace
    ws = BaseWorkspace()
    assert hasattr(ws, "name")
    assert hasattr(ws, "display_name")
    assert hasattr(ws, "icon_svg")
    assert hasattr(ws, "description")
    assert hasattr(ws, "config_schema")
    assert hasattr(ws, "validate_config")
    assert hasattr(ws, "render_summary")
    assert hasattr(ws, "get_config_files")
    assert hasattr(ws, "supports_launch")
    assert hasattr(ws, "launch_command")


def test_validate_config_base_returns_empty():
    """Base validate_config returns no errors."""
    from flowgency.workspaces import BaseWorkspace
    ws = BaseWorkspace()
    assert ws.validate_config({}) == []


def test_render_summary_base():
    """Base render_summary returns a generic string."""
    from flowgency.workspaces import BaseWorkspace
    ws = BaseWorkspace()
    result = ws.render_summary({})
    assert isinstance(result, str)


class TestTmuxWorkspace:
    def test_config_schema(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        schema = ws.config_schema()
        keys = [f["key"] for f in schema]
        assert "script_path" in keys

    def test_validate_config_requires_script_path(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        errors = ws.validate_config({})
        assert any("script_path" in e for e in errors)

    def test_validate_config_valid(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        errors = ws.validate_config({"script_path": "/tmp/test.sh"})
        assert errors == []

    def test_get_config_files(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        files = ws.get_config_files({"script_path": "/tmp/test.sh"})
        assert len(files) == 1
        assert files[0]["path"] == "/tmp/test.sh"
        assert files[0]["language"] == "bash"

    def test_supports_launch(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        assert ws.supports_launch() is True

    def test_launch_command(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        cmd = ws.launch_command({"script_path": "/tmp/test.sh"}, "/tmp/group")
        assert cmd == "bash /tmp/test.sh"

    def test_render_summary(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        summary = ws.render_summary({"script_path": "/tmp/agents.sh"})
        assert "/tmp/agents.sh" in summary

    def test_detect_finds_tmux_script(self, tmp_path):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        script = tmp_path / "tmux-agents.sh"
        script.write_text("#!/bin/bash\ntmux new-session")
        result = ws.detect(str(tmp_path))
        assert result is not None
        assert result["script_path"] == str(script)

    def test_detect_returns_none_when_absent(self, tmp_path):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("tmux")
        result = ws.detect(str(tmp_path))
        assert result is None


class TestCursorWorkspace:
    def test_config_schema_has_project_path(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("cursor")
        keys = [f["key"] for f in ws.config_schema()]
        assert "project_path" in keys

    def test_validate_config_requires_project_path(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("cursor")
        errors = ws.validate_config({})
        assert any("project_path" in e for e in errors)

    def test_get_config_files_finds_rules(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("cursor")
        files = ws.get_config_files({"project_path": "/tmp/project"})
        assert isinstance(files, list)

    def test_detect_finds_cursor_dir(self, tmp_path):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("cursor")
        cursor_dir = tmp_path / ".cursor" / "rules"
        cursor_dir.mkdir(parents=True)
        (cursor_dir / "agents.mdc").write_text("---\n---\nrules")
        result = ws.detect(str(tmp_path))
        assert result is not None
        assert result["project_path"] == str(tmp_path)


class TestSupersetWorkspace:
    def test_config_schema(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("superset")
        keys = [f["key"] for f in ws.config_schema()]
        assert "project_path" in keys

    def test_detect_finds_superset_dir(self, tmp_path):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("superset")
        ss_dir = tmp_path / ".superset"
        ss_dir.mkdir()
        (ss_dir / "config.json").write_text("{}")
        result = ws.detect(str(tmp_path))
        assert result is not None


class TestIdeWorkspace:
    def test_config_schema(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("ide")
        keys = [f["key"] for f in ws.config_schema()]
        assert "ide_name" in keys
        assert "project_path" in keys

    def test_validate_config(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("ide")
        errors = ws.validate_config({})
        assert len(errors) > 0
        errors = ws.validate_config({"ide_name": "VS Code", "project_path": "/tmp"})
        assert errors == []


class TestChatWorkspace:
    def test_config_schema(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("chat")
        keys = [f["key"] for f in ws.config_schema()]
        assert "platform" in keys

    def test_validate_config(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("chat")
        errors = ws.validate_config({})
        assert len(errors) > 0
        errors = ws.validate_config({"platform": "Mattermost", "channel_url": "https://mm.example.com/team/channel"})
        assert errors == []

    def test_render_summary(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("chat")
        summary = ws.render_summary({"platform": "Slack", "channel_url": "#agents"})
        assert "Slack" in summary


class TestCustomWorkspace:
    def test_config_schema(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("custom")
        keys = [f["key"] for f in ws.config_schema()]
        assert "config_path" in keys
        assert "language" in keys

    def test_get_config_files(self):
        from flowgency.workspaces import get_workspace
        ws = get_workspace("custom")
        files = ws.get_config_files({"config_path": "/tmp/config.yaml", "language": "yaml"})
        assert len(files) == 1
        assert files[0]["language"] == "yaml"


class TestWorkspaceRoutes:
    """Smoke tests for workspace routes."""

    def _make_app(self, tmp_path):
        """Create a test app with a group that has workspaces configured."""
        from flowgency.app import app
        import flowgency.app as app_mod

        (tmp_path / "tmux.sh").write_text("#!/bin/bash\ntmux new-session")
        (tmp_path / "agent-library").mkdir(parents=True, exist_ok=True)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,

                    "flowgency": {
                        "title": "Test",
                        "default_team": "test",
                        "ai_backend": "claude-code",
                        "agent_library": str((tmp_path / "agent-library").resolve()),
                        "compilation_cache": str((tmp_path / "compiled-agents").resolve()),
                        "memory_store": str((tmp_path / "memory").resolve()),
                        "prompt_store": str((tmp_path / "prompts").resolve()),
                    },
                    "teams": {
                        "test": {
                            "name": "Test Group",
                            "workspace_path": str(tmp_path.resolve()),
                            "path": str(tmp_path.resolve()),
                            "default_integration": "claude-code",
                            "agents": [],
                            "workspaces": [
                                {
                                    "name": "Terminal Grid",
                                    "type": "tmux",
                                    "config": {"script_path": str(tmp_path / "tmux.sh")},
                                }
                            ],
                        }
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        app_mod.CONFIG_PATH = config_path
        app_mod.refresh_services()
        return TestClient(app)

    def test_workspaces_list(self, tmp_path):
        client = self._make_app(tmp_path)
        resp = client.get("/test/workspaces")
        assert resp.status_code == 200
        assert "Terminal Grid" in resp.text

    def test_workspace_file_view(self, tmp_path):
        client = self._make_app(tmp_path)
        resp = client.get("/test/workspaces/0/file")
        assert resp.status_code == 200
        assert "tmux new-session" in resp.text

    def test_workspace_file_view_invalid_index(self, tmp_path):
        client = self._make_app(tmp_path)
        resp = client.get("/test/workspaces/99/file")
        assert resp.status_code == 404

    def test_workspace_file_save_disallowed_path(self, tmp_path):
        client = self._make_app(tmp_path)
        resp = client.post(
            "/test/workspaces/0/file/save",
            data={"file_path": "/etc/passwd", "content": "hacked"},
        )
        assert resp.status_code == 403


# ── Live refresh ──────────────────────────────────────────────────────────────

_LIVE_NOTES_TEXT = "# Editorial notes\n\nSaved by the editor.\n"
_LIVE_SCRIPT_TEXT = "#!/bin/bash\ntmux new-session -d\n"
_LIVE_INITIAL = re.compile(r'<script type="application/json" id="live-initial">(.*?)</script>', re.S)
_NAVIGATION_REGIONS = [
    "navigation-teams",
    "navigation-primary",
    "navigation-workflows",
    "navigation-workspace",
]


def _live_workspaces(root: Path) -> list[dict]:
    return [
        {
            "name": "Editorial Notes",
            "type": "custom",
            "config": {
                "label": "Notes",
                "config_path": str(root / "notes.md"),
                "language": "markdown",
            },
        },
        {
            "name": "Session Script",
            "type": "tmux",
            "config": {"script_path": str(root / "session.sh")},
        },
    ]


def _write_live_config(root: Path, workspaces: list[dict]) -> Path:
    (root / "agent-library").mkdir(parents=True, exist_ok=True)
    config_path = root / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "flowgency": {
                    "title": "Test",
                    "default_team": "test",
                    "ai_backend": "claude-code",
                    "agent_library": str((root / "agent-library").resolve()),
                    "compilation_cache": str((root / "compiled-agents").resolve()),
                    "memory_store": str((root / "memory").resolve()),
                    "prompt_store": str((root / "prompts").resolve()),
                },
                "teams": {
                    "test": {
                        "name": "Test Group",
                        "workspace_path": str(root.resolve()),
                        "path": str(root.resolve()),
                        "default_integration": "claude-code",
                        "agents": [],
                        "workspaces": workspaces,
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config_path


@pytest.fixture
def live_workspaces(tmp_path, monkeypatch):
    from flowgency import app as app_mod

    (tmp_path / "notes.md").write_text(_LIVE_NOTES_TEXT, encoding="utf-8")
    (tmp_path / "session.sh").write_text(_LIVE_SCRIPT_TEXT, encoding="utf-8")
    config_path = _write_live_config(tmp_path, _live_workspaces(tmp_path))
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()

    class Environment:
        root = tmp_path
        client = TestClient(app_mod.app)

        @staticmethod
        def rewrite(workspaces: list[dict]) -> None:
            _write_live_config(tmp_path, workspaces)

    return Environment


def _live_regions(response) -> dict[str, str]:
    assert response.status_code == 200, response.text
    return {region["key"]: region["html"] for region in response.json()["regions"]}


def _page_region_keys(page: str) -> list[str]:
    return re.findall(r'data-live-region="([^"]+)"', page)


def _assert_snapshot_matches_page(page: str, regions: dict[str, str]) -> None:
    assert sorted(regions) == sorted(_page_region_keys(page))
    for key, html in regions.items():
        assert html in page, key


def _file_snapshot_url(page: str) -> str:
    return json.loads(_LIVE_INITIAL.search(page).group(1))["url"]


def _forbid_file_access(monkeypatch, *paths, methods=("read_text", "read_bytes", "stat")) -> None:
    """Fail the test when a guarded path is read or stat'ed; everything else is untouched."""
    guarded = {Path(path) for path in paths}
    for name in methods:
        original = getattr(Path, name)

        def guard(self, *args, _original=original, _name=name, **kwargs):
            if self in guarded:
                raise AssertionError(f"{self} must not be accessed with {_name}")
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, guard)


class TestLiveWorkspaceIdentity:
    def test_identity_is_the_sha256_of_the_canonical_json(self):
        from flowgency.workspaces.live import workspace_identity

        workspace = {"name": "Grid", "type": "tmux", "config": {"script_path": "a.sh"}}
        canonical = '{"config":{"script_path":"a.sh"},"name":"Grid","type":"tmux"}'

        assert workspace_identity(workspace) == hashlib.sha256(canonical.encode()).hexdigest()

    def test_identity_ignores_key_order_and_follows_every_value(self):
        from flowgency.workspaces.live import workspace_identity

        base = {"name": "Grid", "type": "tmux", "config": {"script_path": "a.sh"}}
        reordered = {"config": {"script_path": "a.sh"}, "type": "tmux", "name": "Grid"}

        assert workspace_identity(base) == workspace_identity(reordered)
        assert workspace_identity(base) != workspace_identity({**base, "name": "Other"})
        assert workspace_identity(base) != workspace_identity(
            {**base, "config": {"script_path": "b.sh"}}
        )

    @pytest.mark.parametrize(
        "value",
        [None, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, ("a" * 64) + "\n", " " + "a" * 64],
    )
    def test_a_loaded_identity_must_be_strict_lowercase_sha256_hex(self, value):
        from flowgency.workspaces.live import parse_loaded_identity

        with pytest.raises(ValueError):
            parse_loaded_identity(value)

    def test_a_well_formed_loaded_identity_is_returned_unchanged(self):
        from flowgency.workspaces.live import parse_loaded_identity

        assert parse_loaded_identity("0123456789abcdef" * 4) == "0123456789abcdef" * 4

    def test_row_keys_are_unique_for_identical_workspaces(self):
        from flowgency.workspaces.live import workspace_identity, workspace_row_keys

        grid = {"name": "Grid", "type": "tmux", "config": {}}
        other = {"name": "Other", "type": "tmux", "config": {}}

        keys = workspace_row_keys([grid, other, grid])

        assert keys == [
            workspace_identity(grid),
            workspace_identity(other),
            f"{workspace_identity(grid)}-2",
        ]

    def test_source_metadata_reports_size_time_and_a_revision_that_follows_the_file(self, tmp_path):
        from flowgency.workspaces.live import source_metadata

        source = tmp_path / "source.txt"
        source.write_text("abc", encoding="utf-8")
        first = source_metadata(str(source))
        source.write_text("abcd", encoding="utf-8")
        second = source_metadata(str(source))

        assert first.available and first.size == 3 and second.size == 4
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC", first.modified)
        assert first.revision != second.revision

    @pytest.mark.parametrize("kind", ["missing", "directory", "nul"])
    def test_source_metadata_reports_an_unreadable_source_as_unavailable(self, tmp_path, kind):
        from flowgency.workspaces.live import source_metadata

        path = {
            "missing": str(tmp_path / "absent.txt"),
            "directory": str(tmp_path),
            "nul": str(tmp_path / "bad\x00name"),
        }[kind]

        metadata = source_metadata(path)

        assert metadata.available is False
        assert metadata.size is None and metadata.modified is None
        assert metadata.revision == source_metadata(str(tmp_path / "absent.txt")).revision


class TestLiveWorkspaceList:
    URL = "/test/workspaces?__live=1"

    def test_snapshot_renders_the_same_regions_as_the_page(self, live_workspaces):
        page = live_workspaces.client.get("/test/workspaces")
        snapshot = live_workspaces.client.get(self.URL)
        regions = _live_regions(snapshot)

        assert snapshot.json()["binding"] == {
            "page": "workspaces", "team": "test", "entity": None, "tab": None, "query": {}
        }
        assert snapshot.json()["structure"] == "workspaces-list:1"
        assert list(regions) == [*_NAVIGATION_REGIONS, "workspaces-status", "workspaces-list"]
        _assert_snapshot_matches_page(page.text, regions)
        registration = json.loads(_LIVE_INITIAL.search(page.text).group(1))
        assert registration["url"] == self.URL
        assert registration["binding"] == snapshot.json()["binding"]
        assert "__live_identity" not in page.text

    def test_rows_are_keyed_by_workspace_identity_never_by_position(self, live_workspaces):
        from flowgency.workspaces.live import workspace_identity

        workspaces = _live_workspaces(live_workspaces.root)
        first = _live_regions(live_workspaces.client.get(self.URL))["workspaces-list"]
        for workspace in workspaces:
            assert f'data-live-key="workspace:{workspace_identity(workspace)}"' in first

        live_workspaces.rewrite(list(reversed(workspaces)))
        reordered = _live_regions(live_workspaces.client.get(self.URL))["workspaces-list"]

        assert sorted(re.findall(r'data-live-key="(workspace:[0-9a-f]{64})"', reordered)) == sorted(
            re.findall(r'data-live-key="(workspace:[0-9a-f]{64})"', first)
        )
        assert reordered.index("Session Script") < reordered.index("Editorial Notes")
        assert first.index("Editorial Notes") < first.index("Session Script")

    def test_identical_workspaces_get_distinct_keys_instead_of_failing(self, live_workspaces):
        workspace = _live_workspaces(live_workspaces.root)[1]
        live_workspaces.rewrite([workspace, workspace])

        response = live_workspaces.client.get(self.URL)

        keys = re.findall(r'data-live-key="(workspace:[^"]+)"', _live_regions(response)["workspaces-list"])
        assert len(keys) == 2 and len(set(keys)) == 2

    def test_view_files_links_follow_the_current_position(self, live_workspaces):
        workspaces = _live_workspaces(live_workspaces.root)
        live_workspaces.rewrite(list(reversed(workspaces)))

        html = _live_regions(live_workspaces.client.get(self.URL))["workspaces-list"]

        assert html.index("Session Script") < html.index('href="/test/workspaces/0/file"')
        assert html.index('href="/test/workspaces/0/file"') < html.index("Editorial Notes")
        assert html.index("Editorial Notes") < html.index('href="/test/workspaces/1/file"')

    def test_status_announces_the_workspace_count(self, live_workspaces):
        status = _live_regions(live_workspaces.client.get(self.URL))["workspaces-status"]
        assert status.strip() == "2 workspaces configured"

        live_workspaces.rewrite(_live_workspaces(live_workspaces.root)[:1])
        one = _live_regions(live_workspaces.client.get(self.URL))["workspaces-status"]
        assert one.strip() == "1 workspace configured"

        live_workspaces.rewrite([])
        none = _live_regions(live_workspaces.client.get(self.URL))["workspaces-status"]
        assert none.strip() == "No workspaces configured"

    def test_an_empty_team_keeps_its_empty_state_keyed(self, live_workspaces):
        live_workspaces.rewrite([])

        page = live_workspaces.client.get("/test/workspaces")
        regions = _live_regions(live_workspaces.client.get(self.URL))

        assert 'data-live-key="workspaces:empty"' in regions["workspaces-list"]
        assert "No workspaces configured for this team." in regions["workspaces-list"]
        _assert_snapshot_matches_page(page.text, regions)

    def test_an_unavailable_source_is_reported_without_reading_it(self, live_workspaces, monkeypatch):
        notes = live_workspaces.root / "notes.md"
        assert "Config file not found" not in _live_regions(live_workspaces.client.get(self.URL))["workspaces-list"]
        notes.unlink()

        _forbid_file_access(
            monkeypatch, notes, live_workspaces.root / "session.sh", methods=("read_text", "read_bytes")
        )
        html = _live_regions(live_workspaces.client.get(self.URL))["workspaces-list"]

        assert "Config file not found: Notes" in html
        assert "Config file not found: Session Script" not in html

    def test_every_registered_plugin_icon_and_summary_survives_the_live_markup_checks(self, live_workspaces):
        from flowgency.workspaces import REGISTRY

        live_workspaces.rewrite(
            [{"name": f"{name} space", "type": name, "config": {}} for name in sorted(REGISTRY)]
        )

        regions = _live_regions(live_workspaces.client.get(self.URL))

        for name in REGISTRY:
            assert f"{name} space" in regions["workspaces-list"]

    def test_snapshot_follows_the_team_and_is_conditional(self, live_workspaces):
        first = live_workspaces.client.get(self.URL)
        assert live_workspaces.client.get(
            self.URL, headers={"If-None-Match": first.headers["etag"]}
        ).status_code == 304
        assert live_workspaces.client.get("/missing/workspaces?__live=1").status_code == 404

        live_workspaces.rewrite(_live_workspaces(live_workspaces.root)[:1])
        changed = live_workspaces.client.get(self.URL, headers={"If-None-Match": first.headers["etag"]})
        assert changed.status_code == 200 and changed.headers["etag"] != first.headers["etag"]


class TestLiveWorkspaceFile:
    def url(self, environment, index=0, path=None, identity=None, live=True):
        from flowgency.workspaces.live import workspace_identity

        workspace = _live_workspaces(environment.root)[index]
        query = {}
        query["path"] = path or workspace["config"].get("config_path") or workspace["config"]["script_path"]
        query["__live_identity"] = identity or workspace_identity(workspace)
        if live:
            query["__live"] = "1"
        return f"/test/workspaces/{index}/file?{urlencode(query)}"

    def test_snapshot_renders_the_same_regions_as_the_page(self, live_workspaces):
        page = live_workspaces.client.get("/test/workspaces/0/file")
        snapshot = live_workspaces.client.get(_file_snapshot_url(page.text))
        regions = _live_regions(snapshot)

        assert snapshot.json()["structure"] == "workspace-file:1"
        assert list(regions) == [*_NAVIGATION_REGIONS, "workspace-metadata"]
        _assert_snapshot_matches_page(page.text, regions)
        assert snapshot.headers["cache-control"] == "private, no-cache"

    def test_binding_carries_the_position_the_file_and_the_loaded_identity(self, live_workspaces):
        from flowgency.workspaces.live import workspace_identity

        workspace = _live_workspaces(live_workspaces.root)[0]
        page = live_workspaces.client.get("/test/workspaces/0/file")
        registration = json.loads(_LIVE_INITIAL.search(page.text).group(1))
        notes = workspace["config"]["config_path"]
        identity = workspace_identity(workspace)

        assert registration["binding"] == {
            "page": "workspace-file",
            "team": "test",
            "entity": "0",
            "tab": None,
            "query": {"path": notes, "__live_identity": identity},
        }
        assert registration["url"] == "/test/workspaces/0/file?" + urlencode(
            {"path": notes, "__live_identity": identity, "__live": "1"}
        )

    def test_the_identity_is_only_embedded_in_the_file_page(self, live_workspaces):
        for path in ("/test/workspaces", "/test/logs", "/test/jobs", "/test/agents"):
            assert "__live_identity" not in live_workspaces.client.get(path).text, path

    def test_the_status_line_reports_size_and_modification_time(self, live_workspaces):
        regions = _live_regions(live_workspaces.client.get(self.url(live_workspaces)))
        size = (live_workspaces.root / "notes.md").stat().st_size

        metadata = regions["workspace-metadata"]
        assert str(live_workspaces.root / "notes.md") in metadata
        assert re.search(rf"{size} bytes · Last changed \d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d UTC", metadata)

    def test_snapshot_never_reads_the_file_content(self, live_workspaces, monkeypatch):
        notes = live_workspaces.root / "notes.md"
        notes.write_text("TOP-SECRET-CONTENT\n", encoding="utf-8")
        reads = []
        original_read_text, original_read_bytes = Path.read_text, Path.read_bytes

        def watch_text(self, *args, **kwargs):
            reads.append(self)
            return original_read_text(self, *args, **kwargs)

        def watch_bytes(self, *args, **kwargs):
            reads.append(self)
            return original_read_bytes(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", watch_text)
        monkeypatch.setattr(Path, "read_bytes", watch_bytes)
        response = live_workspaces.client.get(self.url(live_workspaces))

        assert response.status_code == 200
        assert "TOP-SECRET-CONTENT" not in response.text
        assert notes not in reads

    def test_an_external_change_moves_the_revision_marker_and_the_status_text(self, live_workspaces):
        notes = live_workspaces.root / "notes.md"
        first = _live_regions(live_workspaces.client.get(self.url(live_workspaces)))["workspace-metadata"]
        revision = re.search(r'data-live-revision="([^"]+)"', first).group(1)
        first_size = notes.stat().st_size

        notes.write_bytes(b"Changed elsewhere, longer now, by another process.\n")
        second = _live_regions(live_workspaces.client.get(self.url(live_workspaces)))["workspace-metadata"]

        assert re.search(r'data-live-revision="([^"]+)"', second).group(1) != revision
        assert f"{first_size} bytes" in first
        assert f"{notes.stat().st_size} bytes" in second
        assert first_size != notes.stat().st_size
        assert 'data-live-scope="workspace-file"' in second
        assert "This file changed outside the editor. Reload to see the saved version." in second

    def test_a_removed_source_file_is_reported_as_missing(self, live_workspaces):
        (live_workspaces.root / "notes.md").unlink()

        metadata = _live_regions(live_workspaces.client.get(self.url(live_workspaces)))["workspace-metadata"]

        assert "File not found" in metadata
        assert "bytes" not in metadata

    def test_the_editor_and_its_draft_state_stay_outside_every_live_region(self, live_workspaces):
        page = live_workspaces.client.get("/test/workspaces/0/file").text
        regions = _live_regions(live_workspaces.client.get(self.url(live_workspaces)))

        assert "<textarea" in page and "<form" in page
        assert all("<textarea" not in html and "<form" not in html for html in regions.values())
        assert re.search(r'<div id="editor"[^>]*data-live-owned', page)
        assert Path(live_workspaces.root / "notes.md").read_text(encoding="utf-8").strip() in page
        assert '<div data-live-notices role="status" aria-live="polite"></div>' in page
        assert '<script src="/static/agent-detail-notices.js"></script>' in page

    def test_a_changed_workspace_at_the_same_position_is_incompatible_and_carries_no_content(self, live_workspaces):
        url = self.url(live_workspaces)
        workspaces = _live_workspaces(live_workspaces.root)
        live_workspaces.rewrite(list(reversed(workspaces)))

        response = live_workspaces.client.get(url)

        assert response.status_code == 200
        body = response.json()
        assert body["regions"] == []
        assert body["structure"] == "workspace-file:1"
        assert body["binding"]["page"] == "workspace-file"
        assert body["binding"]["entity"] == "0"
        assert body["binding"]["query"]["__live_identity"] != parse_qs(urlsplit(url).query)["__live_identity"][0]
        assert response.headers["cache-control"] == "no-store"
        assert "etag" not in response.headers
        assert "tmux new-session" not in response.text and "Editorial notes" not in response.text

    def test_a_changed_workspace_configuration_is_incompatible(self, live_workspaces):
        url = self.url(live_workspaces)
        workspaces = _live_workspaces(live_workspaces.root)
        workspaces[0]["name"] = "Renamed Notes"
        live_workspaces.rewrite(workspaces)

        body = live_workspaces.client.get(url).json()

        assert body["regions"] == []

    def test_the_identity_is_compared_before_the_file_is_read(self, live_workspaces, monkeypatch):
        url = self.url(live_workspaces)
        live_workspaces.rewrite(list(reversed(_live_workspaces(live_workspaces.root))))

        _forbid_file_access(
            monkeypatch, live_workspaces.root / "notes.md", live_workspaces.root / "session.sh"
        )

        assert live_workspaces.client.get(url).json()["regions"] == []

    @pytest.mark.parametrize(
        "identity",
        ["", "A" * 64, "a" * 63, "a" * 65, "z" * 64, "../" * 21 + "a"],
    )
    def test_a_malformed_loaded_identity_is_rejected(self, live_workspaces, identity):
        notes = str(live_workspaces.root / "notes.md")
        query = urlencode({"path": notes, "__live_identity": identity, "__live": "1"})

        response = live_workspaces.client.get(f"/test/workspaces/0/file?{query}")

        assert response.status_code == 400
        assert "Editorial" not in response.text

    def test_a_snapshot_without_a_loaded_identity_is_rejected(self, live_workspaces):
        notes = str(live_workspaces.root / "notes.md")

        response = live_workspaces.client.get("/test/workspaces/0/file?" + urlencode({"path": notes, "__live": "1"}))

        assert response.status_code == 400

    def test_the_plugin_allowlist_rejects_a_foreign_path_on_every_snapshot(self, live_workspaces, monkeypatch):
        outside = live_workspaces.root / "outside-secret.txt"
        outside.write_text("OUTSIDE-SECRET\n", encoding="utf-8")
        _forbid_file_access(monkeypatch, outside, live_workspaces.root / "session.sh")
        for foreign in (str(outside), "/etc/passwd", str(live_workspaces.root / "session.sh")):
            response = live_workspaces.client.get(self.url(live_workspaces, path=foreign))
            assert response.status_code == 403, foreign
            assert "OUTSIDE-SECRET" not in response.text

    def test_an_unknown_or_removed_workspace_is_not_found(self, live_workspaces):
        url = self.url(live_workspaces, index=1)
        live_workspaces.rewrite(_live_workspaces(live_workspaces.root)[:1])

        assert live_workspaces.client.get(url).status_code == 404
        assert live_workspaces.client.get(
            self.url(live_workspaces).replace("/workspaces/0/", "/workspaces/-1/")
        ).status_code == 404

    def test_the_initial_page_uses_the_current_identity_and_ignores_a_supplied_one(self, live_workspaces):
        from flowgency.workspaces.live import workspace_identity

        workspace = _live_workspaces(live_workspaces.root)[0]
        page = live_workspaces.client.get(
            "/test/workspaces/0/file?" + urlencode({"__live_identity": "f" * 64})
        )

        assert page.status_code == 200
        assert workspace_identity(workspace) in page.text
        assert "f" * 64 not in page.text

    def test_a_second_allowed_file_keeps_its_own_binding(self, live_workspaces):
        page = live_workspaces.client.get("/test/workspaces/1/file")
        registration = json.loads(_LIVE_INITIAL.search(page.text).group(1))

        assert registration["binding"]["entity"] == "1"
        assert registration["binding"]["query"]["path"] == str(live_workspaces.root / "session.sh")
        assert live_workspaces.client.get(registration["url"]).status_code == 200

    def test_saving_a_file_still_redirects_and_writes_only_allowed_content(self, live_workspaces):
        notes = live_workspaces.root / "notes.md"

        response = live_workspaces.client.post(
            "/test/workspaces/0/file/save",
            data={"file_path": str(notes), "content": "saved from the editor\n"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert notes.read_text(encoding="utf-8") == "saved from the editor\n"


