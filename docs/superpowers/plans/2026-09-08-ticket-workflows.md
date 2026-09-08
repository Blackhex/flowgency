# Ticket Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed observation/proposal/decision pipeline with team-owned, blueprint-defined ticket workflows, live agent operations, Local ticket storage, and the approved Kanban and configuration interfaces.

**Architecture:** One workflow service authorizes operations and evaluates contracts; a separate ticket-storage provider persists generic tickets with atomic revisions and operation receipts. Canonical configuration selects team-owned instances and blueprint/storage bindings, while job-scoped runtime access supplies agent identity and coordinates assignment and active work. The FastAPI/Jinja UI uses user-only operations and never exposes a ticket-state mutation.

**Tech Stack:** Python >=3.11, FastAPI >=0.116, Pydantic >=2.8,<3, PyYAML, portalocker >=3,<4, Jinja2, existing atomic filesystem utilities, pytest, and the repository's Playwright UI suite. Preserve the existing theme tokens and browser tooling. Add transport-specific dependencies only in the task that exercises them.

## Global Constraints

The following requirements apply to every task; quoted lines are copied from the
approved [design specification](../specs/2026-09-08-ticket-workflows-design.md).

- "The existing canonical configuration remains the sole control-plane authority."
- "There is one ownership concept: assignment. Do not add a second per-ticket claim or expiring assignment lease."
- "Only agents can move tickets between workflow states."
- "Sign-off is optional, not a completion rule."
- "Taking another ticket does not create a new durable job."
- "There is no blanket lock based on ticket count."
- "No existing ticket or history is transferred, rewritten, or deleted."
- "Label equality alone does not establish identity."
- "There is no separate `evidence_required` flag."
- "The shared Workflow Library owns blueprint authoring. Its editor has exactly Overview, States, and Transitions tabs; there is no Source tab."
- "No identifier fields are shown in any of these tabs."
- "Shell access is not an assumed prerequisite."
- "An unavailable or unreadable root is not an empty ticket set."
- "The ticket operation is atomic; the external project is not."
- "The same ticket persists throughout its workflow; observations, proposals, and decisions are no longer special built-in kinds of work item."
- Ship Local only. Do not implement GitHub/Azure DevOps providers, record imports, an archive UI, automatic storage migration, event subscriptions, agent-role bindings, user moves, or per-ticket execution worktrees.
- Run all commands from `C:/Projekty/Flowgency/.worktrees/ticket-workflows` on `feat/ticket-workflows`. Do not implement on `master` or run tests from a different checkout.
- Preserve runtime-local and user-owned files. Stage explicit task files only; never stage `config.yaml`, lock files, logs, team data, or another checkout's output.
- The design is committed as `405ba7f`; this plan must be a separate documentation-only commit before application implementation.
- Every implementation task requires its own focused test cycle, review, and Conventional Commit before dependent work.
- Full-suite baseline, whole-branch review, full-suite integration verification, fast-forward-only integration, publishing both branches, and worktree cleanup follow [AGENTS.md](../../../AGENTS.md).

---

## Execution Setup

This is one dependent replacement feature, not three independently deployable
subsystems. Keep unfinished new services unregistered until their owning route or
runtime task supplies a tested consumer; do not add runtime feature-flag authority.

The user authorized planning from the written specification on 2026-09-08 by
invoking `writing-plans`. The specification commit's review-status line describes
the earlier handoff; it is not an outstanding approval blocker for this plan.

- [ ] Confirm the worktree branch and clean status with `git status --short --branch`.
- [ ] Install the worktree package and test dependencies with `python -m pip install -e '.[test]'` only when execution begins, then use the existing Node lockfile for UI dependencies.
- [ ] Establish the baseline with `python -m pytest tests/ -q`. Installed-runtime failures are failures, not automatic skips. Record results and resolve any baseline blocker with the user before implementation.
- [ ] Keep an execution log recording each task's red test, green test, reviewer result, and commit. Do not mark checkboxes complete before their commands run.

On Windows use a PowerShell script block with `Push-Location` / `finally
{ Pop-Location }` if the harness starts the terminal elsewhere. Check `$PWD` before
pytest: this repository has a local `tests` package whose resolution depends on the
active checkout. Do not run independent terminal commands concurrently in the same
persistent shell.

## File and Responsibility Map

Create these ownership groups; do not move unrelated existing modules:

| Group | Files | Responsibility |
| --- | --- | --- |
| Workflow definitions | `flowgency/workflows/models.py`, `rules.py`, `library.py`, `editing.py`, `configuration.py` | Strict blueprint contracts, evaluation, current-source reads, identity-preserving edits, compatible publication and instance changes. |
| Ticket domain | `flowgency/tickets/models.py`, `errors.py`, `service.py`, `transitions.py`, `artifacts.py`, `views.py` | Generic records, authorization, mutation, audit snapshots, safe evidence, board projections. |
| Ticket providers | `flowgency/tickets/storages/base.py`, `local.py`, `registry.py` | Separate integration family; path-safe persistence, locking, revisions and operation receipts. |
| Live access | `flowgency/tickets/access.py`, `protocol.py`, `broker.py`, `mcp_server.py` | Trusted job contexts, provider-neutral operations, live transport and tool schemas. |
| Job coordination | `flowgency/jobs/tickets.py`, `processes.py`; existing resolution/submission/execution/queue/reconciliation | Original-binding cleanup, run reservations, current-context preflight, proven stopped-process cleanup. |
| Runtime adapters | Existing integration models/base and supported CLI adapters; `flowgency/integrations/ticket_tools.py` | Explicit live-ticket capability and per-job tool injection without shell or permission widening. |
| Web UI | New workflow/ticket/library/settings routes, templates and static assets | The approved board, inspector, editor and instance settings; user-only operations. |
| Replacement | Existing app/config/CLI/dashboard/setup/reporting modules | Retire old pipeline authority, preserve unrelated behavior and historical jobs. |
| Verification | New focused pytest modules, existing fixture helpers, Playwright specs and live runtime probes | Real persistence/concurrency/security tests plus approved-layout checks. |

Every new Python package receives an empty `__init__.py`; do not export every
internal symbol from it or introduce compatibility forwarding modules. New public
interfaces are defined in the task that first produces them.

## Canonical Interface Vocabulary

Use these names consistently throughout the plan:

- `WorkflowDefinition`: immutable validated blueprint; a top-level `fields` catalog
  owns field IDs, labels and types. Transitions reference the catalog through
  `FieldUse(field_id, required)` instead of duplicating mutable field definitions.
- `WorkflowSnapshot`: `definition: WorkflowDefinition`, `digest: str`, `source_path: Path`.
- `WorkflowBinding`: current configured `team_id`, `workflow_id`, `blueprint_id`,
  `storage: StorageBinding`, plus a `context_digest` over the relevant configuration
  selection. Display-name edits are excluded from that digest.
- `StorageBinding`: `integration: str`, canonical validated `config: dict`,
  `team_id: str`, `workflow_id: str`, and a computed `binding_id`.
- `TicketRef`: `binding_id: str`, `team_id: str`, `workflow_id: str`, `ticket_id: str`.
- `TicketRecord`: operational record with no blueprint pin. Its `state_id` and
  `field_values` are interpreted using current board context.
- `TicketVersion`: `ref: TicketRef`, `revision: int`, `workflow_digest: str`,
  `context_digest: str`. Every agent mutation includes it.
- `AgentTicketContext`: trusted `job_id`, `team_id`, `agent_name`, `session_id`;
  constructed from registered live access, never parsed from actor JSON.
- `UserTicketContext`: trusted local UI actor and team; no transition authority.
- `TicketOperation`: unique `operation_id` and a canonical request digest.
- `TicketMutationResult`: committed `TicketRecord`, accepted event ID and
  `replayed: bool`; persisted receipts return the original result on replay.

Keep mutable mappings out of cached authority where callers can mutate them.
Pydantic `frozen=True` is not recursive immutability; deep-copy payloads at write
boundaries and serialize/validate on provider reads.

## Task 1: Define Workflow Contracts and Pure Evaluation

**Files**

- Create: `flowgency/workflows/__init__.py`, `models.py`, `rules.py`.
- Create tests: `tests/test_workflow_contracts.py`, `tests/test_workflow_rules.py`.

**Interfaces**

- Consumes: Pydantic 2 and `flowgency.configuration.issues.ValidationIssue`.
- Produces: `FieldDefinition`, `FieldUse`, `StateDefinition`, `Precondition`,
  `AgentCriterion`, `TransitionDefinition`, `WorkflowDefinition`, `ArtifactRef`,
    `CriterionAssessment`, `FieldKind`, `FieldValue` and `ContractError` from
    `workflows.models`. `FieldKind` is `Literal["text", "number", "boolean", "artifact"]`.
- Definition sequences (`states`, `fields`, `transitions`, uses and criteria) are
    tuples. `WorkflowDefinition.state(id)`, `.field(id)`, and `.transition(id)` return
    the matching typed object or raise `ContractError("unknown-reference", ...)`.
- Produces: `evaluate_transition(definition: WorkflowDefinition, transition_id: str,
  state_id: str, current_values: dict[str, FieldValue], supplied_inputs:
  dict[str, FieldValue], outputs: dict[str, FieldValue], assessments:
  tuple[CriterionAssessment, ...]) -> EvaluatedTransition` from `workflows.rules`.
- `EvaluatedTransition` contains destination state, effective inputs, outputs,
  assessments and a serializable transition/field-definition audit snapshot. It
  does not write a ticket or inspect arbitrary project paths.

- [ ] **Step 1: Add the definition fixture and requiredness regression tests.**

Use stable example IDs in tests; production editor IDs are generated later. Define
`sample_definition()` in this test module and move it to the shared fixture helper
only when Task 2 actually needs it.

```python
from decimal import Decimal
import pytest
from flowgency.workflows.models import ContractError, WorkflowDefinition
from flowgency.workflows.rules import validate_field_value


@pytest.mark.parametrize("kind,value", [("boolean", False), ("number", 0)])
def test_required_false_and_zero_are_values(kind, value):
    assert validate_field_value(kind, value, required=True) == value


@pytest.mark.parametrize("value", [None, "", "   "])
def test_required_text_cannot_be_blank(value):
    with pytest.raises(ContractError):
        validate_field_value("text", value, required=True)


@pytest.mark.parametrize("value", [True, "1", float("nan"), Decimal("1")])
def test_number_has_no_implicit_coercion(value):
    with pytest.raises(ContractError):
        validate_field_value("number", value, required=True)
```

- [ ] **Step 2: Run the red check.**

Run `python -m pytest tests/test_workflow_rules.py -q`.
Expected: import failure for the new workflow module, not an environment failure.

- [ ] **Step 3: Implement strict types and the pure validator.**

Use `ConfigDict(extra="forbid", frozen=True)` on all definition models. Use a
`FieldValue` union of strict Boolean/int/float/string, `ArtifactRef`, and `None`;
do not let Pydantic turn `True` into `1`. Artifact references are either an opaque
retained-artifact ID or an HTTPS URL, tagged by `kind`; reject `file:`, relative
paths, credentials in URLs and arbitrary server filesystem paths. Size limits are
explicit: 1 MiB blueprint source, 256 states, 1,024 fields, 2,048 transitions, and
128 criteria per transition. Reject duplicate IDs, undeclared field references,
invalid color hex, missing initial state and malformed comparison values.

The ordinary value validator follows this executable logic:

```python
import math
from flowgency.workflows.models import ArtifactRef, ContractError


def validate_field_value(kind, value, *, required):
    if value is None:
        if required:
            raise ContractError("required-value", "A value is required")
        return None
    if kind == "text" and type(value) is str:
        if required and not value.strip():
            raise ContractError("required-value", "Text must not be blank")
        return value
    if kind == "boolean" and type(value) is bool:
        return value
    if kind == "number" and type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ContractError("invalid-number", "Number must be finite")
        return value
    if kind == "artifact" and isinstance(value, ArtifactRef):
        return value
    raise ContractError("invalid-type", f"Expected {kind}")
```

Declare `validate_field_value(kind: FieldKind, value: object, *, required: bool)
-> FieldValue` in `rules.py` and `ContractError(code: str, message: str,
field_id: str | None = None)` in `models.py`. Use field IDs in structured errors
and resolve display labels only in view/form layers.

- [ ] **Step 4: Add and run transition tests before implementing each case.**

Construct this definition in `sample_definition()` using `model_validate`:

```python
def sample_definition():
    return WorkflowDefinition.model_validate({
        "schema_version": 1,
        "id": "delivery",
        "name": "Delivery",
        "description": "Deliver verified work",
        "initial_state": "review",
        "states": [
            {"id": "review", "name": "Review", "color": "#ebc77c"},
            {"id": "done", "name": "Done", "color": "#7ad7bf"},
        ],
        "fields": [
            {"id": "verdict", "label": "Review verdict", "type": "boolean"},
            {"id": "summary", "label": "Review summary", "type": "text"},
        ],
        "transitions": [{
            "id": "complete", "name": "Complete", "from_state": "review",
            "to_state": "done", "inputs": [{"field_id": "verdict", "required": True}],
            "outputs": [{"field_id": "summary", "required": True}],
            "preconditions": [{"field_id": "verdict", "operator": "equals", "value": True}],
            "criteria": [],
        }],
    })


def test_negative_verdict_cannot_transition():
    with pytest.raises(ContractError, match="precondition"):
        evaluate_transition(
            sample_definition(), "complete", "review", {"verdict": False},
            {}, {"summary": "Reviewed"}, (),
        )
```

Add cases for missing versus explicit null; optional absent fields; typed Equals
and Not equal; missing values never satisfying Not equal; Is present; undeclared
outputs; wrong source state; duplicate/missing/negative criterion assessments;
empty reasoning; output-reference validation and immutable returned snapshots.
Assessments have `criterion_id`, `satisfied: StrictBool`, nonblank `reasoning`, and
`supporting_fields: tuple[str, ...]`. Every defined criterion needs exactly one
positive assessment. References must point to supplied/effective values, not merely
a field that exists in the catalog. No `eval`, shell command or automatic test
execution belongs here.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_workflow_contracts.py tests/test_workflow_rules.py -q`.
Expected: all tests pass. Review the strict coercion and evidence boundaries before
committing `feat(workflows): define transition contracts` with these files only.

## Task 2: Implement Atomic Local Ticket Storage

**Files**

- Create: `flowgency/tickets/__init__.py`, `models.py`, `errors.py`.
- Create: `flowgency/tickets/storages/__init__.py`, `base.py`, `local.py`, `registry.py`.
- Create tests/helpers: `tests/_ticket_helpers.py`, `tests/test_ticket_storage_contract.py`,
  `tests/test_local_ticket_storage.py`, `tests/test_ticket_storage_concurrency.py`.
- Reuse: `flowgency/fs/atomic.py`, `flowgency/fs/locks.py`,
  `tests/_lock_helpers.py`; do not introduce another lock implementation.

**Interfaces**

- Consumes: `ArtifactRef`, `FieldValue` from Task 1; existing atomic-write/lock APIs.
- Produces: `StorageBinding`, `TicketRef`, `TicketRecord`, `TicketEvent`,
  `TicketOperation`, `TicketMutationResult` from `tickets.models`.
- Produces: `TicketStorage` protocol: `create(record, operation)`, `read(ref)`,
  `list(team_id, workflow_id)`, `apply(ref, expected_revision, operation, mutate)`,
  `receipt(ref, operation)`, `check()`; typed returns are `TicketMutationResult`,
  `TicketRecord`, `tuple[TicketRecord, ...]`, `TicketMutationResult`,
  `TicketMutationResult | None`, `StorageHealth`, respectively.
- `mutate: Callable[[TicketRecord], TicketRecord]` runs while the per-ticket lock
  is held. It is internal trusted code, never agent-provided code.
- Produces: `LocalTicketStorage(root: Path, *, clock: Callable[[], datetime])`;
  `resolve_storage(binding: StorageBinding) -> TicketStorage` in `registry.py`.
- Errors in `tickets.errors`: `TicketNotFound`, `StorageUnavailable`,
  `TicketCorrupt`, `TicketConflict`, `OperationConflict`, `TicketForbidden` and
  `WorkflowUnavailable`, all carrying `code`, `message`, and HTTP-safe details.

- [ ] **Step 1: Add a real-filesystem fixture and red atomic-write test.**

`tests/_ticket_helpers.py` provides `ticket_record(ticket_id="ticket-a",
state_id="review", agent=None, active_run=None) -> TicketRecord`, plus
`storage_binding(root, team_id="team-a", workflow_id="board-a") -> StorageBinding`.
Use UTC timestamps and fixed test IDs. Ticket fields are `id`, `number`, `title`,
`description`, `state_id`, `assignee`, `active_run`, `field_values`,
`field_provenance`, `revision`, `events`, `receipts`, `created_at`, and `updated_at`.
Include board namespace in the provider envelope, not a blueprint pin.

```python
def test_failed_replace_keeps_previous_ticket(tmp_path, monkeypatch):
    from flowgency.tickets.storages import local
    from tests._ticket_helpers import ticket_record, storage_binding
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    original = ticket_record()
    binding = storage_binding(tmp_path)
    ref = TicketRef.from_binding(binding, original.id)
    provider.create(original.with_ref(ref), TicketOperation("create-a", "digest-a"))
    before = provider.read(ref)

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(local, "atomic_write_text", fail_write)
    with pytest.raises(StorageUnavailable):
        provider.apply(ref, before.revision, TicketOperation("edit-a", "digest-b"),
                       lambda ticket: ticket.model_copy(update={"title": "Changed"}))
    assert provider.read(ref) == before
