from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from agency.configuration import ConfigStore
from agency.integrations import BaseIntegration
from agency.web.directory_browser import DirectoryBrowseError, list_directories
from agency.web.setup_flow import (
    build_setup_prompt,
    inspect_setup_status,
    launchable_integrations,
)


def test_build_setup_prompt_supplies_guided_data_root_context(tmp_path: Path):
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    config_path = tmp_path / "config.yaml"

    prompt = build_setup_prompt(
        data_root,
        config_path,
        selected_integration="copilot",
    )

    for line in (
        "Setup mode: guided-first-run.",
        f"Agency data root: {data_root.resolve()}.",
        f"Authoritative config: {config_path.resolve()}.",
        "Selected integration: copilot.",
    ):
        assert line in prompt
    assert "The Agency data root was selected in the browser; do not ask for it again." in prompt
    assert (
        "Ask for the first group project workspace as the first user-facing question."
        in prompt
    )
    assert "Project workspace:" not in prompt


def test_build_setup_prompt_hands_context_aware_team_synthesis_to_skill(
    tmp_path: Path,
):
    data_root = tmp_path / "Agency"
    data_root.mkdir()

    prompt = build_setup_prompt(
        data_root,
        tmp_path / "config.yaml",
        selected_integration="copilot",
    )

    for phrase in (
        "carry inspected project facts and every approved setup answer forward",
        "Approve the group display name and stable ID, then an initial positive agent count",
        "first complete team draft with exactly that many profiles",
        "Do not use a fixed role slate",
        "Keep team drafts and revisions inside this interactive conversation",
        "The canonical config remains the only setup completion output",
        "do not emit or request a second application-side team payload",
    ):
        assert phrase in prompt

    workspace = prompt.index(
        "Ask for the first group project workspace as the first user-facing question."
    )
    inspection = prompt.index("inspect that project read-only")
    synthesis = prompt.index(
        "carry inspected project facts and every approved setup answer forward"
    )
    assert workspace < inspection < synthesis


def test_build_setup_prompt_keeps_derived_path_approval(tmp_path: Path):
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    prompt = build_setup_prompt(
        data_root,
        tmp_path / "config.yaml",
        selected_integration="copilot",
    )

    for phrase in (
        "agency.agent_library as <root>/agent-library",
        "agency.compilation_cache as <root>/compiled-agents",
        "agency.memory_store as <root>/memory",
        "agency.prompt_store as <root>/prompts",
        "groups.<group-id>.path as <root>/groups/<group-id>",
        "Customize the derived storage paths?",
        "review all five derived paths together",
        "consolidated path summary",
        "before creating any derived directory or blueprint",
    ):
        assert phrase in prompt


def test_build_setup_prompt_phases_team_and_storage_approvals(tmp_path: Path):
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    prompt = build_setup_prompt(
        data_root,
        tmp_path / "config.yaml",
        selected_integration="copilot",
    )

    # Team approval covers complete operating profiles (not storage paths)
    assert "consolidated team approval covers complete operating profiles" in prompt
    # Storage paths come after team approval in a separate step
    assert "Storage paths are approved afterward" in prompt


def test_build_setup_prompt_storage_question_deferred_until_team_approval(
    tmp_path: Path,
):
    data_root = tmp_path / "Agency"
    data_root.mkdir()

    prompt = build_setup_prompt(
        data_root,
        tmp_path / "config.yaml",
        selected_integration="copilot",
    )

    assert (
        "After one consolidated team approval, ask `Customize the derived storage paths?` once."
        in prompt
    )
    assert "After the group ID is approved, ask" not in prompt


def test_build_setup_prompt_survives_deleted_data_root(tmp_path: Path):
    data_root = tmp_path / "Agency"
    data_root.mkdir()
    resolved = data_root.resolve(strict=True)
    data_root.rmdir()

    prompt = build_setup_prompt(
        resolved,
        tmp_path / "config.yaml",
        selected_integration="copilot",
    )

    assert f"Agency data root: {resolved}." in prompt


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


def test_list_directories_returns_only_sorted_child_directories(
    tmp_path: Path,
) -> None:
    (tmp_path / "zeta").mkdir()
    (tmp_path / "Alpha").mkdir()
    (tmp_path / "notes.txt").write_text("not a directory", encoding="utf-8")

    listing = list_directories(str(tmp_path), default_path=tmp_path)

    assert listing.path == tmp_path.resolve()
    assert listing.parent == tmp_path.resolve().parent
    assert [item.name for item in listing.directories] == ["Alpha", "zeta"]
    assert [item.path for item in listing.directories] == [
        (tmp_path / "Alpha").resolve(),
        (tmp_path / "zeta").resolve(),
    ]


def test_list_directories_uses_default_path_when_request_is_empty(
    tmp_path: Path,
) -> None:
    listing = list_directories("", default_path=tmp_path)

    assert listing.path == tmp_path.resolve()


def test_list_directories_rejects_relative_or_missing_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(DirectoryBrowseError, match="absolute"):
        list_directories("relative", default_path=tmp_path)
    with pytest.raises(DirectoryBrowseError, match="does not exist"):
        list_directories(str(tmp_path / "missing"), default_path=tmp_path)


def test_list_directories_reports_permission_denied_during_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    restricted = tmp_path / "restricted"
    original_resolve = Path.resolve

    def deny_restricted_path(self: Path, *args, **kwargs):
        if self == restricted:
            raise PermissionError("access denied")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", deny_restricted_path)

    with pytest.raises(DirectoryBrowseError, match="cannot be accessed"):
        list_directories(str(restricted), default_path=tmp_path)
