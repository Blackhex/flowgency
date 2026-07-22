from copy import deepcopy
import os
from pathlib import Path
import stat

import pytest

from agency.configuration.models import parse_config
from agency.configuration.paths import job_store_root, validate_resolved_paths


def _resolved_config(tmp_path: Path, raw_config: dict):
    raw = deepcopy(raw_config)
    library = tmp_path / "library"
    cache = tmp_path / "cache"
    memory = tmp_path / "memory"
    workspace = tmp_path / "workspace"
    restricted = tmp_path / "restricted"
    for path in (library, cache, memory, workspace, restricted):
        path.mkdir(exist_ok=True)
    raw["agency"].update(
        agent_library=str(library),
        compilation_cache=str(cache),
        memory_store=str(memory),
    )
    group = raw["groups"]["newsletter"]
    group["workspace_path"] = str(workspace)
    group["path"] = str(tmp_path / "groups" / "newsletter")
    group["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": [str(restricted)]}
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
def test_missing_or_non_directory_group_workspace_path_fails_closed(tmp_path, raw_config, kind):
    raw, _ = _resolved_config(tmp_path, raw_config)
    workspace_path = tmp_path / "bad-workspace"
    if kind == "file":
        workspace_path.write_text("not a directory", encoding="utf-8")
    raw["groups"]["newsletter"]["workspace_path"] = str(workspace_path)
    config = parse_config(raw, tmp_path / "config.yaml").resolved

    issues = validate_resolved_paths(config)

    assert any(issue.code == "invalid-group-workspace" for issue in issues)


def test_missing_restricted_root_fails_closed(tmp_path, raw_config):
    raw, _ = _resolved_config(tmp_path, raw_config)
    raw["groups"]["newsletter"]["runtime"]["sandbox"]["roots"] = [
        str(tmp_path / "missing-root")
    ]
    config = parse_config(raw, tmp_path / "config.yaml").resolved

    issues = validate_resolved_paths(config)

    assert any(issue.code == "invalid-sandbox-root" for issue in issues)


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
    raw["groups"]["newsletter"]["path"] = str(runtime)
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


def test_resolved_group_paths_have_no_shared_segment(tmp_path, raw_config):
    from agency.configuration.group_paths import resolve_group_paths
    from agency.configuration.models import parse_config

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    raw_config["groups"]["newsletter"]["workspace_path"] = str(workspace)
    raw_config["groups"]["newsletter"]["path"] = str(
        tmp_path / "groups" / "newsletter"
    )
    group = parse_config(raw_config, tmp_path / "config.yaml").resolved.groups[
        "newsletter"
    ]

    paths = resolve_group_paths(group)

    assert paths.workspace_root == workspace.resolve()
    assert paths.group_root == (tmp_path / "groups" / "newsletter").resolve()
    assert paths.observations == paths.group_root / "observations"
    assert paths.proposals == paths.group_root / "proposals"
    assert paths.decisions == paths.group_root / "decisions"
    assert paths.locks == paths.group_root / "locks"
    assert paths.logs == paths.group_root / "logs"
    assert "shared" not in {
        part for path in paths.record_directories for part in path.parts
    }


def test_initialization_creates_group_state_but_not_workspace_shared(
    tmp_path, raw_config
):
    from agency.configuration.models import parse_config
    from agency.configuration.paths import initialize_storage_directories

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    raw_config["groups"]["newsletter"]["workspace_path"] = str(workspace)
    raw_config["groups"]["newsletter"]["path"] = str(
        tmp_path / "groups" / "newsletter"
    )
    config = parse_config(raw_config, tmp_path / "config.yaml").resolved

    initialize_storage_directories(config)

    group_root = tmp_path / "groups" / "newsletter"
    assert {
        child.name for child in group_root.iterdir() if child.is_dir()
    } == {"observations", "proposals", "decisions", "locks", "logs"}
    assert not (workspace / "shared").exists()


@pytest.mark.parametrize(
    ("field", "other_authority"),
    [
        ("workspace_path", "agency.memory_store"),
        ("path", "agency.agent_library"),
        ("path", "groups.other.path"),
        ("path", "groups.other.workspace_path"),
        ("workspace_path", "path"),
    ],
)
def test_group_authorities_must_not_overlap(
    tmp_path, raw_config, field, other_authority
):
    raw = deepcopy(raw_config)
    group = raw["groups"]["newsletter"]
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    group["workspace_path"] = str(workspace)
    group["path"] = str(tmp_path / "groups" / "newsletter")

    if other_authority.startswith("agency."):
        _, agency_field = other_authority.split(".", 1)
        group[field] = raw["agency"][agency_field]
    elif other_authority == "path":
        group[field] = group["path"]
    else:
        other_workspace = tmp_path / "other-workspace"
        other_workspace.mkdir()
        raw["groups"]["other"] = {
            **deepcopy(group),
            "name": "Other",
            "workspace_path": str(other_workspace),
            "path": str(tmp_path / "groups" / "other"),
        }
        other_field = other_authority.rsplit(".", 1)[-1]
        group[field] = raw["groups"]["other"][other_field]

    config = parse_config(raw, tmp_path / "config.yaml").resolved

    issues = validate_resolved_paths(config)

    assert any(
        issue.code == "unsafe-path-overlap"
        and issue.scope == "groups.newsletter"
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
    raw_config["groups"]["newsletter"]["workspace_path"] = str(workspace)
    cache_root = tmp_path / "cache-root"
    raw_config["agency"]["compilation_cache"] = str(cache_root)
    config = parse_config(raw_config, tmp_path / "config.yaml").resolved
    target = tmp_path / "cache-target"
    target.mkdir()
    mode = _make_hostile_directory_entry(cache_root, target, monkeypatch)

    with pytest.raises(ValueError, match="symlink|reparse"):
        initialize_storage_directories(config)

    assert mode in {"real-link", "simulated-reparse"}