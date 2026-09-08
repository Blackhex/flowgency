# Task 5 Report: Ticket Assignment and Multi-Ticket Active Work

## Implemented interfaces

- Added `flowgency.tickets.service.TicketService` with `inspect`, `create`, `assign`, `start_work`, `end_work`, `sign_off`, and `update`.
- Extended `flowgency.tickets.models` with `ActiveTicketRun`, `FieldProvenance`, `AgentTicketContext`, `UserTicketContext`, `TicketActor`, `TicketVersion`, `TicketView`, and `TicketPatch`.
- Replaced `TicketRecord.active_run: str | None` with `ActiveTicketRun | None`.
- Replaced `TicketRecord.field_provenance: dict[str, str]` with `dict[str, FieldProvenance]`.
- `TicketService` now fences mutations on the current `WorkflowBinding`, current `context_digest`, current workflow source digest, trusted agent session validation, and configured-agent existence before provider replay.
- `TicketService` stamps exactly one trusted event per accepted mutation and server-side field provenance for persisted field updates.
- `inspect` returns a readable `TicketView` with `definition=None`, `version=None`, and explicit `issues` when the current workflow definition is unavailable.

## Files changed

- `flowgency/tickets/models.py`
- `flowgency/tickets/service.py`
- `tests/_ticket_helpers.py`
- `tests/test_ticket_ownership.py`
- `tests/test_ticket_service.py`
- `tests/test_ticket_storage_concurrency.py`
- `tests/test_local_ticket_storage.py`

## RED cycle

Command:

```powershell
& {
    Push-Location -LiteralPath 'C:/Projekty/Flowgency/.worktrees/ticket-workflows'
    try { ./.venv/Scripts/python.exe -m pytest tests/test_ticket_ownership.py -q }
    finally { Pop-Location }
}
```

Output:

```text
EEE                                                                      [100%]
ImportError: cannot import name 'AgentTicketContext' from 'flowgency.tickets.models'
3 errors in 0.23s
```

Follow-up focused repair cycle:

- Fixed unsafe ticket-id generation in service-backed create.
- Fixed `ActiveTicketRun.generation` to remain read-only without being serialized into persisted records.

## GREEN checks

Focused ownership slice:

```text
python -m pytest tests/test_ticket_ownership.py -q
...                                                                      [100%]
3 passed in 0.66s
```

Focused service slice:

```text
python -m pytest tests/test_ticket_service.py -q
..............                                                           [100%]
14 passed in 2.03s
```

Focused provider/model regression slice:

```text
python -m pytest tests/test_local_ticket_storage.py -q
................................                                         [100%]
32 passed in 0.92s
```

Focused concurrency slice:

```text
python -m pytest tests/test_ticket_storage_concurrency.py -q
....                                                                     [100%]
4 passed in 1.76s
```

Prescribed final Task 5 suite:

```text
python -m pytest tests/test_ticket_ownership.py tests/test_ticket_service.py tests/test_ticket_storage_concurrency.py -q
.....................                                                    [100%]
21 passed in 4.25s
```

Additional combined service/provider check during iteration:

```text
python -m pytest tests/test_ticket_service.py tests/test_local_ticket_storage.py -q
.............................................                            [100%]
45 passed in 2.63s
```

Warnings observed:

- No new pytest warnings in the focused Task 5 checks.
- `git diff` reported CRLF-to-LF normalization warnings for touched files; no runtime or test impact observed.

## Test coverage added

- Ownership and assignment enforcement across multiple tickets.
- Optional sign-off and active-run-only end-work semantics.
- User edit allowance during active work without granting state authority.
- Trusted field provenance stamping.
- Patch allowlist rejection for state/actor injection.
- Stale revision and binding/context fencing.
- Missing workflow definition readable views.
- Unknown/revoked agent session rejection.
- Two runs of the same agent and later session of the same durable job conflict.
- Older run/generation cannot clear a later active marker.
- Deleted configured agent rejection before receipt replay.
- Same local ticket id in another team remains distinct.
- Real-process CAS race on `start_work` preserves a single persisted active run.
- Provider roundtrip for `ActiveTicketRun` and `FieldProvenance`.

## Self-review

- Verified the service performs trust and configured-agent checks before calling provider replay paths.
- Verified mutation callbacks append exactly one trusted event and leave state/job creation untouched for `start_work`.
- Verified assignment remains the only ownership concept; no separate lease token was introduced.
- Verified user edits during active work update content and provenance while preserving agent assignment/active-run state.
- Verified local storage still round-trips strict field values and now round-trips explicit active-run/provenance models.

