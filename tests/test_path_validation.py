from copy import deepcopy
import os
from pathlib import Path

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