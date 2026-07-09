# Copilot least-privilege paths & tools — Design

**Date:** 2026-07-09
**Status:** Approved (design phase)
**Component:** `agency/integrations/agency/copilot.py`, `agency/config.py`,
`agency/integrations/__init__.py`

## Problem

Confined Copilot dispatch currently pre-authorizes the agent with two blanket
flags: `--allow-all-paths` (all filesystem paths) and `--allow-all-tools` (all
tools). This is broader than necessary. We want a least-privilege posture where
a group can declare exactly which filesystem roots and which tools a Copilot
agent may use, while keeping the existing blanket behavior as the default for
unconfigured groups.

Two independent axes:

- **Paths** — replace `--allow-all-paths` with an explicit list of allowed
  roots (`--add-dir` per root).
- **Tools** — replace `--allow-all-tools` with an explicit list of granted
  tools (`--allow-tool` per tool).

## Validated foundation (real-session probes)

All findings below come from running the **real** sentinel routine through the
shipped launch mechanism (real `copilot.exe`, no console, `stdin=DEVNULL`) and
scanning output for `Permission denied and could not request permission from
user`.

| Configuration | `--autopilot` | Tool grant | Path grant | Denials |
|---|---|---|---|---|
| Option C probe | no | `--allow-tool shell/write` | `--add-dir` | **0** |
| Earlier A/B | yes | `--allow-all-tools` | `--allow-all-paths` | 0 |
| Final probe (cwd=agent_dir) | yes | `--allow-tool shell/write` | `--add-dir` | 4 |
| Final probe (cwd=first root) | yes | `--allow-tool shell/write` | `--add-dir` | 4 |

**Conclusions:**

