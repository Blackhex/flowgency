from __future__ import annotations

import copy
import sys
import types
from pathlib import Path

import pytest
import yaml

from agency.configuration import ConfigStore
from agency.integrations import BaseIntegration
from agency.web.folder_picker import pick_directory
from agency.web.setup_flow import (
    build_setup_prompt,
    inspect_setup_status,
    launchable_integrations,
)


def test_build_setup_prompt_names_project_and_config(tmp_path: Path) -> None:
    prompt = build_setup_prompt(tmp_path, tmp_path / "config.yaml")

    assert "agency-setup" in prompt
    assert str(tmp_path.resolve()) in prompt
    assert str((tmp_path / "config.yaml").resolve()) in prompt
    assert "Do not write a partial configuration" in prompt


def test_status_waits_when_config_is_absent(tmp_path: Path) -> None:
    status = inspect_setup_status(ConfigStore(tmp_path / "config.yaml"))

    assert status.state == "waiting"


def test_status_is_invalid_for_validation_errors(tmp_path: Path, raw_config) -> None:
    store = ConfigStore(tmp_path / "config.yaml")
    invalid = copy.deepcopy(raw_config)
    invalid["groups"]["newsletter"]["default_integration"] = ""
    store.path.write_text(yaml.safe_dump(invalid, sort_keys=False), encoding="utf-8")

    status = inspect_setup_status(store)

    assert status.state == "invalid"
    assert status.message == "Group default integration is required."


def test_status_is_invalid_for_yaml_parse_errors(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.yaml")
    store.path.write_text("agency: [\n", encoding="utf-8")

    status = inspect_setup_status(store)

    assert status.state == "invalid"
    assert "\n" not in status.message


def test_status_is_incomplete_when_no_groups(tmp_path: Path, raw_config) -> None:
    store = ConfigStore(tmp_path / "config.yaml")
    incomplete = copy.deepcopy(raw_config)
    incomplete["agency"]["default_group"] = ""
    incomplete["groups"] = {}
    store.create(incomplete)

    status = inspect_setup_status(store)

    assert status.state == "incomplete"


def test_status_is_ready_only_with_a_group(tmp_path: Path, raw_config) -> None:
    store = ConfigStore(tmp_path / "config.yaml")
    store.create(raw_config)

    status = inspect_setup_status(store)

    assert status.state == "ready"


class _Integration(BaseIntegration):
    def __init__(self, name: str, display_name: str, priority: int, *, interactive: bool, detected: bool) -> None:
        self.name = name
        self.display_name = display_name
        self.detect_priority = priority
        self._interactive = interactive
        self._detected = detected

    def interactive_setup_available(self) -> bool:
        return self._interactive

    def detect(self, agent_dir: Path) -> bool:
        return self._detected


def test_launchable_integrations_filter_and_order(tmp_path: Path) -> None:
    integrations = {
        "beta": _Integration("beta", "Beta", 5, interactive=True, detected=False),
        "alpha": _Integration("alpha", "alpha", 5, interactive=True, detected=False),
        "copilot": _Integration("copilot", "GitHub Copilot", 10, interactive=True, detected=True),
        "hidden": _Integration("hidden", "Hidden", 1, interactive=False, detected=True),
    }

    result = launchable_integrations(integrations, tmp_path)

    assert tuple(item.name for item in result) == ("copilot", "alpha", "beta")


def test_launchable_integrations_prefers_lower_priority_for_nondetected(tmp_path: Path) -> None:
    integrations = {
        "later": _Integration("later", "Later", 10, interactive=True, detected=False),
        "earlier": _Integration("earlier", "Earlier", 5, interactive=True, detected=False),
    }

    result = launchable_integrations(integrations, tmp_path)

    assert tuple(item.name for item in result) == ("earlier", "later")


def test_pick_directory_returns_none_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_filedialog = types.ModuleType("tkinter.filedialog")
    fake_filedialog.askdirectory = lambda **kwargs: ""
    fake_tkinter = types.ModuleType("tkinter")
    fake_tkinter.filedialog = fake_filedialog
    fake_tkinter.TclError = RuntimeError
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_filedialog)

    assert pick_directory() is None


def test_pick_directory_returns_resolved_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    selected = tmp_path / "nested"
    selected.mkdir()
    fake_filedialog = types.ModuleType("tkinter.filedialog")
    fake_filedialog.askdirectory = lambda **kwargs: str(selected)
    fake_tkinter = types.ModuleType("tkinter")
    fake_tkinter.filedialog = fake_filedialog
    fake_tkinter.TclError = RuntimeError
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_filedialog)

    assert pick_directory() == selected.resolve()


def test_pick_directory_returns_none_when_graphical_picker_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoPickerError(RuntimeError):
        pass

    fake_filedialog = types.ModuleType("tkinter.filedialog")
    fake_filedialog.askdirectory = lambda **kwargs: (_ for _ in ()).throw(NoPickerError("no display"))
    fake_tkinter = types.ModuleType("tkinter")
    fake_tkinter.filedialog = fake_filedialog
    fake_tkinter.TclError = NoPickerError
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_filedialog)

    assert pick_directory() is None


def test_pick_directory_returns_none_when_resolved_directory_disappears(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "vanishing"
    selected.mkdir()
    fake_filedialog = types.ModuleType("tkinter.filedialog")
    fake_filedialog.askdirectory = lambda **kwargs: str(selected)
    fake_tkinter = types.ModuleType("tkinter")
    fake_tkinter.filedialog = fake_filedialog
    fake_tkinter.TclError = RuntimeError
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_filedialog)
    selected.rmdir()

    assert pick_directory() is None


def test_pick_directory_returns_none_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_filedialog = types.ModuleType("tkinter.filedialog")

    def raise_oserror(**kwargs: object) -> str:
        raise OSError("picker unavailable")

    fake_filedialog.askdirectory = raise_oserror
    fake_tkinter = types.ModuleType("tkinter")
    fake_tkinter.filedialog = fake_filedialog
    fake_tkinter.TclError = RuntimeError
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_filedialog)

    assert pick_directory() is None
