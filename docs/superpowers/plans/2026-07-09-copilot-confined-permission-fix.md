# Copilot Confined-Mode Permission Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three enumerated `--allow-tool` grants in Copilot confined-mode execution with a single `--allow-all-tools` pre-grant to eliminate intermittent permission-round-trip failures, then validate through a real agent dispatch session.

**Architecture:** One-line flag swap in the confined branch of `CopilotIntegration.run()` in `agency/integrations/agency/copilot.py`, keeping `cwd=sandbox_root` path confinement intact (no `--allow-all-paths`). Update the corresponding behavioral test assertions. Validate via the real dispatch code path (`agency.dispatch.run.run_agent_prompt`) against a live Copilot agent in the `msvc-digest` sandbox.

**Tech Stack:** Python 3.11+, pytest, GitHub Copilot CLI (`copilot -p --autopilot --experimental`), FastAPI (unaffected).

> **ACTUAL OUTCOME (2026-07-09, supersedes the flag-only plan below).** The
> real-session gate (Task 2) disproved the flag-only hypothesis. Root cause was
> **structural**, in two stacked parts (see the updated spec “Root cause”):
> 1. On Windows `copilot` resolves to a `.bat` wrapper → `powershell` →
>    `copilot.ps1` → `copilot.EXE`; under `subprocess.run` the real `.exe` gets
>    a console and behaves interactively, failing closed headless.
> 2. `--allow-all-paths` was missing, so shell/read tools denied the routine's
>    legitimate out-of-sandbox reads (`~/.agency-cowork/...`).
>
> **Shipped fix:** new `_resolve_real_cmd()` bypasses the wrapper to invoke
> `copilot.EXE` directly; the shared `subprocess.run` adds
> `stdin=DEVNULL` + `creationflags=CREATE_NO_WINDOW`; the confined branch adds
> `--allow-all-paths` (production parity, user-approved). Confined flags shipped:
> `--no-custom-instructions --no-ask-user --allow-all-tools --allow-all-paths
> --no-color`, `cwd=sandbox_root`. Validated: **2/2 full runs clean, 0 denials,
> shell evidence present** (was 24–62 denials before). The “never add
> --allow-all-paths” constraint below is **retracted**.

## Global Constraints

- ~~Path confinement is achieved ONLY via `cwd=sandbox_root`. NEVER add
  `--allow-all-paths`.~~ **RETRACTED** — real routines need out-of-sandbox reads;
  `--allow-all-paths` is shipped (production parity). `cwd=sandbox_root` still
  anchors relative writes.
- Tests assert argv + cwd + kwargs via monkeypatched `subprocess.run` — they
  cannot validate CLI runtime permission behavior. Runtime behavior is validated
  only by the real-session task.
- Reference spec: `docs/superpowers/specs/2026-07-09-copilot-confined-permission-fix-design.md`.

---

### Task 1: Swap confined-mode flags to `--allow-all-tools` (TDD)

**Files:**
- Modify: `agency/integrations/agency/copilot.py` (confined branch of `run()`, ~lines 40-56)
- Test: `tests/test_integration_sidecar.py::TestCopilotIntegration::test_copilot_run_set_sandbox_runs_from_sandbox_root` (~lines 447-481)

**Interfaces:**
- Consumes: `CopilotIntegration().run(agent_dir, prompt_file, timeout, *, sandbox_root=None) -> RunResult` (existing signature, unchanged).
- Produces: confined-mode argv = `[cmd, "-p", prompt_text, "--autopilot", "--allow-all-tools", "--experimental"]` with `cwd == str(sandbox_root)`.

- [ ] **Step 1: Update the failing test assertions**

In `tests/test_integration_sidecar.py`, replace the assertion block at the end of `test_copilot_run_set_sandbox_runs_from_sandbox_root`:

```python
        args = captured["args"]
        # Confined mode runs FROM the sandbox root so all paths are under cwd
        assert captured["cwd"] == str(root)
        assert "--add-dir" not in args
        assert "--allow-all-paths" not in args
        assert "--autopilot" in args
        assert "--allow-all-tools" in args
        assert "--allow-tool=read" not in args
        assert "--allow-tool=write" not in args
        assert "--allow-tool=shell" not in args
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration_sidecar.py::TestCopilotIntegration::test_copilot_run_set_sandbox_runs_from_sandbox_root -v`
Expected: FAIL — `assert "--allow-all-tools" in args` fails because the current code emits `--allow-tool=read/write/shell`.

- [ ] **Step 3: Swap the confined-branch flags**

In `agency/integrations/agency/copilot.py`, replace the confined branch (the `if sandbox_root is not None:` block) with:

```python
        if sandbox_root is not None:
            # Confined mode: run FROM the sandbox root. Copilot reliably grants
            # native file access to paths under the working directory, so
            # launching with cwd=sandbox_root puts the whole tree in scope
            # (this mirrors the proven task-scheduler launch). With cwd at the
            # root, --autopilot has nothing outside-scope to approve.
            #
            # Tools are pre-authorized with a single --allow-all-tools rather
            # than enumerated --allow-tool grants: under --autopilot -p the
            # enumerated shell/write grants still trigger a permission
            # round-trip that fails closed once the permission channel degrades
            # mid-session (github/copilot-cli#2971, #3699). The blanket
            # pre-grant removes that round-trip. It is approval-only —
            # --allow-all-paths is deliberately omitted so native file tools
            # stay scoped to the sandbox tree via cwd.
            work_dir = str(sandbox_root)
            cmd_args = [
                cmd, "-p", prompt_text, "--autopilot",
                "--allow-all-tools",
                "--experimental",
            ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_integration_sidecar.py::TestCopilotIntegration::test_copilot_run_set_sandbox_runs_from_sandbox_root -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `python -m pytest tests/ -q`
Expected: all tests pass (the unrestricted-mode test `test_copilot_run_unset_sandbox_uses_allow_all_paths` still passes unchanged).

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/agency/copilot.py tests/test_integration_sidecar.py
git commit -m "fix: use --allow-all-tools for Copilot confined mode to avoid permission round-trip"
```

---

### Task 2: Real-session validation via the dispatch code path

**Files:**
- No source changes. This task runs the real dispatch path and inspects logs.
- Reference: `agency/dispatch/run.py::run_agent_prompt` (~line 157)

**Interfaces:**
- Consumes: `agency.dispatch.run.run_agent_prompt(group_path, agent_name, prompt_filename, timeout, log_dir, agent_config, agent_dir=None, *, sandbox_root=None) -> None` — reads `group_path/shared/prompts/{prompt_filename}`, resolves the integration from `agent_config["integration"]`, calls `integration.run(..., sandbox_root=sandbox_root)`, and writes `{agent_name}-{stem}-{ts}.out` / `.err` into `log_dir`.
- Produces: `.out`/`.err` log files whose contents are the pass/fail evidence.

- [ ] **Step 1: Confirm the environment is ready**

Run:
```powershell
Get-Command copilot -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
Test-Path C:\Projects\msvc-digest\agents\sentinel
Test-Path C:\Projects\msvc-digest\agents\shared\prompts\sentinel-routine.md
```
Expected: a copilot path prints, both `Test-Path` return `True`. If any fails, STOP and report — validation cannot run.

- [ ] **Step 2: Run the real agent session through the dispatch helper**

Run (from `C:\Projects\christag-agency`):
```powershell
python -c "import tempfile, pathlib; from agency.dispatch.run import run_agent_prompt; ld = pathlib.Path(tempfile.mkdtemp(prefix='copilot-validate-')); print('LOG_DIR', ld); run_agent_prompt(pathlib.Path(r'C:\Projects\msvc-digest\agents'), 'sentinel', 'sentinel-routine.md', 900, ld, {'integration': 'copilot'}, sandbox_root=pathlib.Path(r'C:\Projects\msvc-digest')); print('done')"
```
Expected: prints `LOG_DIR <path>`, then `done`. Note the printed `LOG_DIR` path. A dispatch log line reports `DONE: sentinel` (success), or `ERROR`/`TIMEOUT` (failure).