```

Define `NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)` and explicit imports in
the test module. `TicketRecord.with_ref(ref)` returns a validated copy; it is not
a persistent mutation. `TicketRef.from_binding` computes the full scoped ref.

- [ ] **Step 2: Run the red check.**

Run `python -m pytest tests/test_local_ticket_storage.py -q`.
Expected: the new provider is missing. Then implement the record/receipt models
and rerun until the failing assertion exercises atomic persistence, not imports.

- [ ] **Step 3: Implement provider transaction semantics and readable documents.**

Use `<root>/<team_id>/<workflow_id>/tickets/<ticket_id>.md`, namespace-local
creation/sequence metadata and per-ticket locks. Encode the validated record and
receipt ledger in YAML frontmatter and Markdown description in the body. The
serialized envelope has `schema_version: 1`. A record and its audit history are
one atomic replace; never append one file and update another as a single claimed
transaction. Use UUID internal IDs and a namespace-scoped integer display number;
gaps after failed creation are allowed, duplicate numbers are not.

The critical update order is:

```python
def apply(self, ref, expected_revision, operation, mutate):
    with exclusive_lock(self.lock_path(ref), wait=True):
        current = self.read(ref)
        previous = current.find_receipt(operation.operation_id)
        if previous is not None:
            previous.require_digest(operation.request_digest)
            return previous.result(replayed=True)
        if current.revision != expected_revision:
            raise TicketConflict("stale-ticket", "Refresh the ticket")
        candidate = TicketRecord.model_validate(mutate(current).model_dump())
        self.require_same_identity(current, candidate)
        updated, result = candidate.commit_operation(current, operation, self.clock())
        self.write_record(updated)
        return result
```

Define the methods used above in this task: `lock_path`, `read`,
`require_same_identity`, `write_record`, `find_receipt`, and `commit_operation`.
`commit_operation` increments revision, stamps updated time, finalizes the event
already supplied by the service callback and stores an original-result snapshot
excluding the receipt ledger itself to avoid recursive growth. It must not append
a second domain event. Provider-only conformance tests may use a system-event
fixture; every production service mutation supplies exactly one trusted audit
event. `receipt.result()` reconstructs that original snapshot, not the ticket's
latest revision. The request digest covers actor, operation kind, target and
payload; a reused ID with different content raises `OperationConflict`.

Reject non-slug path components, paths escaping the configured root, symlinks and
Windows reparse points anywhere below the root. Distinguish missing root,
unreadable root, empty existing namespace and corrupt record. A configured root
is created explicitly by configuration/setup, never recreated because a board
read finds it missing. Namespace creation is permitted only during creation.
Do not ignore invalid records in `list`; return a corruption error with the
ticket ID without exposing absolute private paths.

- [ ] **Step 4: Add concurrency, receipt, isolation and filesystem-failure tests.**

Run two real processes, coordinated by barriers/events using the existing
cross-process helper pattern, against the same revision. Assert one accepted
mutation and one conflict, not two successful updates. Also demonstrate that two
different ticket locks can be held independently.

```python
def test_receipt_replay_precedes_revision_check(provider, ref):
    before = provider.read(ref)
    operation = TicketOperation("rename-a", "rename-digest")
    first = provider.apply(ref, before.revision, operation,
                           lambda ticket: ticket.model_copy(update={"title": "Renamed"}))
    replay = provider.apply(ref, before.revision, operation,
                            lambda ticket: pytest.fail("must not execute twice"))
    assert replay.replayed is True
    assert replay.ticket == first.ticket
    assert len(provider.read(ref).events) == len(first.ticket.events)
```

The same contract suite must be parametrizable over provider factories; Local is
the only factory initially. Cover same local number across namespaces, replacement
failure, duplicate create operation, conflicting receipt digest, truncation/corrupt
YAML, oversized files, traversal, reparse points, and missing versus empty roots.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_ticket_storage_contract.py tests/test_local_ticket_storage.py tests/test_ticket_storage_concurrency.py -q`.
Expected: no skipped concurrency cases on Windows. Review receipt replay,
atomicity and path safety, then commit `feat(tickets): add atomic local storage`.

## Task 3: Register Workflow Instances Through Canonical Configuration

**Files**

- Modify: `flowgency/configuration/models.py`, `paths.py`, `patches.py`.
- Modify existing global settings surfaces: `flowgency/templates/admin_settings.html`,
  `flowgency/app.py`'s `admin_save_settings`, and
  `flowgency/web/state.py`'s `flowgency_settings` projection.
- Create: `flowgency/workflows/configuration.py`.
- Create tests: `tests/test_workflow_configuration.py`.
- Extend: `tests/test_config_patches.py`, `tests/test_config_normalization.py`.

**Interfaces**

- Consumes: `ConfigStore.patch`, `ConfigSnapshot`, the current team model and
  Task 2's provider configuration validation.
- Produces: `WorkflowInstance(name: str, blueprint: str, integration: str,
  integration_config: dict[str, Any], context_generation: int = 0)`, keyed by generated stable ID in
  `TeamConfig.workflows: dict[str, WorkflowInstance]`.
- Produces: optional `FlowgencySettings.workflow_library: Path | None` and
  resolved `WorkflowBinding`; require a library when any workflows are configured.
- Produces: `WorkflowInstancePatch` and `patch_workflow_instance(store:
  ConfigStore, expected_revision: str, team_id: str, workflow_id: str,
  patch: WorkflowInstancePatch, *, create: bool = False) -> ConfigSnapshot`.
- Produces: `resolve_workflow_binding(snapshot: ConfigSnapshot, team_id: str,
  workflow_id: str) -> WorkflowBinding`.

The stored shape is explicitly named, not inferred from a directory:

```yaml
schema_version: 1
flowgency:
  workflow_library: C:/Flowgency/workflow-library
teams:
  newsletter:
    workflows:
      workflow-493fdf7e:
        name: Delivery
        blueprint: blueprint-202e742e
        integration: local
        integration_config:
          root: C:/Flowgency/tickets
```

This is an excerpt, not a full replacement configuration. Preserve all existing
required roots, agents, permissions and runtime settings. Keep control-plane
`schema_version: 1`; introducing optional workflow configuration does not justify
startup conversion. IDs accept current valid slugs and generated UUID-prefixed
slugs. Labels are not keys, so rename never relocates tickets.

- [ ] **Step 1: Add the revision/preservation red test using the existing raw-config fixture.**

```python
def test_workflow_patch_preserves_agents_and_rejects_stale_revision(config_store):
    snapshot = config_store.load()
    patch = WorkflowInstancePatch(
        name="Delivery", blueprint="blueprint-one", integration="local",
        integration_config={"root": "tickets"},
    )
    updated = patch_workflow_instance(
        config_store, snapshot.revision, "newsletter", "workflow-one", patch,
        create=True,
    )
    assert updated.raw["teams"]["newsletter"]["agents"] == snapshot.raw["teams"]["newsletter"]["agents"]
    with pytest.raises(ConfigConflictError):
        patch_workflow_instance(config_store, snapshot.revision, "newsletter",
                                "workflow-one", patch)
```

Extend the fixture to create a readable workflow library and Local root before
the patch. Reuse `config_store` from `tests/test_config_patches.py` there, or
create the same fixture explicitly in the new test module; do not assume fixtures
in another test module are automatically imported.

- [ ] **Step 2: Run the red check.**

Run `python -m pytest tests/test_workflow_configuration.py tests/test_config_patches.py -k workflow -q`.
Expected: missing new symbols or unparsed workflows. Keep existing normalization
tests green as the shape is added.

- [ ] **Step 3: Add typed model, raw-shape validation, resolution and patches.**

Follow `configuration.models`' explicit shape-validation and normalized-model
pipeline. A malformed workflows value must not be silently accepted as an extra
team field. Resolve relative library/root paths against the config directory,
not the current process directory. Store authored relative strings unchanged in
raw YAML; normalized objects carry absolute paths.

Use the existing patch pattern without replacing the whole team mapping:

```python
def patch_workflow_instance(store, expected_revision, team_id, workflow_id,
                            patch, *, create=False):
    def apply(raw):
        team = raw["teams"][team_id]
        workflows = team.setdefault("workflows", {})
        if create and workflow_id in workflows:
            raise ValueError("Workflow already exists")
        if not create and workflow_id not in workflows:
            raise KeyError(workflow_id)
        current = workflows.setdefault(workflow_id, {})
        previous_selection = (current.get("blueprint"), current.get("integration"),
                              current.get("integration_config", {}))
        next_selection = (patch.blueprint, patch.integration, patch.integration_config)
        generation = int(current.get("context_generation", 0))
        if not create and previous_selection != next_selection:
            generation += 1
        current.update({
            "name": patch.name,
            "blueprint": patch.blueprint,
            "integration": patch.integration,
            "integration_config": dict(patch.integration_config),
            "context_generation": generation,
        })
    return store.patch(expected_revision, apply)
```

Retain per-instance unknown extension fields only where consistent with existing
config policy; forbid unknown provider configuration keys in the Local validator.
Do not reach into ticket directories from generic Pydantic validators. Compatibility
checks against current/destination tickets belong to Task 4's workflow-specific
configuration service before this low-level patch is committed.

`context_generation` is internal stale-operation fencing, not a release, adoption
step or editable revision selector. App-mediated changes to blueprint selection
or provider settings advance it; renames do not. Normalize provider settings before
the selection comparison in the final patch implementation so equivalent path
spellings do not create misleading changes. Include the generation in
`WorkflowBinding.context_digest`, not in the storage's physical namespace. An A/B/A
settings switch therefore cannot revive an earlier queued target or mutation.

Workflow root availability is checked by the workflow service at operation time;
do not make a temporarily missing ticket root disable all unrelated app services.
Global path validation still forbids unsafe overlaps and malformed path settings.
Do not add existing ticket roots to unconditional startup directory creation.

Extend `FlowgencySettingsPatch` with optional `workflow_library` at the end of the
dataclass. An omitted value preserves the saved path; an explicit change validates
the new library and advances `context_generation` for affected instances in the
same canonical config patch. Expose that root using the existing global path-setting
style rather than a workflow Source tab. `admin_save_settings` participates in
`revision_bound_team_operation(all_teams=True, expected_revision=revision)` before
the patch; it currently calls the patch directly. Tests must prove a stale global
root change cannot race a ticket mutation and that unrelated setting saves preserve
the workflow root and generation.

- [ ] **Step 4: Add provider-binding identity and no-workflows regressions.**

```python
def test_display_name_is_not_storage_identity(configured_snapshot):
    before = resolve_workflow_binding(configured_snapshot, "newsletter", "workflow-one")
    renamed_snapshot = rename_workflow_fixture(configured_snapshot, "Renamed")
    after = resolve_workflow_binding(renamed_snapshot, "newsletter", "workflow-one")
    assert after.storage.binding_id == before.storage.binding_id
    assert after.context_digest == before.context_digest
```

Define `rename_workflow_fixture` in this test as a real `patch_workflow_instance`
call, not a mock of binding resolution. Hash canonical provider ID, normalized
configuration, team ID and workflow ID for `binding_id`; include the blueprint
selection, canonical workflow-library root and `context_generation` as well for
`context_digest`. Canonicalization normalizes equivalent
Windows paths and uses sorted JSON keys. Changing the root or selected blueprint
changes the relevant digest; labels, display titles and unrelated config edits do
not. Add a real A/B/A patch test showing the physical binding returns to its
original identity while the current context digest does not. External editors
that bypass cooperating writers are detected by current-byte checks when observed;
do not claim to detect an unobserved external change-and-revert atomically.

Also test existing current-shape configurations with no workflows: they load without
a fabricated board or a newly created workflow-library root. Reject a configured
workflow with missing library, unavailable provider, overlapping source workspace
root, wrong provider types or traversal-capable IDs.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_workflow_configuration.py tests/test_config_patches.py tests/test_config_normalization.py -q`.
Review raw-data preservation, path normalization and digest inputs, then commit
`feat(config): register team workflow instances`.

## Task 4: Publish Current Blueprints and Compatible Configuration Changes

**Files**

- Create: `flowgency/workflows/library.py`, `editing.py`, `locking.py`.
- Extend: `flowgency/workflows/configuration.py`.
- Create tests: `tests/test_workflow_library.py`, `tests/test_workflow_editing.py`,
  `tests/test_workflow_compatibility.py`.
- Extend helpers: `tests/_ticket_helpers.py`; add `workflow_env` fixture in
  `tests/conftest.py` using the factory below.

**Interfaces**

- Consumes: Tasks 1-3; `revision_bound_team_operation` from `jobs.store`,
  `ConfigStore`, `exclusive_lock` and `atomic_write_text`.
- Produces: `WorkflowLibrary(root: Path)`, with `inspect(blueprint_id: str)
  -> WorkflowSnapshot`, `list() -> tuple[WorkflowInspection, ...]`, and
  `write_candidate(blueprint_id, expected_digest, definition) -> WorkflowSnapshot`.
  `WorkflowInspection` includes a definition or validation issues; one invalid
  blueprint must not erase the rest of the library listing.
- Produces: `WorkflowConfigurationService(store, library, storage_factory)` with
  `save_instance(expected_revision, team_id, workflow_id, patch, *, create=False)`
  and `save_blueprint(expected_revision, blueprint_id, expected_digest, definition)`.
- Produces: `workflow_operation(store: ConfigStore, team_ids: tuple[str, ...],
  blueprint_ids: tuple[str, ...], *, expected_revision: str | None = None)` context
  manager yielding the reloaded `ConfigSnapshot`; locks never span agent work.
- Produces editing commands in `editing.py`: `new_definition(name)`,
  `add_state(definition, name, color)`, `rename_state(definition, state_id, name)`,
  `move_state(definition, state_id, index)`, `add_transition(definition, name,
  from_state, to_state)`, `add_field(definition, label, kind)`,
  `use_field(definition, transition_id, direction, field_id, required)`, and
  `rename_field(definition, field_id, label)`. Each returns a validated new
    definition in `DefinitionEdit(definition: WorkflowDefinition,
    created_id: str | None)`. Creation commands set `created_id`; editing/reordering
    commands return None there. Use that exact result type for every editing command.

- [ ] **Step 1: Create the shared real-filesystem fixture and a red compatibility test.**

`make_workflow_environment(tmp_path: Path, raw_config: dict) -> WorkflowTestEnv`
deep-copies the existing `raw_config`, creates `workflow-library/delivery/workflow.yaml`
from Task 1's sample definition, sets the library root, creates Local roots A/B,
registers `newsletter.workflows.board-a`, and adds an `observer` agent beside
`builder`. It writes config through `ConfigStore.create`, not mocks. Set a
read-only workspace policy for observer. Return `store`, `library`, `binding`,
`provider`, `root_a`, `root_b` and `configuration_service` on the dataclass.

```python
@pytest.fixture
def workflow_env(tmp_path, raw_config):
    from tests._ticket_helpers import make_workflow_environment
    return make_workflow_environment(tmp_path, raw_config)


