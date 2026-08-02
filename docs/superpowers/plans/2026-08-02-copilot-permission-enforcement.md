# Copilot Permission Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Copilot actually enforce the permission model through its sandbox, and stop the other eight integrations disarming themselves.

**Architecture:** Capability stops being a class constant and becomes something an integration derives from the CLI it finds. Copilot writes a per-job `settings.json` into a relocated `COPILOT_HOME`, mapping permission rules onto `readonlyPaths` and `readwritePaths` — a second gate beside the existing global tool allowlist. What it cannot enforce is recorded against the job rather than assumed.

**Tech Stack:** Python 3.13, FastAPI/Jinja2, pytest. No new third-party dependencies.

## Global Constraints

- Run tests from the worktree root: `python -m pytest tests/ -q`. There is **no** `.venv`; the interpreter is plain `python` (3.13.14). The suite takes about four minutes.
- Baseline at the start of this plan: **1868 passed, 6 skipped, 0 failed**.
- Do **not** run `tests/test_runtime_projectors_live.py` on its own. Its `real_runtime` tests launch real CLIs and consume AI credits. The full-suite run handles them; they skip when no CLI is installed.
- **Unlike the previous phase, the suite stays green throughout.** Every task ends green. A red suite is a defect, not a stage.
- The permission model itself is settled. Rules, merge semantics, negotiation, eligibility and the `generated` marker do not change.
- PowerShell has **no heredoc**. One command per line; `git commit -F <file>` for multi-line messages.
- Do not modify `config.yaml`, `config.yaml.lock`, group-state directories or logs.
- All writes into Agency-owned storage use `atomic_write_text` / `atomic_write_bytes` from `agency/fs/atomic.py`.

## Established facts

Verified by exploration against this branch; do not re-derive.

- `runtime_capabilities` is a plain class attribute: `BaseIntegration` line 89, and one per integration (`copilot.py:63`, the other eight around lines 21-28). No integration declares any `path_scopable_tools`.
- It is read by `validate_runtime_policy` (`agency/integrations/__init__.py:174`), called from `resolve_effective_policy` (`agency/configuration/effective.py:121`) and `validate_run` (`agency/integrations/__init__.py:219`).
- `tests/test_integration_contract.py:130` compares `{name: integration.runtime_capabilities ...}` against a **static expected dict**. Making capabilities environment-dependent breaks it.
- `tests/test_permission_capabilities.py:105` asserts **no** integration scopes `write`. It must change when Copilot starts doing so.
- Copilot's `run()` is `agency/integrations/agency/copilot.py:426-563`. It already strips `generated` rules before building the tool allowlist, already emits no `--allow-all-tools` when `granted == ()`, and already passes `--experimental`.
- `COPILOT_HOME` is read in exactly one place: `_usage_summary()` at `copilot.py:361`. It is never set. `subprocess.run` passes no `env=`, so the child inherits Agency's environment.
- Copilot writes no CLI settings file today and never probes `copilot --version`.
- `JobRecord.execution_summary` (`agency/jobs/models.py:324`) is Markdown, persisted in the job JSON, rendered at `agency/templates/job_detail.html:118`, and propagated into decision records by `project_decision`.
- `tests/test_copilot_launch_arguments.py` has the `_launch(policy, tmp_path, monkeypatch)` helper returning the argv, and `_granted(args)` extracting `--allow-tool` values.
- `tests/_runtime_probe_helpers.py` provides `installed_ai_cli_runtimes()`, `write_boundary_supported(integration)` (which tests `"restricted" in permission_modes and bool(path_scopable_tools)`), and `assert_protected_state_unchanged()`.

---

### Task 1: Capabilities become derived

**Files:**
- Modify: `agency/integrations/__init__.py`
- Modify: `tests/test_integration_contract.py`
- Test: `tests/test_capability_detection.py`

**Interfaces:**
- Produces: `BaseIntegration.runtime_capabilities` as a **property**, backed by `detect_runtime_capabilities() -> RuntimeCapabilities`, plus `_capability_cache_key() -> str | None`.
- The base implementation returns `declared_runtime_capabilities`, a class attribute holding what each integration statically guarantees. Nothing changes behaviourally for the eight.

- [ ] **Step 1: Write the failing test**

Create `tests/test_capability_detection.py`:

