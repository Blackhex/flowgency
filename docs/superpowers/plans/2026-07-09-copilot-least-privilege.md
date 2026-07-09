# Copilot least-privilege paths & tools — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-07-09-copilot-least-privilege-design.md`
**Date:** 2026-07-09
**Tech stack:** Python 3.11+, pytest, GitHub Copilot CLI, FastAPI (unaffected).

## Goal

Replace the two blanket Copilot flags with explicit, config-driven grants:

- `--allow-all-paths` → one `--add-dir` per declared sandbox root.
- `--allow-all-tools` → one `--allow-tool` per declared tool.

Driven by a `sandbox_root` that accepts a **string or list**, plus a new optional
`allowed_tools` list. Empty/absent lists preserve today's blanket behavior.
`--autopilot` is emitted **only** in the blanket-tools case (proven incompatible
with explicit `--allow-tool` in `-p` mode).

## Global Constraints

- The value threaded through `run(..., sandbox_root=...)` changes type from
  `Path | None` to `SandboxSpec | None`. **Only `copilot.py` consumes it**; the
  other 11 integrations bind-and-ignore it — do NOT change them.
- Do NOT touch the proven launch mechanism in `copilot.py`: `_resolve_real_cmd`,
  `CREATE_NO_WINDOW`, `stdin=DEVNULL`, the `subprocess.run` call.
- Do NOT change the web-UI file boundary (`get_allowed_roots` /
  `validate_file_access`). This feature governs agent runtime only.
- Backward compatibility: a single-string `sandbox_root` with no `allowed_tools`
  MUST behave exactly as today (confined paths via `--add-dir`, blanket tools +
  `--autopilot`).
- Unit tests assert argv + cwd + kwargs via monkeypatched `subprocess.run`; they
  cannot validate runtime permission behavior. Runtime is validated only by the
  real-session task (Task 4).

## Automated verification

```powershell
python -m pytest tests/ -q            # full suite (must stay green at each task boundary)
python -m pytest tests/test_config_normalization.py tests/test_integration_sidecar.py -q
```

---

## Task 1 — `SandboxSpec` + `get_sandbox_root` (config layer)

**Files:** `agency/config.py`, `tests/test_config_normalization.py`

**TDD — write/adjust tests first** in `tests/test_config_normalization.py`
(replace the 5 existing `get_sandbox_root` tests; keep the section header):

