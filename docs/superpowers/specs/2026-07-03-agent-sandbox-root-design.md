# Agent Sandbox Root — Design

**Date:** 2026-07-03
**Status:** Implemented — Copilot mechanism revised during testing (see below)

> **Revision (2026-07-04) — Copilot uses cwd, not `--add-dir`.** Live testing
> against a real repo-nested agent disproved this spec's Option A (keep
> `cwd = agent_dir`, add `--add-dir <root>`). Under non-interactive `--autopilot`,
> Copilot did **not** reliably grant access to paths added via `--add-dir`; the
> denials ("could not request permission from user") persisted regardless of
> `--add-dir` / `--allow-tool` / `permissions-config.json` edits. Root cause:
> **Copilot scopes native file access to the working directory.** The proven
> mechanism (matching the repo's own task-scheduler launch, which always worked)
> is to run confined mode with **`cwd = sandbox_root`** plus
> `--autopilot --allow-tool=read/write/shell`. Unrestricted mode still runs from
> `cwd = agent_dir` with `--allow-all-paths --allow-all-tools`. The config,
> resolution, plumbing, admin UI, and security model in this spec are unchanged
> and correct; only the Copilot invocation row of the matrix is superseded. See
> `agency/integrations/agency/copilot.py` for the shipped behavior.

## Problem

Agency launches every agent runtime with `cwd = agent_dir` (the agent's own
folder). Some runtimes treat that `cwd` as a hard filesystem boundary. GitHub
Copilot, launched with `--autopilot`, confines all file operations to the
launch directory. Agents whose routines need files **outside** their own
folder therefore fail.

Concretely, in a repo-nested layout like:

```
C:\Projects\msvc-digest\           <- repository root
├── AGENTS.md                      <- repo instructions
├── memory\                        <- Knowledgebase, DailyLogs (gitignored)
├── output\
└── agents\                        <- group path
    ├── shared\                    <- shared memory, prompts, observations, proposals
    └── advisor\                   <- agent dir (cwd)
        ├── AGENTS.md              <- advisor identity
        └── memory.md              <- advisor memory
```

A Copilot run of the `advisor` agent could only read/write `agents\advisor\`.
Everything else — `agents\shared\`, `memory\`, `output\`, and the repo-root
`AGENTS.md` — was denied. The agent's only workaround was `git show HEAD:<path>`
(git object reads from cwd, which bypass the working-tree sandbox). It could not
write observation or proposal files at all.

There is currently **no config lever** to widen (or deliberately confine) an
agent's runtime filesystem access.

## Non-Goals

- **The Agency web-UI file boundary is unchanged.** `get_allowed_roots(g)` in
  `agency/config.py` (used only by `validate_file_access` in `app.py` to
  constrain the dashboard's document/memory/log browsers) is a separate
  security concern and is **not** touched by this feature. A wider agent
  sandbox must not silently widen what the dashboard exposes.
- No changes to agent identity resolution, dispatch scheduling, or the
  workspace system.
- We are not building a generic per-runtime permission model. We add one
  clearly-scoped knob and wire it into the runtimes that support sandboxing.

## Core Concept

Add an optional **group-level** config field: `sandbox_root`.

The presence/absence of `sandbox_root` decides **how each sandbox-capable
runtime is launched**:

- **`sandbox_root` unset** → launch the runtime in **full-access mode** (no
  filesystem confinement). This is the "yolo" mode most runtimes already use.
- **`sandbox_root` set** → launch the runtime **confined to `cwd + sandbox_root`**
  using that runtime's own sandbox/allow-dir flags. The agent can read/write its
  own folder plus everything under the sandbox root, and nothing else.

Sandboxing is therefore **opt-in**: an unset `sandbox_root` (the default for all
existing groups) means agents run unconfined.

A runtime that has **no** sandbox capability always runs in its normal mode, and
the UI warns the user that `sandbox_root` will have no effect for it.

The agent still launches with `cwd = agent_dir` in all cases, so agent identity
(`AGENTS.md` etc.) and the per-agent `memory.md` convention resolve exactly as
they do today. `sandbox_root` only changes the filesystem *permission* flags,
never the working directory.

### Behavior change to call out

Today Copilot always runs `--autopilot --experimental` with no path flag, which
implicitly confines it to `cwd`. Under this design, the default (no
`sandbox_root`) becomes **full-access** (`--autopilot --allow-all-paths
--experimental`). So existing Copilot groups gain *broader* access than before
unless they set a `sandbox_root`. This matches the approved "unset → yolo" rule
and must be documented in the changelog / KB.

## Config Schema

```yaml
groups:
  agents:
    name: Agents
    path: C:\Projects\msvc-digest\agents
    sandbox_root: C:\Projects\msvc-digest   # NEW — optional
    default_integration: copilot
    agents:
      - advisor
```

Resolution rules (implemented in a new helper `get_sandbox_root(g)` in
`agency/config.py`):

- Absolute path → used as-is.
- Relative path → resolved against the group `path`.
- Missing / empty / whitespace → returns `None` (feature off).
- The helper returns a `Path | None`. It does **not** verify existence;
  existence is checked at launch time (see Security).

## Per-Runtime Invocation Matrix

Each integration declares a class attribute `supports_sandbox: bool` (default
`False` on `BaseIntegration`). Integrations that support sandboxing build their
argv from the two modes below.

| Runtime | `supports_sandbox` | `sandbox_root` unset (full access) | `sandbox_root` set (confined to cwd + root) |
|---------|:---:|---|---|
| `copilot` | `True` | `-p <prompt> --autopilot --allow-all-paths --experimental` | `-p <prompt> --autopilot --add-dir <root> --experimental` |
| `claude-code` | `True` | `--dangerously-skip-permissions` (as today) | `--permission-mode acceptEdits --add-dir <root>` *(exact flag confirmed at impl)* |
| `codex` | `True` | `exec --yolo <prompt>` (as today) | `exec --sandbox workspace-write` + writable-root config for `<root>` *(exact flag confirmed at impl)* |
| `gemini`, `goose`, `aider`, `opencode`, `pi` | `False` (for now) | run as today | run as today; UI warns setting has no effect |
| `script` | `False` | user's command template | user's command template; setting ignored |
| `sdk` | `False` (no execution) | n/a | n/a |

Notes:

- **Copilot** flags are confirmed against the installed CLI: `--autopilot` (mode)
  and `--allow-all-paths` / `--add-dir` (path axis) are orthogonal and combine.
  `--allow-all-paths` disables path verification; `--add-dir <dir>` keeps
  verification on and adds `dir` to the allow-list (repeatable). Autopilot is
  constant across both modes so unattended dispatch never blocks on prompts.
- **claude-code** and **codex** currently launch in bypass mode. Their exact
  "confine to dir" flags (`--permission-mode`, `--add-dir`; `--sandbox
  workspace-write` + writable roots) must be **verified against the installed
  CLI version during implementation** before wiring. If a confine flag can't be
  verified, that integration ships with `supports_sandbox = False` and is
  treated as "warn, no effect" until confirmed — we do not guess flags.
- The **default-policy** runtimes (`gemini`, `goose`, `aider`, `opencode`, `pi`)
  start with `supports_sandbox = False`. When one later gains a verified
  allow-dir flag, its integration reads the same `sandbox_root` value and
  translates it — no interface change needed.

## Integration API Change

`BaseIntegration.run` gains an optional keyword-only argument:

```python
class BaseIntegration:
    supports_sandbox: bool = False

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int,
            *, sandbox_root: Path | None = None) -> RunResult:
        raise NotImplementedError