```python
from __future__ import annotations

from agency.integrations import get_integration
from agency.integrations.models import RuntimeCapabilities


def test_capabilities_are_readable_as_a_property():
    caps = get_integration("aider").runtime_capabilities

    assert isinstance(caps, RuntimeCapabilities)
    assert "unrestricted" in caps.permission_modes


def test_detection_is_cached_per_key(monkeypatch):
    integration = get_integration("aider")
    calls = {"n": 0}

    def counting_detect():
        calls["n"] += 1
        return RuntimeCapabilities(permission_modes=frozenset({"unrestricted"}))

    monkeypatch.setattr(integration, "detect_runtime_capabilities", counting_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "alpha")
    integration.invalidate_capability_cache()

    integration.runtime_capabilities
    integration.runtime_capabilities

    assert calls["n"] == 1


def test_cache_invalidates_when_the_key_changes(monkeypatch):
    integration = get_integration("aider")
    calls = {"n": 0}
    key = {"value": "alpha"}

    def counting_detect():
        calls["n"] += 1
        return RuntimeCapabilities(permission_modes=frozenset({"unrestricted"}))

    monkeypatch.setattr(integration, "detect_runtime_capabilities", counting_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: key["value"])
    integration.invalidate_capability_cache()

    integration.runtime_capabilities
    key["value"] = "beta"
    integration.runtime_capabilities

    assert calls["n"] == 2


def test_detection_failure_narrows_rather_than_widens(monkeypatch):
    integration = get_integration("aider")

    def failing_detect():
        raise OSError("cli not found")

    monkeypatch.setattr(integration, "detect_runtime_capabilities", failing_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "boom")
    integration.invalidate_capability_cache()

    caps = integration.runtime_capabilities

    assert caps.path_scopable_tools == frozenset()
    assert caps.permission_modes <= integration.declared_runtime_capabilities.permission_modes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_capability_detection.py -v`
Expected: FAIL with `AttributeError` on `declared_runtime_capabilities`

- [ ] **Step 3: Implement the property**

In `agency/integrations/__init__.py`, rename the class attribute to `declared_runtime_capabilities` and add:

```python
    declared_runtime_capabilities: RuntimeCapabilities = RuntimeCapabilities()

    def _capability_cache_key(self) -> str | None:
        """Identifies the environment the detection result describes."""
        return None

    def detect_runtime_capabilities(self) -> RuntimeCapabilities:
        """What this integration can enforce here. Never wider than declared."""
        return self.declared_runtime_capabilities

    def invalidate_capability_cache(self) -> None:
        self._capability_cache = None

    @property
    def runtime_capabilities(self) -> RuntimeCapabilities:
        key = self._capability_cache_key()
        cached = getattr(self, "_capability_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            detected = self.detect_runtime_capabilities()
        except Exception:
            detected = self.declared_runtime_capabilities
        self._capability_cache = (key, detected)
        return detected
```

Rename the attribute on all nine concrete integrations from `runtime_capabilities` to `declared_runtime_capabilities`. Do not change any value.

- [ ] **Step 4: Fix the contract test**

`tests/test_integration_contract.py:130` compares live capabilities to a static dict. Change it to compare `declared_runtime_capabilities`, so it keeps pinning what each integration promises without depending on the machine. Add a separate assertion that every integration's *detected* capabilities are a subset of its declared ones — that is the invariant that matters.

- [ ] **Step 5: Run the focused tests, then the suite**

Run: `python -m pytest tests/test_capability_detection.py tests/test_integration_contract.py -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: **1868 passed** or more, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/ tests/test_capability_detection.py tests/test_integration_contract.py
git commit -m "feat(integrations): derive capabilities from the environment"
```

---

### Task 2: Copilot renders a sandbox policy

**Files:**
- Create: `agency/integrations/agency/copilot_sandbox.py`
- Test: `tests/test_copilot_sandbox_policy.py`

**Interfaces:**
- Produces: `build_sandbox_settings(policy) -> tuple[dict, tuple[ResolvedPermissionRule, ...]]` returning the `settings.json` mapping and the rules it could **not** express.

This task is pure translation with no process changes — it writes no files and launches nothing. Task 3 wires it in.

- [ ] **Step 1: Write the failing test**

Create `tests/test_copilot_sandbox_policy.py`. Cover, at minimum:

```python
from __future__ import annotations

from pathlib import Path

from agency.integrations.agency.copilot_sandbox import build_sandbox_settings
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def rule(path, tools, generated=False):
    return ResolvedPermissionRule(path=Path(path), tools=tools, generated=generated)


def policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(timeout=60, mode=mode, rules=tuple(rules))


def test_read_only_rule_becomes_a_readonly_path(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",))), launch_dir=tmp_path / "launch"
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") in fs["readonlyPaths"]
    assert str(tmp_path / "ws") not in fs["readwritePaths"]


def test_write_rule_becomes_a_readwrite_path(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read", "write"))), launch_dir=tmp_path / "launch"
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") in fs["readwritePaths"]


def test_generated_zone_rules_are_rendered(tmp_path):
    launch = tmp_path / "launch"
    settings, _ = build_sandbox_settings(
        policy(
            rule(launch / "instructions", ("read",), generated=True),
            rule(launch / ".agency" / "outbox", ("read", "write"), generated=True),
        ),
        launch_dir=launch,
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(launch / "instructions") in fs["readonlyPaths"]
    assert str(launch / ".agency" / "outbox") in fs["readwritePaths"]


def test_omitted_tools_is_writable(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", None)), launch_dir=tmp_path / "launch"
    )

    assert str(tmp_path / "ws") in settings["sandbox"]["userPolicy"]["filesystem"]["readwritePaths"]


def test_empty_tools_grants_neither(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ())), launch_dir=tmp_path / "launch"
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") not in fs["readonlyPaths"]
    assert str(tmp_path / "ws") not in fs["readwritePaths"]


def test_denied_paths_is_never_used(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ())), launch_dir=tmp_path / "launch"
    )

    assert "deniedPaths" not in settings["sandbox"]["userPolicy"]["filesystem"]


def test_bypass_is_disabled_and_cwd_is_not_implicit(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",))), launch_dir=tmp_path / "launch"
    )
    sandbox = settings["sandbox"]

    assert sandbox["enabled"] is True
    assert sandbox["allowBypass"] is False
    assert sandbox["addCurrentWorkingDirectory"] is False


def test_a_pathless_rule_cannot_be_expressed_and_is_reported(tmp_path):
    _, unenforced = build_sandbox_settings(
        policy(ResolvedPermissionRule(path=None, tools=("fetch",))),
        launch_dir=tmp_path / "launch",
    )

    assert len(unenforced) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_copilot_sandbox_policy.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the translation**

Create `agency/integrations/agency/copilot_sandbox.py`. A rule granting `write` (or `tools is None`) becomes a `readwritePaths` entry; a rule granting any tool but not `write` becomes `readonlyPaths`; a rule granting nothing appears in neither, because the filesystem policy is default-deny and omission is the denial. `deniedPaths` is never emitted — Windows ignores it, so the policy grants narrowly instead of carving out.

Generated rules **are** rendered here. That is the point of the task: the sandbox is the gate that can express them, unlike the global tool allowlist.

Return alongside the settings the rules the policy could not express, so Task 5 can report them.

- [ ] **Step 4: Run the focused tests, then the suite**

Run: `python -m pytest tests/test_copilot_sandbox_policy.py -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: **1868 passed** or more, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add agency/integrations/agency/copilot_sandbox.py tests/test_copilot_sandbox_policy.py
git commit -m "feat(copilot): translate permission rules into sandbox policy"
```

---

### Task 3: Per-job `COPILOT_HOME`

**Files:**
- Modify: `agency/integrations/agency/copilot.py`
- Test: `tests/test_copilot_home.py`

**Interfaces:**
- Produces: `CopilotIntegration._prepare_copilot_home(request) -> Path`, and `_usage_summary` gains an explicit `copilot_home` argument.

- [ ] **Step 1: Write the failing test**

Create `tests/test_copilot_home.py` covering:

- the prepared home contains `settings.json` whose content matches `build_sandbox_settings`
- the prepared home contains `config.json` copied from the real home, so authentication survives (skip the assertion if the real home has none, but assert the copy is attempted)
- `subprocess.run` receives an `env` whose `COPILOT_HOME` is the prepared home
- `_usage_summary` reads session state from the **prepared** home, not from `os.environ`
- the home lives under the job's own directory, not a shared location

Follow the `_launch` monkeypatch pattern in `tests/test_copilot_launch_arguments.py` — capture the `subprocess.run` kwargs rather than only the argv.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_copilot_home.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the relocation**

Write `settings.json` and seed `config.json` into a per-job directory, pass `env={**os.environ, "COPILOT_HOME": str(home)}` to `subprocess.run`, and thread the same path into `_usage_summary`.

**The auth seed is the operational trap.** A fresh home has no credentials and the CLI cannot start. Copy `config.json` from the real home (`COPILOT_HOME` if set, else `~/.copilot`). If it is absent, do not fail the job silently — record it as an unenforced-policy condition in Task 5 and fall back to the shared home rather than launching something that cannot authenticate.

- [ ] **Step 4: Fix resume**

`resume_command()` returns an argv the operator copies. A session created under a relocated home is invisible without it. Make the resume command carry the home — either as an environment prefix in the displayed command or by returning the home alongside it for the template to render. Choose one, and update the job-detail template accordingly.

- [ ] **Step 5: Run the focused tests, then the suite**

Run: `python -m pytest tests/test_copilot_home.py tests/test_copilot_launch_arguments.py -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: **1868 passed** or more, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/agency/copilot.py tests/test_copilot_home.py
git commit -m "feat(copilot): give each job its own copilot home"
```

---

### Task 4: Copilot detects whether the sandbox is usable

**Files:**
- Modify: `agency/integrations/agency/copilot.py`
- Modify: `tests/test_permission_capabilities.py`
- Test: `tests/test_copilot_capability_detection.py`

**Interfaces:**
- Produces: `CopilotIntegration.detect_runtime_capabilities()` and `_capability_cache_key()` returning the detected CLI version.

- [ ] **Step 1: Write the failing test**

Create `tests/test_copilot_capability_detection.py`. Stub the version probe rather than invoking a real CLI, and assert:

- a recognised version declares `path_scopable_tools` containing `write`
- an unrecognised or absent CLI declares no scopable tools
- a probe that raises leaves capabilities at the declared value
- the cache key is the detected version

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_copilot_capability_detection.py -v`
Expected: FAIL