1. `sandbox_root` absolute string → `SandboxSpec(roots=(Path("/repo/root"),), allowed_tools=())`.
2. `sandbox_root` relative string → resolved against group path, single-element `roots`.
3. `sandbox_root` **list** `["/a", "rel"]` → `roots=(Path("/a"), (group/"rel").resolve())`, order preserved.
4. `sandbox_root` missing AND `allowed_tools` missing → `None`.
5. `sandbox_root` blank/whitespace-only, no tools → `None`.
6. No group `path` → `None` (relative roots can't resolve; matches current guard).
7. `allowed_tools: ["shell", "write"]` with no `sandbox_root` →
   `SandboxSpec(roots=(), allowed_tools=("shell", "write"))` (not `None`).
8. Both set → both tuples populated.
9. List with blank entries (`["/a", "  "]`) → blanks dropped.

**Implementation** in `agency/config.py`:

- Add:
  ```python
  from dataclasses import dataclass, field

  @dataclass(frozen=True)
  class SandboxSpec:
      roots: tuple[Path, ...] = ()
      allowed_tools: tuple[str, ...] = ()
  ```
- Rewrite `get_sandbox_root(g) -> SandboxSpec | None`:
  - Parse `sandbox_root`: accept `str` or `list`; for each non-blank entry apply
    the existing absolute/relative resolution (relative resolved against
    `g["path"]`, requiring `path` present — reuse current logic per entry).
  - Parse `allowed_tools`: list of non-blank strings → tuple (empty if absent).
  - Return `None` **only** when both `roots` and `allowed_tools` are empty
    (preserves existing None-equivalence and minimizes caller churn).
  - Otherwise return `SandboxSpec(roots=..., allowed_tools=...)`.

**Verification:** `python -m pytest tests/test_config_normalization.py -q` green.
(Full suite will be red until Task 2 — expected; note in commit that 1+2 land
together, or commit 1+2 before running full suite.)

---

## Task 2 — Unified command builder in `copilot.py`

**Files:** `agency/integrations/agency/copilot.py`,
`tests/test_integration_sidecar.py`, `tests/test_execute_decision.py`

**TDD — write tests first** in `tests/test_integration_sidecar.py`
(`TestCopilot`), reusing the existing `fake_run` capture pattern
(captures `args`, `cwd`, `kwargs`). Import `SandboxSpec` from `agency.config`.
Replace/extend `test_copilot_run_set_sandbox_runs_from_sandbox_root`:

1. **roots + tools set** — `SandboxSpec(roots=(r1, r2), allowed_tools=("shell","write"))`:
   - `--add-dir str(r1)` and `--add-dir str(r2)` both present.
   - `--allow-tool shell` and `--allow-tool write` both present.
   - `--autopilot` NOT in args; `--allow-all-paths` NOT in args;
     `--allow-all-tools` NOT in args.
   - `cwd == str(r1)`.
   - Headless kwargs still asserted: `stdin is subprocess.DEVNULL`,
     `"creationflags" in kwargs`.
2. **roots set, tools empty** — `SandboxSpec(roots=(r1,), allowed_tools=())`:
   - `--add-dir str(r1)` present; `--allow-all-tools` present; `--autopilot` present.
   - `--allow-all-paths` NOT present; no `--allow-tool`. `cwd == str(r1)`.
3. **both empty / None** — `sandbox_root=None` (and separately `SandboxSpec()`):
   - argv contains `--allow-all-paths --allow-all-tools --autopilot --experimental`.
   - `cwd == str(agent_dir)`.
4. Keep `test_copilot_resolve_real_cmd_*` tests unchanged.

Also update `tests/test_execute_decision.py::test_execute_decision_passes_sandbox_root`:
the assertion `captured["sandbox_root"] == Path(...)` becomes
`captured["sandbox_root"] == SandboxSpec(roots=(Path(...),), allowed_tools=())`.

**Implementation** in `copilot.py` `run()` — replace the `if sandbox_root is not
None: ... else: ...` flag block (keep everything below `start = time.monotonic()`):

```python
from agency.config import SandboxSpec   # top of file

spec = sandbox_root or SandboxSpec()
roots, tools = spec.roots, spec.allowed_tools

cmd_args = [
    cmd, "-p", prompt_text,
    "--no-custom-instructions",
    "--no-ask-user",
    "--no-color",
    "--experimental",
]

if roots:
    for p in roots:
        cmd_args += ["--add-dir", str(p)]
    work_dir = str(roots[0])
else:
    cmd_args += ["--allow-all-paths"]
    work_dir = str(agent_dir)

if tools:
    for t in tools:
        cmd_args += ["--allow-tool", t]
else:
    cmd_args += ["--allow-all-tools", "--autopilot"]
```

- Replace the stale confined/unrestricted comment block with a concise comment:
  autopilot only with blanket tools (copilot-cli#2971), explicit grants validated
  by real-session probe 2026-07-09.
- Import safety: verified `agency/config.py` imports only `pathlib` and nothing
  in `agency/integrations` imports `config`, so a top-level
  `from agency.config import SandboxSpec` in `copilot.py` is cycle-free.

**Verification:**
`python -m pytest tests/test_integration_sidecar.py tests/test_execute_decision.py tests/test_config_normalization.py -q`
then full `python -m pytest tests/ -q` green.

---

## Task 3 — Base type hint + ripple check

**Files:** `agency/integrations/__init__.py`, and a sweep of remaining tests.

- Update `BaseIntegration.run` signature docstring/type: `sandbox_root:
  "SandboxSpec | None" = None`. A top-level import is cycle-free (verified in
  Task 2), but a `TYPE_CHECKING` import keeps the base module dependency-light —
  either is acceptable. Do NOT change `_template.py` or the other integrations'
  behavior; a comment noting the value is opaque to non-consumers is enough.
- Run the full suite and fix any remaining comparisons that assumed a `Path`
  return from `get_sandbox_root` (candidates flagged by grep:
  `tests/test_agent_run.py`, `tests/test_dispatch_run.py`,
  `tests/test_integration_contract.py`, `tests/test_integrations.py`). Most only
  assert the kwarg is *threaded through*, not its type — update only those that
  assert an equality against `Path`.
- `tests/test_admin_org_sandbox.py` concerns config *persistence* (string
  written to YAML) — should be unaffected; confirm green.

**Verification:** `python -m pytest tests/ -q` fully green.

---

## Task 4 — Real-session validation gate (decisive)

**Not a unit test.** Run the full sentinel routine through the **shipped code
path** via `run_agent_prompt`, exercising each mode, and scan for
`Permission denied` / `could not request permission from user`.

- Reuse `.superpowers/sdd/validate_permission_fix.py` (or a thin wrapper) but
  route through the real integration so `get_sandbox_root` → `SandboxSpec` →
  `copilot.run` is the actual code under test. Configure the sentinel group with:
  `sandbox_root: [C:/Projects/msvc-digest, ~/.agency-cowork]`,
  `allowed_tools: [shell, write]`.
- **Pass criteria (ALL):** exit 0; zero permission-denied strings; positive
  shell **and** write evidence in the log.
- Read the log back and report findings verbatim. If denials appear, diagnose
  from the log before any code change (do not guess-and-retry flags).

**Verification:** one clean full-routine run (repeat once to confirm).

---

## Task 5 — Docs, memory, cleanup

**Files:** `kb/configuration.md`, `CLAUDE.md`, `/memories/repo/copilot-integration.md`

- `kb/configuration.md` — document `sandbox_root` as string-or-list and the new
  `allowed_tools` key, with the empty-list → blanket semantics and the
  `--autopilot`-only-with-blanket-tools note.
- `CLAUDE.md` — update the `sandbox_root` config-format line to mention the list
  form + `allowed_tools`.
- `/memories/repo/copilot-integration.md` — record the final flag matrix (the
  three scenarios) and the autopilot/explicit-tool incompatibility.
- Delete scratch probes: `.superpowers/sdd/probe_allow_tool_pmode.py`,
  `.superpowers/sdd/probe_final_cwd.py` (and any `copilot-probe-*` temp dirs).

**Verification:** `python -m pytest tests/ -q` green; `git status` clean except
intended changes.

---

## Finishing

- Whole-branch review (`git diff` of the range), then integrate per user's
  workflow preference (fast-forward only; no push/branch-delete without consent).