## Concerns

- No functional concerns from the implemented Task 5 scope.
- `git diff` reported CRLF-to-LF normalization warnings for touched files; no runtime or test impact observed.

## Commit

- Initial Task 5 commit: `6f932e1` (`feat(tickets): enforce assignment and active work`)
- Identity correction commit: `928498e` (`fix(tickets): scope creation ids to operations`)

## Pre-review identity correction

### Allocation interface and behavior

- `TicketService.create` now allocates `ticket-<uuid>` ids through a deterministic UUID derived from the canonical storage binding, trusted actor identity, and `operation_id`.
- The binding scope is the UUID5 namespace: `uuid.uuid5(uuid.NAMESPACE_URL, binding.binding_id)`.
- The UUID5 name is canonical JSON with sorted keys and compact separators: `{"actor": <trusted actor identity>, "operation_id": <trusted operation id>}`.
- `request_digest` is no longer part of ticket-id allocation. Receipt replay and digest comparison remain provider-owned and unchanged.
- Same binding + same trusted actor + same `operation_id` replays the original ticket and still routes changed digests into `OperationConflict`.
- Different `operation_id` values create different tickets even when `request_digest` is identical.
- Different trusted actors or different bindings do not collide even when both the request content and `operation_id` are the same.

Observed deterministic ids from the worktree after the correction:

- Same content, first operation: `ticket-4fe30149-ed54-528f-9ce0-0b9ea3ce610e`
- Same content, second operation: `ticket-41fde7ee-d131-598f-b613-d73a618a69fb`
- Same actor plus shared operation on binding A: `ticket-15cb656a-8d64-5f2d-b22e-409b66eec93b`
- Different actor plus shared operation on binding A: `ticket-c9061319-ba21-5f13-9f28-13e522541ce7`
- Same actor plus shared operation on binding B: `ticket-79095f01-534c-5039-8444-90aba76baf07`

### RED

Command:

```powershell
& {
    Push-Location -LiteralPath 'C:/Projekty/Flowgency/.worktrees/ticket-workflows'
    try {
        ./.venv/Scripts/python.exe -m pytest tests/test_ticket_service.py -q
    }
    finally { Pop-Location }
}
```

Output:

