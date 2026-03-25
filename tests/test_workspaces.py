"""Tests for workspace plugin system."""

import pytest
from pathlib import Path
from starlette.testclient import TestClient


def test_registry_is_populated():
    """Shipped workspaces are auto-registered."""
    from agency.workspaces import REGISTRY
    assert "tmux" in REGISTRY
    assert "cursor" in REGISTRY
    assert "superset" in REGISTRY
    assert "ide" in REGISTRY
    assert "chat" in REGISTRY
    assert "custom" in REGISTRY


def test_get_workspace():
    """Can retrieve a workspace by name."""
    from agency.workspaces import get_workspace
    ws = get_workspace("tmux")
    assert ws.name == "tmux"
    assert ws.display_name == "tmux"


def test_get_workspace_unknown_raises():
    """Unknown workspace name raises KeyError."""
    from agency.workspaces import get_workspace
    with pytest.raises(KeyError):
        get_workspace("nonexistent")


def test_base_workspace_interface():
    """BaseWorkspace defines the expected interface."""
    from agency.workspaces import BaseWorkspace
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
    from agency.workspaces import BaseWorkspace
    ws = BaseWorkspace()
    assert ws.validate_config({}) == []


def test_render_summary_base():
    """Base render_summary returns a generic string."""
    from agency.workspaces import BaseWorkspace
    ws = BaseWorkspace()
    result = ws.render_summary({})
    assert isinstance(result, str)


