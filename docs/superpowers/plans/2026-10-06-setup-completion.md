# Explicit Setup Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent first-run navigation until setup is explicitly complete, preferring verified graceful CLI exit and otherwise preserving the healthy terminal.

**Architecture:** Keep canonical validation, conversational completion and process lifecycle separate. An environment-authorized `flowgency setup finish` command acknowledges the active launch through a bounded loopback callback; the session manager records its revision-bound completion state. Setup routes and polling use a common navigation decision, and graceful exit is enabled only after a supported safe-boundary probe succeeds.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, standard-library HTTP/cryptography, existing connected-process adapters, Jinja2, vanilla JavaScript, pytest and Playwright.

## Global Constraints

- Approved spec: [2026-10-06-setup-completion-design.md](../specs/2026-10-06-setup-completion-design.md).
- Branch: `feature/setup-completion`; active root: `.worktrees/setup-done/`.
- Configuration readiness is necessary but insufficient.
- Completion is temporary lifecycle state.
- Do not add a config completion flag, second configuration artifact, saved team proposal, or new control-plane authority.
- Do not convert setup to non-interactive mode or grant additional permissions to obtain automatic exit.
- Neither process inactivity, terminal-text matching nor exit code zero without acknowledgement proves completion.
- Scheduler refusal permits completion without changing saved dispatch settings.
- Scheduler failure or unknown status requires an explicitly acknowledged limitation.
- Do not send forced termination as a completion mechanism.
- Reopening a terminal for inspection must remain an inspection view.
- No approved mockup or diagram exists; preserve the current UI rather than adding a wizard.
- Do not touch the user's current setup session, installed scheduler or runtime-local configuration.
- Review each independently testable task before dependent work; use Conventional Commits and selective staging.
- Keep specification, plan and implementation changes in separate commits.
- Complete Python and browser suites run sequentially, never concurrently against the shared UI runtime.

---

## Preparation And Baseline

Run from the active root, not the parent checkout. Do not start application
implementation until the complete baseline is recorded and green, or the user
has explicitly decided how to handle a demonstrated unrelated failure.

```powershell
Set-Location C:\Projekty\Flowgency\.worktrees\setup-done
git status --short --branch
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
npm install --package-lock=false
npm install --no-save --no-package-lock @playwright/test@1.61.1
$env:PLAYWRIGHT_SKIP_BROWSER_GC = '1'
npx playwright install chromium
.venv\Scripts\python.exe -m pytest tests/ -q
npm run test:ui
```

Reuse a valid existing worktree-local venv instead of recreating it. Verify the
installed Playwright/browser pair against the last green gate; 1.61.1 is the
measured repository pair, not a proposed dependency-manifest change. Preserve
failed reports before rerunning. Port 8765 belongs to the test fixture server;
do not reuse or stop the user's port-8500 dashboard.

Record results under ignored
`.superpowers/evidence/setup-completion-20261006/`. The two wheel-isolation tests
must use the worktree venv; global user-site Python is not an equivalent gate.

## File Structure And Boundaries

| File | Responsibility |
| --- | --- |
| Create `flowgency/web/setup_completion.py` | Closed completion schema, launch credential type, acknowledgement/decision records, bounded loopback client |
| Create `flowgency/web/validation.py` | The existing CLI validation issue collector, shared without changing semantics |
| [setup_sessions.py](../../../flowgency/web/setup_sessions.py) | Active launch completion state, credential matching, lifecycle/replacement guards and exit coordination |
| [setup_terminal.py](../../../flowgency/web/routes/setup_terminal.py) | Authorized callback and owner-only session/inspection responses |
| [admin_teams.py](../../../flowgency/web/routes/admin_teams.py) | Prepare launch credentials, propagate the launch context and gate setup entry/status paths |
| [models.py](../../../flowgency/integrations/models.py) and integration launchers | Optional setup-only environment overlay; no secret-bearing argv |
| [cli.py](../../../flowgency/cli.py) | Register `setup finish`, preserve existing validation output and exit contracts |
| [setup_flow.py](../../../flowgency/web/setup_flow.py) and packaged skill | Final-summary/acknowledgement ordering and existing readiness validation |
| [setup.html](../../../flowgency/templates/setup.html) | Poll the explicit navigation decision without replacing the terminal |
| Create `tools/probe_setup_exit.py` | Isolated safe-exit feasibility receipt; no production-session interaction |
| Existing setup tests and [tests/ui/server.py](../../../tests/ui/server.py) | Deterministic lifecycle, transport and browser regressions |

