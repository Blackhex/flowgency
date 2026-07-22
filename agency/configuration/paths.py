from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from .group_paths import resolve_group_paths
from .issues import ValidationIssue
from .models import AgencyConfig


def job_store_root(memory_store: Path) -> Path:
    return (Path(memory_store).resolve(strict=False) / ".jobs").resolve(
        strict=False
    )


def _issue(
    code: str,
    scope: str,
    field: str,
    message: str,
    hint: str,
) -> ValidationIssue:
    return ValidationIssue(code, scope, field, message, hint)


@dataclass(frozen=True)
class _Authority:
    scope: str
    field: str
    label: str
    path: Path


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _overlap(left: Path, right: Path) -> bool:
    left_key = Path(_path_key(left))
    right_key = Path(_path_key(right))
    return (
        left_key == right_key
        or left_key in right_key.parents
        or right_key in left_key.parents
    )


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = Path(path)
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent
    return candidate


def _stat_is_symlink_or_reparse(stat_result: os.stat_result) -> bool:
    file_attributes = getattr(stat_result, "st_file_attributes", 0) or 0
    return bool(
        stat.S_ISLNK(stat_result.st_mode)
        or (
            file_attributes
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    )


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    return _stat_is_symlink_or_reparse(stat_result)


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
    if _is_symlink_or_reparse(path):
        issues.append(
            _issue(
                code,
                scope,
                field,
                f"Configured path must be a real directory, not a symlink or reparse point: {path}",
                "Use a real local directory that Agency can access directly.",
            )
        )
        return issues
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


def _validate_creatable_directory(
    path: Path,
    *,
    code: str,
    scope: str,
    field: str,
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
    if (
        parent is None
        or _is_symlink_or_reparse(parent)
        or not parent.is_dir()
        or not os.access(parent, os.W_OK)
    ):
        return [
            _issue(
                "unwritable-control-parent",
                scope,
                field,
                f"No writable real parent can create configured directory: {path}",
                "Choose a local path whose nearest existing parent is a writable real directory.",
            )
        ]
    return []


def _overlap_issue(
    authority: _Authority,
    other: _Authority,
    *,
    hint: str,
) -> ValidationIssue:
    return _issue(
        "unsafe-path-overlap",
        authority.scope,
        authority.field,
        f"Resolved authority {authority.label}={authority.path} overlaps {other.label}={other.path}",
        hint,
    )


def validate_resolved_paths(config: AgencyConfig) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    library = Path(config.agency.agent_library).resolve(strict=False)
    cache = Path(config.agency.compilation_cache).resolve(strict=False)
    memory = Path(config.agency.memory_store).resolve(strict=False)
    control_authorities = (
        _Authority("agency", "agent_library", "agency.agent_library", library),
        _Authority(
            "agency",
            "compilation_cache",
            "agency.compilation_cache",
            cache,
        ),
        _Authority("agency", "memory_store", "agency.memory_store", memory),
    )
    group_paths = {
        group_id: resolve_group_paths(group)
        for group_id, group in config.groups.items()
    }

    issues.extend(
        _validate_existing_directory(
            library,
            code="invalid-agent-library",
            scope="agency",
            field="agent_library",
            writable=False,
        )
    )
    issues.extend(
        _validate_creatable_directory(
            cache,
            code="invalid-control-directory",
            scope="agency",
            field="compilation_cache",
        )
    )
    issues.extend(
        _validate_creatable_directory(
            memory,
            code="invalid-control-directory",
            scope="agency",
            field="memory_store",
        )
    )

    authorities = list(control_authorities)
    for group_id, paths in group_paths.items():
        scope = f"groups.{group_id}"
        issues.extend(
            _validate_existing_directory(
                paths.workspace_root,
                code="invalid-group-workspace",
                scope=scope,
                field="workspace_path",
                writable=True,
            )
        )
        issues.extend(
            _validate_creatable_directory(
                paths.group_root,
                code="invalid-group-root",
                scope=scope,
                field="path",
            )
        )
        authorities.extend(
            (
                _Authority(
                    scope,
                    "workspace_path",
                    f"{scope}.workspace_path",
                    paths.workspace_root,
                ),
                _Authority(scope, "path", f"{scope}.path", paths.group_root),
            )
        )

    for index, left in enumerate(authorities):
        for right in authorities[index + 1 :]:
            if not _overlap(left.path, right.path):
                continue
            hint = (
                "Use disjoint local directories for global stores, group roots, and workspaces."
            )
            issues.append(_overlap_issue(left, right, hint=hint))
            issues.append(_overlap_issue(right, left, hint=hint))

    for group_id, group in config.groups.items():
        scope = f"groups.{group_id}"
        if group.runtime.sandbox.mode == "restricted":
            for index, root in enumerate(group.runtime.sandbox.roots):
                field = f"runtime.sandbox.roots[{index}]"
                issues.extend(
                    _validate_existing_directory(
                        root,
                        code="invalid-sandbox-root",
                        scope=scope,
                        field=field,
                        writable=True,
                    )
                )
                root_authority = _Authority(
                    scope,
                    field,
                    f"{scope}.{field}",
                    root.resolve(strict=False),
                )
                for control in control_authorities:
                    if _overlap(control.path, root_authority.path):
                        issues.append(
                            _overlap_issue(
                                root_authority,
                                control,
                                hint="Move control-plane storage and configured runtime-writable roots into disjoint directories.",
                            )
                        )
        for agent_id, agent in group.agents.items():
            agent_scope = f"{scope}.agents.{agent_id}"
            for index, root in enumerate(agent.runtime.sandbox.additional_roots):
                field = f"runtime.sandbox.additional_roots[{index}]"
                issues.extend(
                    _validate_existing_directory(
                        root,
                        code="invalid-sandbox-root",
                        scope=agent_scope,
                        field=field,
                        writable=True,
                    )
                )
                root_authority = _Authority(
                    agent_scope,
                    field,
                    f"{agent_scope}.{field}",
                    root.resolve(strict=False),
                )
                for control in control_authorities:
                    if _overlap(control.path, root_authority.path):
                        issues.append(
                            _overlap_issue(
                                root_authority,
                                control,
                                hint="Move control-plane storage and configured runtime-writable roots into disjoint directories.",
                            )
                        )

    return tuple(
        sorted(
            issues,
            key=lambda item: (item.scope, item.field, item.code, item.message),
        )
    )


def _assert_real_directory(path: Path) -> None:
    try:
        stat_result = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"Missing directory: {path}") from exc
    if _stat_is_symlink_or_reparse(stat_result):
        raise ValueError(
            f"Directory must not be a symlink or reparse point: {path}"
        )
    if not stat.S_ISDIR(stat_result.st_mode):
        raise ValueError(f"Path is not a directory: {path}")


def _path_chain(path: Path) -> tuple[Path, ...]:
    chain: list[Path] = []
    current = Path(path)
    while True:
        chain.append(current)
        parent = current.parent
        if parent == current:
            return tuple(reversed(chain))
        current = parent


def _ensure_real_directory(path: Path, *, create: bool) -> Path:
    for component in _path_chain(Path(path)):
        if component.exists():
            _assert_real_directory(component)
            continue
        if not create:
            raise ValueError(f"Missing directory: {component}")
        component.mkdir(exist_ok=True)
        _assert_real_directory(component)
    return Path(path).resolve(strict=False)


def initialize_storage_directories(config: AgencyConfig) -> None:
    directories = [
        Path(config.agency.compilation_cache),
        Path(config.agency.memory_store),
        job_store_root(Path(config.agency.memory_store)),
    ]
    for group in config.groups.values():
        paths = resolve_group_paths(group)
        directories.extend((paths.group_root, *paths.record_directories))
    for path in directories:
        _ensure_real_directory(path, create=True)


__all__ = [
    "initialize_storage_directories",
    "job_store_root",
    "validate_resolved_paths",
]
