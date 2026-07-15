from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import shutil
from typing import Literal

from agency.blueprints.library import BlueprintLibrary
from agency.configuration import (
    ConfigConflictError,
    ConfigSnapshot,
    ConfigStore,
)
from agency.configuration.issues import ValidationFailed, ValidationIssue
from agency.configuration.models import (
    AgentIdentity,
    AgentInstance,
    MemorySelector,
)
from agency.configuration.patches import (
    create_agent_instance,
    remove_agent_instance,
)
from agency.fs.locks import exclusive_lock
from agency.integrations import get_integration
from agency.jobs import active_jobs
from agency.jobs.store import acquire_group_operation_locks
from agency.memory import (
    MemoryStore,
    ResolvedMemory,
    resolve_memory_selector,
    select_effective_memory,
)
from agency.memory.store import (
    _ensure_canonical_directory,
    _read_canonical_files,
    _replace_canonical_files,
    memory_content_revision,
)


MemoryMode = Literal["copy", "empty"]


@dataclass(frozen=True)
class AgentInstanceCreate:
    name: str
    blueprint: str
    integration: str
    display_name: str

    def to_model(self) -> AgentInstance:
        return AgentInstance(
            name=self.name,
            blueprint=self.blueprint,
            integration=self.integration,
            identity=AgentIdentity(display_name=self.display_name),
        )


@dataclass(frozen=True)
class InstanceMutationResult:
    snapshot: ConfigSnapshot
    instance: AgentInstance


@dataclass(frozen=True)
class RemoveInstanceResult:
    snapshot: ConfigSnapshot
    orphaned_memories: tuple[ResolvedMemory, ...]


@dataclass(frozen=True)
class MovePreview:
    source_group: str
    target_group: str
    agent_name: str
    memory_mode: MemoryMode
    source_memories: tuple[ResolvedMemory, ...]
    destination_memories: tuple[ResolvedMemory, ...]
    blocked_by: tuple[str, ...]
    config_revision: str
    source_revisions: tuple[tuple[str, str | None], ...]
    memory_pairs: tuple[tuple[ResolvedMemory, ResolvedMemory], ...]


class InstanceMoveConflict(RuntimeError):
    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = tuple(reasons)
        super().__init__(", ".join(reasons) or "instance move conflict")


class InstanceMoveRollbackError(RuntimeError):
    def __init__(self, orphaned_targets: tuple[ResolvedMemory, ...]):
        self.orphaned_targets = orphaned_targets
        super().__init__("move failed after creating destination memories")


def list_instances(
    snapshot: ConfigSnapshot,
    group_id: str,
) -> tuple[AgentInstance, ...]:
    return tuple(snapshot.config.groups[group_id].agents.values())


def get_instance(
    snapshot: ConfigSnapshot,
    group_id: str,
    agent_id: str,
) -> AgentInstance:
    return snapshot.config.groups[group_id].agents[agent_id]


def create_instance(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    agent: AgentInstance,
) -> ConfigSnapshot:
    snapshot = store.load()
    group_path = snapshot.config.groups[group_id].path
    with acquire_group_operation_locks(group_path):
        refreshed = store.load()
        if refreshed.revision != expected_revision:
            raise ConfigConflictError(
                "config.yaml changed; reload before saving"
            )
        return create_agent_instance(
            store,
            refreshed.revision,
            group_id,
            agent.model_dump(mode="json", exclude_none=True),
        )


def remove_instance(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    agent_id: str,
) -> ConfigSnapshot:
    snapshot = store.load()
    group_path = snapshot.config.groups[group_id].path
    with acquire_group_operation_locks(group_path):
        refreshed = store.load()
        if refreshed.revision != expected_revision:
            raise ConfigConflictError(
                "config.yaml changed; reload before saving"
            )
        return remove_agent_instance(
            store,
            refreshed.revision,
            group_id,
            agent_id,
        )