The package-owned skill source is
[flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md](../../../flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md).
The repository discovery paths point at that source; verify their existing link
arrangement rather than editing three independent copies.

## Interfaces To Keep Consistent

Task 1 defines these names; later tasks consume them unchanged:

```python
SchedulerResult = Literal[
    "manual-only", "inactive", "declined", "confirmed", "failed", "unknown"
]

class SetupCompletionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    launch_id: StrictStr
    revision: StrictStr
    scheduler_result: SchedulerResult
    all_questions_answered: StrictBool
    summary_delivered: StrictBool
    limitations_acknowledged: StrictBool = False

@dataclass(frozen=True)
class SetupCompletionLaunch:
    launch_id: str
    origin: str
    token: str = field(repr=False)

@dataclass(frozen=True)
class SetupCompletionDecision:
    launch_id: str | None
    phase: Literal["pending", "acknowledged", "complete", "attention", "cancelled"]
    redirect_allowed: bool
    message: str
```

Constrain `launch_id` to a UUID hex value and `revision` to a 64-character
lowercase SHA-256 hex value. Both completion assertions must be exactly `True`;
strict validation must reject strings and integer stand-ins. A failed/unknown
scheduler result requires `limitations_acknowledged is True`.

Environment keys are exactly `FLOWGENCY_SETUP_ORIGIN`,
`FLOWGENCY_SETUP_TOKEN` and `FLOWGENCY_SETUP_LAUNCH_ID`. They are optional
setup-only values, never config fields or general runtime-agent credentials.

### Task 1: Closed Completion Contract And Shared Final Validation

**Files:** Create `flowgency/web/setup_completion.py` and
`flowgency/web/validation.py`; modify [cli.py](../../../flowgency/cli.py);
test [test_setup_flow.py](../../../tests/test_setup_flow.py) and
[test_cli_contract.py](../../../tests/test_cli_contract.py).

**Interfaces:** Produce the types above and
`collect_validation_issues(services: FlowgencyServices, snapshot: ConfigSnapshot) -> tuple[ValidationIssue, ...]`.
The collector consumes existing prompt issues, blueprint-library validation and
workflow-reference validation. Preserve their ordering and duplicate key
`(code, field, message)`.

- [ ] **Step 1: Add failing schema and collector regressions.** Include this concrete rejection test, then parameterize false assertions, string/integer booleans, extra fields, malformed launch/revision values and unacknowledged scheduler limitations:

```python
def test_setup_completion_rejects_unacknowledged_scheduler_failure():
    with pytest.raises(ValueError):
        SetupCompletionCommand(
            launch_id="a" * 32,
            revision="b" * 64,
            scheduler_result="failed",
            all_questions_answered=True,
            summary_delivered=True,
            limitations_acknowledged=False,
        )
```

- [ ] **Step 2: Run the focused red check.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_setup_flow.py tests/test_cli_contract.py -q -k "completion or validate"
```

Expected: the new completion contract is missing; existing CLI tests remain a
separate baseline, not failures to be weakened.

- [ ] **Step 3: Implement the strict models and extract only the issue collector.** Use field constraints and a model validator:

```python
@model_validator(mode="after")
def require_finished_work(self):
    if self.all_questions_answered is not True or self.summary_delivered is not True:
        raise ValueError("Setup work is not complete")
    if self.scheduler_result in {"failed", "unknown"} and not self.limitations_acknowledged:
        raise ValueError("Scheduler limitation requires acknowledgement")
    return self
