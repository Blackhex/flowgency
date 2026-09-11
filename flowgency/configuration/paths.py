from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from .team_paths import resolve_team_paths
from .issues import ValidationIssue
from .models import FlowgencyConfig


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


class DirectoryPreparationError(ValueError):
    """A requested writable directory could not be prepared safely."""


def prepare_writable_directory(path: Path, *, label: str) -> Path:
    """Create if needed, revalidate, and return a strict real writable path."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise DirectoryPreparationError(f"{label} must be an absolute path.")

    try:
        if is_symlink_or_reparse(candidate):
            raise DirectoryPreparationError(
                f"{label} must be a real directory, not a symlink or reparse point: "
                f"{candidate}"
            )
        if candidate.exists() and not candidate.is_dir():
            raise DirectoryPreparationError(
                f"{label} must be a directory: {candidate}"
            )
        if not candidate.exists():
            parent = _nearest_existing_parent(candidate)
            if (
                parent is None
                or is_symlink_or_reparse(parent)
                or not parent.is_dir()
                or not os.access(parent, os.W_OK)
            ):
                raise DirectoryPreparationError(
                    f"No writable real parent can create {label}: {candidate}"
                )
            candidate.mkdir(parents=True, exist_ok=True)

        if is_symlink_or_reparse(candidate):
            raise DirectoryPreparationError(
                f"{label} must be a real directory, not a symlink or reparse point: "
                f"{candidate}"
            )
        resolved = candidate.resolve(strict=True)
    except DirectoryPreparationError:
        raise
    except OSError as exc:
        raise DirectoryPreparationError(
            f"Could not create or inspect {label}: {candidate}"
        ) from exc

    if not resolved.is_dir():
        raise DirectoryPreparationError(f"{label} must be a directory: {resolved}")
    if not os.access(resolved, os.R_OK | os.W_OK):
        raise DirectoryPreparationError(
            f"{label} is not readable and writable: {resolved}"
        )
    return resolved


def _stat_is_symlink_or_reparse(stat_result: os.stat_result) -> bool:
    file_attributes = getattr(stat_result, "st_file_attributes", 0) or 0
    return bool(
        stat.S_ISLNK(stat_result.st_mode)
        or (
            file_attributes
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    )


def is_symlink_or_reparse(path: Path) -> bool:
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
    if is_symlink_or_reparse(path):
        issues.append(
            _issue(
                code,
                scope,
                field,
                f"Configured path must be a real directory, not a symlink or reparse point: {path}",
                "Use a real local directory that Flowgency can access directly.",
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
                "Create the directory and grant Flowgency the required access.",
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
                "Grant Flowgency the required filesystem permissions.",
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
        or is_symlink_or_reparse(parent)
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


def validate_resolved_paths(config: FlowgencyConfig) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    library = Path(config.flowgency.agent_library).resolve(strict=False)
    cache = Path(config.flowgency.compilation_cache).resolve(strict=False)
    memory = Path(config.flowgency.memory_store).resolve(strict=False)
    prompt_store = Path(config.flowgency.prompt_store).resolve(strict=False)
    control_authorities = (
        _Authority("flowgency", "agent_library", "flowgency.agent_library", library),
        _Authority(
            "flowgency",
            "compilation_cache",
            "flowgency.compilation_cache",
            cache,
        ),
        _Authority("flowgency", "memory_store", "flowgency.memory_store", memory),
        _Authority("flowgency", "prompt_store", "flowgency.prompt_store", prompt_store),
    )
    team_paths = {
        team_id: resolve_team_paths(team)
        for team_id, team in config.teams.items()
    }

    issues.extend(
        _validate_existing_directory(
            library,
            code="invalid-agent-library",
            scope="flowgency",
            field="agent_library",
            writable=False,
        )
    )
    issues.extend(
        _validate_creatable_directory(
            cache,
            code="invalid-control-directory",
            scope="flowgency",
            field="compilation_cache",
        )
    )
    issues.extend(
        _validate_creatable_directory(
            memory,
            code="invalid-control-directory",
            scope="flowgency",
            field="memory_store",
        )
    )
    issues.extend(
        _validate_creatable_directory(
            prompt_store,
            code="invalid-control-directory",
            scope="flowgency",
            field="prompt_store",
        )
    )

    workflow_library_authority: _Authority | None = None
    if config.flowgency.workflow_library is not None:
        workflow_library = Path(config.flowgency.workflow_library).resolve(
            strict=False
        )
        issues.extend(
            _validate_existing_directory(
                workflow_library,
                code="invalid-workflow-library",
                scope="flowgency",
                field="workflow_library",
                writable=False,
            )
        )
        workflow_library_authority = _Authority(
            "flowgency",
            "workflow_library",
            "flowgency.workflow_library",
            workflow_library,
        )

    authorities = list(control_authorities)
    if workflow_library_authority is not None:
        authorities.append(workflow_library_authority)
    for team_id, paths in team_paths.items():
        scope = f"teams.{team_id}"
        issues.extend(
            _validate_existing_directory(
                paths.workspace_root,
                code="invalid-team-workspace",
                scope=scope,
                field="workspace_path",
                writable=True,
            )
        )
        issues.extend(
            _validate_creatable_directory(
                paths.team_root,
                code="invalid-team-root",
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
                _Authority(scope, "path", f"{scope}.path", paths.team_root),
            )
        )

    for index, left in enumerate(authorities):
        for right in authorities[index + 1 :]:
            if not _overlap(left.path, right.path):
                continue
            hint = (
                "Use disjoint local directories for global stores, team roots, and workspaces."
            )
            issues.append(_overlap_issue(left, right, hint=hint))
            issues.append(_overlap_issue(right, left, hint=hint))

    for team_id, team in config.teams.items():
        scope = f"teams.{team_id}"
        for workflow_id, workflow in team.workflows.items():
            if workflow.integration != "local":
                continue
            root_value = workflow.integration_config.get("root")
            if root_value is None:
                continue
            workflow_root = Path(str(root_value)).resolve(strict=False)
            workflow_authority = _Authority(
                f"{scope}.workflows.{workflow_id}",
                "integration_config.root",
                f"{scope}.workflows.{workflow_id}.integration_config.root",
                workflow_root,
            )
            hint = (
                "Store ticket roots in a directory disjoint from control-plane "
                "storage, team roots, and source workspaces."
            )
            for other in authorities:
                if not _overlap(other.path, workflow_root):
                    continue
                issues.append(_overlap_issue(workflow_authority, other, hint=hint))
                issues.append(_overlap_issue(other, workflow_authority, hint=hint))

    for team_id, team in config.teams.items():
        scope = f"teams.{team_id}"
        for index, rule in enumerate(team.permissions.rules):
            if rule.path is None:
                continue
            field = f"permissions.rules[{index}].path"
            issues.extend(
                _validate_existing_directory(
                    rule.path,
                    code="invalid-permission-path",
                    scope=scope,
                    field=field,
                    writable=True,
                )
            )
            root_authority = _Authority(
                scope,
                field,
                f"{scope}.{field}",
                rule.path.resolve(strict=False),
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
        for agent_id, agent in team.agents.items():
            agent_scope = f"{scope}.agents.{agent_id}"
            for index, rule in enumerate(agent.permissions.rules):
                if rule.path is None:
                    continue
                field = f"permissions.rules[{index}].path"
                issues.extend(
                    _validate_existing_directory(
                        rule.path,
                        code="invalid-permission-path",
                        scope=agent_scope,
                        field=field,
                        writable=True,
                    )
                )
                root_authority = _Authority(
                    agent_scope,
                    field,
                    f"{agent_scope}.{field}",
                    rule.path.resolve(strict=False),
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


def initialize_storage_directories(config: FlowgencyConfig) -> None:
    directories = [
        Path(config.flowgency.compilation_cache),
        Path(config.flowgency.memory_store),
        Path(config.flowgency.prompt_store),
        job_store_root(Path(config.flowgency.memory_store)),
    ]
    for team in config.teams.values():
        paths = resolve_team_paths(team)
        directories.extend(paths.runtime_directories)
    for path in directories:
        _ensure_real_directory(path, create=True)


def _local_workflow_roots(config: FlowgencyConfig) -> tuple[Path, ...]:
    roots: dict[str, Path] = {}
    for team in config.teams.values():
        for workflow in team.workflows.values():
            if workflow.integration != "local":
                continue
            root_value = workflow.integration_config.get("root")
            if root_value is None:
                continue
            root = Path(str(root_value)).resolve(strict=False)
            roots.setdefault(_path_key(root), root)
    return tuple(roots.values())


def initialize_new_local_workflow_roots(
    config: FlowgencyConfig,
    *,
    previous_config: FlowgencyConfig | None,
) -> None:
    previous_roots = set()
    if previous_config is not None:
        previous_roots = {
            _path_key(root) for root in _local_workflow_roots(previous_config)
        }
    for root in _local_workflow_roots(config):
        if _path_key(root) in previous_roots:
            continue
        _ensure_real_directory(root, create=True)


__all__ = [
    "initialize_new_local_workflow_roots",
    "initialize_storage_directories",
    "job_store_root",
    "validate_resolved_paths",
]