```text
.FF.F.............                                                       [100%]
================================== FAILURES ===================================
_ test_create_same_content_with_different_operation_ids_creates_distinct_tickets _

workflow_env = WorkflowTestEnv(tmp_path=WindowsPath('C:/Users/Blackhex/AppData/Local/Temp/pytest-of-Blackhex/pytest-583/test_create_s...egistered_sessions=set(), team_id='newsletter', workflow_id='board-a', blueprint_id='delivery', _seq=0, _session_seq=0)

    def test_create_same_content_with_different_operation_ids_creates_distinct_tickets(workflow_env):
        env = workflow_env
        digest = hashlib.sha256(b"same-content").hexdigest()
        first = env.service.create(
            env.user,
            env.workflow_id,
            "Created",
            "Body text.",
            {"summary": "hello"},
            TicketOperation(operation_id="op-user-first", request_digest=digest),
        )
>       second = env.service.create(
            env.user,
            env.workflow_id,
            "Created",
            "Body text.",
            {"summary": "hello"},
            TicketOperation(operation_id="op-user-second", request_digest=digest),
        )

tests\test_ticket_service.py:34:
flowgency\tickets\service.py:122: in create
    return provider.create(record, operation)
flowgency\tickets\storages\local.py:335: in create
    raise TicketConflict(
E   flowgency.tickets.errors.TicketConflict: [duplicate-ticket] Ticket already exists

_ test_create_reused_operation_id_with_changed_digest_conflicts_without_extra_ticket _

workflow_env = WorkflowTestEnv(tmp_path=WindowsPath('C:/Users/Blackhex/AppData/Local/Temp/pytest-of-Blackhex/pytest-583/test_create_r...egistered_sessions=set(), team_id='newsletter', workflow_id='board-a', blueprint_id='delivery', _seq=0, _session_seq=0)

    def test_create_reused_operation_id_with_changed_digest_conflicts_without_extra_ticket(workflow_env):
        env = workflow_env
        first = TicketOperation(
            operation_id="op-user-reused",
            request_digest=hashlib.sha256(b"first-digest").hexdigest(),
        )
        second = TicketOperation(
            operation_id="op-user-reused",
            request_digest=hashlib.sha256(b"second-digest").hexdigest(),
        )
        created = env.service.create(
            env.user,
            env.workflow_id,
            "Created",
            "Body text.",
            {"summary": "hello"},
            first,
        )
        assert created.ticket.ref is not None
>       with pytest.raises(TicketConflict):
E       Failed: DID NOT RAISE TicketConflict

tests\test_ticket_service.py:66: Failed
___________ test_create_scopes_ticket_identity_by_actor_and_binding ___________

workflow_env = WorkflowTestEnv(tmp_path=WindowsPath('C:/Users/Blackhex/AppData/Local/Temp/pytest-of-Blackhex/pytest-583/test_create_s...egistered_sessions=set(), team_id='newsletter', workflow_id='board-a', blueprint_id='delivery', _seq=0, _session_seq=0)

    def test_create_scopes_ticket_identity_by_actor_and_binding(workflow_env):
        env = workflow_env
        digest = hashlib.sha256(b"same-content").hexdigest()
        user_ticket = env.service.create(
            env.user,
            env.workflow_id,
            "Created",
            "Body text.",
            {"summary": "hello"},
            TicketOperation(operation_id="op-shared", request_digest=digest),
        )
        other_actor = UserTicketContext(team_id=env.team_id, actor_name="other-user")
        other_actor_ticket = env.service.create(
            other_actor,
            env.workflow_id,
            "Created",
            "Body text.",
            {"summary": "hello"},
            TicketOperation(operation_id="op-shared", request_digest=digest),
        )
        env.set_storage_root(env.root_b)
        other_binding_ticket = env.service.create(
            env.user,
            env.workflow_id,
            "Created",
            "Body text.",
            {"summary": "hello"},
            TicketOperation(operation_id="op-shared", request_digest=digest),
        )
        assert user_ticket.ticket.ref is not None
        assert other_actor_ticket.ticket.ref is not None
        assert other_binding_ticket.ticket.ref is not None
>       assert user_ticket.ticket.ref.ticket_id != other_actor_ticket.ticket.ref.ticket_id
E       AssertionError: assert 'ticket-cae1b3faaa5e4ac7' != 'ticket-cae1b3faaa5e4ac7'

tests\test_ticket_service.py:145: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_ticket_service.py::test_create_same_content_with_different_operation_ids_creates_distinct_tickets - flowgency.tickets.errors.TicketConflict: [duplicate-ticket] Ticket already exists
FAILED tests/test_ticket_service.py::test_create_reused_operation_id_with_changed_digest_conflicts_without_extra_ticket - Failed: DID NOT RAISE TicketConflict
FAILED tests/test_ticket_service.py::test_create_scopes_ticket_identity_by_actor_and_binding - AssertionError: assert 'ticket-cae1b3faaa5e4ac7' != 'ticket-cae1b3faaa5e4ac7'
3 failed, 15 passed in 2.61s
```

### GREEN

Focused regression command:

```powershell
& {
    Push-Location -LiteralPath 'C:/Projekty/Flowgency/.worktrees/ticket-workflows'
    try {
        ./.venv/Scripts/python.exe -m pytest tests/test_ticket_service.py -q
    }
    finally { Pop-Location }
}
```

Output:

```text
..................                                                       [100%]
18 passed in 2.54s
```

Covering command required by the brief:

```powershell
& {
    Push-Location -LiteralPath 'C:/Projekty/Flowgency/.worktrees/ticket-workflows'
    try {
        ./.venv/Scripts/python.exe -m pytest tests/test_ticket_service.py tests/test_ticket_ownership.py tests/test_local_ticket_storage.py tests/test_ticket_storage_contract.py tests/test_ticket_storage_concurrency.py tests/test_repository_boundaries.py -q
    }
    finally { Pop-Location }
}
```

Output:

```text
........................................................................ [100%]
72 passed in 6.07s
```

### Self-review

- Verified the correction is isolated to `TicketService.create` id allocation and the service regression slice.
- Verified public interfaces and provider receipt semantics are unchanged; only the service-side ticket id derivation moved away from `request_digest`.
- Verified the reused-operation mismatch now reaches the existing receipt guard and returns `OperationConflict` without creating another ticket.