```

Move the existing `cmd_validate` collection loop unchanged into the shared
collector. `cmd_validate` still builds services, loads a snapshot, raises the
existing `CliFailure` for issues and preserves text/JSON output. Do not call a
CLI printing function from a route or introduce another validator.

- [ ] **Step 4: Rerun the same focused command and the existing workflow-validation tests.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_setup_flow.py tests/test_cli_contract.py tests/test_workflow_setup.py -q
```

Expected: strict inputs fail deterministically, valid inputs pass, and existing
CLI/setup validation remains unchanged.

- [ ] **Step 5: Review this task, stage only its files, and commit.**

```text
feat(setup): define explicit completion contract
```

### Task 2: Launch-Bound Completion State In The Session Manager

**Files:** Modify [setup_sessions.py](../../../flowgency/web/setup_sessions.py);
test [test_setup_sessions.py](../../../tests/test_setup_sessions.py) and reuse
[_connected_setup_helpers.py](../../../tests/_connected_setup_helpers.py).

**Interfaces:** Consume Task 1 types. Produce:

```python
def require_completion_token(self, token: str) -> str: ...

async def prepare_completion(
    self, owner: str, integration_name: str, data_root: Path,
    config_path: Path, origin: str,
) -> SetupCompletionLaunch: ...

async def acknowledge_completion(
    self, token: str, command: SetupCompletionCommand,
    validated_revision: str,
) -> SetupCompletionDecision: ...

def completion_decision(
    self, owner: str, current_revision: str | None, ready: bool,
) -> SetupCompletionDecision: ...
```

An active completion attempt is independent of whether a connected PTY exists.
It therefore also covers a Flowgency-launched external terminal. Only the
connected manager can attest to process shutdown; external exit remains
unobserved and uses the explicit completion fallback.

- [ ] **Step 1: Add an asynchronous fake-process regression.** Prepare a launch with a test origin, start the fake PTY through the existing `start` API, and prove that ready configuration without acknowledgement still blocks:

```python
decision = manager.completion_decision("owner", "b" * 64, True)
assert decision.phase == "pending"
assert decision.redirect_allowed is False
assert fake.running is True
```

Also cover wrong owner/launch/token/revision, idempotent duplicate completion,
replacement attempts, Stop, natural exit without acknowledgement and stale
completion after cancellation.

- [ ] **Step 2: Run the red lifecycle slice.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_setup_sessions.py -q -k completion
```

Expected: missing preparation/decision methods, not an unrelated real-runtime
timeout.

- [ ] **Step 3: Implement the active attempt under existing locks.** Mint a 256-bit capability; keep its value private and use constant-time comparison. Reuse a matching same-owner active attempt rather than rotating its credential on reattach. Reject another owner or a different selection while the existing launch owns the slot. Bind the connected lifecycle generation to the prepared launch identity.

Validation happens outside blocking PTY locks. On acknowledgement, recheck the
active launch and validated revision under `_state_lock` before committing
state. A cancelled/replaced attempt cannot regain completion. Never add token
fields to `SetupSessionSnapshot` or overwrite process state with completion.

Use the following decision order, including external attempts:

```python
if attempt.cancelled or attempt.unexpected_failure:
    return blocked_decision(attempt)
if not ready or current_revision != attempt.acknowledged_revision:
    return pending_decision(attempt)
if not attempt.acknowledged:
    return pending_decision(attempt)
if attempt.exit_capability_verified:
    return decision_from_confirmed_exit(attempt)