- [ ] **Step 3: Implement detection**

Probe `copilot --version`, parse the version, and cache on it.

Declaring `write` as path-scopable is the claim that the sandbox will hold. It rests on measurement: on 1.0.78-2 a write into a `readonlyPaths` entry was refused with `Edit (sandbox policy)` and produced no file, while a write into a `readwritePaths` entry succeeded. Do not extend the claim to `shell` — the container backend is unavailable on non-Insiders Windows, which Task 5 handles.

- [ ] **Step 4: Update the capability assertion**

`tests/test_permission_capabilities.py:105` currently asserts **no** integration scopes `write`. That was true and is now deliberately false for Copilot. Rewrite it to assert the eight still do not, and that Copilot does when detection succeeds. Do not delete it.

- [ ] **Step 5: Run the focused tests, then the suite**

Run: `python -m pytest tests/test_copilot_capability_detection.py tests/test_permission_capabilities.py -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: **1868 passed** or more, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/agency/copilot.py tests/
git commit -m "feat(copilot): declare path-scoped write when the sandbox holds"
```

---

### Task 5: Unenforced rules are reported

**Files:**
- Modify: `agency/integrations/models.py` (the run result)
- Modify: `agency/jobs/execution.py`
- Test: `tests/test_unenforced_policy_reporting.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_unenforced_policy_reporting.py` asserting:

- a run whose integration could not enforce some rules produces a `JobRecord.execution_summary` naming them
- a fully enforced run adds no such note
- the note survives into the decision record via `project_decision`
- the note names the paths and says what was not enforced, not merely that something was not

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_unenforced_policy_reporting.py -v`
Expected: FAIL

- [ ] **Step 3: Carry the gap out of the integration**

Add a field to the integration run result listing the rules that were not enforced, populate it in Copilot from `build_sandbox_settings` and from the shell-backend condition, and have the worker append a Markdown note to `execution_summary`.

`execution_summary` is rendered as Markdown at `agency/templates/job_detail.html:118` and propagated by `project_decision`, so no new field or template is required.

The note must be specific. "Some rules could not be enforced" tells an operator nothing; naming the path and the tool tells them what to change.

- [ ] **Step 4: Run the focused tests, then the suite**

Run: `python -m pytest tests/test_unenforced_policy_reporting.py -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: **1868 passed** or more, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add agency/ tests/test_unenforced_policy_reporting.py
git commit -m "feat(jobs): record the rules an integration could not enforce"
```

---

### Task 6: Credentials

**Files:**
- Modify: `agency/integrations/agency/copilot_sandbox.py`
- Modify: `agency/integrations/agency/copilot.py`
- Test: `tests/test_copilot_credentials.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_copilot_credentials.py` asserting:

- `gitAuth` and `ghAuth` are `false` for an agent with no `write` on the group's `workspace_path`
- both are `true` for an agent that has it
- the launch environment passed to `subprocess.run` does not carry a credential-shaped variable that the job does not need

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_copilot_credentials.py -v`
Expected: FAIL

- [ ] **Step 3: Tie tokens to executor eligibility**

