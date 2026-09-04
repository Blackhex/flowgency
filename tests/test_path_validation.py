from copy import deepcopy
import os
from pathlib import Path
import stat

import pytest

from agency.configuration.models import parse_config
from agency.configuration.paths import (
    DirectoryPreparationError,
    job_store_root,
    prepare_writable_directory,
    validate_resolved_paths,
)


def _resolved_config(tmp_path: Path, raw_config: dict):
    raw = deepcopy(raw_config)
    library = tmp_path / "library"
    cache = tmp_path / "cache"
    memory = tmp_path / "memory"
    prompt_store = tmp_path / "prompts"
    workspace = tmp_path / "workspace"
    restricted = tmp_path / "restricted"
    for path in (library, cache, memory, prompt_store, workspace, restricted):
        path.mkdir(exist_ok=True)
    raw["agency"].update(
        agent_library=str(library),
        compilation_cache=str(cache),
        memory_store=str(memory),
        prompt_store=str(prompt_store),
    )
    team = raw["teams"]["newsletter"]
    team["workspace_path"] = str(workspace)
    team["path"] = str(tmp_path / "groups" / "newsletter")
    team["runtime"] = {
        "permissions": {
            "mode": "restricted",
            "rules": [{"path": str(restricted), "tools": ["read"]}],
        }
    }
    return raw, parse_config(raw, tmp_path / "config.yaml").resolved


def _make_hostile_directory_entry(
    path: Path, target: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    try:
        path.symlink_to(target, target_is_directory=True)
        return "real-link"
    except OSError:
        original = Path.lstat
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if not reparse_flag:
            pytest.skip("symlink creation unavailable")

        class FakeStatResult:
            def __init__(self, result):
                self.st_mode = result.st_mode
                self.st_file_attributes = reparse_flag

        def fake_lstat(self):
            result = original(self)
            if self == path:
                return FakeStatResult(result)
            return result

        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "lstat", fake_lstat)
        return "simulated-reparse"


def test_job_store_is_under_memory_control_plane(tmp_path, raw_config):
    _, config = _resolved_config(tmp_path, raw_config)
    assert job_store_root(config.agency.memory_store) == (
        config.agency.memory_store / ".jobs"
    ).resolve()


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_missing_or_non_directory_team_workspace_path_fails_closed(tmp_path, raw_config, kind):
    raw, _ = _resolved_config(tmp_path, raw_config)
    workspace_path = tmp_path / "bad-workspace"
    if kind == "file":
        workspace_path.write_text("not a directory", encoding="utf-8")
    raw["teams"]["newsletter"]["workspace_path"] = str(workspace_path)
    config = parse_config(raw, tmp_path / "config.yaml").resolved

    issues = validate_resolved_paths(config)

    assert any(issue.code == "invalid-team-workspace" for issue in issues)


def test_missing_restricted_root_fails_closed(tmp_path, raw_config):
    raw, _ = _resolved_config(tmp_path, raw_config)
    raw["teams"]["newsletter"]["runtime"]["permissions"]["rules"] = [
        {"path": str(tmp_path / "missing-root"), "tools": ["read"]}
    ]
    config = parse_config(raw, tmp_path / "config.yaml").resolved

    issues = validate_resolved_paths(config)

    assert any(issue.code == "invalid-permission-path" for issue in issues)


@pytest.mark.parametrize("control_is_ancestor", [True, False])
def test_control_and_runtime_overlap_is_rejected_in_both_directions(
    tmp_path, raw_config, control_is_ancestor
):
    raw, _ = _resolved_config(tmp_path, raw_config)
    if control_is_ancestor:
        raw["agency"]["memory_store"] = str(tmp_path / "control")
        runtime = tmp_path / "control" / "workspace"
    else:
        runtime = tmp_path / "runtime"
        raw["agency"]["memory_store"] = str(runtime / "memory")
    runtime.mkdir(parents=True)
    Path(raw["agency"]["memory_store"]).mkdir(parents=True, exist_ok=True)
    raw["teams"]["newsletter"]["path"] = str(runtime)
    config = parse_config(raw, tmp_path / "config.yaml").resolved

    issues = validate_resolved_paths(config)

    assert any(issue.code == "unsafe-path-overlap" for issue in issues)


def test_unwritable_nearest_parent_is_rejected_where_portable(tmp_path, raw_config):
    if os.name == "nt":
        pytest.skip("Windows ACL writability is not represented by mode bits")
    raw, _ = _resolved_config(tmp_path, raw_config)
    parent = tmp_path / "locked"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        raw["agency"]["compilation_cache"] = str(parent / "missing-cache")
        config = parse_config(raw, tmp_path / "config.yaml").resolved
        issues = validate_resolved_paths(config)
    finally:
        parent.chmod(0o700)

    assert any(issue.code == "unwritable-control-parent" for issue in issues)


def test_resolved_team_paths_have_no_shared_segment(tmp_path, raw_config):
    from agency.configuration.team_paths import resolve_team_paths
    from agency.configuration.models import parse_config

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    raw_config["teams"]["newsletter"]["workspace_path"] = str(workspace)
    raw_config["teams"]["newsletter"]["path"] = str(
        tmp_path / "groups" / "newsletter"
    )
    team = parse_config(raw_config, tmp_path / "config.yaml").resolved.teams[
        "newsletter"
    ]

    paths = resolve_team_paths(team)

    assert paths.workspace_root == workspace.resolve()
    assert paths.team_root == (tmp_path / "groups" / "newsletter").resolve()
    assert paths.observations == paths.team_root / "observations"
    assert paths.proposals == paths.team_root / "proposals"
    assert paths.decisions == paths.team_root / "decisions"
    assert paths.locks == paths.team_root / "locks"
    assert paths.logs == paths.team_root / "logs"
    assert "shared" not in {
        part for path in paths.record_directories for part in path.parts
    }