return completed_fallback_decision(attempt)
```

Implement the four decision helpers in the same module. The verified path
requires natural successful exit and confirmed whole-tree stop evidence; the
fallback applies only to a healthy continuing launch. Neither Stop nor a
non-zero/unconfirmed exit is a successful fallback.

- [ ] **Step 4: Run all lifecycle tests.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_setup_sessions.py -q
```

Expected: existing spawn cancellation, single-writer, limits, exclusion and
unconfirmed-cleanup behavior remains intact.

- [ ] **Step 5: Review and commit only this slice.**

```text
feat(setup): bind completion to launch lifecycle
```

### Task 3: Bounded Callback And Secret-Free Launch Propagation

**Files:** Modify [setup_terminal.py](../../../flowgency/web/routes/setup_terminal.py),
[setup_security.py](../../../flowgency/web/setup_security.py),
[admin_teams.py](../../../flowgency/web/routes/admin_teams.py),
[models.py](../../../flowgency/integrations/models.py), and the registered
integration launchers that pass setup environment values into the existing
[terminal launcher](../../../flowgency/integrations/interactive.py).
Test [test_server.py](../../../tests/test_server.py),
[test_setup_security.py](../../../tests/test_setup_security.py) and
[test_interactive_setup.py](../../../tests/test_interactive_setup.py).

**Interfaces:** `InteractiveSetupRequest` gains optional
`environment: Mapping[str, str] = field(default_factory=dict, repr=False)`.
Only the three named completion variables may be overlaid. Produce
`POST /setup/session/completion` accepting Task 1's command with a bearer
capability, and returning a sanitized `SetupCompletionDecision`.
Redact secret-bearing environment values from `RuntimeLaunch` representations
as well as `InteractiveSetupRequest` representations. Add a launch test that
checks `repr`, exceptions and fallback formatting for the sentinel capability;
redaction must not change the environment actually supplied to the child.
Define `require_completion_peer_and_bearer(request, manager) -> str`,
`read_completion_command(request, *, max_bytes: int) -> SetupCompletionCommand`
as an async bounded reader, and
`validate_current_completion(config_path: Path, expected_revision: str) -> str`
in this task. The validation helper returns only the successfully rechecked
revision and raises sanitized domain errors for the route to translate.

- [ ] **Step 1: Add failing route and launch tests.** Use `_start_connected_session`, `_local_client` and existing origin/CSRF helpers. Prove the callback rejects a missing/invalid token, non-loopback peer, foreign Host/Origin, wrong launch, stale revision, malformed/extra fields, oversized declared/chunked bodies and an unexpected failed session. Assert no received secret appears in any response or captured fallback command.

```python
response = client.post(
    "/setup/session/completion",
    json={"launch_id": "a" * 32},
    headers={"Authorization": "Bearer invalid-test-capability"},
)
assert response.status_code in {401, 403}
assert "invalid-test-capability" not in response.text
```

- [ ] **Step 2: Run the red security/launch slice.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_server.py tests/test_setup_security.py tests/test_interactive_setup.py -q -k "completion or environment"
```

- [ ] **Step 3: Implement the transport boundary and launch overlay.** Authenticate the bearer capability and local peer before expensive body processing or validation. Allow only the prepared literal-loopback origin/authority, preserving HTTPS verification when applicable; localhost browser launches normalize to the corresponding loopback address, not an arbitrary DNS target. Reject a foreign Origin; the internal client sends no browser cookie and no Origin.

Cap both declared and streamed bodies at 4096 bytes. Validate the closed schema.
Use 401 for invalid credentials, 403 for origin/peer denial, 413 for excess body,
422 for schema rejection, 409 for stale launch/revision and sanitized 503 for
validation/service unavailability. Serialize acknowledgement validation per
attempt and return cached idempotent receipts for identical requests; do not
start overlapping validation operations.
Every rejection has a fixed safe code/message. Do not include Pydantic input
values, bearer headers, raw submitted fields or exception traces in its error
body. Do not leave the manual schema reader's exceptions to an unsanitized
default handler.

Load the authoritative revision, build current services, run Task 1's shared
collector and existing readiness checks, and re-read the revision before
calling `acknowledge_completion`. Never initialize or repair missing sources as
part of this check.

```python
@router.post("/setup/session/completion")
async def setup_completion_callback(request: Request, services=Depends(get_services)):
    manager = request.app.state.setup_sessions
    token = require_completion_peer_and_bearer(request, manager)
    launch_id = manager.require_completion_token(token)
    command = await read_completion_command(request, max_bytes=4096)
    if command.launch_id != launch_id:
        raise HTTPException(status_code=409, detail="Setup launch changed")
    revision = await run_in_threadpool(
        validate_current_completion, services.config_path, command.revision,
    )
    decision = await manager.acknowledge_completion(token, command, revision)
    return JSONResponse({"ok": True, "completion": asdict(decision)})