An agent that may not change the project may not push it. Reuse the eligibility rule rather than inventing a second notion of trust — `agency/permissions/eligibility.py::may_execute_decisions` already answers exactly this question, so the sandbox builder needs whatever input lets it reach the same answer. Decide how to thread it and say so in your report.

- [ ] **Step 4: Reduce the launch environment**

The sandbox inherits Agency's environment apart from a fixed blocklist, so a cloud key or registry token in Agency's environment is visible to every agent regardless of its rules. A path-based model cannot express that.

Pass an explicitly constructed environment rather than `{**os.environ, ...}`. Keep what the CLI needs — `PATH`, `COPILOT_HOME`, the platform's essential variables, and whatever the existing code depends on — and drop the rest. Err toward keeping a variable if removing it breaks a run, and record what you kept and why.

- [ ] **Step 5: Run the focused tests, then the suite**

Run: `python -m pytest tests/test_copilot_credentials.py -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: **1868 passed** or more, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/agency/ tests/test_copilot_credentials.py
git commit -m "feat(copilot): gate credentials on executor eligibility"
```

---

### Task 7: The eight stop disarming themselves

**Files:**
- Modify: `agency/integrations/agency/claude_code.py`, `codex.py`
- Test: `tests/test_integration_launch_arguments.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_integration_launch_arguments.py`, following the `_launch` helper pattern from `tests/test_copilot_launch_arguments.py` but parametrised across all eight. For each, assert the rendered argv for `tools=()`, `tools=None`, an explicit narrow list with generated zone rules present, and confirm no generated rule path appears in any argument.

Then assert specifically:

- `claude_code` does **not** pass `--dangerously-skip-permissions` when the policy does not grant write
- `codex` does **not** pass `--yolo` when the policy does not grant write

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration_launch_arguments.py -v`
Expected: FAIL — both flags are currently unconditional

- [ ] **Step 3: Make the bypass conditional**

`claude_code.py:82` passes `--dangerously-skip-permissions` and `codex.py:43` passes `--yolo` on every run. Each disables that CLI's own permission model, which is the only enforcement those integrations have.

Pass them only when the effective policy grants at least `write` somewhere. An agent the operator restricted must not have its tool's own safety switched off on its behalf. Where the flag is withheld, the CLI may prompt and the run may not complete unattended — that is a real behaviour change, so state it in your report and cover it in the docs task.

Do **not** add sandboxing to these integrations. This task only stops them over-granting.

- [ ] **Step 4: Run the focused tests, then the suite**

Run: `python -m pytest tests/test_integration_launch_arguments.py -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: **1868 passed** or more, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add agency/integrations/agency/ tests/test_integration_launch_arguments.py
git commit -m "fix(integrations): stop disabling tool permission models"
```

---

### Task 8: Live verification and documentation

**Files:**
- Modify: `tests/test_runtime_projectors_live.py`
- Modify: `AGENTS.md`, `kb/integrations.md`, `kb/configuration.md`

- [ ] **Step 1: Add a live zone test**

Add a `real_runtime` scenario asserting, against a genuinely installed Copilot, that a read into the instructions zone succeeds, a write into it is refused, and a write into the outbox succeeds. Use `assert_protected_state_unchanged()` so the probe cannot damage the checkout.

Gate it on `write_boundary_supported(integration)` so it skips where the sandbox cannot hold. **Do not run this file on its own** — it consumes AI credits. It runs as part of the full suite.

- [ ] **Step 2: Document what is enforced and what is not**

Update `AGENTS.md` and `kb/integrations.md` to say:

- Copilot enforces path-scoped writes through its sandbox when the CLI supports it; a read-only agent can write only its outbox and memory.
- Shell commands are unavailable under the sandbox on non-Insiders Windows, and Agency records the gap against the job rather than pretending.
- Built-in file edits are policed in-process and cooperatively; only shell is OS-contained.
- Environment-borne credentials are outside the boundary.
- The other eight integrations do not enforce path rules at all; their permission modes are `unrestricted` only, and narrow rules under `unrestricted` are not enforced by them.
- `claude_code` and `codex` no longer disable their own permission models unconditionally, and may now prompt where they previously did not.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. Report the final counts against the 1868/6 baseline.

- [ ] **Step 4: Commit**

```bash
git add tests/ AGENTS.md kb/
git commit -m "docs(copilot): document what the sandbox enforces"
```

---

## Completion

- [ ] Run the complete suite from the worktree root and confirm it is green.
- [ ] Review the whole branch before integrating.
- [ ] Fast-forward `master` to the reviewed tip, re-run the suite, push both branches, and remove the worktree.
- [ ] Confirm the editable install still resolves to the main checkout.
