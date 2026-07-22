from __future__ import annotations

import os
from pathlib import Path

from .issues import ValidationIssue
from .models import AgencyConfig


def job_store_root(memory_store: Path) -> Path:
    return (Path(memory_store).resolve() / ".jobs").resolve()


def _issue(
    code: str,
    scope: str,
    field: str,
    message: str,
    hint: str,
) -> ValidationIssue:
    return ValidationIssue(code, scope, field, message, hint)


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _overlap(left: Path, right: Path) -> bool:
    left_key = Path(_path_key(left))
    right_key = Path(_path_key(right))
    return left_key == right_key or left_key in right_key.parents or right_key in left_key.parents


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = path.resolve(strict=False)
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent
    return candidate


def _validate_existing_directory(
    path: Path,
    *,
    code: str,
    scope: str,
    field: str,
    writable: bool,
) -> list[ValidationIssue]:
    path = path.resolve(strict=False)
    issues: list[ValidationIssue] = []
    if not path.exists() or not path.is_dir():
        issues.append(
            _issue(
                code,
                scope,
                field,
                f"Configured path must exist as a directory: {path}",
                "Create the directory and grant Agency the required access.",
            )
        )
        return issues
    required = os.R_OK | (os.W_OK if writable else 0)
    if not os.access(path, required):
        issues.append(
            _issue(
                code,
                scope,
                field,
                f"Configured directory is not {'readable and writable' if writable else 'readable'}: {path}",
                "Grant Agency the required filesystem permissions.",
            )
        )
    return issues


def _validate_control_directory(
    path: Path,
    field: str,
    *,
    scope: str = "agency",
    code: str = "invalid-control-directory",
) -> list[ValidationIssue]:
    path = path.resolve(strict=False)
    if path.exists():
        return _validate_existing_directory(
            path,
            code=code,
            scope=scope,
            field=field,
            writable=True,
        )
    parent = _nearest_existing_parent(path)
    if parent is None or not parent.is_dir() or not os.access(parent, os.W_OK):
        return [
            _issue(
                "unwritable-control-parent",
                scope,
                field,
                f"No writable existing parent can create configured directory: {path}",
                "Choose a local path whose nearest existing parent is writable.",
            )
        ]
    return []


def validate_resolved_paths(config: AgencyConfig) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    library = Path(config.agency.agent_library).resolve()
    cache = Path(config.agency.compilation_cache).resolve()
    memory = Path(config.agency.memory_store).resolve()
    authority = job_store_root(memory)

    issues.extend(
        _validate_existing_directory(
            library,
            code="invalid-agent-library",
            scope="agency",
            field="agent_library",
            writable=False,
        )
    )
    issues.extend(_validate_control_directory(cache, "compilation_cache"))
    issues.extend(_validate_control_directory(memory, "memory_store"))

    runtime_paths: list[tuple[str, str, Path]] = []
    for group_id, group in config.groups.items():
        scope = f"groups.{group_id}"
        issues.extend(
            _validate_existing_directory(
                group.workspace_path,
                code="invalid-group-workspace",
                scope=scope,
                field="workspace_path",
                writable=True,
            )
        )
        issues.extend(
            _validate_control_directory(
                group.path,
                "path",
                scope=scope,
                code="invalid-group-path",
            )
        )
        runtime_paths.append((scope, "workspace_path", group.workspace_path.resolve()))
        runtime_paths.append((scope, "path", group.path.resolve(strict=False)))
        if group.runtime.sandbox.mode == "restricted":
            for index, root in enumerate(group.runtime.sandbox.roots):
                issues.extend(
                    _validate_existing_directory(
                        root,
                        code="invalid-sandbox-root",
                        scope=scope,
                        field=f"runtime.sandbox.roots[{index}]",
                        writable=True,
                    )
                )
                runtime_paths.append((scope, f"runtime.sandbox.roots[{index}]", root.resolve()))
        for agent_id, agent in group.agents.items():
            for index, root in enumerate(agent.runtime.sandbox.additional_roots):
                agent_scope = f"{scope}.agents.{agent_id}"
                issues.extend(
                    _validate_existing_directory(
                        root,
                        code="invalid-sandbox-root",
                        scope=agent_scope,
                        field=f"runtime.sandbox.additional_roots[{index}]",
                        writable=True,
                    )
                )
                runtime_paths.append(
                    (agent_scope, f"runtime.sandbox.additional_roots[{index}]", root.resolve())
                )

    controls = (
        ("agent_library", library),
        ("compilation_cache", cache),
        ("memory_store", memory),
    )
    for index, (left_name, left_path) in enumerate(controls):
        for right_name, right_path in controls[index + 1 :]:
            if _overlap(left_path, right_path):
                issues.append(
                    _issue(
                        "unsafe-path-overlap",
                        "agency",
                        f"{left_name},{right_name}",
                        f"Control-plane paths must not overlap: {left_path} and {right_path}",
                        "Use disjoint local directories for library, cache, and memory.",
                    )
                )
    for scope, field, runtime_path in runtime_paths:
        for control_name, control_path in controls:
            if _overlap(control_path, runtime_path):
                issues.append(
                    _issue(
                        "unsafe-path-overlap",
                        scope,
                        field,
                        f"Runtime-writable path overlaps {control_name}: {runtime_path} and {control_path}",
                        "Move control-plane storage and runtime-writable roots into disjoint directories.",
                    )
                )
        if _overlap(authority, runtime_path):
            issues.append(
                _issue(
                    "unsafe-job-store-overlap",
                    scope,
                    field,
                    f"Authoritative job store overlaps a runtime-writable path: {authority}",
                    "Keep memory_store/.jobs outside every workspace and sandbox root.",
                )
            )
    return tuple(
        sorted(issues, key=lambda item: (item.scope, item.field, item.code, item.message))
    )


def initialize_control_directories(config: AgencyConfig) -> None:
    for path in (
        Path(config.agency.compilation_cache),
        Path(config.agency.memory_store),
        job_store_root(Path(config.agency.memory_store)),
        *(Path(group.path) for group in config.groups.values()),
    ):
        path.mkdir(parents=True, exist_ok=True)


__all__ = [
    "initialize_control_directories",
    "job_store_root",
    "validate_resolved_paths",
]