def preview_move(
    snapshot: ConfigSnapshot,
    memory_store: MemoryStore,
    source_group: str,
    agent_id: str,
    target_group: str,
    memory_mode: MemoryMode,
) -> MovePreview:
    source = get_instance(snapshot, source_group, agent_id)
    memory_pairs = _resolve_owned_memory_pairs(
        snapshot,
        memory_store,
        source_group=source_group,
        target_group=target_group,
        agent=source,
    )
    source_memories = tuple(source for source, _ in memory_pairs)
    destination_memories = tuple(
        destination for _, destination in memory_pairs
    )
    blocked = list(
        _preview_blocks(
            snapshot,
            source_group,
            target_group,
            agent_id,
            destination_memories,
        )
    )
    source_revisions = tuple(
        (
            resolved.memory_hash,
            _memory_revision_if_present(memory_store, resolved),
        )
        for resolved in source_memories
    )
    return MovePreview(
        source_group=source_group,
        target_group=target_group,
        agent_name=agent_id,
        memory_mode=memory_mode,
        source_memories=source_memories,
        destination_memories=destination_memories,
        blocked_by=tuple(blocked),
        config_revision=snapshot.revision,
        source_revisions=source_revisions,
        memory_pairs=memory_pairs,
    )


def move_instance(
    store: ConfigStore,
    memory_store: MemoryStore,
    preview: MovePreview,
) -> ConfigSnapshot:
    if preview.blocked_by:
        raise InstanceMoveConflict(preview.blocked_by)

    created_targets: list[ResolvedMemory] = []
    snapshot = store.load()
    source_group_path = snapshot.config.groups[preview.source_group].path
    target_group_path = snapshot.config.groups[preview.target_group].path
    with ExitStack() as stack:
        stack.enter_context(
            acquire_group_operation_locks(source_group_path, target_group_path)
        )

        refreshed = store.load()
        if refreshed.revision != preview.config_revision:
            raise ConfigConflictError(
                "config.yaml changed; reload before saving"
            )
        source_agent = get_instance(
            refreshed,
            preview.source_group,
            preview.agent_name,
        )
        memory_pairs = _resolve_owned_memory_pairs(
            refreshed,
            memory_store,
            source_group=preview.source_group,
            target_group=preview.target_group,
            agent=source_agent,
        )
        source_memories = tuple(source for source, _ in memory_pairs)
        destination_memories = tuple(
            destination for _, destination in memory_pairs
        )
        blocked = tuple(
            _preview_blocks(
                refreshed,
                preview.source_group,
                preview.target_group,
                preview.agent_name,
                destination_memories,
            )
        )
        if blocked:
            raise InstanceMoveConflict(blocked)

        unique = {
            resolved.memory_hash: resolved
            for resolved in (*source_memories, *destination_memories)
        }
        for memory_hash in sorted(unique):
            stack.enter_context(
                exclusive_lock(
                    memory_store._lock_path(unique[memory_hash]),
                    wait=True,
                )
            )

        current_revisions = tuple(
            (
                resolved.memory_hash,
                _memory_revision_if_present(memory_store, resolved),
            )
            for resolved in source_memories
        )
        if current_revisions != preview.source_revisions:
            raise InstanceMoveConflict(("source-memory-changed",))

        source_snapshots = {
            source.memory_hash: _read_memory_without_relocking(source)
            for source in source_memories
            if source.directory.exists()
        }
        for resolved in destination_memories:
            if resolved.directory.exists():
                raise InstanceMoveConflict(("destination-memory-exists",))
        try:
            for source_resolved, target_resolved in memory_pairs:
                created_targets.append(target_resolved)
                _ensure_empty_memory_without_relocking(target_resolved)
                if preview.memory_mode == "copy":
                    source_snapshot = source_snapshots.get(
                        source_resolved.memory_hash
                    )
                    if source_snapshot is not None:
                        _replace_canonical_files(
                            target_resolved.directory,
                            source_snapshot.files,
                        )

            updated = store.patch(
                refreshed.revision,
                lambda raw: _apply_move_patch(
                    raw,
                    source_group=preview.source_group,
                    target_group=preview.target_group,
                    agent_name=preview.agent_name,
                ),
            )
        except Exception as exc:
            rollback_errors = []
            for resolved in reversed(created_targets):
                try:
                    if resolved.directory.exists():
                        shutil.rmtree(resolved.directory)
                except OSError:
                    rollback_errors.append(resolved)
            if rollback_errors:
                raise InstanceMoveRollbackError(
                    tuple(rollback_errors)
                ) from exc
            raise
    return updated