- [ ] **Step 3: Inspect the run output for permission failures**

Run (substitute the `LOG_DIR` from Step 2):
```powershell
$ld = "<LOG_DIR from step 2>"
Get-ChildItem $ld
Get-Content (Join-Path $ld '*.err') -ErrorAction SilentlyContinue
Select-String -Path (Join-Path $ld '*.out'),(Join-Path $ld '*.err') -Pattern 'could not request permission from user','Permission denied' -SimpleMatch
```
Expected (PASS): the `Select-String` finds NO matches. `.err` is empty or contains only benign warnings.

- [ ] **Step 4: Confirm shell + write actually happened**

Run (substitute the `LOG_DIR`):
```powershell
$ld = "<LOG_DIR from step 2>"
Get-Content (Join-Path $ld '*.out')
Get-ChildItem C:\Projects\msvc-digest\agents\shared\observations -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3 Name, LastWriteTime
```
Expected (PASS): `.out` shows evidence of shell command execution (e.g. scheduled-task / security-audit output) AND a recently-written observation/memory file appears under the sandbox. Together these prove shell+write succeeded in-tree.

- [ ] **Step 5: Record the outcome**

Evaluate against the spec success criteria (ALL required for PASS):
1. Exit code 0 (dispatch logged `DONE`, not `ERROR`/`TIMEOUT`).
2. No `could not request permission from user` / `Permission denied` in `.out`/`.err`.
3. Positive evidence at least one shell command ran.
4. Positive evidence at least one file write landed under the sandbox.

- If PASS: proceed to Task 3.
- If FAIL: STOP. Do NOT claim the fix works. Report the exact `.out`/`.err` excerpts. The documented fallback is Option B (detect-and-retry) from the spec — raise it with the user before implementing.

> Honest limit: the bug is intermittent and stateful; a single green run proves the fix does not break confined execution and that shell+write succeed through the real path, but does not statistically prove the intermittent failure is eradicated. Report exactly what the run demonstrates.

---

### Task 3: Update repo memory with the committed fix and validation outcome

**Files:**
- Modify: `/memories/repo/copilot-integration.md` (memory tool)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the memory note**

Using the memory tool, update `/memories/repo/copilot-integration.md`:
- Correct the stale claim that the confined-mode fix was applied — note it is now ACTUALLY committed (Task 1 commit hash) as `--allow-all-tools` replacing the three enumerated grants.
- Record the confined-mode flags are now `--autopilot --allow-all-tools --experimental` with `cwd=sandbox_root`, no `--allow-all-paths`.
- Record the real-session validation outcome from Task 2 (PASS/FAIL, what it demonstrated, date).

- [ ] **Step 2: Verify the note reads correctly**

Using the memory tool, view `/memories/repo/copilot-integration.md` and confirm the confined-mode description matches the shipped code and no contradictory stale lines remain.

---

## Self-Review

**Spec coverage:**
- Fix (Option A flag swap, comment update, path confinement held) → Task 1 Steps 3 (comment + flags), Global Constraints (no `--allow-all-paths`). ✓
- Test updates (swap three grant assertions for `--allow-all-tools`, keep path-confinement assertions) → Task 1 Steps 1-5. ✓
- Real-session validation via `run_agent_prompt` with the exact arg table + `sentinel-routine.md` → Task 2. ✓
- Success criteria (exit 0, no permission strings, shell evidence, write evidence) → Task 2 Steps 3-5. ✓
- Honest limits → Task 2 Step 5 note. ✓
- Fallback (Option B) → Task 2 Step 5 FAIL branch. ✓
- Memory update → Task 3. ✓

**Placeholder scan:** No TBD/TODO; all code and commands are literal. The `<LOG_DIR from step 2>` substitution is an intentional runtime value, not a placeholder omission. ✓

**Type consistency:** `run()` signature and `run_agent_prompt()` signature match the current source exactly; confined argv naming (`--allow-all-tools`) is consistent across Task 1 and the assertions. ✓