def test_state_removal_cannot_rewrite_existing_ticket(workflow_env):
    env = workflow_env
    ref = env.seed_ticket(state_id="review")
    original = env.provider.read(ref)
    source = env.library.inspect("delivery")
    candidate = source.definition.model_copy(update={
        "initial_state": "done",
        "states": tuple(state for state in source.definition.states if state.id != "review"),
        "transitions": (),
    })
    with pytest.raises(ValidationFailed):
        env.configuration_service.save_blueprint(
            env.store.load().revision, "delivery", source.digest, candidate,
        )
    assert env.provider.read(ref) == original
    assert env.library.inspect("delivery").digest == source.digest
```

`WorkflowTestEnv.seed_ticket(state_id="review", values=None)` calls the real
provider create method and returns a `TicketRef`. It is fixture seeding only, not
an agent/user path that bypasses service authorization in production.

- [ ] **Step 2: Run the red check.**

Run `python -m pytest tests/test_workflow_library.py tests/test_workflow_compatibility.py -q`.
Expected: missing publication/compatibility service. Keep failure output so the
eventual passing check proves ticket-state preservation.

- [ ] **Step 3: Implement current-source inspection and ordered publication guards.**

Use `<workflow_library>/<blueprint_id>/workflow.yaml`; reject unsafe path segments,
symlinks/reparse points and files over the Task 1 limit before parsing. Hash source
bytes with SHA-256, parse with `yaml.safe_load`, then validate. No persisted
last-good runtime definition, adoption record, native-file detection or automatic
conversion is permitted. `list` catches per-blueprint validation errors and reports
them; `inspect` never returns a silently older valid snapshot.

All new cooperating writers use this order: existing team-operation locks in
canonical order; blueprint locks in canonical order; a provider namespace lock
only for creation/catalog enumeration if needed; then individual ticket locks.
Configuration file locks are taken only through ConfigStore operations and never
recursively. Do not call `ConfigStore.load()` while holding its own lock; the
current `exclusive_lock` uses a non-reentrant thread lock. Re-read relevant config
and source digests immediately before the persistence step. App-mediated config
writers that can change team paths, agents, workflows or library roots must enter
the same operation guard; audit their exact entry points in Task 14.

The publication body is concrete:

```python
def save_blueprint(self, expected_revision, blueprint_id, expected_digest, definition):
    teams = self.teams_using(blueprint_id)
    with workflow_operation(self.store, teams, (blueprint_id,),
                            expected_revision=expected_revision) as snapshot:
        library = self.library_for(snapshot)
        source = library.inspect(blueprint_id)
        if source.digest != expected_digest:
            raise ConfigConflictError("Blueprint changed; reload before saving")
        candidate = WorkflowDefinition.model_validate(definition.model_dump())
        for binding in self.bindings_using(snapshot, blueprint_id):
            records = self.storage_factory(binding.storage).list(
                binding.team_id, binding.workflow_id,
            )
            require_compatible(candidate, records)
        return library.write_candidate(blueprint_id, expected_digest, candidate)
```

Define `teams_using`, `bindings_using` and
`require_compatible(definition, records) -> None` here. Compatibility checks the
existing state ID and types of still-declared stored fields. Preserve unreferenced
historical field values rather than deleting them. A new required transition
output need not already be present on every ticket. A temporarily unavailable
referencing storage prevents proof of a potentially incompatible publication;
report that exact validation dependency, not a ticket-count lock.

Define `library_for(snapshot: ConfigSnapshot) -> WorkflowLibrary` on the
configuration and ticket services. Resolve its root from the guarded current
configuration, reusing a library object only if the normalized root still matches.
The existing `get_services` cache is keyed by config path, so keeping a constructor-
time library object forever would otherwise ignore external root changes. Current
binding resolution, blueprint locks and reads all use the same guarded root.

External editors cannot be forced to participate in Flowgency locks. Detect
changed source bytes before publication/transition and reject known conflicts;
do not promise an OS transaction across arbitrary external edits and ticket files.
Use the final validated byte snapshot as the operation's audit contract. Add an
external-change race test at the final check boundary.

- [ ] **Step 4: Implement compatible rebinding and stable editing commands, then test them.**

For same-storage blueprint changes, validate the candidate definition against
that storage's current namespace. For storage changes, validate the destination
namespace only; the old tickets remain untouched and hidden by the new selection.
Compare configuration revision inside the guarded low-level patch. Do not create
destination directories during an ordinary read. Initial setup explicitly prepares
the selected root using `prepare_writable_directory`.

```python
def test_storage_switch_is_not_a_transfer(workflow_env):
    env = workflow_env
    old_ref = env.seed_ticket()
    old_record = env.provider.read(old_ref)
    env.set_storage_root(env.root_b)
    assert env.current_provider().list("newsletter", "board-a") == ()
    assert env.provider.read(old_ref) == old_record
    env.set_storage_root(env.root_a)
    assert env.current_provider().read(old_ref) == old_record


def test_rename_keeps_field_references():
    definition = sample_definition()
    edited = rename_field(definition, "verdict", "Review result").definition
    assert edited.field("verdict").label == "Review result"
    assert edited.transition("complete").preconditions[0].field_id == "verdict"
```

`set_storage_root(root)` calls the real configuration service; `current_provider()`
resolves from `store.load()`. Define `WorkflowDefinition.field(id)` and
`.transition(id)` as exact lookup helpers in Task 1. Use `uuid4().hex` with type
prefixes for new definition IDs, preserving existing slug IDs. Reuse fields through
`FieldUse`, never by automatically merging equal labels. Tests must cover blank
labels, duplicate uses, referenced deletion, generated IDs after delete/recreate,
blueprint/state/transition renames and unchanged initial-state references.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_workflow_library.py tests/test_workflow_editing.py tests/test_workflow_compatibility.py tests/test_workflow_configuration.py -q`.
Review all lock acquisition order and external-edit limitations; commit
`feat(workflows): validate current blueprint changes`.

## Task 5: Enforce Ticket Assignment and Multi-Ticket Active Work

**Files**

- Create: `flowgency/tickets/service.py`.
- Extend: `flowgency/tickets/models.py`, `tests/_ticket_helpers.py`, `tests/conftest.py`.
- Create tests: `tests/test_ticket_ownership.py`, `tests/test_ticket_service.py`.

**Interfaces**

- Consumes: Tasks 1-4's definitions, bindings, operation guards and providers.
- Produces trusted contexts `AgentTicketContext(job_id, team_id, agent_name,
  session_id)` and `UserTicketContext(team_id, actor_name="local-user")` in
  `tickets.models`, plus `TicketActor` union, `TicketVersion`, `TicketView(record,
  version, definition, issues)` and `TicketPatch(title=None, description=None,
  field_values=None)`. Actor fields are not accepted by request models.
- `TicketView.version` and `.definition` can be None for a readable ticket whose
    current blueprint is unavailable; such a view has explicit issues and cannot
    be used for mutation. Valid views always include both. `TicketRecord.ref` carries
    the namespace identity assigned by the provider envelope.
- Produces `TicketService(config_store, library, storage_factory,
  validate_agent_context: Callable[[AgentTicketContext], None], clock)`.
- Methods: `inspect(actor, ref) -> TicketView`,
  `create(actor, workflow_id, title, description, values, operation) -> TicketMutationResult`,
  `assign(actor: UserTicketContext, version, assignee: str | None, operation)`,
  `start_work(actor: AgentTicketContext, version, operation)`,
  `end_work(actor: AgentTicketContext, version, operation)`,
  `sign_off(actor: AgentTicketContext, version, operation)`, and
  `update(actor: TicketActor, version, patch: TicketPatch, operation)`.
  Mutation methods return `TicketMutationResult`. `inspect` constructs the current
  `TicketVersion`; `TicketView.ref` derives from `record.ref`.

- [ ] **Step 1: Add a real service fixture and ownership red tests.**

Extend `workflow_env` with `service`, `user`, and test helpers `agent(name, job_id)`,
`create(title="Sample", values=None) -> TicketView`, `read(ref) -> TicketView`, and
`operation(label) -> TicketOperation`. `create` uses `service.create` followed by
`inspect`, not provider seeding. The fixture's context validator accepts only
explicitly registered fixture session IDs; no production default no-op validator.
`operation(label)` computes a unique fixture receipt digest from the label and
test actor; production canonical request digests arrive in Task 7.

```python
def test_another_run_cannot_take_assigned_ticket(workflow_env):
    env = workflow_env
    ticket = env.create()
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign"))
    version = env.read(ticket.ref).version
    with pytest.raises(TicketForbidden):
        env.service.start_work(env.agent("observer", "run-b"), version,
                               env.operation("steal"))
    assert env.read(ticket.ref).record.active_run is None


def test_one_run_works_two_tickets_and_sign_off_is_optional(workflow_env):
    env = workflow_env
    first, second = env.create("First"), env.create("Second")
    actor = env.agent("builder", "run-a")
    for ticket in (first, second):
        env.service.start_work(actor, ticket.version, env.operation(ticket.ref.ticket_id))
    env.service.end_work(actor, env.read(first.ref).version, env.operation("end-first"))
    assert env.read(first.ref).record.assignee == "builder"
    assert env.read(first.ref).record.active_run is None
    assert env.read(second.ref).record.active_run.job_id == "run-a"
```

- [ ] **Step 2: Run the red check.**

Run `python -m pytest tests/test_ticket_ownership.py -q`.
Expected: service operations missing. Do not satisfy tests by mocking storage CAS.

- [ ] **Step 3: Implement ownership inside the provider transaction.**

`start_work` validates the current configured agent, registered run context and
full binding before the atomic callback. An unassigned ticket becomes assigned to
that agent; a ticket assigned to another agent is forbidden; another active run
is a conflict even if its agent name is identical. Starting work does not change
`state_id` or create a job. The callback's core is:

```python
def begin_work(record, actor, now):
    if record.assignee not in (None, actor.agent_name):
        raise TicketForbidden("assigned-elsewhere", "Ticket belongs to another agent")
    if record.active_run is not None and (
        record.active_run.job_id, record.active_run.session_id
    ) != (actor.job_id, actor.session_id):
        raise TicketConflict("already-working", "Another run is working on this ticket")
    return record.model_copy(update={
        "assignee": actor.agent_name,
        "active_run": ActiveTicketRun(job_id=actor.job_id, session_id=actor.session_id,
                                      started_at=now),
    })
```

Define `ActiveTicketRun` in `tickets.models` with job ID, session ID and UTC start
time. Its read-only `generation` property returns `session_id`; there is no second
independently assigned lease token. The trusted session ID identifies the execution
attempt and is also carried in `ProcessStopEvidence.generation`. Repeated accepted `start_work`
uses a receipt, not a second event. Assignment, update, end and sign-off events
stamp actor and time in trusted service code. User assignment requires an idle
record, including when changing to None. Agent sign-off clears both fields only
for its own authorized ticket/run; end-work clears only the active marker.

All agent updates require matching assignee and active run. Reject `state_id`,
`assignee`, `active_run`, actor metadata or arbitrary record fields submitted in
`TicketPatch`. Users may update allowed ticket content/inputs during active work;
the revision changes, invalidating older transition requests. Creation by either
actor uses the blueprint's initial state and is a separate audited operation.

Define `FieldProvenance(actor_kind: Literal["user", "agent"], actor_name: str,
job_id: str | None, event_id: str, recorded_at: datetime)` and stamp it server-side
for every persisted value update. Do not accept provenance in `TicketPatch`,
TransitionRequest or creation payloads. The agent receives provenance with its
ticket data, so a Boolean value is not mislabeled as a verified human approval.
The initial value-comparison operators do not independently prove human approval;
do not advertise an extra provenance-enforcement operator absent from the editor.

Implement `TicketService._mutate(actor, version, operation, mutation)` as the
single new-mutation path: validate caller/session and scope, acquire the workflow
guard, compare current context, and call provider.apply with a callback that checks
ownership and appends exactly one event. Receipt replay stays bound to the original
actor and request digest. Rechecking an operation's old state/assignment inside
the callback must not prevent its accepted receipt from replaying; invalid caller
credentials or a different storage context are still rejected before replay.

- [ ] **Step 4: Add stale-context, cross-team, mutation and race tests.**

```python
def test_user_cannot_reassign_active_ticket(workflow_env):
    env = workflow_env
    ticket = env.create()
    env.service.start_work(env.agent("builder", "run-a"), ticket.version,
                           env.operation("start"))
    active = env.read(ticket.ref)
    for assignee in (None, "observer"):
        with pytest.raises(TicketConflict):
            env.service.assign(env.user, active.version, assignee,
                               env.operation(f"assign-{assignee}"))
    assert env.read(ticket.ref).record == active.record
```

Use two processes from Task 2's harness to race start-work on the same unassigned
ticket; assert a single agent/run in the persisted record. Test two runs of the
    same agent, a later execution session of the same durable job, same local ticket
    ID in another team, unknown/deleted configured agent,