class InstanceService:
    def __init__(
        self,
        config_store: ConfigStore,
        library: BlueprintLibrary,
        memory_store: MemoryStore,
    ):
        self.config_store = config_store
        self.library = library
        self.memory_store = memory_store

    def list(self, group_id: str) -> tuple[AgentInstance, ...]:
        snapshot = self.config_store.load()
        return list_instances(snapshot, group_id)

    def get(self, group_id: str, agent_id: str) -> AgentInstance:
        snapshot = self.config_store.load()
        return get_instance(snapshot, group_id, agent_id)

    def create(
        self,
        group_id: str,
        request: AgentInstanceCreate,
    ) -> InstanceMutationResult:
        self.library.inspect(request.blueprint)
        _validate_integration(request.integration)
        snapshot = self.config_store.load()
        updated = create_instance(
            self.config_store,
            snapshot.revision,
            group_id,
            request.to_model(),
        )
        return InstanceMutationResult(
            snapshot=updated,
            instance=updated.config.groups[group_id].agents[request.name],
        )

    def remove(self, group_id: str, agent_id: str) -> RemoveInstanceResult:
        snapshot = self.config_store.load()
        agent = get_instance(snapshot, group_id, agent_id)
        orphaned = _resolve_owned_memories(
            snapshot,
            self.memory_store,
            group_id=group_id,
            agent=agent,
        )
        updated = remove_instance(
            self.config_store,
            snapshot.revision,
            group_id,
            agent_id,
        )
        return RemoveInstanceResult(
            snapshot=updated,
            orphaned_memories=orphaned,
        )

    def preview_move(
        self,
        source_group: str,
        agent_id: str,
        target_group: str,
        memory_mode: MemoryMode,
    ) -> MovePreview:
        snapshot = self.config_store.load()
        return preview_move(
            snapshot,
            self.memory_store,
            source_group,
            agent_id,
            target_group,
            memory_mode,
        )

    def move(self, preview: MovePreview) -> ConfigSnapshot:
        return move_instance(self.config_store, self.memory_store, preview)


def _validate_integration(name: str) -> None:
    try:
        integration = get_integration(name)
    except KeyError as exc:
        raise ValidationFailed(
            (
                ValidationIssue(
                    code="unknown-integration",
                    scope="integration",
                    field="integration",
                    message=f"Integration '{name}' is not registered.",
                    corrective_hint=(
                        "Choose an installed integration or register it "
                        "before creating the instance."
                    ),
                ),
            )
        ) from exc
    issues: list[ValidationIssue] = []
    if not integration.supports_execution:
        issues.append(
            ValidationIssue(
                code="integration-not-executable",
                scope=f"integrations.{name}",
                field="integration",
                message=(
                    f"Integration '{name}' does not support runtime "
                    "execution."
                ),
                corrective_hint=(
                    "Choose an executable integration before creating "
                    "the instance."
                ),
            )
        )
    if integration.projector is None:
        issues.append(
            ValidationIssue(
                code="missing-runtime-projector",
                scope=f"integrations.{name}",
                field="integration",
                message=f"Integration '{name}' has no runtime projector.",
                corrective_hint=(
                    "Choose an integration with a runtime projector "
                    "before creating the instance."
                ),
            )
        )
    if issues:
        raise ValidationFailed(tuple(issues))


def _resolve_owned_memories(
    snapshot: ConfigSnapshot,
    memory_store: MemoryStore,
    *,
    group_id: str,
    agent: AgentInstance,
) -> tuple[ResolvedMemory, ...]:
    resolved: dict[str, ResolvedMemory] = {}
    for selector, routine_id in _owned_memory_specs(agent):
        item = resolve_memory_selector(
            selector,
            job_id="instance-preview",
            group_key=group_id,
            agent_name=agent.name,
            routine_id=routine_id,
            channels=snapshot.config.memory.channels,
            store_root=memory_store.root,
        )
        resolved[item.memory_hash] = item
    return tuple(resolved[key] for key in sorted(resolved))


