"""Tests for workspace plugin system."""

import pytest
from pathlib import Path


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
