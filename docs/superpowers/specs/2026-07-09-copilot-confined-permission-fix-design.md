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

## Root cause (verified 2026-07-09 by real-session validation)

Two **stacked, independent** causes, both structural (not the intermittent
upstream flag bug originally suspected):

1. **Windows wrapper re-allocates an interactive console.** On Windows
   `shutil.which("copilot")` returns a `copilot.BAT` bootstrapper that runs
   `powershell -File copilot.ps1`, which in turn launches the real
   `copilot.EXE`. Under `subprocess.run` this chain gives the grandchild `.exe`
   a console, so the CLI decides it is **interactive** and tries to prompt for
   tool permission — which fails closed headless with *“Permission denied and
   could not request permission from user”* even with `--allow-all-tools` set.
   The proven production launch (task scheduler → `Start-Job` → `.ps1` → `.exe`,
   no console) never hits this. Measured: our Python `.bat` path produced
   24–62 denials per full routine; the no-console production replica produced 0.

2. **`--allow-all-paths` was missing.** Copilot's shell **and** native file
   tools deny operations touching paths outside the working directory without
   `--allow-all-paths`, surfacing the same *“Permission denied…”* string. Real
   routines legitimately read agency data outside the sandbox (the sentinel
   routine reads `~/.agency-cowork/monitor-config.json` and the monitor venv).
   The proven production launch sets `--allow-all-paths`; the confined branch
   had dropped it. This falsifies the earlier assumption that “the shell tool
   was never path-confined.”

Upstream issues [copilot-cli#3699](https://github.com/github/copilot-cli/issues/3699)
(enumerated `--allow-tool` not honored in `-p` mode) and
[copilot-cli#2971](https://github.com/github/copilot-cli/issues/2971)
(permission round-trip) informed flag choices but were **not** the operative
cause — the failure reproduced ~100% on the full routine and was eliminated by
fixing the launch mechanism and path scope, not by a flag workaround alone.

## Current state (before this fix)

`agency/integrations/agency/copilot.py` `run()`:

- Resolved `copilot` via `shutil.which` — on Windows a `.bat` wrapper — and
  invoked it with a plain `subprocess.run` (console-attached).
- **Confined** (`sandbox_root` set): `cwd=sandbox_root`, no `--allow-all-paths`.
- **Unrestricted** (`sandbox_root` None): `cwd=agent_dir`,
  `--autopilot --allow-all-paths --allow-all-tools --experimental`.

A note in repo memory claimed a confined-mode fix was applied, but git history
showed it was **never committed**.

## The fix

Three coordinated changes in `agency/integrations/agency/copilot.py` `run()`:

1. **Bypass the Windows wrapper.** New `CopilotIntegration._resolve_real_cmd()`
   (Windows-only): if the resolved command is a `.bat/.cmd/.ps1` wrapper,
   re-search `PATH` with the wrapper's own directory removed and return the real
   `copilot.EXE`. On other platforms (or when no wrapper is detected) the
   command is returned unchanged. `run()` now calls
   `cmd = self._resolve_real_cmd(self._find_cmd())`.

2. **Launch headless.** The shared `subprocess.run` uses
   `stdin=subprocess.DEVNULL` and `creationflags=CREATE_NO_WINDOW`
   (`getattr(subprocess, "CREATE_NO_WINDOW", 0)` so non-Windows is a no-op), so
   the real `.exe` gets no console and stays non-interactive — matching the
   production `Start-Job` launch. This applies to both confined and unrestricted
   modes.

3. **Restore `--allow-all-paths` in the confined branch**, alongside
   `--allow-all-tools`, so out-of-sandbox agency data is readable (production
   parity). `cwd = sandbox_root` still anchors relative writes to the sandbox
   tree. `--autopilot` remains omitted in confined mode.

Confined `cmd_args`:

```python
work_dir = str(sandbox_root)
cmd_args = [
    cmd, "-p", prompt_text,
    "--no-custom-instructions",
    "--no-ask-user",
    "--allow-all-tools",
    "--allow-all-paths",
    "--no-color",
]
```

### Security posture

`sandbox_root` sets the working directory (anchoring relative writes), not an OS
filesystem jail — true OS sandboxing is not performed on Windows, and the shell
tool was never OS-confined. `--allow-all-paths` matches the proven production
invocation and is required for real routines to function. The change does not
weaken a boundary that was actually enforced.

## Test updates

`tests/test_integration_sidecar.py`:

- `test_copilot_run_set_sandbox_runs_from_sandbox_root`: assert
  `--allow-all-paths` **in** args (was asserted absent), `--allow-all-tools` in
  args, `--autopilot` not in args, no enumerated `--allow-tool=*`, `cwd == root`;
  and assert the headless kwargs (`stdin is subprocess.DEVNULL`, `creationflags`
  present).
- New `test_copilot_resolve_real_cmd_bypasses_windows_wrapper` and
  `test_copilot_resolve_real_cmd_noop_off_windows` cover the wrapper-bypass.

The unrestricted-mode test is unchanged. Full suite runs green.

> Behavioral tests assert argv + cwd + kwargs via monkeypatched
> `subprocess.run`. They **cannot** catch CLI runtime permission behavior —
> hence the real-session step is the decisive gate.

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
3. Positive evidence at least one shell command executed (`shell_evidence`).

Logs are read back and the findings reported verbatim.

### Honest limits

The validated fix eliminated the failure across full-routine runs (the last two
consecutive runs were clean with positive shell evidence). Because the original
report described intermittency, the report states exactly what the runs
demonstrate — the structural causes (console re-allocation + missing path scope)
are removed — without overclaiming statistical eradication of any unrelated
intermittent upstream behavior.

## Validated outcome

Progression across full sentinel-routine runs via `run_agent_prompt`:

| Variant | Denials |
|---|---|
| `.bat` wrapper, various flags | 24–62 |
| direct `.exe` + `CREATE_NO_WINDOW`, no `--allow-all-paths` | 10 (all out-of-sandbox reads) |
| direct `.exe` + `CREATE_NO_WINDOW` + `--allow-all-paths` | **0 (2/2 runs, shell evidence present)** |
| production replica (`Start-Job` `.ps1`, no console, full flags) | 0 |

## Fallback (if validation had failed)

If real sessions had still failed after the launch-mechanism and path-scope
fixes, the next escalation would be detect-and-retry (parse stderr for the
permission failure and retry/escalate). Not needed — validation passed clean.

## Scope

- One new private helper (`_resolve_real_cmd`) and two subprocess kwargs; the
  confined flag list gains `--allow-all-paths`. No refactoring beyond this and
  the corresponding test/comment updates.
- Update repo memory (`/memories/repo/copilot-integration.md`) with the real
  root cause, the committed fix, and the validation outcome.