ended/revoked fixture session, direct agent data-patch bypass, state injection,
stale revision, optional sign-off, and ownership retained after transition-ready
state. `end_work` and `sign_off` cannot clear a marker owned by a later generation.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_ticket_ownership.py tests/test_ticket_service.py tests/test_ticket_storage_concurrency.py -q`.
Review that no transport/UI-only check is required for ownership to hold, then
commit `feat(tickets): enforce assignment and active work`.

## Task 6: Commit Transitions, Reports and Immutable Evidence

**Files**

- Create: `flowgency/tickets/transitions.py`, `artifacts.py`.
- Extend: `flowgency/tickets/service.py`, `models.py`, `storages/base.py`, `storages/local.py`.
- Create tests: `tests/test_ticket_transitions.py`, `tests/test_ticket_artifacts.py`.

**Interfaces**

- Consumes: Tasks 1-5; no decision/proposal helper is a new ticket dependency.
- Produces `TransitionRequest(transition_id, inputs, outputs, assessments)` and
  `TicketReport(message: str, assessments: tuple[CriterionAssessment, ...])`.
- Extends `TicketService` with `transition(actor, version, request, operation)`,
  `report(actor, version, report, operation)` and `publish_artifact(actor,
  version, filename: str, media_type: str, content: bytes) -> ArtifactRef`.
- Extends `TicketStorage` with `put_artifact(namespace: TicketRef, artifact:
  RetainedArtifact) -> ArtifactRef` and `read_artifact(namespace, artifact_id)
  -> RetainedArtifact`. `RetainedArtifact` has sanitized display filename,
  media type, content bytes and digest. References are scoped to the storage
  binding/team/board; an arbitrary opaque ID alone is not download authority.

- [ ] **Step 1: Add red transition and failed-report separation tests.**

```python
def test_rejected_transition_leaves_record_unchanged(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": False})
    actor = env.agent("builder", "run-a")
    env.service.start_work(actor, ticket.version, env.operation("start"))
    before = env.read(ticket.ref)
    request = TransitionRequest(transition_id="complete", inputs={},
                                outputs={"summary": "Reviewed"}, assessments=())
    with pytest.raises(ContractError):
        env.service.transition(actor, before.version, request, env.operation("reject"))
    assert env.read(ticket.ref).record == before.record
    env.service.report(actor, before.version, TicketReport(message="Review declined",
                       assessments=()), env.operation("report"))
    after = env.read(ticket.ref).record
    assert after.state_id == before.record.state_id
    assert len(after.events) == len(before.record.events) + 1
```

- [ ] **Step 2: Run the red check.**

Run `python -m pytest tests/test_ticket_transitions.py tests/test_ticket_artifacts.py -q`.
Expected: transition/report/artifact methods missing; no skipped filesystem cases.

- [ ] **Step 3: Evaluate within the authorized atomic update and retain audit snapshots.**

`transition` reuses the same guarded mutation path as assignment and invokes the
pure evaluator only after checking ownership, source state, binding and digest.
It commits outputs and the state in a single provider replace. Preserve assignment
and active-run reference. The audit event contains old/new states and names,
blueprint/transition names, workflow/context/storage digests, effective inputs,
accepted outputs, assessments, actor, job ID, UTC time and operation ID. Store
snapshot values, not mutable references into a loaded definition.

```python
def transition_record(record, definition, request, actor):
    require_active_owner(record, actor)
    evaluated = evaluate_transition(
        definition, request.transition_id, record.state_id,
        record.field_values, request.inputs, request.outputs, request.assessments,
    )
    return record.model_copy(update={
        "state_id": evaluated.to_state,
        "field_values": {**record.field_values, **evaluated.outputs},
        "events": record.events + (transition_event(record, evaluated, actor),),
    })
```

Define `require_active_owner` in `service.py` and `transition_event(record,
evaluated, actor) -> TicketEvent` in `transitions.py`. `commit_operation` from Task 2
stamps the new revision and receipt; avoid appending the same event twice. Update
`field_provenance` for accepted outputs using trusted actor/job/event values.
Inputs supplied only for this attempt are retained in its audit snapshot; persist
them as current fields only through an explicit data update or declared output.

Retained artifacts are immutable, content-addressed envelopes below the Local
namespace's `artifacts/` directory. Hash the canonical envelope of filename, media
type and bytes, limit decoded content to 1 MiB, and validate before publication.
Publish once using a short artifact-specific lock and atomic replacement; verify
existing content if the digest already exists. An artifact can be written before
the ticket transition and remain unreferenced after rejection; do not delete it
through job-finalization cleanup. Do not reuse `jobs.artifacts._prepare_artifact_target`,
which removes an existing job artifact directory.

External HTTPS artifact links are validated and rendered safely but never fetched
by the server as part of rule evaluation. Retained artifacts can be downloaded
only after namespace authorization, with attachment disposition and `nosniff`;
never render uploaded HTML inline. Internal references must exist and their digest
must match before a transition accepts them.

- [ ] **Step 4: Add positive, replay, stale-definition and artifact regressions.**

```python
def test_transition_preserves_owner_and_old_definition(workflow_env):
    env = workflow_env
    ticket = env.create(values={"verdict": True})
    actor = env.agent("builder", "run-a")
    env.service.start_work(actor, ticket.version, env.operation("start"))
    request = TransitionRequest(transition_id="complete", inputs={},
                                outputs={"summary": "Verified existing work"}, assessments=())
    accepted = env.service.transition(actor, env.read(ticket.ref).version, request,
                                      env.operation("complete"))
    assert accepted.ticket.state_id == "done"
    assert accepted.ticket.assignee == "builder"
    assert accepted.ticket.active_run.job_id == "run-a"
    event = accepted.ticket.events[-1]
    env.rename_transition("complete", "Finish review")
    assert env.read(ticket.ref).record.events[-1] == event
```

Add `rename_transition` to the fixture via Task 4's real publication API.
Test stale workflow digest after a criteria edit; stale ticket revision after user
input; a storage switch to a destination with the same ticket ID; negative and
missing assessments; forged state/actor in payload; accepted replay after later
ticket changes; artifact upload/path traversal/cross-team download; tampered
retained bytes; rejection after artifact publication; later job cleanup not
deleting evidence. Reports update history without making a rejected transition
appear accepted.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_ticket_transitions.py tests/test_ticket_artifacts.py tests/test_ticket_ownership.py -q`.
Review audit durability and the distinction between evidence presence and truth,
then commit `feat(tickets): validate and audit live transitions`.

## Task 7: Expose Authenticated Job-Scoped Live Ticket Tools

**Files**

- Create: `flowgency/tickets/access.py`, `protocol.py`, `broker.py`, `mcp_server.py`.
- Extend: `flowgency/tickets/service.py`, `flowgency/tickets/models.py`.
- Modify dependency declaration: `pyproject.toml`.
- Create tests: `tests/test_ticket_access.py`, `tests/test_ticket_broker.py`,
    `tests/test_ticket_mcp.py`.

**Interfaces**

- Consumes: Tasks 1-6 and `JobStore` / `JobAuthorityRef` for authentic job lookup.
- Produces `TicketAccessRegistry(job_store)` with `open(authority)
    -> TicketAccessGrant`, `authenticate(token) -> AgentTicketContext`,
    `validate_context(context) -> None`, `close(session_id) -> None`, and
    `register_target(context, binding, ref) -> None`. `TicketAccessGrant` contains
    `session_id`, a one-time bearer `token`, and trusted context; the persisted
    registry contains only the token hash, not the token itself.
- Produces `TicketBroker(service, registry, *, authority)` context manager with
    `start() -> LiveTicketEndpoint` and `close()`; endpoint has loopback URL and
    grant. It is worker-owned, not dependent on a running dashboard server.
- `TicketBroker.__enter__` calls start, stores `.endpoint` and returns the broker;
    `__exit__` closes/revokes it and joins the server thread. `TicketToolClient`
    uses ordinary HTTP status and JSON responses; `.call` returns structured service
    errors instead of masking them as an empty ticket set.
- Produces `TicketToolClient(endpoint: str, token: str).call(operation: str,
    payload: dict) -> dict` and `build_mcp_server(client) -> MCPServer`.
- Produces `TicketService.list_workflows(actor) -> tuple[WorkflowBinding, ...]`
    and `list_tickets(actor, workflow_id, *, assignee=None, state_id=None,
    query="") -> tuple[TicketView, ...]`. Provider errors are not swallowed as empty
    results. Definitions and labels are included through scoped service views, not
    exposed as arbitrary server filesystem paths.

**Transport choice:** use the official Python MCP SDK 2 (`mcp>=2.0,<3`) for the
stdio protocol and a small authenticated loopback HTTP broker for trusted service
execution. The bridge process receives only endpoint/token and sends typed tool
requests; it cannot choose another actor or configure a storage root. The SDK's
current [server guide](https://modelcontextprotocol.io/docs/develop/build-server)
uses `from mcp.server import MCPServer` and `.run(transport="stdio")`. Verify this
import in the task's dependency smoke test; do not silently switch SDK major APIs.
Use the standard library HTTP client with proxies disabled for the loopback hop.

- [ ] **Step 1: Add red actor-forgery and session-revocation tests.**

Extend `workflow_env` with `running_job(agent, job_id) -> JobAuthorityRef` using
the real `JobStore` helpers, and `broker_for(authority)` yielding a loopback broker
plus a TestClient-independent `TicketToolClient`. The fixture must register a
valid JobSpec/JobRecord, not let an arbitrary string create a session.

```python
def test_broker_rejects_actor_fields(workflow_env):
        env = workflow_env
        authority = env.running_job("observer", "run-observer")
        with env.broker_for(authority) as client:
                result = client.call("list_tickets", {
                        "workflow_id": "board-a", "agent_name": "builder", "team_id": "other",
                })
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid-request"


def test_closed_session_cannot_mutate(workflow_env):
        env = workflow_env
        authority = env.running_job("builder", "run-a")
        grant = env.access_registry.open(authority)
        env.access_registry.close(grant.session_id)
        with pytest.raises(TicketForbidden):
                env.access_registry.authenticate(grant.token)
```

- [ ] **Step 2: Run the red check, then install the scoped dependency.**

Run `python -m pytest tests/test_ticket_access.py tests/test_ticket_broker.py -q`.
Expected: missing access/broker implementations. Add the MCP dependency and run
`python -m pip install -e '.[test]'` from the worktree. Then run
`python -c "from mcp.server import MCPServer; print(MCPServer.__name__)"`.
Expected: `MCPServer`. This command does not call an AI service.

- [ ] **Step 3: Implement the trusted broker and fixed tool catalog.**

Persist session metadata and original-target ledger under the owning canonical
job directory as `ticket-access.json`, protected by the existing job lock. Token
hashes are SHA-256 of `secrets.token_urlsafe(32)` values and compared with
`hmac.compare_digest`; do not log bearer values, serialize them into JobSpec, or
place them in task text. Each authenticate/mutate check verifies the job authority
digest and current run lifecycle. Registration is allowed only to a worker that
holds its valid launch authority. Ended or superseded sessions cannot be reopened
by the agent.

The HTTP API is private to the job broker: bind `127.0.0.1` using a pre-bound
ephemeral socket handed to Uvicorn; no wildcard bind or fixed dashboard port.
Reject unexpected Host/Origin, disable CORS, require the bearer header on every
request, cap JSON at 2 MiB, and do not redirect requests. A worker readiness event
and bounded shutdown/join replace sleep-based startup guesses. Synchronous
filesystem services run in Starlette's threadpool from async handlers.

```python
@app.post("/operations/{operation}")
async def operate(operation: str, request: Request):
        actor = registry.authenticate(read_bearer(request))
        payload = await read_bounded_json(request, max_bytes=2 * 1024 * 1024)
        command = parse_ticket_command(operation, payload)
        return await run_in_threadpool(dispatch_ticket_command, service, actor, command)
```

Define `read_bearer`, `read_bounded_json` in `broker.py`, and the strict Pydantic
command union plus `parse_ticket_command` / `dispatch_ticket_command` in
`protocol.py`. Each command is `extra="forbid"`. Response envelopes are
`{"ok": true, "result": ...}` or `{"ok": false, "error": {"code", "message",
"details"}}`, with 401/403/409/422/503 HTTP statuses as appropriate. The bridge
returns structured rejection to the agent instead of raising an uninformative
transport exception. Mutations compute their request digest server-side from the
validated command, trusted actor and target; the agent supplies only operation ID.

Expose these MCP tools, each with a typed schema: `workflows_list`, `tickets_list`,
`ticket_get`, `ticket_create`, `ticket_start_work`, `ticket_update`,
`ticket_report`, `ticket_transition`, `ticket_end_work`, `ticket_sign_off`, and
`ticket_artifact_publish`. All target mutations take a `TicketVersion` and
operation ID except artifact publication, which uses content addressing. Never
expose user assignment-to-another-agent or config mutation through this catalog.

```python
def build_mcp_server(client):
        server = MCPServer("flowgency-tickets")

        @server.tool()
        def tickets_list(workflow_id: str, query: str = "") -> dict:
                return client.call("list_tickets", {"workflow_id": workflow_id, "query": query})

        @server.tool()
        def ticket_start_work(version: TicketVersion, operation_id: str) -> dict:
                return client.call("start_work", {
                        "version": version.model_dump(mode="json"), "operation_id": operation_id,
                })

        register_remaining_ticket_tools(server, client)
        return server
```

Define `register_remaining_ticket_tools(server, client)` in `mcp_server.py` with
one schema-matched wrapper for each remaining tool listed above. `main()` reads
`FLOWGENCY_TICKET_ENDPOINT` and `FLOWGENCY_TICKET_TOKEN` from its launch environment,
validates a literal loopback URL, builds `TicketToolClient`, and runs stdio. No
logging/print on stdout. SDK handles JSON-RPC framing and handshake; do not write
a homegrown MCP parser. Broker requests never accept arbitrary URLs, config paths,
agent names or storage settings from the bridge.

- [ ] **Step 4: Add real stdio, authorization and stale-request tests.**

Use `asyncio.run` from a normal pytest test and the SDK's stdio client to start
`sys.executable -m flowgency.tickets.mcp_server` against the fixture broker. Perform
initialize, list tools, create/get/start/transition and a rejected retry while the
broker is still alive; assert ticket files change before the client process ends.

```python
def test_request_digest_is_not_agent_controlled(workflow_env):
        env = workflow_env
        with env.broker_for(env.running_job("builder", "run-a")) as client:
                response = client.call("start_work", {
                        "version": env.create().version.model_dump(mode="json"),
                        "operation_id": "start-one", "request_digest": "forged",
                })
        assert response["error"]["code"] == "invalid-request"
```

Add tests for missing/incorrect token, rejected browser Origin, cross-team refs,
ended session, stale ticket/definition/binding, duplicate accepted operation,
loopback proxy bypass, broker shutdown, oversized body, unsafe artifact bytes and
redacted errors. Write original binding/target ledger before active-work commits
so a crash between registration and commit leaves at worst a harmless cleanup
candidate, not untracked ownership. Cleanup reads only registered bindings, never
user-supplied path strings (Task 9).

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_ticket_access.py tests/test_ticket_broker.py tests/test_ticket_mcp.py -q`.
Review the authentication boundary and actual mid-run persistence, then commit
`feat(tickets): expose job-scoped live tools`.

## Task 8: Add Explicit Runtime Ticket-Tool Support

**Files**

- Create: `flowgency/integrations/ticket_tools.py`.
- Modify: `flowgency/integrations/models.py`, `__init__.py`,
    `flowgency/integrations/flowgency/copilot.py`.
- Extend adapters only after their contract is verified:
    `flowgency/integrations/flowgency/claude_code.py`, `codex.py`.
- Create: `tests/test_ticket_runtime_capabilities.py`,
    `tests/test_copilot_ticket_tools.py`.
- Extend: `tests/test_copilot_launch_arguments.py`, `tests/test_copilot_credentials.py`.

**Interfaces**

- Consumes: Task 7's `LiveTicketEndpoint` and tool catalog.
- Produces `TicketToolLaunch(command: str, args: tuple[str, ...], env: dict[str, str],
    server_name: str = "flowgency-tickets")` in integration models, excluded from
    persisted job-spec serialization and repr of secrets.
- Extends `IntegrationRunRequest` with optional `ticket_tools: TicketToolLaunch | None`.
- Extends `RuntimeCapabilities` with `live_ticket_transport: Literal["mcp-stdio"]
    | None = None`; default means unsupported, never guessed from native files.
- Produces `build_ticket_tool_launch(endpoint) -> TicketToolLaunch` and
    `write_copilot_ticket_config(launch, path: Path) -> Path` in `ticket_tools.py`.
- Integration validation rejects a requested but unsupported channel before
    starting the runtime. Jobs with no configured ticket workflows remain usable
    under their existing non-ticket behavior.

- [ ] **Step 1: Add red command-contract tests and unavailable-runtime tests.**

```python
def test_ticket_tools_do_not_grant_shell(copilot_request, monkeypatch, tmp_path):
        request = dataclasses.replace(copilot_request, ticket_tools=TicketToolLaunch(
                command=sys.executable, args=("-m", "flowgency.tickets.mcp_server"),
                env={"FLOWGENCY_TICKET_ENDPOINT": "http://127.0.0.1:9999",
                         "FLOWGENCY_TICKET_TOKEN": "fixture-only-token"},
        ))
        captured = capture_copilot_launch(monkeypatch)
        CopilotIntegration().run(request)
        args = captured.argv
        assert "--additional-mcp-config" in args
        assert "flowgency-tickets" in args
        assert not any(value.startswith("shell") for value in captured.tool_grants)
        assert "fixture-only-token" not in " ".join(args)
```

Use the existing Copilot request/subprocess mocks as the starting fixture; define
`capture_copilot_launch` in the new test module to return captured argv, environment
and parsed `--allow-tool` values. Do not execute authenticated AI work in this
deterministic test. The only fake secret is the literal fixture value above.

- [ ] **Step 2: Run red and capture capability evidence.**

Run `python -m pytest tests/test_ticket_runtime_capabilities.py tests/test_copilot_ticket_tools.py -q`.
Run `copilot --help` and record the installed CLI version and relevant options in
the execution log. Planning verified `--additional-mcp-config` accepts an `@file`
and `--allow-tool` accepts an MCP server name. Command help is not proof of runtime
enforcement; Task 16's live probe is required before advertising support.

- [ ] **Step 3: Implement per-job generated MCP configuration for Copilot.**

Generate JSON through `json.dumps`, outside canonical source/config files:

```python
payload = {
        "mcpServers": {
                launch.server_name: {
                        "command": launch.command,
                        "args": list(launch.args),
                        "env": dict(launch.env),
                        "tools": ["*"],
                },
        },
}
atomic_write_text(config_path, json.dumps(payload, indent=2))
cmd_args += ["--additional-mcp-config", "@" + str(config_path),
                         "--allow-tool", launch.server_name]
```

Use a private per-job runtime configuration path, not the workspace or agent
blueprint. Keep existing Copilot path sandbox and authored tool grants unchanged;
the added grant is limited to Flowgency's authenticated ticket server. Do not
allow shell, external provider tools or all MCP servers for convenience. Pass only
the endpoint/token plus necessary process environment to the bridge, not Flowgency's
full inherited credentials. Do not write user-home MCP configuration or modify the
compiled artifact. Destroy the private bearer-bearing file on terminal cleanup;
persist only sanitized capability metadata.

For Claude Code and Codex, add adapters only with deterministic CLI-configuration
tests and the same installed-runtime proof. Use per-launch configuration (`--mcp-config`
for Claude when advertised by that version; Codex's documented per-launch
`mcp_servers` configuration using its config override mechanism). Probe the installed
help/config parser in the task before coding exact flags. If the required option
or permission-preserving invocation is unavailable, keep capability `None` and
return `unsupported-ticket-channel`. Do not add speculative support to Aider,
Gemini, Goose, OpenCode, Pi, Script or SDK adapters. Their existing non-ticket
execution remains covered by the deterministic integration suite.

- [ ] **Step 4: Verify exact capability rejection and credential isolation.**

```python
@pytest.mark.parametrize("integration_name", ["aider", "gemini", "goose", "opencode", "pi"])
def test_unsupported_live_channel_is_not_an_outbox_fallback(integration_name, ticket_request):
        integration = get_integration(integration_name)
        issues = integration.validate_run(ticket_request)
        assert any(issue.code == "unsupported-ticket-channel" for issue in issues)
```

Add a supported-adapter test with read/search-only workspace policy that retains
the ticket-server grant but no workspace write/shell expansion. Assert broker
credentials are absent from task text, JobSpec, public logs and subprocess argv;
sanitized failure paths must not serialize the generated config. Capability-cache
keys include CLI version and actual ticket-channel probe result. Unknown versions
do not inherit an earlier measured support claim.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_ticket_runtime_capabilities.py tests/test_copilot_ticket_tools.py tests/test_copilot_launch_arguments.py tests/test_copilot_credentials.py -q`.
Review the supported/unsupported matrix and permission changes, then commit
`feat(runtime): attach live ticket tools to jobs`.

## Task 9: Coordinate Durable Ticket Runs and Confirmed Cleanup

**Files**

- Create: `flowgency/jobs/tickets.py`, `processes.py`.
- Modify: `flowgency/jobs/models.py`, `resolution.py`, `submission.py`,
    `execution.py`, `reconciliation.py`, `queue.py`.
- Modify supported ticket runtimes' process launch in
    `flowgency/integrations/flowgency/copilot.py` and any adapter enabled in Task 8.
- Create tests: `tests/test_ticket_jobs.py`, `tests/test_ticket_job_recovery.py`,
    `tests/test_runtime_process_lifecycle.py`.
- Extend: `tests/test_job_submission.py`, `tests/test_job_execution.py`,
    `tests/test_job_models.py`, `tests/test_job_reconciliation.py`.

**Interfaces**

- Consumes: Tasks 5-8 plus existing durable submission, job authority and queue.
- Produces `TicketJobCoordinator(service, job_store, config_store, submitter)`:
    `submit(actor: UserTicketContext, version: TicketVersion, operation_id: str)
    -> JobHandle`, `preflight(record: JobRecord) -> TicketView`,
    `cleanup(authority: JobAuthorityRef, stopped: ProcessStopEvidence) -> CleanupResult`.
- Produces `TicketJobTarget` in `jobs.models`: original `StorageBinding`,
    `TicketRef`, assigned agent, assignment event ID, and context digest. It is an
    immutable launch target, not a ticket snapshot to execute without refreshing.
- New job schema is 6, with `ticket_target: TicketJobTarget | None` and trigger
    `ticket`. Keep schema-5 decoding and digest calculation byte-for-byte compatible
    for historical job readability; never inject schema-6 keys into schema-5
    `to_dict()` or rewrite old records at startup.
- `JobRequest` gains the same optional `ticket_target`; `resolve_job_request`
    emits schema 6 for new jobs. Historical trigger validation and new-submission
    validation are separate, so accepting a schema-5 record does not allow a new
    decision job to be submitted.
- Produces `ProcessStopEvidence(job_id, generation, confirmed: bool, reason)` and
    `run_supervised(argv, *, cwd, env, timeout, lifecycle) -> CompletedRuntimeProcess`
    in `jobs.processes`; result includes stdout/stderr/exit code and stopped evidence.
- TicketService adds a private cleanup operation that clears only matching
    job/session/generation metadata using registered original bindings. It is not
    exposed as an agent or user force-unlock tool.

- [ ] **Step 1: Add red queued-assignment and multi-ticket-cleanup tests.**

```python
def test_queued_ticket_job_rechecks_assignment(ticket_job_env):
        env = ticket_job_env
        ticket = env.create_assigned("builder")
        handle = env.coordinator.submit(env.user, ticket.version, "run-request")
        env.assign_idle(ticket.ref, "observer")
        with pytest.raises(TicketConflict):
                env.coordinator.preflight(env.jobs.read(handle))
        assert env.read(ticket.ref).record.active_run is None


def test_cleanup_retains_assignment_for_all_run_targets(ticket_job_env):
        env = ticket_job_env
        authority, targets = env.start_two_tickets(agent="builder", job_id="run-a")
        env.coordinator.cleanup(authority, ProcessStopEvidence(
                job_id="run-a", generation=env.generation, confirmed=True, reason="exited",
        ))
        for ref in targets:
                record = env.read(ref).record
                assert record.assignee == "builder"
                assert record.active_run is None
```

Define `ticket_job_env` by extending the real workflow fixture with a captured
JobLauncher/submitter and actual JobStore files. Helpers `create_assigned`,
`assign_idle`, `start_two_tickets` must call service/broker methods and register the
original targets, not directly change final records. `env.jobs.read(handle)`
resolves the returned JobHandle through the store's authority reference.

- [ ] **Step 2: Run the red check.**

Run `python -m pytest tests/test_ticket_jobs.py tests/test_ticket_job_recovery.py -q`.
Expected: missing coordinator or schema support. Preserve a serialized schema-5
fixture with its old digest and assert it remains readable before extending models.

- [ ] **Step 3: Implement idempotent job reservations and current-target preflight.**

Add optional `pending_run: TicketRunReservation` to ticket records; it records
job ID, request ID, assignee and assignment event ID. It is queue linkage, not an
assignment or active-work lease. Under the short team/ticket guard, reserve a
preallocated job ID and persist its original target, then release the guard before
calling the existing `submit_job_request(JobRequest(...))`, which acquires its own
non-reentrant team-operation lock. Reacquire the short guard to reconcile the
reservation against actual job creation. Never nest the public submitter under
the same team/ticket lock. If submission fails, clear only that
reservation and keep assignment. Crash recovery distinguishes a recorded durable
job from a reservation whose job was never created. The board shows Queued only
when the job exists. Duplicate Run returns the same live job or rejects conflicting
requests; never enqueues a second effective run for the same assignment.

Preflight reads the current ticket, assigned agent, pending reservation and
current storage/blueprint context. Reassignment invalidates the pending request;
it does not permit a worker to start using its old snapshot. Same-agent
reassignment after an intervening owner change is detected by the persisted
assignment event ID. A definition-body update requires the job to read current
rules; a replaced blueprint/storage binding invalidates its old target.

The coordinator's submission operation ID, reserved job ID and original target
are written durably in the owning team/job area before enqueue. On replay, inspect
that exact job ID; a missing job after an interrupted attempt can be safely retried
only for the still-current assignment/context. A job that exists after reassignment
is retained as a cancelled/failed stale request, never linked to the new owner as
its run. `TicketJobTarget` includes Task 3's generated context digest to fence
switch-away/switch-back cases without locking configuration.

Run registration starts after memory acquisition and before launching the CLI.
Create the broker, attach its launch descriptor, and use fresh ticket views in the
task instructions. Manual/scheduled runs in teams with workflows receive the live
tool catalog too and may discover multiple tickets. Unsupported runtime capability
fails submission before a job claims active work. No ticket event creates a run.

```python
with TicketBroker(service, registry, authority=authority) as broker:
        if record.spec.ticket_target is not None:
                coordinator.preflight(record)
        request = dataclasses.replace(request, ticket_tools=build_ticket_tool_launch(broker.endpoint))
        result = integration.run(request)
        registry.close(broker.endpoint.grant.session_id)
        coordinator.cleanup(authority, result.process_stop_evidence)
```

Wire exception paths through `finally` so broker shutdown/revocation always occurs;
cleanup receives unknown/not-confirmed evidence on launcher failure rather than
fabricated success. Add `process_stop_evidence` as optional runtime result metadata,
defaulting to unknown for older/unmodified integrations. Preserve existing memory
staging, failed-stage retention, logs, native change capture and pin release.

- [ ] **Step 4: Supervise runtime processes and make recovery conservative.**

For Windows, use the existing pywin32 dependency to create a per-run Windows Job
Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; create the CLI process suspended,
assign it to that object before resuming its thread, and retain handles until its
tree is stopped. Track the worker's own broker separately. For POSIX, start a new
session/process group, terminate the group on timeout, and reap before reporting
confirmed stop. Do not treat a dead worker PID or elapsed grace period as proof
that an untracked CLI subtree is gone. If supervision cannot be established,
ticket-enabled launch fails explicitly; do not downgrade to unsafe automatic unlock.

The stop-decision function is deliberately small and unit-testable:

```python
def may_clear_active_work(record, evidence):
        return (
                evidence.confirmed
                and record.active_run is not None
                and record.active_run.job_id == evidence.job_id
                and record.active_run.generation == evidence.generation
        )
```

Use real child/grandchild test executables from Python scripts in a temp directory
to verify timeout and crash handling. Capture process creation identity, not only
PID, to avoid PID-reuse false positives. Unknown process state returns a visible
cleanup error and retains assignment/active protection. No elevated commands or
user prompts belong in normal supervision. On confirmed completion, clear only
the matching active markers for every ledger target, including old storage after
a settings switch. Unavailable original storage records pending cleanup; no
fallback to the currently selected provider or same-number ticket.

- [ ] **Step 5: Run lifecycle regressions, review, and commit.**

Run `python -m pytest tests/test_ticket_jobs.py tests/test_ticket_job_recovery.py tests/test_runtime_process_lifecycle.py tests/test_ticket_ownership.py -q`.
Cover run failure after accepted transition, failed enqueue retaining assignee,
duplicate Run, stale assignment, two runs of one agent, two tickets in one run,
optional sign-off, later-generation protection, old-storage cleanup, broker close
on exceptions and unchanged schema-5 digests. Review process-tree evidence and
partial reservation recovery, then commit
`feat(jobs): coordinate durable ticket execution`.

## Task 10: Add User-Only Ticket Routes and Board Projections

**Files**

- Create: `flowgency/tickets/views.py`, `flowgency/web/workflow_context.py`,
    `flowgency/web/routes/workflows.py`, `flowgency/web/routes/tickets.py`.
- Modify: `flowgency/web/dependencies.py`, `flowgency/app.py` (router registration
    and service construction only in this task).
- Create: `tests/test_workflow_routes.py`, `tests/test_ticket_routes.py`.
- Extend: `tests/_ticket_helpers.py`, `tests/conftest.py`.

**Interfaces**

- Consumes: Tasks 4-9's configuration service, ticket service and job coordinator.
- Produces `BoardView(binding, name, columns, ticket_count, working_count,
    selected_ticket, issues, revision)` and `TicketDetailView` in `tickets.views`.
    Columns follow the current definition order. Ticket counts use the entire board;
    column counts use the current search/assignee filters.
- Produces `build_board_view(service, actor, workflow_id, *, query="",
    assignee=None, selected_ticket_id=None) -> BoardView` and
    `build_ticket_detail_view(service, actor, ref) -> TicketDetailView`.
- Adds optional `workflow_library`, `workflow_configuration`, `tickets`,
    `ticket_jobs` services to `FlowgencyServices`. Invalid individual blueprints or
    unavailable ticket storage become workflow-scoped issues rather than erasing
    unrelated agents/routines/memory services at startup.
- User endpoints are enumerated below. All route IDs are generated internal IDs
    or validated existing IDs; none is accepted as an arbitrary path.

| Method | Path | Outcome |
| --- | --- | --- |
| GET | `/{team}/workflows/{workflow}` | Redirects 303 to the board snapshot; replaced by the HTML board page in Task 11. |
| GET | `/{team}/workflows/{workflow}/snapshot` | Board JSON, including current revisions. |
| GET | `/{team}/workflows/{workflow}/tickets/{ticket}` | Redirects 303 to the scoped detail snapshot; replaced by the HTML detail page in Task 11. |
| GET | `/{team}/workflows/{workflow}/tickets/{ticket}/snapshot` | Scoped detail JSON. |
| POST | `/{team}/workflows/{workflow}/tickets` | Create in initial state, then 303. |
| POST | `/{team}/workflows/{workflow}/tickets/{ticket}/update` | Content/inputs only, then 303. |
| POST | `/{team}/workflows/{workflow}/tickets/{ticket}/assignee` | Save idle assignment, then 303. |
| POST | `/{team}/workflows/{workflow}/tickets/{ticket}/run` | Submit durable run, then 303. |
| GET | `/{team}/workflows/{workflow}/artifacts/{artifact}` | Authorized attachment download. |

There is deliberately no user transition/force-move route. An agent's live broker
uses Task 7's private endpoint, not these UI operations. Preserve Flowgency's
trusted-local-user model; this feature does not add account authentication or claim
to defend against an unrestricted process acting as the local OS user.

- [ ] **Step 1: Add route fixtures and red state-injection/ownership tests.**

`workflow_web_env` combines `make_workflow_environment` with the existing
`tests.test_agent_detail._seed_app` pattern: select a temp `FLOWGENCY_CONFIG`, call
`app_mod.refresh_services()` and use FastAPI TestClient. Expose `.client`, `.create`,
`.service`, `.user`, `.agent`, `.read`, `.operation` and `.base_path` pointing to
`/newsletter/workflows/board-a`. Store real tickets and jobs under temp roots.

```python
def test_user_update_cannot_set_state(workflow_web_env):
        env = workflow_web_env
        ticket = env.create()
        response = env.client.post(
                f"{env.base_path}/tickets/{ticket.ref.ticket_id}/update",
                data={"payload": json.dumps({
                        "version": ticket.version.model_dump(mode="json"),
                        "operation_id": "edit-a", "patch": {"state_id": "done"},
                })}, follow_redirects=False,
        )
        assert response.status_code == 422
        assert env.read(ticket.ref).record.state_id == "review"


def test_no_user_transition_endpoint(workflow_web_env):
        env = workflow_web_env
        ticket = env.create()
        response = env.client.post(
                f"{env.base_path}/tickets/{ticket.ref.ticket_id}/transition",
                json={"state": "done", "agent_name": "builder", "job_id": "run-a"},
        )
        assert response.status_code in (404, 405)
```

- [ ] **Step 2: Run red.**

Run `python -m pytest tests/test_workflow_routes.py tests/test_ticket_routes.py -q`.
Expect missing routes/service registration, then implement the server slice.

- [ ] **Step 3: Implement routes with strict payloads and explicit error mapping.**

Use async FastAPI handlers, service dependencies and `run_in_threadpool` for
filesystem work. Parse form `payload` as JSON through a strict Pydantic model;
derive `UserTicketContext` from the route/team and local user, not posted actor
fields. Validate ref team/workflow against the route before invoking service.
Reject unknown fields; do not discard a posted state change silently.

```python
@router.post("/{team}/workflows/{workflow}/tickets/{ticket}/assignee")
async def save_assignee(team: str, workflow: str, ticket: str, request: Request):
        services = get_services(request)
        payload = AssigneeForm.model_validate_json((await request.form())["payload"])
        actor = user_context(team)
        require_route_ref(payload.version.ref, team, workflow, ticket)
        await run_in_threadpool(services.tickets.assign, actor, payload.version,
                                                     payload.assignee, operation_for_user(actor, payload))
        return RedirectResponse(ticket_return_url(request, team, workflow, ticket), status_code=303)
```

Define `AssigneeForm(version, assignee, operation_id)`, `user_context`,
`require_route_ref`, `operation_for_user`, and `ticket_return_url` in
`routes/tickets.py`. Extract shared parsing only when another route actually reuses
it. `ticket_return_url` redirects to the scoped detail snapshot GET until Task 11
introduces the HTML detail page; Task 11 replaces this with an internal board/detail
URL for ordinary forms while retaining snapshot redirect for `Accept: application/json`.
The browser follows that 303 and reads canonical JSON. Errors return 409/422/503
with the submitted draft for HTML or the same structured issue payload for JSON.
No arbitrary return URL or redirect from posted data is allowed.

Board/detail reads load records even when a current definition is invalid and
surface `WorkflowUnavailable` with readable history. Unknown-state tickets in an
externally invalid definition are shown in an explicitly labeled invalid-data
section, not moved into a fabricated state column or omitted from totals. Storage
unavailability/corruption produces a visible board error, not an empty collection.
Use the existing sanitized Markdown renderer (`nh3` policy) and Jinja autoescape;
never embed raw ticket strings in executable JavaScript.

- [ ] **Step 4: Add positive and negative HTTP tests.**

```python
def test_assignee_post_is_revision_checked(workflow_web_env):
        env = workflow_web_env
        ticket = env.create()
        payload = {"version": ticket.version.model_dump(mode="json"),
                             "assignee": "builder", "operation_id": "assign-a"}
        response = env.client.post(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/assignee",
                                                             data={"payload": json.dumps(payload)}, follow_redirects=False)
        assert response.status_code == 303
        payload.update(assignee="observer", operation_id="assign-b")
        stale = env.client.post(f"{env.base_path}/tickets/{ticket.ref.ticket_id}/assignee",
                                                        data={"payload": json.dumps(payload)}, follow_redirects=False)
        assert stale.status_code == 409
        assert env.read(ticket.ref).record.assignee == "builder"
```

Cover active reassignment/unassignment returning conflict; read-only agent eligible
for Run when its channel is supported; unsupported channel failing before enqueue;
create initial state; no assignee on Run; pending-run duplication; search/assignee
filters; board count versus job count; unsafe Markdown; unknown route refs;
cross-team artifact access; invalid blueprint with retained history; and no retired-directory
reads during the new routes.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_workflow_routes.py tests/test_ticket_routes.py tests/test_ticket_jobs.py -q`.
Review server-side authority, not just disabled controls; commit
`feat(web): add scoped workflow and ticket routes`.

## Task 11: Build the Approved Board and Ticket Inspector

**Files**

- Create: `flowgency/templates/workflow_board.html`, `ticket_detail.html`,
    `_ticket_inspector.html`, `flowgency/static/workflow-board.js`, `workflow-board.css`.
- Modify: `flowgency/templates/base.html`; extend `flowgency/web/routes/workflows.py`
    and `flowgency/web/routes/tickets.py` to replace Task 10's snapshot redirects with
    HTML board and detail GET routes and update `ticket_return_url`.
- Extend fixtures: `tests/ui/server.py`, `tests/ui/fixtures/config.yaml`.
- Create: `tests/ui/workflow_board.spec.ts`; extend `tests/test_workflow_routes.py`.

**Interfaces**

- Consumes: Task 10's BoardView, snapshots and POST routes; Task 9 Run coordinator.
- Delivers `GET /{team}/workflows/{workflow}` (board page) and
    `GET /{team}/workflows/{workflow}/tickets/{ticket}` (expanded detail),
    replacing Task 10's temporary snapshot redirects for these paths.
- Produces a script JSON payload at `#workflow-initial` with board/ticket views,
    route URLs and revisions. Encode via Jinja `tojson`, not string interpolation.
- Produces `WorkflowBoardController` in `workflow-board.js` with
    `refreshBoard()`, `openTicket(id)`, `saveAssignee(value)`, `saveInputs()`,
    `runAssignedAgent()`, `closeTicket()` and a local `renderAssignment()` DOM update;
    server operations all use scoped URLs.
- Preserve draft fields separately from the last-read record. Assignment updates
    must not overwrite an unrelated dirty title/description/input draft.

The draft stores its base values and base `TicketVersion`, not only edited text.
After assignment saves, advance that base revision only if every dirty field's
server value still equals its captured base value; otherwise preserve the old
revision and surface a conflict on save. `saveInputs` submits only dirty fields.
Do not silently adopt a newer revision for stale input just because an unrelated
assignment request returned a fresh snapshot.

**Normative assets**

- `docs/superpowers/specs/assets/2026-09-07-ticket-workflows/board-inspector.html`
- `board-inspector-desktop.png`, `ticket-page-desktop.png`, `board-mobile.png`,
    `ticket-detail-mobile.png` in that same directory.

- [ ] **Step 1: Seed real workflow fixtures and add the red browser test.**

Extend the UI fixture server with two workflow instances under newsletter:
`delivery` and `research-workflow`, and reusable definitions under its runtime
workflow library. Seed stable UUID-like fixture IDs, readable numbers matching the
approved samples, 8 Delivery tickets, 4 Research tickets, two active tickets and
one assigned idle Review ticket. Keep test jobs deterministic; do not launch real
AI runs from the fixture server. Use a captured test launcher that writes real
queued JobRecords. It is installed only by `tests/ui/server.py`, never a production
environment flag that changes authority.

```typescript
import { expect, test } from '@playwright/test';
import { assertNoLayoutIssues, assertNoConsoleErrors, installConsoleErrorGate } from './layout';

test('assignee selection saves without moving the ticket or losing a draft', async ({ page }) => {
    installConsoleErrorGate(page);
    await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
    await page.getByLabel('Acceptance criteria', { exact: true }).fill('Keep this unsaved note');
    await page.getByLabel('Assigned agent', { exact: true }).selectOption('builder');
    await expect(page.getByRole('button', { name: 'Assign', exact: true })).toHaveCount(0);
    await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Keep this unsaved note');
    await expect(page.locator('[data-ticket-state]')).toHaveText('Review');
    const saved = await page.request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot');
    expect((await saved.json()).record.assignee).toBe('builder');
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
});
```

Reset fixture mutations through real revision-checked APIs in `afterEach`, following
the existing routines fixture pattern. Do not let test ordering mutate the expected
board screenshot state. Use test-only seeding functions for active-run fixtures;
there is no public force-state helper endpoint.

- [ ] **Step 2: Run red in one project.**

Run `npm run test:ui -- tests/ui/workflow_board.spec.ts --project=desktop-dark`.
Expected: missing board/inspector controls. If `.venv/Scripts/python.exe` is absent,
create the worktree venv and install `.[test]` there, as required by the existing
Playwright server command; do not use the main checkout's running dashboard.

- [ ] **Step 3: Implement the approved responsive layout and confirmed updates.**

Reuse existing Lucide package assets, theme CSS variables and base navigation.
Vendor/build the package's browser icon asset through the existing asset process;
do not add a new CDN dependency or use handcrafted icons. Add a base-template
content-width/layout block so boards can use the available workspace width without
changing unrelated pages' max-width layout.

The desktop header is one approximately 49px row: title and matching
ticket/working counts left, search/filter/New ticket right. No blueprint/provider
badge, raw ID field or permanent healthy-storage indicator. Cards have <=6px
corners and remain stable in width; their state is the containing column, not an
editable control. Inspector expands to a dedicated route, collapses back without
losing board filters, and uses full available width on narrow screens.

Keep keyboard focus and drafts through refreshes with separate model state:

```javascript
async function postTicketAction(url, payload) {
    const response = await fetch(url, {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: new URLSearchParams({ payload: JSON.stringify(payload) }),
    });
    const result = await response.json();
    if (!response.ok) throw new TicketActionError(result);
    return result;
}

async function saveAssignee(assignee) {
    const savedDraft = structuredClone(this.inputDraft);
    const snapshot = await postTicketAction(this.urls.assignee, {
        version: this.ticket.version,
        operation_id: crypto.randomUUID(),
        assignee: assignee || null,
    });
    this.ticket = snapshot;
    this.inputDraft = savedDraft;
    this.renderAssignment();
    await this.refreshBoard();
}

WorkflowBoardController.prototype.saveAssignee = saveAssignee;
```

Define `TicketActionError(payload)` with structured issues in this module and
handle it at each UI action to display errors and restore the confirmed assignee.
Disable only the assignment control while its save is pending; do not lose
selection on an error. Prevent out-of-order responses from replacing newer state
using a request sequence/AbortController. Poll visible boards with an ETag-backed
snapshot at a modest interval (2 seconds); pause hidden pages, preserve dirty
drafts, and display stale-data/errors. Do not rebuild the whole inspector on every
poll. No optimistic state movement or status-based autoplay exists.

Run is separate from assignment and reflects durable queued/active status. The
Requirements tab distinguishes structured checks from qualitative assessments;
History shows old display names/values from snapshots, not current renamed labels
substituted into old events. All state controls are read-only; no drag handler.

- [ ] **Step 4: Add visual and behavior coverage against the approved assets.**

```typescript
test('active assignment is disabled and status counts share styling', async ({ page }) => {
    await page.goto('/newsletter/workflows/delivery?ticket=fixture-active');
    await expect(page.getByLabel('Assigned agent', { exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeDisabled();
    await expect(page.locator('[draggable="true"]')).toHaveCount(0);
    await expect(page.locator('[data-board-stat]')).toHaveCount(2);
    await expect(page).toHaveScreenshot('workflow-board-inspector.png', { fullPage: true });
});
```

Test input validation/conflict preservation, live external transition refresh,
matching active counts for multiple tickets in one job, no automatic state change
after Run, no-workflows/empty-board states, invalid-definition and storage-error
states, inspector links, tabs, search, unassigned filter, mobile navigation,
keyboard access, and long names. Use `layout.ts` and `keyboard.ts`; include axe
checks in the shared accessibility suite. Generate baselines only after comparing
with the approved PNGs, not merely to make failing snapshots green.

Add two draft-save tests: editing inputs, changing assignee and then saving inputs
succeeds when no input changed remotely; the same sequence with a concurrent server
edit to a dirty field returns a conflict and preserves both the remote value and
the unsaved local draft. A refresh or assignment response must not turn the latter
case into an accidental last-write-wins overwrite.

- [ ] **Step 5: Run all UI projects, review and commit.**

Run `python -m pytest tests/test_workflow_routes.py tests/test_ticket_routes.py -q`
and `npm run test:ui -- tests/ui/workflow_board.spec.ts`.
Review desktop-light/dark and mobile-light/dark screenshots, then commit
`feat(ui): add workflow boards and ticket inspector`.

## Task 12: Build the Approved Workflow Library Editor

**Files**

- Create: `flowgency/web/routes/workflow_library.py`, `flowgency/workflows/forms.py`.
- Create templates: `workflow_library.html`, `workflow_blueprint.html`.
- Create assets: `flowgency/static/workflow-editor.js`, `workflow-editor.css`.
- Modify: `flowgency/web/dependencies.py`, `flowgency/app.py`, `templates/base.html`.
- Create: `tests/test_workflow_library_routes.py`, `tests/test_workflow_forms.py`,
    `tests/ui/workflow_library.spec.ts`.

**Interfaces**

- Consumes: Task 4's editing commands and compatible publication service.
- Produces `WorkflowEditorDraft`, `parse_editor_draft(source: WorkflowSnapshot,
    payload: dict) -> WorkflowDefinition` and `editor_payload(snapshot)
    -> dict` in `workflows.forms`.
- Routes: GET `/admin/workflow-library`; GET/POST
    `/admin/workflow-library/blueprints/new`; GET/POST
    `/admin/workflow-library/blueprints/{blueprint}`; POST
    `/admin/workflow-library/blueprints/{blueprint}/preview` returns validated draft
    issues without publishing. Successful saves use 303 redirects.
- Draft rows identify existing objects with server-issued stable IDs and new rows
    with temporary UI keys. The parser assigns real IDs once, validates references,
    and rejects duplicate/forged source IDs; names never replace identity.

**Normative assets**

- `docs/superpowers/specs/assets/2026-09-07-ticket-workflows/workflow-blueprint-editor.html`
- `workflow-overview-desktop.png`, `workflow-states-desktop.png`,
    `workflow-transitions-desktop.png`, and their three `-mobile.png` counterparts
    in the same directory.

- [ ] **Step 1: Add red route/draft tests for hidden stable identities.**

```python
def test_editor_rename_keeps_ids(workflow_web_env):
        env = workflow_web_env
        source = env.library.inspect("delivery")
        draft = editor_payload(source)
        draft["name"] = "Renamed delivery"
        draft["states"][0]["name"] = "Inspection"
        draft["transitions"][0]["name"] = "Finish"
        parsed = parse_editor_draft(source, draft)
        assert parsed.id == source.definition.id
        assert parsed.states[0].id == source.definition.states[0].id
        assert parsed.transitions[0].id == source.definition.transitions[0].id
        assert parsed.transitions[0].from_state == source.definition.transitions[0].from_state
```

Add a route test asserting rendered editable controls contain no labels named
Identifier, Source, or Evidence required, while draft JSON can carry immutable
references. Use a structured HTML parser or browser test, not a text assertion
that incorrectly forbids every internal ID in the page payload.

- [ ] **Step 2: Run red.**

Run `python -m pytest tests/test_workflow_forms.py tests/test_workflow_library_routes.py -q`
and `npm run test:ui -- tests/ui/workflow_library.spec.ts --project=desktop-dark`.
Expect missing editor routes/forms.

- [ ] **Step 3: Implement the three-tab form with field reuse and strict publication.**

Library listing shows valid and invalid definitions and the instances using them.
The editor has Overview, States, Transitions only. State and transition names are
editable; all technical IDs are hidden. Initial-state radio and up/down controls
operate on stable IDs. No Source tab, raw YAML textarea or evidence-required
checkbox is reintroduced.

The transition editor lays out name, From/To, Preconditions, Inputs/Outputs,
and Agent criteria as approved. Inputs/outputs use the top-level field catalog:
the Add control opens a small menu to create a field by label/type or reuse an
existing labeled field. Choosing an existing field creates `FieldUse` with the
existing ID. Do not synthesize identity by matching labels. Renaming a catalog
field updates its rendered uses and precondition labels; stored references remain
unchanged. For Boolean/Number precondition values use checkbox/number controls,
not a string textbox that silently coerces values.

```python
def resolve_field_use(row, source_fields, new_fields):
        if row.existing_field_id is not None:
                field = source_fields[row.existing_field_id]
        else:
                field = new_fields[row.draft_field_key]
        return FieldUse(field_id=field.id, required=row.required)
```

Define strict draft row types in `workflows.forms`: `DraftField(key,
existing_field_id, label, type)`, `DraftFieldUse(existing_field_id,
draft_field_key, required)`, `DraftState`, `DraftTransition`, `DraftCriterion`.
Require exactly one existing/new reference per use and allocate each temporary
key once per save. Reject duplicate IDs, dangling state/input refs, invalid field
types, conflicting catalog edits and removed fields still referenced by rules.
Never return a generated replacement ID for a renamed existing row.

Save posts expected config revision, blueprint digest and draft version. A newer
preview/save response cannot be overwritten by an earlier response. 409/422 errors
retain the draft and give field-level corrections. The publication service checks
actual state compatibility and current source bytes; the UI's disabled Delete is
not the only protection. A name-only edit does not require regenerating source
identity or modifying unrelated definitions.

- [ ] **Step 4: Add required regression and visual tests.**

```typescript
test('field labels and state names do not expose technical identifiers', async ({ page }) => {
    await page.goto('/admin/workflow-library/blueprints/delivery');
    await expect(page.getByRole('tab')).toHaveText(['Overview', 'States', 'Transitions']);
    for (const name of ['Overview', 'States', 'Transitions']) {
        await page.getByRole('tab', { name, exact: true }).click();
        await expect(page.getByLabel(/identifier/i)).toHaveCount(0);
        await expect(page.getByRole('checkbox', { name: /Evidence required/i })).toHaveCount(0);
        await assertNoLayoutIssues(page);
    }
    await expect(page).toHaveScreenshot('workflow-transitions.png', { fullPage: true });
});
```

Use the same Playwright imports/helpers as Task 11. Cover create/edit/revert,
renaming criteria without changing audit identity, generated IDs after deletion
and recreation, adding/reusing a field across transitions, stale source/config
save, malformed external YAML, invalid type/requiredness, incompatible state
removal, state reorder, mobile transition picker, and all three approved tab
screenshots. Editing source externally stays supported through the library parser,
not a UI Source tab.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_workflow_forms.py tests/test_workflow_library_routes.py tests/test_workflow_compatibility.py -q`
and `npm run test:ui -- tests/ui/workflow_library.spec.ts`.
Review identity preservation and approved asset comparison, then commit
`feat(ui): add structured workflow blueprint editor`.

## Task 13: Build Editable Workflow Settings and Storage Switching

**Files**

- Create: `flowgency/web/routes/workflow_settings.py`,
    `flowgency/templates/workflow_settings.html`, `flowgency/static/workflow-settings.js`.
- Extend: `flowgency/workflows/forms.py`, `flowgency/workflows/configuration.py`,
    `flowgency/templates/base.html`, `flowgency/app.py`.
- Create tests: `tests/test_workflow_settings.py`, `tests/ui/workflow_settings.spec.ts`.

**Interfaces**

- Consumes: Task 4's configuration service and provider health; Task 12 library.
- Routes: GET/POST `/{team}/workflows/new`, GET/POST
    `/{team}/workflows/{workflow}/settings`, POST
    `/{team}/workflows/{workflow}/settings/check-storage` returning scoped health JSON.
- Produces `WorkflowSettingsForm(name, blueprint, integration,
    integration_config, expected_revision)`; IDs for new instances are server-generated.

**Normative assets**

- `docs/superpowers/specs/assets/2026-09-07-ticket-workflows/workflow-instance-settings.html`
- `workflow-settings-desktop.png`, `workflow-settings-mobile.png`,
    `workflow-create-desktop.png`, `workflow-create-mobile.png`,
    `workflow-storage-error-desktop.png` in the same directory.

- [ ] **Step 1: Add red populated-workflow edit and switch-back tests.**

```python
def test_populated_workflow_storage_change_does_not_migrate(workflow_web_env):
        env = workflow_web_env
        ticket = env.create()
        original = env.read(ticket.ref).record
        response = env.save_settings(root=env.root_b)
        assert response.status_code == 303
        assert env.current_provider().list("newsletter", "board-a") == ()
        assert env.provider.read(ticket.ref) == original
        env.save_settings(root=env.root_a)
        assert env.read(ticket.ref).record == original
```

Define `save_settings(root, blueprint=None, name=None, expected_revision=None)` in
the fixture as an actual POST with the current configuration revision and no
redirect following. The helper must not mutate records or bypass compatibility.

- [ ] **Step 2: Run red.**

Run `python -m pytest tests/test_workflow_settings.py -q` and
`npm run test:ui -- tests/ui/workflow_settings.spec.ts --project=desktop-dark`.
Expect missing settings routes and form controls.

- [ ] **Step 3: Implement the approved fields and actual validation, not a count lock.**

Render Name, Blueprint with editor link, Local integration, Storage root and Check
storage. Add the creation action beside Workflows and a settings icon on a board
with an accessible tooltip. Keep these as scoped navigation actions, not a second
settings authority. No revision/ID editor, ticket-count lock or in-use note.

```python
@router.post("/{team}/workflows/{workflow}/settings")
async def save_settings(team: str, workflow: str, request: Request):
        services = get_services(request)
        form = WorkflowSettingsForm.model_validate(dict(await request.form()))
        await run_in_threadpool(
                services.workflow_configuration.save_instance,
                form.expected_revision, team, workflow, form.to_patch(),
        )
        return RedirectResponse(f"/{team}/workflows/{workflow}/settings", status_code=303)
```

`WorkflowSettingsForm.to_patch()` produces Task 3's typed patch; parse provider
settings through its registry validator, not an arbitrary JSON editor. Name-only
changes preserve binding IDs and do not recreate missing storage. A new root is
explicitly prepared/validated during setting selection, and subsequent reads
never recreate a missing configured root. Storage switching changes the binding
only. Do not copy tickets, clear old assignments, rewrite evidence, or infer a
state mapping. The destination must be compatible with its selected blueprint if
it already contains tickets; report actual incompatibilities while fields remain
editable.

Availability checks report a measured result for the entered destination. Do not
reuse a cached green result after root/provider changes. They are not agent jobs
and cannot write into the source repository. On a failed save preserve all form
values and the current authoritative binding. In-flight broker requests remain
guarded by current bindings and original-target cleanup (Tasks 7-9).

- [ ] **Step 4: Add UI and failure tests.**

```typescript
test('existing tickets do not disable workflow settings', async ({ page }) => {
    await page.goto('/newsletter/workflows/delivery/settings');
    await expect(page.getByLabel('Blueprint', { exact: true })).toBeEnabled();
    await expect(page.getByLabel('Integration', { exact: true })).toBeEnabled();
    await expect(page.getByLabel('Storage root', { exact: true })).toBeEnabled();
    await expect(page.getByText(/Blueprint and storage in use/)).toHaveCount(0);
    await expect(page.getByLabel(/identifier/i)).toHaveCount(0);
    await assertNoLayoutIssues(page);
    await expect(page).toHaveScreenshot('workflow-settings.png', { fullPage: true });
});
```

Test missing root/name, stale revision, incompatible candidate blueprint, valid
renaming, empty destination, switch-back without transfer, unavailable storage
versus empty board, source-workspace overlap, provider error details without
secrets, active job switching and cleanup to old root, new workflow creation,
navigation to the selected (not hard-coded) blueprint, mobile layout and keyboard
labels. Do not use the sketch's synthetic health toggle in production routes.

- [ ] **Step 5: Run green, review, and commit.**

Run `python -m pytest tests/test_workflow_settings.py tests/test_workflow_compatibility.py tests/test_ticket_job_recovery.py -q`
and `npm run test:ui -- tests/ui/workflow_settings.spec.ts`.
Review the no-migration/no-lock requirements against the archived settings assets,
then commit `feat(ui): configure workflow instances and storage`.

## Task 14: Retire the Fixed Pipeline Without Losing Jobs or Memory

**Files**

- Modify: `flowgency/app.py`, `flowgency/cli.py`, `flowgency/cli_output.py`,
    `flowgency/configuration/team_paths.py`, `flowgency/configuration/paths.py`.
- Modify: `flowgency/permissions/eligibility.py`, `flowgency/permissions/__init__.py`
    and `tests/test_executor_eligibility.py` to preserve workspace-write eligibility
    under a neutral name after decision execution is removed.
- Modify: `flowgency/jobs/execution.py`, `resolution.py`, `reconciliation.py`,
    `models.py`, `flowgency/jobs/artifacts.py`.
- Modify: `flowgency/web/routes/agent_detail.py`, `agents.py`, `jobs.py`,
    `flowgency/templates/home.html`, `base.html`, `agent_detail.html` and
    `agent_detail_activity.html`, `agents.html`, `job_detail.html`, `jobs.html`.
- Create: `flowgency/memory/launch.py`, `flowgency/tickets/reporting.py`,
    `flowgency/tickets/cli.py`.
- Remove after reference checks: `flowgency/records/ingest.py`, `outbox.py`,
    `protocol.py`, `validation.py`, `flowgency/proposals.py` and pipeline-only
    templates `observations.html`, `observation_detail.html`, `proposals.html`,
    `proposal_detail.html`, `decisions.html`, `decision_detail.html`.
- Preserve: `flowgency/records/frontmatter.py` if generic Markdown consumers still
    use it. Do not delete the whole records package because one subsystem is retired.
- Create: `tests/test_pipeline_retirement.py`, `tests/test_ticket_cli.py`,
    `tests/test_ticket_reporting.py`, `tests/test_memory_launch.py`.
- Update affected existing tests: `test_dashboard.py`, `test_agent_detail.py`,
    `test_agent_status.py`, `test_job_models.py`, `test_job_execution.py`,
    `test_job_reconciliation.py`, `test_cli.py`, `test_cli_contract.py`,
    `test_records_outbox.py`, `test_records_worker.py`, and pipeline-specific tests.

**Interfaces**

- Consumes: all new workflow/ticket services and the existing memory publication
    contract. No public retired pipeline API is retained as a compatibility loader.
- Produces `LaunchMemory(root: Path, memory: Path)`,
    `prepare_launch_memory(launch_view: Path, *, memory_files: Mapping[str, bytes])
    -> LaunchMemory` and `copy_launch_memory_to_stage(launch: LaunchMemory,
    stage_directory: Path) -> None` in `memory.launch`.
- Produces `build_ticket_reporting_protocol(*, workflows_available: bool,
    tool_mode: str, tool_names: tuple[str, ...]) -> str` and
    `append_ticket_reporting_protocol(task_input, **kwargs) -> str` in
    `tickets.reporting`. Retain memory instructions and truthful permission notes.
- Produces `register_ticket_commands(subparsers) -> None` and
    command handlers in `tickets.cli`, using user contexts only.
- Rename `may_execute_decisions(config, team_key, agent_name)` to
    `may_write_workspace(config, team_key, agent_name)` while preserving its exact
    effective-permission calculation. `grants_write_on(rules, workspace)` remains
    unchanged; it requires write on the workspace root, not merely a child path.
- `ResolvedTeamPaths` retains workspace/team/locks/logs and exposes
    `runtime_directories`; it no longer makes observations/proposals/decisions
    required startup directories. Existing retired directories are untouched.

- [ ] **Step 1: Add a retired-files-unchanged red integration test.**

```python
def test_dashboard_and_jobs_do_not_touch_retired_records(workflow_web_env, tmp_path):
        env = workflow_web_env
        retired_record = env.team_root / "observations" / "old.md"
        retired_record.parent.mkdir(parents=True)
        retired_record.write_text("---\nstatus: open\nttl_days: 1\ndate: 2000-01-01\n---\nOld record\n",
                                            encoding="utf-8")
        before = retired_record.read_bytes()
        for url in ("/newsletter/", "/newsletter/agents", "/newsletter/jobs", env.base_path):
                assert env.client.get(url).status_code == 200
        assert retired_record.read_bytes() == before
        for url in ("/newsletter/observations", "/newsletter/proposals", "/newsletter/decisions"):
                assert env.client.get(url).status_code in (404, 410)
                assert env.client.post(url, data={}).status_code in (404, 405, 410)
```

Expose `.team_root` on `workflow_web_env` from its real normalized configuration.
Also create a team with no workflows and no retired directories; startup/dashboard
must not manufacture either retired directories or a board.

- [ ] **Step 2: Run red and identify the exact pipeline callers being removed.**

Run `python -m pytest tests/test_pipeline_retirement.py -q`.
Expected: old routes are still active or TTL modifies the retired record. Search
the active worktree with `rg -n "list_observations|list_proposals|list_decisions|project_decision|validate_outbox|create_outbox" flowgency tests`.
Use the result to update direct consumers, not to retain forwarding wrappers.

- [ ] **Step 3: Separate memory, stop old execution, and replace reporting.**

Move the bounded memory-directory preparation/copying behavior out of the outbox
module without changing the memory publication protocol. Preserve limits of 20
Markdown memory files, 65,536 bytes per file and 100 directory entries from the
current implementation; test symlinks/reparse points and non-regular files. Do not
remove `.flowgency` recursively after a live tool descriptor has been created;
prepare memory before ticket runtime metadata and mutate only its owned directory.

```python
def prepare_launch_memory(launch_view, *, memory_files):
        memory = Path(launch_view).joinpath(*ZONE_MEMORY.split("/"))
        require_safe_memory_directory(memory)
        for name, payload in memory_files.items():
                require_memory_name_and_size(name, payload)
                atomic_write_bytes(memory / name, payload)
        return LaunchMemory(root=Path(launch_view), memory=memory)
```

Define the two validation helpers in `memory.launch`, reusing the proven outbox
checks before deleting their old owner. `copy_launch_memory_to_stage` preserves
delete semantics for intentionally removed memory files. Keep failed-memory
artifacts distinct from immutable ticket evidence. Replace imports of record byte
limits in `jobs.artifacts` with the memory-owned limits actually applicable to
retained memory; do not let deleting record validation break log/artifact imports.

Remove `project_decision` calls from execution/reconciliation and unregister old
pipeline HTTP/CLI actions. Historical schema-5 decision jobs remain readable and
unchanged on disk. New submission rejects decision/decision_retry. If an old queued
decision is encountered, mark it failed with a specific retired-trigger summary
under the existing job transition path without creating or modifying a decision
file; do not automatically convert it into a ticket job. Active old runs are
not force-terminated by a configuration load, and terminal historical records
are not rewritten merely for being old.

Update the direct eligibility consumers in `app.py`, `cli.py` and
`web/routes/agent_detail.py` to the neutral workspace-write name; remove the retired
record-validation consumer. Preserve Copilot's existing `grants_write_on`-based
Git/GitHub credential policy. Being able to report or transition a ticket must
never grant workspace write, `gitAuth`, `ghAuth` or shell capability. Retain the
root-versus-subdirectory and read-only credential regression cases.

New reporting instructions are concise and operational:

```text
Use Flowgency ticket tools for work-item reporting. Inspect the current workflow
and ticket before acting. Start work only on an unassigned ticket you claim or a
ticket assigned to you. Evaluate the current project against transition rules;
existing completed work may already satisfy them. Submit required outputs and an
assessment for each criterion. Only an accepted transition changes ticket state.
Assignment persists unless you choose to sign off. One run may work on several
tickets. Write memory only in the provided memory directory.
```

Append real available workflow/tool context, not hard-coded ticket states or
executor names. No observations/proposals outbox instructions remain. A team with
no configured workflows is reported as such; do not fail unrelated memory-only
runs solely for having nothing to report.

- [ ] **Step 4: Replace dashboard, activity and CLI consumers with ticket views.**

Keep the existing dashboard's agent fleet, runtime availability, routines, jobs,
memory and workspace content. Replace pipeline counts/feeds with per-workflow
ticket totals, active ticket counts and ticket events. Do not infer human attention
from a state name; show explicitly missing inputs and latest report issues, along
with unassigned tickets and job failures. Agent activity combines job/log entries
with ticket events attributed to that agent, not old record directories.

Add `flowgency workflows [--team ...] [--json]`,
`flowgency tickets --workflow <id> [--state <id>] [--assignee <agent>] [--json]`,
`flowgency ticket show <ticket-id> --workflow <id>`,
`flowgency ticket create --workflow <id> --title <text>`,
`flowgency ticket assign <ticket-id> --workflow <id> --agent <name>`,
`flowgency ticket unassign <ticket-id> --workflow <id>`, and
`flowgency ticket run <ticket-id> --workflow <id>`. Resolve current versions and
call the same user service; CLI has no state-move or actor-spoofing command.
Expose existing `flowgency status`/`inbox` via ticket summaries and retain JSON
error conventions from `cli_output.py`.

```python
def test_cli_has_no_manual_transition_command(capsys):
        parser = build_parser()
        with pytest.raises(SystemExit) as error:
                parser.parse_args(["ticket", "transition", "ticket-a", "--state", "done"])
        assert error.value.code != 0
```

Reuse the existing `flowgency.cli.build_parser() -> ArgumentParser`, confirmed in
the current CLI, and test existing non-pipeline parsing unchanged. CLI creation accepts no state argument;
assignment/run use the generated operation ID and current revision internally.

Retire tests whose only contract is the removed pipeline, replacing their useful
path-safety, concurrent-write, XSS and failure cases with ticket tests. Keep generic
frontmatter, memory, permission and job tests. Do not keep old actions alive merely
to satisfy obsolete expectations, and do not suppress unrelated failing tests.

- [ ] **Step 5: Run green, review and commit.**

Run `python -m pytest tests/test_pipeline_retirement.py tests/test_ticket_cli.py tests/test_ticket_reporting.py tests/test_memory_launch.py tests/test_dashboard.py tests/test_agent_detail.py tests/test_job_execution.py tests/test_job_reconciliation.py tests/test_cli_contract.py -q`.
Review the direct retired read/write removal and preserved behavior, then commit
`refactor(pipeline): replace records with ticket workflows`.

## Task 15: Update Setup, Examples and User Documentation

**Files**

- Modify: `config.yaml.example`, `README.md`, `AGENTS.md`.
- Modify: `kb/configuration.md`, `kb/data-formats.md`, `kb/directory-structure.md`,
    `kb/getting-started.md`, `kb/setup-skill.md`, `kb/integrations.md`,
    `kb/contributing-integrations.md`, `kb/dispatch.md`, `kb/agent-identity.md`.
- Modify canonical setup source only:
    `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`,
    `references/templates.md`, `references/dispatch-templates.md`.
- Replace pipeline-only `references/observation-system-steps.md` with
    `references/ticket-workflow-steps.md` and update its links.
- Add shipped blueprint examples under
    `flowgency/setup_assets/workflows/software-delivery/workflow.yaml` and
    `flowgency/setup_assets/workflows/research/workflow.yaml`; include them in
    setuptools package data.
- Update examples under `examples/code-review-team/` and `examples/content-team/`
    only where their agent instructions/configuration promise old reporting:
    `examples/code-review-team/README.md`, `reviewer/CLAUDE.md`, `security/CLAUDE.md`;
    `examples/content-team/README.md`, `editor/CLAUDE.md`, `researcher/CLAUDE.md`,
    `writer/CLAUDE.md` (agent paths relative to their listed team directory).
- Extend: `tests/test_flowgency_setup_skill.py`, `tests/test_setup_assets.py`,
    `tests/test_setup_flow.py`, `tests/test_cli_contract.py`.
- Create: `tests/test_workflow_setup.py`.

**Interfaces**

- Consumes: current canonical config/blueprint models, live reporting contract and
    supported-runtime capability matrix.
- Produces `workflow_example_root() -> Path` in `flowgency.setup_assets` for
    package-owned examples, and setup output using `flowgency.workflow_library`
    plus explicit team workflow instances and Local roots.
- `skills/flowgency-setup` and `.github/skills/flowgency-setup` must continue to
    resolve to the package-owned canonical source; do not fork separate copies.

- [ ] **Step 1: Load the customization-writing procedures and add red setup tests.**

Before editing actual skill files, load the applicable `writing-skills` and
`agent-customization` skills. This plan does not edit those files itself. The
tests assert generated canonical output, not only phrases in a README.

```python
def test_shipped_workflows_are_valid_and_local_only():
        from flowgency.setup_assets import workflow_example_root
        library = WorkflowLibrary(workflow_example_root())
        examples = library.list()
        assert {item.definition.name for item in examples} == {"Software delivery", "Research"}
        assert all(not item.issues for item in examples)
        for item in examples:
                definition = item.definition
                assert definition.state(definition.initial_state) is not None
                assert "evidence_required" not in definition.model_dump_json()
```

`WorkflowDefinition.state(id)` is the state lookup counterpart of `.field()` and
`.transition()` from Task 1. Example definitions are source data, not globally
registered boards and not automatic startup imports.

- [ ] **Step 2: Run red.**

Run `python -m pytest tests/test_workflow_setup.py tests/test_setup_assets.py -q`.
Expected: missing workflow example source/config output. Preserve setup source
parity tests before changing instructions.

- [ ] **Step 3: Update guided setup and package assets.**

After workspace/team/agent intent is understood, propose useful workflow
blueprints and their named team instances for user approval. Derive
`<data_root>/workflow-library` and `<data_root>/tickets` from the already-approved
data root; do not restart a long path interview or create one directory per agent.
Write only approved workflows, and keep dispatch activation as its separate
existing approval. Empty workflows configuration is valid if the user declines.

The example config shape is:

```yaml
flowgency:
    workflow_library: C:/Flowgency/workflow-library
teams:
    newsletter:
        workflows:
            workflow-delivery:
                name: Delivery
                blueprint: software-delivery
                integration: local
                integration_config:
                    root: C:/Flowgency/tickets
```

Preserve all existing schema-1 required config roots and agent/routine fields.
Generate fresh stable IDs for newly created user definitions/instances without
asking users to name technical IDs. Shipped reusable blueprint IDs are stable and
can be referenced by configured instances. Saved prompt instructions direct agents
to discover/claim tickets and obey current rules; no role binding or guaranteed
workspace write permission is inferred from a workflow.

Add package data for `flowgency.setup_assets`:

```toml
"flowgency.setup_assets" = [
        "copilot/.github/skills/flowgency-setup/*.md",
        "copilot/.github/skills/flowgency-setup/references/*.md",
        "workflows/*/workflow.yaml",
]
```

Use the existing packaging tests to build/inspect an installed package; do not
assume files exist only because they are in the repository. Document how external
YAML edits are validated, IDs differ from labels, invalid definitions block only
affected transitions, storage switching does not transfer data, assignment is
ownership and sign-off remains optional.

- [ ] **Step 4: Update reference documentation and assert retirement language.**

Document the new CLI commands, Local provider contract, runtime ticket-tool
capability/support limitations, field/assessment semantics, original-binding
cleanup and no-user-move UI. Update the README's "How tickets flow" to describe
generic workflows, not a fixed observation-to-decision chain. Keep preserved
agent/routine/memory/permission guidance intact. In AGENTS.md replace superseded
pipeline reporting statements with the new authority model while retaining its
development/commit/integration workflow verbatim.

```python
def test_setup_uses_ticket_reporting_without_native_authority():
        skill = (copilot_discovery_root() / ".github/skills/flowgency-setup/SKILL.md").read_text(encoding="utf-8")
        assert "flowgency.workflow_library" in skill
        assert "ticket-workflow-steps.md" in skill
        assert "observation-system-steps.md" not in skill
        assert "one authoritative canonical Flowgency config" in skill
```

Retain the current canonical-path parity tests and configuration validation tests.
Do not alter the user's actual setup/config/team directories. Removing retired
instructions from shipped examples is not permission to rewrite user-created
prompts at startup.

- [ ] **Step 5: Run green, review and commit.**

Run `python -m pytest tests/test_workflow_setup.py tests/test_flowgency_setup_skill.py tests/test_setup_assets.py tests/test_setup_flow.py tests/test_cli_contract.py -q`.
Review all documentation examples with `flowgency validate --config <temporary-example-config>`
using temp roots prepared by the tests; do not validate against the live user config.
Commit `docs(workflows): update setup and ticket guidance` with code/package asset
changes split into a preceding `feat(setup): create ticket workflow examples` commit
if they add behavior. Keep each commit single-purpose under repository conventions.

## Task 16: Verify the Complete User and Runtime Contract

**Files**

- Create: `tests/test_ticket_runtime_live.py`, `tests/test_ticket_end_to_end.py`.
- Extend: `tests/_runtime_probe_helpers.py`, `tests/ui/accessibility.spec.ts`,
    `tests/ui/dashboard.spec.ts`, `tests/ui/fixtures/config.yaml`, `tests/ui/server.py`.
- Add reviewed snapshot baselines for the three workflow UI spec files.
- Record execution evidence in
    `docs/superpowers/verification/2026-09-08-ticket-workflows.md` during execution.

**Interfaces**

- Consumes: the fully registered application, real broker/provider/job operations,
    existing installed-runtime discovery and UI gate helpers.
- Produces verified capability evidence; this task does not manufacture a green
    runtime-support flag from deterministic mocks or command-line help.

- [ ] **Step 1: Add a true end-to-end pre-satisfied-project test.**

Create a small temp project with an already-correct output file and a blueprint
whose transition requires a retained test report and positive qualitative
assessment. Start a real durable job through the runtime fixture. The test agent
reads the current project and ticket, claims it, submits evidence and transitions
without rewriting the completed project file.

```python
@pytest.mark.real_runtime
@pytest.mark.parametrize("runtime", ticket_capable_installed_runtimes(), ids=lambda item: item.name)
def test_agent_can_verify_existing_work_without_repeating_it(runtime, ticket_live_env):
        env = ticket_live_env(runtime)
        before = env.project_result.read_bytes()
        handle = env.submit_verification_job()
        record = env.wait_for_terminal_job(handle)
        assert record.status == "complete", record.execution_summary
        ticket = env.provider.read(env.ticket_ref)
        assert ticket.state_id == env.done_state_id
        assert ticket.assignee == env.agent_name
        assert ticket.active_run is None
        assert env.project_result.read_bytes() == before
        assert env.retained_report(ticket).content
```

Define `ticket_capable_installed_runtimes()` in `_runtime_probe_helpers.py` by
intersecting installed runtimes with Task 8's explicitly supported adapters.
Capability probing must not recursively require this same test; deterministic
declaration selects candidates, a successful live run establishes measured
availability. `ticket_live_env(runtime)` writes isolated canonical config/source
and uses actual submission, not live user data. Its wait helper uses process/job
completion with a bounded deadline and diagnostics, never drops authentication or
timeout failures. No installed capable runtime produces one explicit skip with a
recorded capability gap; the required Copilot read-only probe must run on a
configured environment before the feature's runtime guarantee is claimed.

- [ ] **Step 2: Verify negative runtime capability and permission cases.**

Run `python -m pytest tests/test_ticket_runtime_live.py -m real_runtime -v`.
Add installed-runtime scenarios for live create/update before run completion,
read/search-only workspace agent ticket reporting, denied workspace edits with
permitted ticket operations, multi-ticket run, stale transition refresh, optional
sign-off and failed run after a committed transition. Capture hashes of protected
project/config/blueprint files using existing runtime probe helpers. Never feed
real credentials through the assistant; installed CLIs use their configured
authentication. Authentication/quota/network/timeout failures remain failures.

- [ ] **Step 3: Run the complete deterministic and UI acceptance matrix.**

Run `python -m pytest tests/ -m 'not real_runtime' -q`, then
`npm run test:ui`. The four UI projects are `desktop-light`, `desktop-dark`,
`mobile-light`, and `mobile-dark`. Reuse `@axe-core/playwright`, layout and keyboard
helpers. Check real routes for all approved surfaces and their error states.
Include a 320px explicit viewport case for longest labels, no clipped controls,
no body horizontal overflow, and scrollable Kanban tracks without hiding errors.

```typescript
test('workflow surfaces have no serious accessibility violations', async ({ page }) => {
    for (const url of [
        '/newsletter/workflows/delivery',
        '/admin/workflow-library/blueprints/delivery',
        '/newsletter/workflows/delivery/settings',
    ]) {
        await page.goto(url);
        const report = await new AxeBuilder({ page }).analyze();
        expect(report.violations.filter(item => ['serious', 'critical'].includes(item.impact ?? ''))).toEqual([]);
    }
});
```

Use the existing accessibility test imports and theme initialization. Review
screenshots against the archived approved assets as well as new fixture baselines;
differences in synthetic dates/titles may be masked narrowly, not entire content
regions or status/assignment controls. A generic `data-allow-scroll` must not hide
clipped forms outside the board track.

- [ ] **Step 4: Run full suite and whole-branch review.**

Run `python -m pytest tests/ -q` from the worktree, including installed live probes.
Inspect the full branch diff against master and record review findings by severity.
Review auth/context derivation, concurrency/lock order, same-agent multi-run races,
original-target cleanup, idempotent reservations, no migration, compatibility
validation, hidden IDs, no user moves and retired-file preservation. Repair any
findings with their own regression tests and rerun affected tests before the final
full suite. Do not integrate with unverified required gates.

- [ ] **Step 5: Commit only verified test and documentation evidence.**

Commit new tests as `test(workflows): verify ticket orchestration` and the concise
verification record separately as `docs(workflows): record verification evidence`.
The record includes commands, actual pass/fail/skip totals, runtime versions,
approved asset comparisons, reviewer result and any genuinely unverified platform
requirements. Never prefill it with this plan's expected outcomes.

Replace the README's illustrative board image with a screenshot from the verified
fixture-backed application only after the UI comparison is approved. Keep that
image update in the verification/documentation commit, not in a test-baseline
rewrite used to conceal a layout difference.

## Task 17: Integrate, Publish and Clean Up

This task is pre-authorized by the repository guide once implementation, review
and required verification succeed. It is not performed while writing this plan.
Do not offer merge/squash/PR alternatives or create a merge commit.

- [ ] **Step 1: Verify the feature is committed and reviewed.**

From the feature worktree run `git status --short --branch`,
`git log --oneline master..HEAD`, and `git diff --check master...HEAD`. Record the
reviewed tip. Require no uncommitted feature changes; unrelated runtime files are
not staged to obtain a clean status.

- [ ] **Step 2: Bring the feature up to date only if necessary.**

Run `git fetch origin` and compare local/origin master without overwriting local
changes. If master gained commits, rebase `feat/ticket-workflows` onto the current
master as required for fast-forward integration. Resolve conflicts without
discarding user changes, rerun the complete pytest and UI gates in the worktree,
and update review evidence for the rebased tip. Do not rebase for cosmetic history.

- [ ] **Step 3: Preserve a dirty main checkout and fast-forward.**

Inspect the main checkout at `C:/Projekty/Flowgency`. If it has user changes, record
their status and stash them before integration as the guide requires; do not stage
or delete runtime-local files. Do not use `git clean` or `git reset --hard`.
Record the exact stash ID and restore it after the fast-forward. Use
`git -C C:/Projekty/Flowgency merge --ff-only feat/ticket-workflows`.
Any stash restoration conflict is a stop-and-resolve condition, not permission to
discard the saved work.

- [ ] **Step 4: Verify master and publish both branches.**

Run `python -m pytest tests/ -q` from the fast-forwarded main checkout, with the
correct environment/package resolution. Reinstall editable mode from that checkout
if the environment still points to the soon-to-be-removed feature worktree. Run the UI gate there if integration
changed any UI fixture/config context relative to the verified worktree. When
green, run `git push origin master feat/ticket-workflows`. Confirm both remote
refs contain the reviewed feature tip. Do not leave successfully integrated work
unpublished.

- [ ] **Step 5: Remove the feature worktree and retain its branch.**

From the main checkout run `git worktree remove .worktrees/ticket-workflows` and
`git worktree prune` after verifying all work is committed and master carries the
feature. Remove only generated worktree test outputs if they block removal; never
force-remove unknown user data. Keep `feat/ticket-workflows` unless the user asks
to delete it. Point final file links at main-checkout paths and report the final
commit, test evidence, publication and cleanup status.

## Requirements Coverage

| Design requirement | Owning tasks |
| --- | --- |
| Generic reusable definitions, states and hybrid rules | 1, 4, 12 |
| Shared field identity and readable labels | 1, 4, 12 |
| Current-source updates and historical audit snapshots | 4, 6, 7 |
| Canonical explicit team instances | 3, 4, 13 |
| Separate Local ticket-storage integration | 2, 3, 6 |
| Atomic revisions, receipts and corruption handling | 2, 5, 6, 16 |
| Assignment equals claim; one active run per ticket | 5, 7, 9 |
| Multiple tickets per run; optional sign-off | 5, 7, 9, 16 |
| Live job-scoped tools and read-only-agent reporting | 7, 8, 9, 16 |
| User selection assigns immediately; Run is separate | 9, 10, 11 |
| Agent-only transitions and no ownership bypass | 5, 6, 7, 10, 16 |
| Already-satisfied project state can advance tickets | 1, 6, 14, 16 |
| Editable populated-workflow settings | 4, 13 |
| Storage source switching without transfer/deletion | 4, 7, 9, 13 |
| Original-binding cleanup and unknown-process safety | 7, 8, 9, 16 |
| Approved board/inspector, editor and settings designs | 11, 12, 13, 16 |
| Hidden IDs, no Source tab/evidence toggle/provider badge | 4, 11, 12, 13 |
| Preserve jobs, routines, memory, logs and workspaces | 9, 14, 15, 16 |
| No retired import/archive/startup conversion | 3, 14, 15 |
| Cross-team, path, artifact, XSS and credential safety | 2, 6, 7, 8, 10, 16 |
| Full baseline, task reviews and approved-asset comparison | Setup, every task, 16 |
| Fast-forward integration, publishing and cleanup | 17 |

## Execution Handoff

This plan records the approved specification as the implementation authority.
Writing the plan does not authorize treating unrun tests as passing or prototype
logic as production code. No application edits or dependency installs are part of
the planning commit.

Choose one execution workflow after reviewing the plan:

1. Subagent-driven development: one fresh implementation agent per task, with
     task-level specification and code review before dependent work.
2. Inline execution: execute tasks in the current session with explicit checkpoints
     and the same test/review gates.

Both paths keep the work in the existing feature worktree and use the automatic
repository integration sequence only after implementation is complete and verified.