```

Prepare completion before building the actual managed launch. Pass named
variables through `RuntimeLaunch.env` or the existing external launcher's
explicit environment argument, never through argv, prompt text, a sidecar or
the fallback-command formatter. Preserve per-integration permission behavior.
On confirmed connected-launch failure, bind the managed external fallback to
the same attempt only after the existing lifecycle exclusion permits it.

If an external launcher cannot carry the environment, or a displayed fallback
command is run manually outside that environment, report that automatic
completion is unavailable. Keep readiness visible and do not auto-redirect;
deliberate dashboard navigation is not completion. Do not put a capability in
a copyable command to recover this case.

- [ ] **Step 4: Rerun the complete focused boundary checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_server.py tests/test_setup_security.py tests/test_interactive_setup.py -q
```

- [ ] **Step 5: Review the authorization, environment and fallback changes, then commit.**

```text
feat(setup): add scoped completion callback
```

### Task 4: Acknowledgement Command And Final Skill Ordering

**Files:** Modify [cli.py](../../../flowgency/cli.py),
the new `flowgency/web/setup_completion.py`,
[setup_flow.py](../../../flowgency/web/setup_flow.py), and the package-owned
skill. Test [test_cli_contract.py](../../../tests/test_cli_contract.py),
[test_setup_flow.py](../../../tests/test_setup_flow.py),
[test_flowgency_setup_skill.py](../../../tests/test_flowgency_setup_skill.py)
and [test_setup_assets.py](../../../tests/test_setup_assets.py).

**Interfaces:** Produce `flowgency setup finish` with required revision,
scheduler-result and both completion-assertion flags; optional
`--acknowledged-limitations` is required for failed/unknown outcomes. The command
reads the capability only from the three named environment variables and sends
the closed command via `submit_completion(command, environment) -> dict`.

- [ ] **Step 1: Add a failing CLI contract test with a captured in-memory client.**

```python
def test_setup_finish_sends_launch_bound_completion(cli_runner, monkeypatch):
    captured_commands = []
    token = "completion-test-capability"
    monkeypatch.setenv("FLOWGENCY_SETUP_ORIGIN", "http://127.0.0.1:8500")
    monkeypatch.setenv("FLOWGENCY_SETUP_TOKEN", token)
    monkeypatch.setenv("FLOWGENCY_SETUP_LAUNCH_ID", "a" * 32)

    def submit(command, environment):
        captured_commands.append(command)
        assert environment["FLOWGENCY_SETUP_TOKEN"] == token
        return {"ok": True, "completion": {"phase": "complete"}}

    monkeypatch.setattr(cli, "submit_completion", submit)
    result = cli_runner(
        "setup", "finish", "--revision", "b" * 64,
        "--scheduler-result", "declined", "--all-questions-answered",
        "--summary-delivered",
    )
    assert result.exit_code == 0
    assert captured_commands[0].scheduler_result == "declined"
    assert captured_commands[0].launch_id == "a" * 32
    assert token not in result.stdout + result.stderr
```