1. Explicit `--allow-tool` grants **do** work in non-interactive `-p` mode
   (upstream copilot-cli#3699 does not bite this configuration) — *provided
   `--autopilot` is absent*.
2. `--autopilot` is **incompatible** with explicit `--allow-tool` grants: under
   autopilot, shell/write perform a permission round-trip that fails closed
   (copilot-cli#2971) unless tools are blanket-approved via `--allow-all-tools`.
   The 4 denials were shell commands that had been granted `--allow-tool shell`.
3. `cwd` was exonerated — both cwd variants produced identical denials, so the
   cause is `--autopilot`, not the working directory.

Therefore: **use `--autopilot` only when tools are blanket-approved; omit it
when tools are explicitly listed.**

> Caveat recorded for honesty: the final probe added both `--autopilot` and
> `--experimental` relative to the clean Option C run. Evidence and #2971 point
> squarely at `--autopilot`; `--experimental` alone was not isolated. The design
> keeps `--experimental` in all variants (it is harmless in the historical
> unrestricted mode) and drops only `--autopilot` in the explicit-tools case.

## Config schema (backward-compatible)

Two group-level keys, both optional:

```yaml
groups:
  sentinel:
    sandbox_root:                  # string OR list; empty/absent => --allow-all-paths
      - C:/Projects/msvc-digest    # first entry => cwd / relative-write anchor
      - ~/.agency-cowork           # additional allowed root
    allowed_tools:                 # list; empty/absent => --allow-all-tools
      - shell
      - write
```

- `sandbox_root` accepts the existing **single string** (back-compat) or a
  **list**. Each entry is resolved with the current absolute/relative rules.
- `allowed_tools` is a new optional list. **Absent or empty => blanket tools**
  (today's behavior), so unconfigured groups are unchanged.
- The two axes are independent: a group may restrict paths but not tools, or
  vice-versa.

## Interface

`get_sandbox_root(g)` returns a small immutable spec instead of a bare path:

```python
@dataclass(frozen=True)
class SandboxSpec:
    roots: tuple[Path, ...] = ()          # empty => --allow-all-paths
    allowed_tools: tuple[str, ...] = ()   # empty => --allow-all-tools
```

- The value is threaded through the **existing** keyword-only `sandbox_root`
  parameter of `BaseIntegration.run(...)`. Only `copilot.py` reads it; the other
  11 integrations bind and ignore it, so they need **zero** changes.
- `None` and `SandboxSpec()` are equivalent — both mean "fully unrestricted".
  `copilot.py` normalizes `None` to an empty spec.
- The base-class `sandbox_root` type hint/docstring is updated to
  `SandboxSpec | None`.
- The two call sites (`agency/dispatch/run.py`, `agency/app.py`) already pass
  `get_sandbox_root(g)` straight through — unchanged.

## Unified command builder (`copilot.py`)

The `confined` vs `unrestricted` branch is replaced by a single builder driven
by the two lists. Direct-exe resolution (`_resolve_real_cmd`), `CREATE_NO_WINDOW`,
`stdin=DEVNULL`, and the `subprocess.run` call are unchanged.

```python
spec = sandbox_root or SandboxSpec()      # normalize None
roots, tools = spec.roots, spec.allowed_tools

cmd_args = [
    cmd, "-p", prompt_text,
    "--no-custom-instructions",
    "--no-ask-user",
    "--no-color",
    "--experimental",
]

# Paths
if roots:
    for p in roots:
        cmd_args += ["--add-dir", str(p)]
    work_dir = str(roots[0])          # first root anchors relative writes
else:
    cmd_args += ["--allow-all-paths"]
    work_dir = str(agent_dir)

# Tools — --autopilot only with blanket approval (proven incompatible with
# explicit --allow-tool grants in -p mode; copilot-cli#2971).
if tools:
    for t in tools:
        cmd_args += ["--allow-tool", t]
else:
    cmd_args += ["--allow-all-tools", "--autopilot"]
```

### Emitted flags by scenario

| Scenario | Flags (besides `-p`, `--no-*`, `--experimental`) | cwd |
|---|---|---|
| roots + tools set | `--add-dir…` ×N, `--allow-tool…` ×M | first root |
| roots set, tools empty | `--add-dir…` ×N, `--allow-all-tools --autopilot` | first root |
| both empty (unrestricted) | `--allow-all-paths --allow-all-tools --autopilot` | agent_dir |

All three variants match clean real-session evidence.

### Behavior notes

- `cwd = first root` already makes that whole tree allowed (cwd + subdirs), but
  it is **also** added via `--add-dir` for explicitness and to cover the shell
  tool's path checks. Additional roots require `--add-dir`.
- Reads/search are always available and never prompt — no `read` grant exists.
- `allowed_tools: []` with roots set is a valid choice: shell/write are then
  *not* granted (blanket), which is the unrestricted-tools path — documented,
  not guarded against.

## Testing

**Unit tests** (`tests/test_integration_sidecar.py`), argv + cwd via
monkeypatched `subprocess.run`:

1. roots + tools set → one `--add-dir` per root, one `--allow-tool` per tool,
   **no** `--autopilot`, **no** `--allow-all-*`, cwd = first root.
2. roots set, tools empty → `--allow-all-tools` **and** `--autopilot` present,
   `--add-dir` per root, cwd = first root.
3. both empty → today's unrestricted argv (`--allow-all-paths
   --allow-all-tools --autopilot`), cwd = agent_dir.
4. `get_sandbox_root` parses a single string, a list, and `allowed_tools` into a
   `SandboxSpec` with the expected tuples.
5. Back-compat: a single-string `sandbox_root` (no `allowed_tools`) yields
   `roots=(one,)`, `allowed_tools=()`.

**Real-session gate** (decisive): full sentinel routine via `run_agent_prompt`
through the shipped code path → **0 denials**, with shell **and** write evidence.
Argv unit tests cannot validate runtime permission behavior.

Full suite stays green.

## Scope

- `agency/config.py` — `SandboxSpec` dataclass + `get_sandbox_root` returning it.
- `agency/integrations/agency/copilot.py` — unified builder consuming the spec.
- `agency/integrations/__init__.py` — `sandbox_root` type/docstring update.
- `tests/test_integration_sidecar.py` — updated + new cases; any test that
  built a bare `Path` root switches to `SandboxSpec`.
- No changes to the other 11 integrations, `_template.py`, or the two call
  sites (they pass the value through unchanged).
- Update `kb/` docs for the new `sandbox_root` list + `allowed_tools` config.
- Update `/memories/repo/copilot-integration.md` with the final flag matrix.

## Out of scope

- `--disallow-temp-dir` hardening (future option).
- MCP tool-name granularity beyond passing user-declared strings verbatim.
- Changing the proven launch mechanism (direct-exe, no-console, stdin).