def test_initialization_creates_team_state_but_not_workspace_shared(
    tmp_path, raw_config
):
    from agency.configuration.models import parse_config
    from agency.configuration.paths import initialize_storage_directories

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    raw_config["teams"]["newsletter"]["workspace_path"] = str(workspace)
    raw_config["teams"]["newsletter"]["path"] = str(
        tmp_path / "groups" / "newsletter"
    )
    config = parse_config(raw_config, tmp_path / "config.yaml").resolved

    initialize_storage_directories(config)

    team_root = tmp_path / "groups" / "newsletter"
    assert {
        child.name for child in team_root.iterdir() if child.is_dir()
    } == {"observations", "proposals", "decisions", "locks", "logs"}
    assert not (workspace / "shared").exists()


@pytest.mark.parametrize(
    ("field", "other_authority"),
    [
        ("workspace_path", "agency.memory_store"),
        ("path", "agency.agent_library"),
        ("path", "teams.other.path"),
        ("path", "teams.other.workspace_path"),
        ("workspace_path", "path"),
    ],
)
def test_team_authorities_must_not_overlap(
    tmp_path, raw_config, field, other_authority
):
    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    team["workspace_path"] = str(workspace)
    team["path"] = str(tmp_path / "groups" / "newsletter")

    if other_authority.startswith("agency."):
        _, agency_field = other_authority.split(".", 1)
        team[field] = raw["agency"][agency_field]
    elif other_authority == "path":
        team[field] = team["path"]
    else:
        other_workspace = tmp_path / "other-workspace"
        other_workspace.mkdir()
        raw["teams"]["other"] = {
            **deepcopy(team),
            "name": "Other",
            "workspace_path": str(other_workspace),
            "path": str(tmp_path / "groups" / "other"),
        }
        other_field = other_authority.rsplit(".", 1)[-1]
        team[field] = raw["teams"]["other"][other_field]

    config = parse_config(raw, tmp_path / "config.yaml").resolved

    issues = validate_resolved_paths(config)

    assert any(
        issue.code == "unsafe-path-overlap"
        and issue.scope == "teams.newsletter"
        and issue.field == field
        for issue in issues
    )


def test_initialize_storage_directories_rejects_symlink_or_reparse_cache_root(
    tmp_path, raw_config, monkeypatch
):
    from agency.configuration.models import parse_config
    from agency.configuration.paths import initialize_storage_directories

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    raw_config["teams"]["newsletter"]["workspace_path"] = str(workspace)
    cache_root = tmp_path / "cache-root"
    raw_config["agency"]["compilation_cache"] = str(cache_root)
    config = parse_config(raw_config, tmp_path / "config.yaml").resolved
    target = tmp_path / "cache-target"
    target.mkdir()
    mode = _make_hostile_directory_entry(cache_root, target, monkeypatch)

    with pytest.raises(ValueError, match="symlink|reparse"):
        initialize_storage_directories(config)

    assert mode in {"real-link", "simulated-reparse"}


def test_prepare_writable_directory_creates_only_requested_root(tmp_path):
    root = tmp_path / "new" / "Agency"

    resolved = prepare_writable_directory(root, label="Agency data root")

    assert resolved == root.resolve(strict=True)
    assert resolved.is_dir()
    assert list(resolved.iterdir()) == []


def test_prepare_writable_directory_revalidates_existing_root(tmp_path):
    root = tmp_path / "Agency"
    root.mkdir()
    marker = root / "user-content.txt"
    marker.write_text("keep\n", encoding="utf-8")

    first = prepare_writable_directory(root, label="Agency data root")
    second = prepare_writable_directory(root, label="Agency data root")

    assert first == second == root.resolve(strict=True)
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_prepare_writable_directory_requires_absolute_path():
    with pytest.raises(
        DirectoryPreparationError,
        match="Agency data root must be an absolute path",
    ):
        prepare_writable_directory(Path("relative/Agency"), label="Agency data root")


def test_prepare_writable_directory_rejects_file(tmp_path):
    root = tmp_path / "Agency"
    root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(
        DirectoryPreparationError,
        match="Agency data root must be a directory",
    ):
        prepare_writable_directory(root, label="Agency data root")


def test_prepare_writable_directory_rejects_link_or_reparse(
    tmp_path, monkeypatch
):
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "Agency"
    _make_hostile_directory_entry(root, target, monkeypatch)

    with pytest.raises(
        DirectoryPreparationError,
        match="symlink or reparse point",
    ):
        prepare_writable_directory(root, label="Agency data root")


def test_prepare_writable_directory_rejects_unwritable_parent(
    tmp_path, monkeypatch
):
    parent = tmp_path / "locked"
    parent.mkdir()
    root = parent / "Agency"
    original_access = os.access
    monkeypatch.setattr(
        "agency.configuration.paths.os.access",
        lambda path, mode: False
        if Path(path) == parent and mode & os.W_OK
        else original_access(path, mode),
    )

    with pytest.raises(
        DirectoryPreparationError,
        match="No writable real parent can create Agency data root",
    ):
        prepare_writable_directory(root, label="Agency data root")

    assert not root.exists()


def test_prepare_writable_directory_rejects_inaccessible_existing_root(
    tmp_path, monkeypatch
):
    root = tmp_path / "Agency"
    root.mkdir()
    original_access = os.access
    monkeypatch.setattr(
        "agency.configuration.paths.os.access",
        lambda path, mode: False
        if Path(path) == root and mode == os.R_OK | os.W_OK
        else original_access(path, mode),
    )

    with pytest.raises(
        DirectoryPreparationError,
        match="Agency data root is not readable and writable",
    ):
        prepare_writable_directory(root, label="Agency data root")