Import `submit_completion` into `flowgency.cli` for this handler/test boundary.
Also test absent context, refused callback, invalid
booleans/outcome flags, timeout, redirect and oversized response.

- [ ] **Step 2: Run the focused red command.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_cli_contract.py tests/test_setup_flow.py tests/test_flowgency_setup_skill.py -q -k "completion or finish"
```

- [ ] **Step 3: Add the parser/handler and bounded standard-library client.** The client accepts only the pinned loopback origin, disables proxies and redirects, uses a 5-second timeout, bounds response reads at 16 KiB and reports safe errors without tokens, server stack traces or raw response bodies. The handler uses existing `CliFailure`/exit-code conventions and performs no config write.

Add this ordering to guided setup instructions:

```text
Resolve every required question and settle the approved operations.
Validate the saved canonical revision and approved source.
Report scheduler status; obtain explicit acknowledgement of failed/unknown status.
Deliver the final summary without another unresolved setup question.
Run flowgency setup finish for that revision and observed scheduler result.
Do not declare browser completion if the acknowledgement fails.
```

The concrete successful invocation is:

```text
flowgency setup finish --revision <64-character-saved-revision> --scheduler-result declined --all-questions-answered --summary-delivered
```

The revision comes from `ConfigStore(config_path).load().revision`, not a guessed
file timestamp. The angled text above describes a value, not a literal command
argument. Manual skill use without Flowgency launch context omits this command
and retains its existing final summary behavior. Do not add another atomic
configuration write or automatically repair missing workflow/blueprint source.

- [ ] **Step 4: Run CLI, skill, validation and wheel checks.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_cli_contract.py tests/test_setup_flow.py tests/test_flowgency_setup_skill.py tests/test_setup_assets.py tests/test_workflow_setup.py -q
```

- [ ] **Step 5: Review the command, packaging and skill ordering, then commit.**

```text
feat(setup): acknowledge finished guided sessions
```

### Task 5: Gate Every Automatic Setup Navigation Path

**Files:** Modify [admin_teams.py](../../../flowgency/web/routes/admin_teams.py),
[setup_terminal.py](../../../flowgency/web/routes/setup_terminal.py),
[setup.html](../../../flowgency/templates/setup.html), and the owner-only setup
indicator in [app.py](../../../flowgency/app.py) where necessary.
Test [test_server.py](../../../tests/test_server.py) and
[test_workflow_setup.py](../../../tests/test_workflow_setup.py).

**Interfaces:** `GET /setup/status` preserves readiness `state` and adds
`completion: {launch_id, phase, message}` when this browser owns an active
attempt. It includes `redirect` only when the current decision permits it.
`GET /setup/session/state` gains the same non-secret completion presentation.
Terminal inspection uses `GET /setup/session?view=inspection`; never change
browser intent based only on initially rendered readiness.
Define the async shared route helper
`setup_navigation_decision(request, services) -> SetupCompletionDecision | None`
in this task. `None` means no owner-bound tracked attempt; otherwise it performs
current validation before requesting Task 2's decision.

- [ ] **Step 1: Replace the premature success expectation in `test_setup_session_view_and_status_stay_ready_while_session_runs`.**

```python
status = client.get("/setup/status").json()
assert status["state"] == "ready"
assert status["completion"]["phase"] == "pending"
assert "redirect" not in status
assert client.get("/setup", follow_redirects=False).headers["location"] == "/setup/session"
assert client.get("/setup/session").status_code == 200
assert process.running is True
```

Add complete/fallback, stopped, failed, wrong-browser, no-session existing-config
and inspection-intent cases. Stop is deliberate navigation, not a completion
acknowledgement; preserve the existing confirmed Stop response.