def _resolve_owned_memory_pairs(
    snapshot: ConfigSnapshot,
    memory_store: MemoryStore,
    *,
    source_group: str,
    target_group: str,
    agent: AgentInstance,
) -> tuple[tuple[ResolvedMemory, ResolvedMemory], ...]:
    pairs: list[tuple[ResolvedMemory, ResolvedMemory]] = []
    for selector, routine_id in _owned_memory_specs(agent):
        pairs.append(
            (
                resolve_memory_selector(
                    selector,
                    job_id="instance-preview",
                    group_key=source_group,
                    agent_name=agent.name,
                    routine_id=routine_id,
                    channels=snapshot.config.memory.channels,
                    store_root=memory_store.root,
                ),
                resolve_memory_selector(
                    selector,
                    job_id="instance-preview",
                    group_key=target_group,
                    agent_name=agent.name,
                    routine_id=routine_id,
                    channels=snapshot.config.memory.channels,
                    store_root=memory_store.root,
                ),
            )
        )
    deduped: dict[tuple[str, str], tuple[ResolvedMemory, ResolvedMemory]] = {}
    for source, target in pairs:
        deduped[(source.memory_hash, target.memory_hash)] = (source, target)
    return tuple(deduped[key] for key in sorted(deduped))


def _owned_memory_specs(
    agent: AgentInstance,
) -> tuple[tuple[MemorySelector, str | None], ...]:
    selectors: list[tuple[MemorySelector, str | None]] = []
    if (
        agent.default_memory is not None
        and agent.default_memory.scope in {"agent", "routine"}
    ):
        selectors.append((agent.default_memory, None))
    for routine in agent.routines:
        selected = select_effective_memory(
            None,
            routine.memory,
            agent.default_memory,
        )
        if selected.scope not in {"agent", "routine"}:
            continue
        routine_id = routine.id if selected.scope == "routine" else None
        selectors.append((selected, routine_id))
    return tuple(selectors)


def _memory_revision_if_present(
    memory_store: MemoryStore,
    resolved: ResolvedMemory,
) -> str | None:
    if not resolved.directory.exists():
        return None
    return _read_memory_without_relocking(resolved).revision


def _read_memory_without_relocking(resolved: ResolvedMemory):
    files = _read_canonical_files(resolved.directory)
    snapshot_type = type(
        "MemorySnapshotLike",
        (),
        {
            "files": files,
            "revision": memory_content_revision(files),
        },
    )
    return snapshot_type()


def _ensure_empty_memory_without_relocking(resolved: ResolvedMemory) -> str:
    directory = _ensure_canonical_directory(resolved)
    if not any(directory.iterdir()):
        (directory / "memory.md").write_bytes(b"")
    files = _read_canonical_files(directory)
    return memory_content_revision(files)


def _preview_blocks(
    snapshot: ConfigSnapshot,
    source_group: str,
    target_group: str,
    agent_id: str,
    destination_memories: tuple[ResolvedMemory, ...],
):
    if agent_id in snapshot.config.groups[target_group].agents:
        yield "target-instance-exists"
        return
    if _has_active_jobs(snapshot, source_group, target_group, agent_id):
        yield "active-jobs"
        return
    if any(resolved.directory.exists() for resolved in destination_memories):
        yield "destination-memory-exists"


def _has_active_jobs(
    snapshot: ConfigSnapshot,
    source_group: str,
    target_group: str,
    agent_id: str,
) -> bool:
    source_path = snapshot.config.groups[source_group].path
    target_path = snapshot.config.groups[target_group].path
    return bool(
        active_jobs(source_path, agent_id)
        or active_jobs(target_path, agent_id)
    )


def _apply_move_patch(
    raw: dict,
    *,
    source_group: str,
    target_group: str,
    agent_name: str,
) -> None:
    groups = raw["groups"]
    source_agents = groups[source_group].setdefault("agents", [])
    target_agents = groups[target_group].setdefault("agents", [])
    if any(
        isinstance(entry, dict) and entry.get("name") == agent_name
        for entry in target_agents
    ):
        raise ValueError(f"Agent already exists: {agent_name}")
    moved = None
    for index, entry in enumerate(source_agents):
        if isinstance(entry, dict) and entry.get("name") == agent_name:
            moved = dict(entry)
            del source_agents[index]
            break
    if moved is None:
        raise KeyError(agent_name)
    target_agents.append(moved)


__all__ = [
    "AgentInstanceCreate",
    "InstanceMoveConflict",
    "InstanceMutationResult",
    "InstanceService",
    "MovePreview",
    "RemoveInstanceResult",
    "create_instance",
    "get_instance",
    "list_instances",
    "move_instance",
    "preview_move",
    "remove_instance",
]