class TestTmuxWorkspace:
    def test_config_schema(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        schema = ws.config_schema()
        keys = [f["key"] for f in schema]
        assert "script_path" in keys

    def test_validate_config_requires_script_path(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        errors = ws.validate_config({})
        assert any("script_path" in e for e in errors)

    def test_validate_config_valid(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        errors = ws.validate_config({"script_path": "/tmp/test.sh"})
        assert errors == []

    def test_get_config_files(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        files = ws.get_config_files({"script_path": "/tmp/test.sh"})
        assert len(files) == 1
        assert files[0]["path"] == "/tmp/test.sh"
        assert files[0]["language"] == "bash"

    def test_supports_launch(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        assert ws.supports_launch() is True

    def test_launch_command(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        cmd = ws.launch_command({"script_path": "/tmp/test.sh"}, "/tmp/group")
        assert cmd == "bash /tmp/test.sh"

    def test_render_summary(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        summary = ws.render_summary({"script_path": "/tmp/agents.sh"})
        assert "/tmp/agents.sh" in summary

    def test_detect_finds_tmux_script(self, tmp_path):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        script = tmp_path / "tmux-agents.sh"
        script.write_text("#!/bin/bash\ntmux new-session")
        result = ws.detect(str(tmp_path))
        assert result is not None
        assert result["script_path"] == str(script)

    def test_detect_returns_none_when_absent(self, tmp_path):
        from agency.workspaces import get_workspace
        ws = get_workspace("tmux")
        result = ws.detect(str(tmp_path))
        assert result is None


class TestCursorWorkspace:
    def test_config_schema_has_project_path(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("cursor")
        keys = [f["key"] for f in ws.config_schema()]
        assert "project_path" in keys

    def test_validate_config_requires_project_path(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("cursor")
        errors = ws.validate_config({})
        assert any("project_path" in e for e in errors)

    def test_get_config_files_finds_rules(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("cursor")
        files = ws.get_config_files({"project_path": "/tmp/project"})
        assert isinstance(files, list)

    def test_detect_finds_cursor_dir(self, tmp_path):
        from agency.workspaces import get_workspace
        ws = get_workspace("cursor")
        cursor_dir = tmp_path / ".cursor" / "rules"
        cursor_dir.mkdir(parents=True)
        (cursor_dir / "agents.mdc").write_text("---\n---\nrules")
        result = ws.detect(str(tmp_path))
        assert result is not None
        assert result["project_path"] == str(tmp_path)


class TestSupersetWorkspace:
    def test_config_schema(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("superset")
        keys = [f["key"] for f in ws.config_schema()]
        assert "project_path" in keys

    def test_detect_finds_superset_dir(self, tmp_path):
        from agency.workspaces import get_workspace
        ws = get_workspace("superset")
        ss_dir = tmp_path / ".superset"
        ss_dir.mkdir()
        (ss_dir / "config.json").write_text("{}")
        result = ws.detect(str(tmp_path))
        assert result is not None


class TestIdeWorkspace:
    def test_config_schema(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("ide")
        keys = [f["key"] for f in ws.config_schema()]
        assert "ide_name" in keys
        assert "project_path" in keys

    def test_validate_config(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("ide")
        errors = ws.validate_config({})
        assert len(errors) > 0
        errors = ws.validate_config({"ide_name": "VS Code", "project_path": "/tmp"})
        assert errors == []


class TestChatWorkspace:
    def test_config_schema(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("chat")
        keys = [f["key"] for f in ws.config_schema()]
        assert "platform" in keys

    def test_validate_config(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("chat")
        errors = ws.validate_config({})
        assert len(errors) > 0
        errors = ws.validate_config({"platform": "Mattermost", "channel_url": "https://mm.example.com/team/channel"})
        assert errors == []

    def test_render_summary(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("chat")
        summary = ws.render_summary({"platform": "Slack", "channel_url": "#agents"})
        assert "Slack" in summary


class TestCustomWorkspace:
    def test_config_schema(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("custom")
        keys = [f["key"] for f in ws.config_schema()]
        assert "config_path" in keys
        assert "language" in keys

    def test_get_config_files(self):
        from agency.workspaces import get_workspace
        ws = get_workspace("custom")
        files = ws.get_config_files({"config_path": "/tmp/config.yaml", "language": "yaml"})
        assert len(files) == 1
        assert files[0]["language"] == "yaml"


class TestConfigMigration:
    def test_migrate_tmux_config_to_workspaces(self):
        """Old tmux_config string becomes a workspaces list entry."""
        from agency.workspaces import migrate_tmux_config
        group_cfg = {"tmux_config": "/path/to/tmux-agents.sh"}
        result = migrate_tmux_config(group_cfg)
        assert "workspaces" in result
        assert len(result["workspaces"]) == 1
        ws = result["workspaces"][0]
        assert ws["type"] == "tmux"
        assert ws["name"] == "tmux"
        assert ws["config"]["script_path"] == "/path/to/tmux-agents.sh"
        assert "tmux_config" not in result

    def test_migrate_noop_when_no_tmux_config(self):
        """Groups without tmux_config are untouched."""
        from agency.workspaces import migrate_tmux_config
        group_cfg = {"name": "test"}
        result = migrate_tmux_config(group_cfg)
        assert "workspaces" not in result or result.get("workspaces") == []

class TestWorkspaceRoutes:
    """Smoke tests for workspace routes."""

    def _make_app(self, tmp_path):
        """Create a test app with a group that has workspaces configured."""
        from agency.app import app, CONFIG, GROUPS

        group_cfg = {
            "name": "Test Group",
            "path": str(tmp_path),
            "agents": [],
            "_agents_normalized": [],
            "workspaces": [
                {
                    "name": "Terminal Grid",
                    "type": "tmux",
                    "config": {"script_path": str(tmp_path / "tmux.sh")},
                },
            ],
        }
        (tmp_path / "tmux.sh").write_text("#!/bin/bash\ntmux new-session")
        (tmp_path / "shared" / "observations").mkdir(parents=True, exist_ok=True)
        (tmp_path / "shared" / "proposals").mkdir(parents=True, exist_ok=True)

        CONFIG.clear()
        CONFIG.update({"agency": {"title": "Test", "default_group": "test"}, "groups": {"test": group_cfg}})
        GROUPS.clear()
        GROUPS["test"] = group_cfg
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
        resp = client.post("/test/workspaces/0/file/save", data={
            "file_path": "/etc/passwd",
            "content": "hacked",
        })
        assert resp.status_code == 403


    def test_migrate_noop_when_workspaces_already_exist(self):
        """Don't double-migrate if workspaces list already present."""
        from agency.workspaces import migrate_tmux_config
        existing = [{"name": "My Grid", "type": "tmux", "config": {"script_path": "/x.sh"}}]
        group_cfg = {"tmux_config": "/old.sh", "workspaces": existing}
        result = migrate_tmux_config(group_cfg)
        assert len(result["workspaces"]) == 1
        assert result["workspaces"][0]["config"]["script_path"] == "/x.sh"