- [ ] **Step 2: Run the focused red navigation slice.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_server.py tests/test_workflow_setup.py -q -k "setup and (status or session or ready or completion)"
```

- [ ] **Step 3: Use one decision for status, entry, relaunch and session routes.** Check owner/pending attempt before a ready entry route redirects. Revalidate the acknowledged revision and sources before allowing navigation; do not rebuild services in a way that discards the attempt. Preserve operational dashboard startup without a tracked unfinished attempt.

In the browser, honor server `redirect` only for the waiting view. Preserve
inspection intent across reloads. Reject responses from an older launch or
poll generation and prevent concurrent status reads; network failure retains
the terminal and retries. Do not replace xterm nodes, recreate its socket or
send Stop when following a completion redirect.

```python
payload = {"state": status.state}
decision = await setup_navigation_decision(request, services)
if decision is not None:
    payload["completion"] = asdict(decision)
if status.state == "ready" and (decision is None or decision.redirect_allowed):
    payload["redirect"] = "/"
```

- [ ] **Step 4: Rerun the same command, then all route/validation setup tests.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_server.py tests/test_setup_flow.py tests/test_workflow_setup.py -q
```

- [ ] **Step 5: Review all navigation entry points, then commit.**

```text
fix(setup): wait for explicit completion to navigate
```

### Task 6: Prove Graceful Exit Capability Or Keep The Safe Fallback

**Files:** Create `tools/probe_setup_exit.py`; modify
[setup_sessions.py](../../../flowgency/web/setup_sessions.py) and
[copilot.py](../../../flowgency/integrations/flowgency/copilot.py) only if a
supported boundary is proven. Test [test_setup_sessions.py](../../../tests/test_setup_sessions.py),
[test_connected_process.py](../../../tests/test_connected_process.py) and reuse
[_runtime_probe_helpers.py](../../../tests/_runtime_probe_helpers.py).

**Interfaces:** Define `SetupExitCapability(supported: bool, cli_version: str, reason: str)`
in the completion contract. An integration can claim support only for the
measured version and supported boundary. The manager's
`request_completion_exit(launch_id: str) -> bool` is idempotent and cannot bypass
pending work or input ownership. Unsupported capability returns `False` without
writing PTY input.

- [ ] **Step 1: Add deterministic fake-boundary tests.** A fake adapter reports busy, prompting, safe and unsupported states. Prove only the verified safe state writes the exit command once; busy/prompting/unsupported states never write it:

```python
assert await manager.request_completion_exit(grant.launch_id) is False
assert fake.writes == []
assert fake.running is True
```

Then test that the verified path remains non-navigable until natural exit code
zero and confirmed whole-tree shutdown. No timeout calls `stop()` to manufacture
success; unexpected failure remains attention-required.

- [ ] **Step 2: Run the red capability tests.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_setup_sessions.py -q -k "completion_exit or completion"
```

- [ ] **Step 3: Implement a disposable versioned probe and run it once.** Use a temporary source/data root and the existing connected-process containment and cleanup helpers. Do not read or resume the user's live session, write real config, install a scheduler, grant broad tool permission, or send answers to native prompts. A supported programmatic completed/idle boundary, not screen text or silence, is required.

```powershell
.venv\Scripts\python.exe tools/probe_setup_exit.py --evidence-dir .superpowers/evidence/setup-completion-20261006/exit
```

The receipt contains CLI version, supported-boundary identifier, attempted exit
count, exit code, confirmed cleanup and `supported`/`unsupported` result, with
no credentials. If the installed CLI exposes no such boundary, stop the probe
there, record `unsupported`, and implement the approved acknowledgement-only
fallback. Documented `/exit` alone is not a successful capability measurement.

- [ ] **Step 4: Implement only the measured policy and rerun lifecycle tests.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_setup_sessions.py tests/test_connected_process.py -q
```

Expected: safe supported exits are awaited; unsupported launches remain healthy
and accessible with an explicit-completion redirect. No forced termination,
permission widening or guessed boundary is introduced.

- [ ] **Step 5: Review the receipt and the capability decision, then commit.**

```text
feat(setup): apply verified completion exit policy
```

