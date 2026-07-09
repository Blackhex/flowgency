# Design: Fix Copilot confined-mode permission failures

**Date:** 2026-07-09
**Status:** Approved (validation is the decisive gate)

## Problem

Copilot agents running under Agency dispatch in **confined mode** (a group with
`sandbox_root` set) intermittently fail with:

> Permission denied and could not request permission from user

The failure hits `shell` and `write` tools; read-family tools (glob/grep/view)
keep working. This surfaces as agent runs that partially execute then stall on
the first shell command or file write.

## Root cause (verified 2026-07-08, repo memory)

Upstream GitHub Copilot CLI bug, **not** an Agency flag problem:

- [copilot-cli#3699](https://github.com/github/copilot-cli/issues/3699) —
  enumerated `--allow-tool=shell` is not honored in non-interactive `-p` mode.
- [copilot-cli#2971](https://github.com/github/copilot-cli/issues/2971) — under
  `--autopilot`, `shell`/`write` still perform a permission-check round-trip that
  fails closed once the permission channel degrades mid-session (reads bypass it,
  which is why glob/grep/view keep working).

The bug is **intermittent and stateful** — memory records it could not be
reproduced on demand across 6 controlled runs. The prescribed mitigation from
#2971 is a single blanket **pre-grant** (`--allow-all-tools`), which removes the
failing round-trip entirely.

## Current state

`agency/integrations/agency/copilot.py` `run()`:

- **Confined** (`sandbox_root` set): `cwd=sandbox_root`, flags
  `--autopilot --allow-tool=read --allow-tool=write --allow-tool=shell --experimental`.
  ← the three enumerated grants that trigger the failing round-trip.
- **Unrestricted** (`sandbox_root` None): `cwd=agent_dir`, flags
  `--autopilot --allow-all-paths --allow-all-tools --experimental`. ← already uses
  the blanket pre-grant.

A note in repo memory claimed the confined-mode fix was applied, but git history
shows it was **never committed** — code and tests still assert the three grants.

## The fix (Option A)

In the confined branch of `run()`, replace the three enumerated grants with a
single `--allow-all-tools`:

```python
work_dir = str(sandbox_root)
cmd_args = [
    cmd, "-p", prompt_text, "--autopilot",
    "--allow-all-tools",
    "--experimental",
]
```

Constraints held constant:

- `cwd = sandbox_root` is unchanged — this is what scopes native file tools to
  the sandbox tree.
- `--allow-all-paths` is deliberately **not** added. Tool-approval and path-scope
  are independent CLI levers: `--allow-all-tools` = approval only;
  `--allow-all-paths` = disable path verification. Path confinement is unchanged.
- The confining comment block is updated to describe the single flag.

### Security posture (unchanged)

`--allow-all-tools` broadens tool *approval*, not path *scope*. Per memory, the
`shell` tool was never path-confined even under the enumerated grants (true OS
sandboxing is not done on Windows); path verification only governs native file
tools (view/write/apply_patch), which remain scoped to the sandbox tree via
`cwd`. So this change does not weaken the real security boundary.

## Test updates

`tests/test_integration_sidecar.py :: test_copilot_run_set_sandbox_runs_from_sandbox_root`:

- Remove the three `assert "--allow-tool=read|write|shell" in args` assertions.
- Add `assert "--allow-all-tools" in args`.
- Keep `assert "--allow-all-paths" not in args` and `assert cwd == str(root)` —
  path confinement must remain proven.

The unrestricted-mode test is unchanged. Run the full suite; expect green.

> Behavioral tests assert argv + cwd via monkeypatched `subprocess.run`. They
> **cannot** catch CLI runtime permission behavior — hence the real-session step.

## Real-session validation (the decisive gate)

Exercise the actual dispatch code path, not a synthetic harness. Call
`agency.dispatch.run.run_agent_prompt()` directly with:

| Arg | Value |
|-----|-------|
| `group_path` | `C:\Projects\msvc-digest\agents` |
| `agent_name` | `sentinel` |
| `prompt_filename` | `sentinel-routine.md` |
| `agent_config` | `{"integration": "copilot"}` |
| `sandbox_root` | `C:\Projects\msvc-digest` |
| `log_dir` | a temp dir |
| `timeout` | generous (e.g. 900s) |

`sentinel-routine.md` is chosen because it fires several shell commands
(`Get-ScheduledTask`, `python prompt_guard.py`, `security-audit.ps1`) **and**
writes observation/memory files under the sandbox — exercising the exact
shell+write failure mode, all in-tree, within read-only/reporting boundaries
(no destructive operations).

### Success criteria

Pass requires ALL of:

1. Exit code `0` (not `124` timeout, not non-zero).
2. `.out`/`.err` contain **no** `could not request permission from user` or
   `Permission denied` strings.
3. Positive evidence at least one shell command executed.
4. Positive evidence at least one file write landed under the sandbox.

Logs are read back and the findings reported verbatim.

### Honest limits

The bug is intermittent and stateful. A green run proves the fix does not break
confined execution and that shell+write succeed through the real path. It does
**not** statistically prove the intermittent failure is eradicated. The report
will state exactly what the run demonstrates — no overclaiming.

## Fallback (if validation fails)

If a real session still fails under `--allow-all-tools`, escalate to **Option B**
(detect-and-retry): parse stderr for the permission failure and retry/escalate.
This is treated as a follow-up, not part of this change.

## Scope

- No new files. No refactoring beyond the flag swap, its comment update, and the
  test assertion swap.
- Update repo memory (`/memories/repo/copilot-integration.md`) to reflect that
  the fix is now actually committed, plus the validation outcome.