```

- Keyword-only + defaulted to `None` so **every existing `run()` override and
  call site keeps working** without modification.
- Each integration's `run()` signature is updated to accept `*, sandbox_root=None`.
  Non-sandbox integrations simply ignore it. Only copilot / claude-code / codex
  branch on it.
- `supports_sandbox = True` is set on `CopilotIntegration`,
  `ClaudeCodeIntegration`, and `CodexIntegration` (the latter two only once
  their confine flags are verified).

## Plumbing (Call Sites)

There are exactly two places that call `integration.run(...)`. Both already have
the group config dict `g` in scope, so both resolve `get_sandbox_root(g)` and
pass it through.

1. **Dispatch** — `agency/dispatch/run.py`. The per-group loop has `g`
   available. Resolve `sandbox_root = get_sandbox_root(g)` in the loop and thread
   it into the helper that ultimately calls `integration.run(...)`
   (`_run_agent`), passing `sandbox_root=sandbox_root`.

2. **Decision execution** — `agency/app.py`, `execute_decision(...)`. It already
   looks up `g = GROUPS.get(group_key, {})`. Resolve `sandbox_root =
   get_sandbox_root(g)` (note: `get_sandbox_root` must tolerate the partial
   `grp` dict — it only needs `path` and `sandbox_root`, so pass the real group
   config or ensure the field is carried) and pass it to
   `agent_integration.run(agent_dir, prompt_file, timeout=timeout,
   sandbox_root=sandbox_root)`.

The Copilot `prompt()` method (Agency's own AI backbone, not an agent run) is
**not** changed — it keeps its current flags and is unaffected by `sandbox_root`.

## Admin UI

In `agency/templates/admin_org_edit.html`, add a **"Sandbox root"** text input to
the org edit/create form (near the group `path` field):

- Label: **Sandbox root** (optional).
- Helper text: *"Directory the agent may read and write at runtime — e.g. the
  repository root. Leave empty to give agents full filesystem access. Does not
  affect the dashboard's file browsers."*
- If the group's `default_integration` has `supports_sandbox == False`, show an
  inline warning: *"The `<integration>` runtime does not support sandboxing —
  this setting will have no effect for it."*

Wiring in `app.py`:

- `admin_org_edit` (GET): pass `sandbox_root` (current value, `""` if unset) and
  a `default_integration_supports_sandbox` bool into the template context.
- `admin_org_save` (POST) and `admin_org_create` (POST): read the
  `sandbox_root` form field; if non-empty, write `config["groups"][org]["sandbox_root"]`;
  if empty, remove the key. Persist via `save_config` + `reload_groups()`
  (atomic write pattern already in place).

## Security

- `sandbox_root` is **opt-in and empty by default**. Empty means full-access
  agents — which is the current behavior for claude-code/codex and (newly) for
  copilot. Setting it is the way to *confine* an agent.
- At launch, resolve the root and only pass the confine flag if the directory
  **exists**; if it's set but missing, log a warning and fall back to full-access
  (do not crash the run). *(Confirm this fallback vs. hard-fail during planning —
  leaning "warn + full access" so a typo doesn't silently over-confine and break
  every routine.)*
- The web-UI boundary (`get_allowed_roots`) is untouched, so a wide
  `sandbox_root` does not expose the repo through the dashboard.
- No new path-traversal surface: `sandbox_root` is an admin-entered config value,
  not user request input.

## Testing

- `get_sandbox_root(g)`: absolute passthrough; relative resolved against group
  `path`; missing/empty/whitespace → `None`.
- `CopilotIntegration.run`: asserts argv contains `--allow-all-paths` (and not
  `--add-dir`) when `sandbox_root is None`; contains `--add-dir <root>` (and not
  `--allow-all-paths`) when set. `--autopilot` present in both. Follow the argv
  assertion pattern in `tests/test_integration_sidecar.py` (mock
  `subprocess.run`).
- `supports_sandbox` flags: `True` for copilot (and claude-code/codex once
  wired), `False` otherwise.
- Non-sandbox integrations accept the `sandbox_root` kwarg without error.
- Config round-trip: saving an org with a sandbox root persists it; clearing it
  removes the key.
- Plumbing: dispatch and `execute_decision` pass the resolved `sandbox_root`
  into `run()` (assert via a stub integration capturing the kwarg).

## Open Items to Confirm During Implementation

1. Exact claude-code confine flags (`--permission-mode` value, `--add-dir`
   support) against the installed CLI.
2. Exact codex confine invocation (`--sandbox workspace-write` + how to specify
   an extra writable root) against the installed CLI.
3. Missing-`sandbox_root`-directory behavior: warn + full access (proposed) vs.
   hard-fail.