### Task 7: Browser Regression, Documentation And Complete Gates

**Files:** Modify [tests/ui/setup.spec.ts](../../../tests/ui/setup.spec.ts),
[tests/ui/server.py](../../../tests/ui/server.py),
[test_setup_assets.py](../../../tests/test_setup_assets.py) and
[kb/getting-started.md](../../../kb/getting-started.md).

**Interfaces:** The UI fixture gains `POST /__ui/setup/session/complete`, which
uses the fixture owner's prepared attempt and real completion validator without
exposing its token to the browser. Its JSON body accepts only the scheduler
outcome/limitation flags for controlled test cases. `/__ui/setup/ready` remains
readiness-only; emitting EOF remains process-lifecycle-only.

- [ ] **Step 1: Add a browser regression with the existing connected fixture.**

```typescript
test('ready configuration does not finish an unanswered setup', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);
  await request.post('/__ui/setup/ready');
  await expect.poll(() => page.evaluate(() =>
    fetch('/setup/status').then(response => response.json())
  )).toMatchObject({ state: 'ready', completion: { phase: 'pending' } });
  await page.reload();
  await expect(page).toHaveURL(/\/setup\/session$/);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
});
```

Follow it with actual completion, scheduler refusal, acknowledged failure,
unknown-status refusal, Stop/crash, stale-revision, delayed-poll and inspection
cases. Keep a reference to the terminal element and count WebSockets before
completion to prove continuity. A selector that resolves a replacement node is
not sufficient.

- [ ] **Step 2: Run the focused red browser slice.**

```powershell
npm run test:ui -- tests/ui/setup.spec.ts
```

- [ ] **Step 3: Implement fixture hooks and update the short user-facing completion explanation.** Use the real handshake/gate, not a test-only unconditional success flag. Preserve reset isolation, existing font metrics and screenshots. Explain that scheduler enablement is separate from observed scheduler installation and that the terminal may remain open only on the explicit fallback.

- [ ] **Step 4: Run focused and complete gates sequentially.**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_setup_sessions.py tests/test_setup_security.py tests/test_interactive_setup.py tests/test_setup_flow.py tests/test_flowgency_setup_skill.py tests/test_setup_assets.py tests/test_workflow_setup.py tests/test_server.py -q
npm run test:ui -- tests/ui/setup.spec.ts
.venv\Scripts\python.exe -m pytest tests/ -q
npm run test:ui
git diff --check
```

Expected: all required gates pass without weakening old tests, changing unrelated
snapshots or ignoring a real-runtime failure. Preserve receipts and report any
remaining blocker precisely.

- [ ] **Step 5: Review and commit the regression/docs slice.**

```text
test(setup): cover completion and terminal continuity
```

## Whole-Branch Review And Integration

- [ ] Review the complete branch against every approved spec requirement and the exact staged scope. Include authorization/replay, source-validation immutability, revision drift, external/manual fallback, native-exit evidence and process-tree exclusion.
- [ ] If `master` advanced, rebase this feature onto `master`, rerun both full suites sequentially in the feature worktree, and repeat the branch review.
- [ ] Preserve dirty `master` changes with a named stash that includes unrelated untracked user files; do not fold them into this feature or stage ignored runtime data. Fast-forward `master` only, and restore the stash without discarding conflicts.
- [ ] Run the complete Python and browser suites sequentially on fast-forwarded `master`, using its validated environment and the same measured browser pair.
- [ ] Push `master` and `feature/setup-completion` to `origin` after green gates; integration/publication is pre-authorized, not a new merge-choice question.
- [ ] Remove only this owned feature worktree with `git worktree remove .worktrees/setup-done`, verify both deregistration and directory removal, and prune. Keep the feature branch. Do not remove `.worktrees/live-app`.

The app-wide refresh branch must consume the integrated completion gate before
its final integration, but the setup workstream does not depend on app-wide
refresh implementation.