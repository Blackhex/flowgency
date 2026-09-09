"""Canonical workflow-instance configuration: patches and binding resolution.

Workflow instances are named entries in the canonical control plane. This module
mutates only the addressed workflow through the shared revision-checked config
patch, and resolves a stored instance into a :class:`WorkflowBinding` whose
physical identity (``binding_id``) is independent of the display name, while its
``context_digest`` tracks the blueprint selection, workflow-library root, and the
internal ``context_generation`` fence.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from flowgency.configuration.issues import ValidationFailed, ValidationIssue
from flowgency.configuration.paths import prepare_writable_directory
from flowgency.configuration.store import (
    ConfigConflictError,
    ConfigSnapshot,
    ConfigStore,
)
from flowgency.tickets.errors import StorageUnavailable
from flowgency.tickets.models import StorageBinding, TicketRecord
from flowgency.tickets.storages.base import TicketStorage
from flowgency.workflows.library import WorkflowLibrary, WorkflowSnapshot
from flowgency.workflows.locking import workflow_operation
from flowgency.workflows.models import WorkflowDefinition, _kind_matches_value


@dataclass(frozen=True)
class WorkflowInstancePatch:
    name: str
    blueprint: str
    integration: str
    integration_config: dict[str, Any]


@dataclass(frozen=True)
class WorkflowBinding:
    team_id: str
    workflow_id: str
    blueprint_id: str
    storage: StorageBinding
    context_digest: str


def _normalized_provider_config(integration: str, config: Any) -> Any:
    """Return a spelling-insensitive view of provider settings for comparison.

    Equivalent path spellings must compare equal so a no-op re-save does not look
    like a selection change; normalization here is cwd-independent so it cannot
    silently absorb a real move.
    """
    if not isinstance(config, dict):
        return config
    normalized = dict(config)
    if integration == "local" and normalized.get("root") is not None:
        normalized["root"] = os.path.normcase(
            os.path.normpath(str(normalized["root"]))
        )
    return normalized


def patch_workflow_instance(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    workflow_id: str,
    patch: WorkflowInstancePatch,
    *,
    create: bool = False,
) -> ConfigSnapshot:
    """Create or update a single workflow instance under a team.

    Only the addressed workflow is touched; sibling workflows, agents, and every
    other team field are preserved by the shared config patch. A change to the
    blueprint or provider selection advances ``context_generation``; a rename
    does not.
    """

    def apply(raw: dict[str, Any]) -> None:
        _apply_workflow_instance_patch(raw, team_id, workflow_id, patch, create=create)

    return store.patch(expected_revision, apply)


def _apply_workflow_instance_patch(
    raw: dict[str, Any],
    team_id: str,
    workflow_id: str,
    patch: WorkflowInstancePatch,
    *,
    create: bool,
) -> None:
    teams = raw.get("teams")
    if not isinstance(teams, dict):
        raise TypeError("teams must be a mapping")
    team = teams[team_id]
    if not isinstance(team, dict):
        raise TypeError(f"teams.{team_id} must be a mapping")
    workflows = team.setdefault("workflows", {})
    if not isinstance(workflows, dict):
        raise TypeError(f"teams.{team_id}.workflows must be a mapping")
    if create and workflow_id in workflows:
        raise ValueError(f"Workflow already exists: {workflow_id}")
    if not create and workflow_id not in workflows:
        raise KeyError(workflow_id)
    current = workflows.setdefault(workflow_id, {})
    previous_selection = (
        current.get("blueprint"),
        current.get("integration"),
        _normalized_provider_config(
            current.get("integration"), current.get("integration_config", {})
        ),
    )
    next_selection = (
        patch.blueprint,
        patch.integration,
        _normalized_provider_config(patch.integration, patch.integration_config),
    )
    generation = int(current.get("context_generation", 0))
    if not create and previous_selection != next_selection:
        generation += 1
    current.update(
        {
            "name": patch.name,
            "blueprint": patch.blueprint,
            "integration": patch.integration,
            "integration_config": dict(patch.integration_config),
            "context_generation": generation,
        }
    )


def _preview_workflow_instance(
    store: ConfigStore,
    snapshot: ConfigSnapshot,
    team_id: str,
    workflow_id: str,
    patch: WorkflowInstancePatch,
    *,
    create: bool,
) -> ConfigSnapshot:
    raw = deepcopy(snapshot.raw)
    _apply_workflow_instance_patch(raw, team_id, workflow_id, patch, create=create)
    return ConfigSnapshot(
        path=snapshot.path,
        revision=snapshot.revision,
        raw=raw,
        config=store._validated_config(raw),
    )


def _canonical_library(workflow_library: Path | None) -> str | None:
    if workflow_library is None:
        return None
    return os.path.normcase(str(Path(workflow_library).resolve(strict=False)))


def _context_digest(
    *,
    storage: StorageBinding,
    blueprint_id: str,
    workflow_library: Path | None,
    context_generation: int,
) -> str:
    payload = json.dumps(
        {
            "binding_id": storage.binding_id,
            "blueprint_id": blueprint_id,
            "workflow_library": _canonical_library(workflow_library),
            "context_generation": context_generation,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_workflow_binding(
    snapshot: ConfigSnapshot, team_id: str, workflow_id: str
) -> WorkflowBinding:
    """Resolve a stored workflow instance into its physical and context identity."""
    team = snapshot.config.teams[team_id]
    workflow = team.workflows[workflow_id]
    storage = StorageBinding(
        integration=workflow.integration,
        config=dict(workflow.integration_config),
        team_id=team_id,
        workflow_id=workflow_id,
    )
    context_digest = _context_digest(
        storage=storage,
        blueprint_id=workflow.blueprint,
        workflow_library=snapshot.config.flowgency.workflow_library,
        context_generation=workflow.context_generation,
    )
    return WorkflowBinding(
        team_id=team_id,
        workflow_id=workflow_id,
        blueprint_id=workflow.blueprint,
        storage=storage,
        context_digest=context_digest,
    )


StorageFactory = Callable[[StorageBinding], TicketStorage]


def _compatibility_issue(message: str) -> ValidationIssue:
    return ValidationIssue(
        code="incompatible-blueprint",
        scope="workflow-library",
        field="definition",
        message=message,
        corrective_hint=(
            "Keep every stored ticket's state and declared field types "
            "expressible under the published blueprint."
        ),
    )


def require_compatible(
    definition: WorkflowDefinition, records: tuple[TicketRecord, ...]
) -> None:
    """Reject a candidate that cannot interpret an existing stored ticket.

    Only the current state id and the types of still-declared fields are
    checked. Historical values of fields the candidate no longer declares are
    preserved untouched, and a newly required transition output need not already
    be present on every ticket.
    """
    state_ids = {state.id for state in definition.states}
    field_kinds = {field.id: field.type for field in definition.fields}
    issues: list[ValidationIssue] = []
    for record in records:
        if record.state_id not in state_ids:
            issues.append(
                _compatibility_issue(
                    f"Ticket {record.id!r} is in state {record.state_id!r}, "
                    "which the published blueprint no longer declares."
                )
            )
        for field_id, value in record.field_values.items():
            kind = field_kinds.get(field_id)
            if kind is None or value is None:
                continue
            if not _kind_matches_value(kind, value):
                issues.append(
                    _compatibility_issue(
                        f"Ticket {record.id!r} field {field_id!r} value does not "
                        f"match the published field type {kind!r}."
                    )
                )
    if issues:
        raise ValidationFailed(issues)


class WorkflowConfigurationService:
    """Publish blueprints and rebind workflow instances under ordered locks."""

    def __init__(
        self,
        store: ConfigStore,
        library: WorkflowLibrary,
        storage_factory: StorageFactory,
    ) -> None:
        self.store = store
        self.storage_factory = storage_factory
        self._library = library
        self._library_root = _canonical_library(library.root)
        self._library_guard = threading.Lock()

    def library_for(self, snapshot: ConfigSnapshot) -> WorkflowLibrary:
        """Return a library bound to the guarded current library root.

        A cached library object is reused only while the normalized root still
        matches, so an external change to ``workflow_library`` is not ignored by
        a constructor-time object held by the ``get_services`` cache. The cached
        ``(object, root)`` pair is read and written atomically, and the locally
        selected library is returned so a concurrent call cannot hand back a
        library mismatched with the snapshot it was resolved from.
        """
        root = snapshot.config.flowgency.workflow_library
        normalized = _canonical_library(root)
        with self._library_guard:
            cached = self._library
            cached_root = self._library_root
        if cached is not None and cached_root == normalized:
            return cached
        if root is None:
            raise ConfigConflictError("No workflow library is configured")
        library = WorkflowLibrary(root)
        with self._library_guard:
            self._library = library
            self._library_root = normalized
        return library

    def teams_using(self, blueprint_id: str) -> tuple[str, ...]:
        snapshot = self.store.load()
        return self._teams_using(snapshot, blueprint_id)

    @staticmethod
    def _teams_using(snapshot: ConfigSnapshot, blueprint_id: str) -> tuple[str, ...]:
        teams = {
            team_id
            for team_id, team in snapshot.config.teams.items()
            if any(
                workflow.blueprint == blueprint_id
                for workflow in team.workflows.values()
            )
        }
        return tuple(sorted(teams))

    def bindings_using(
        self, snapshot: ConfigSnapshot, blueprint_id: str
    ) -> tuple[WorkflowBinding, ...]:
        bindings: list[WorkflowBinding] = []
        for team_id, team in snapshot.config.teams.items():
            for workflow_id, workflow in team.workflows.items():
                if workflow.blueprint == blueprint_id:
                    bindings.append(
                        resolve_workflow_binding(snapshot, team_id, workflow_id)
                    )
        return tuple(bindings)

    def save_instance(
        self,
        expected_revision: str,
        team_id: str,
        workflow_id: str,
        patch: WorkflowInstancePatch,
        *,
        create: bool = False,
    ) -> ConfigSnapshot:
        """Create or rebind a single workflow instance under the operation guard.

        The selected blueprint's current source and the destination namespace are
        validated first: a missing/invalid blueprint, an unavailable destination,
        or an existing ticket the candidate cannot interpret leaves config bytes,
        old tickets, and destination data untouched. A rebind is not a transfer –
        existing tickets under the previous storage are simply hidden by the new
        selection. The candidate config is validated without committing. Local
        storage roots are prepared only when a new binding selection is created or
        chosen; a display-name-only save keeps the binding untouched and does not
        depend on the current storage remaining readable.
        """
        lock_blueprints = self._locked_blueprints(team_id, workflow_id, patch, create)
        with workflow_operation(
            self.store, (team_id,), lock_blueprints, expected_revision=expected_revision
        ) as snapshot:
            current_workflow = None
            if not create:
                current_workflow = snapshot.config.teams[team_id].workflows[workflow_id]
            selection_changed = create or current_workflow is None or (
                current_workflow.blueprint != patch.blueprint
                or current_workflow.integration != patch.integration
                or _normalized_provider_config(
                    current_workflow.integration,
                    current_workflow.integration_config,
                )
                != _normalized_provider_config(
                    patch.integration,
                    patch.integration_config,
                )
            )
            library = self.library_for(snapshot)
            source = library.inspect(patch.blueprint)
            candidate = WorkflowDefinition.model_validate(source.definition.model_dump())
            candidate_snapshot = _preview_workflow_instance(
                self.store,
                snapshot,
                team_id,
                workflow_id,
                patch,
                create=create,
            )
            destination = resolve_workflow_binding(
                candidate_snapshot, team_id, workflow_id
            ).storage
            if selection_changed:
                destination_root = Path(destination.config["root"])
                if create and destination.integration == "local" and not destination_root.exists():
                    destination_records: tuple[TicketRecord, ...] = ()
                else:
                    destination_records = self._destination_records(destination)
                require_compatible(candidate, destination_records)
            else:
                try:
                    require_compatible(candidate, self._destination_records(destination))
                except ValidationFailed:
                    pass
            if library.inspect(patch.blueprint).digest != source.digest:
                raise ConfigConflictError(
                    "Blueprint source changed; reload before saving"
                )
            if selection_changed and destination.integration == "local":
                prepare_writable_directory(
                    Path(destination.config["root"]),
                    label="workflow storage root",
                )
            return patch_workflow_instance(
                self.store,
                snapshot.revision,
                team_id,
                workflow_id,
                patch,
                create=create,
            )

    def _locked_blueprints(
        self,
        team_id: str,
        workflow_id: str,
        patch: WorkflowInstancePatch,
        create: bool,
    ) -> tuple[str, ...]:
        """Blueprint ids to lock: the new selection plus, for a rebind, the old one.

        The current config is read only to choose locks; the guarded snapshot is
        re-resolved inside :func:`workflow_operation`.
        """
        blueprint_ids = {patch.blueprint}
        if not create:
            try:
                workflow = (
                    self.store.load().config.teams[team_id].workflows[workflow_id]
                )
            except KeyError:
                workflow = None
            if workflow is not None:
                blueprint_ids.add(workflow.blueprint)
        return tuple(sorted(blueprint_ids))

    def _destination_records(self, storage: StorageBinding) -> tuple[TicketRecord, ...]:
        """List the destination namespace, treating an unavailable one as a block.

        A new instance may target an already populated namespace; callers prepare
        a new local root first when needed, then this method checks the actual
        destination namespace that would be selected by the candidate config.
        """
        try:
            return self.storage_factory(storage).list(
                storage.team_id, storage.workflow_id
            )
        except StorageUnavailable as error:
            raise ValidationFailed(
                [
                    _compatibility_issue(
                        "Cannot verify compatibility: destination storage for "
                        f"{storage.workflow_id!r} is unavailable ({error})."
                    )
                ]
            ) from error
    def save_blueprint(
        self,
        expected_revision: str,
        blueprint_id: str,
        expected_digest: str,
        definition: WorkflowDefinition,
    ) -> WorkflowSnapshot:
        """Validate a blueprint change against every referencing board, then publish."""
        teams = self.teams_using(blueprint_id)
        with workflow_operation(
            self.store, teams, (blueprint_id,), expected_revision=expected_revision
        ) as snapshot:
            library = self.library_for(snapshot)
            source = library.inspect(blueprint_id)
            if source.digest != expected_digest:
                raise ConfigConflictError("Blueprint changed; reload before saving")
            candidate = WorkflowDefinition.model_validate(definition.model_dump())
            for binding in self.bindings_using(snapshot, blueprint_id):
                try:
                    records = self.storage_factory(binding.storage).list(
                        binding.team_id, binding.workflow_id
                    )
                except StorageUnavailable as error:
                    raise ValidationFailed(
                        [
                            _compatibility_issue(
                                "Cannot verify compatibility: storage for "
                                f"{binding.workflow_id!r} is unavailable "
                                f"({error})."
                            )
                        ]
                    ) from error
                require_compatible(candidate, records)
            self._assert_config_unchanged(snapshot.revision)
            return library.write_candidate(blueprint_id, expected_digest, candidate)

    def _assert_config_unchanged(self, expected_revision: str) -> None:
        """Detect an external config change immediately before persistence.

        Compatibility reads can span a window in which another writer changes
        bindings or the library root. The config file lock is taken here only
        through :class:`ConfigStore`, never while holding it, so the guard's
        non-reentrant lock is not re-entered.
        """
        if self.store.load().revision != expected_revision:
            raise ConfigConflictError("config.yaml changed; reload before saving")

    def create_blueprint(
        self,
        expected_revision: str,
        blueprint_id: str,
        definition: WorkflowDefinition,
    ) -> WorkflowSnapshot:
        """Create a new blueprint under the shared library guard."""
        with workflow_operation(
            self.store, (), (blueprint_id,), expected_revision=expected_revision
        ) as snapshot:
            library = self.library_for(snapshot)
            self._assert_config_unchanged(snapshot.revision)
            return library.create_candidate(blueprint_id, definition)
