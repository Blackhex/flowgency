# GitHub Copilot Integration — Design

**Date:** 2026-07-03
**Status:** Approved

## Summary

Add a new integration that lets Agency manage agents powered by the GitHub
Copilot CLI. The integration follows the existing sidecar-based plugin pattern
(as used by `codex`): `AGENTS.md` as the identity file plus an
`.agency-meta.yaml` sidecar for display metadata. Copilot can both run/dispatch
agents and serve as Agency's own AI backbone.

## Model Rationale

GitHub Copilot's native model is: repo-wide instructions in
`.github/copilot-instructions.md` at the **repository root**, and custom agents
as flat files `.github/agents/<name>.agent.md` invoked via `copilot --agent
<name>`. There is no native concept of a per-agent directory with its own
`memory.md`.

Agency's model is directory-per-agent. Rather than reshape Agency to Copilot's
flat-file model, this integration keeps Agency's convention — exactly as Agency
already ignores Claude Code's native `.claude/agents/` flat files in favor of
directory-per-agent. Each Agency agent is an isolated directory; Copilot runs
with `cwd=agent_dir` and reads that directory's `AGENTS.md`. Copilot's native
`--agent` / `.github/agents/` feature is intentionally unused, consistent with
every other integration.

## Motivation

Agency already supports Claude Code, Codex, Gemini, Aider, Goose, OpenCode, and
Pi. GitHub Copilot now ships a non-interactive CLI (`copilot -p`), making it a
viable execution engine for autonomous agent dispatch. Adding it lets users run
Copilot-backed agents alongside the rest of their fleet.

## Design

### New file: `agency/integrations/agency/copilot.py`

A `CopilotIntegration(BaseIntegration)` class mirroring the sidecar pattern in
`codex.py` / `opencode.py`.

**Class attributes**

| Attribute | Value |
|-----------|-------|
| `name` | `"copilot"` |
| `display_name` | `"GitHub Copilot"` |
| `supports_execution` | `True` |
| `supports_ai_backend` | `True` |
| `detect_priority` | `7` (lower than `opencode`/`pi` at 8 and `codex` at 10, so Copilot wins when its marker is present) |

### Detection

Copilot shares the `AGENTS.md` identity format with `codex`/`opencode`/`pi`, so
it must NOT detect on `AGENTS.md` presence (that is `codex`'s catch-all signal).
Instead, `detect()` keys off a Copilot-specific marker directory:

```python
def detect(self, agent_dir: Path) -> bool:
    return (agent_dir / ".copilot").is_dir() or (agent_dir / ".github").is_dir()
```

This mirrors how `opencode` detects on `.opencode/` and `pi` on `.pi/` while
both use `AGENTS.md` as their format. With `detect_priority = 7`, Copilot wins
whenever its marker is present; a bare `AGENTS.md` folder still falls through to
`codex` (priority 10).

**Resolution outcomes:**
- `AGENTS.md` only → `codex`
- `AGENTS.md` + `.copilot/` or `.github/` → `copilot`

### Identity

The identity file is `AGENTS.md` — plain markdown, with `display_name` /
`title` / `emoji` stored in the `.agency-meta.yaml` sidecar. This is identical
to `codex`'s identity handling.

- `identity_filename()` → `"AGENTS.md"`
- `parse_identity()` → `self._parse_sidecar_identity(agent_dir, agent_dir / "AGENTS.md")`
- `write_identity()` → `self._write_sidecar_identity(agent_dir, agent_dir / "AGENTS.md", identity)`

### Execution — `run()`

```
copilot -p "<prompt>" --autopilot --experimental
```

Run with `cwd=agent_dir`, capturing stdout/stderr. Standard error handling:
timeout → `RunResult(exit_code=124, ...)`; `FileNotFoundError` →
`IntegrationError`. The prompt text is read from `prompt_file`.

### AI backbone — `prompt()`

```
copilot -p "<text>" --autopilot --experimental
```

A one-shot query with no working directory. Returns stdout; raises
`IntegrationError` on non-zero exit, missing CLI, or timeout. This makes Copilot
selectable as `agency.ai_backend` in `/admin/settings`.

The only difference between `run()` and `prompt()` is that `run()` executes in
the agent's directory while `prompt()` does not.

### CLI resolution

```python
def _find_cmd(self) -> str:
    return self._resolve_cmd("copilot")
```

Uses the inherited `_resolve_cmd` so systemd services with a minimal PATH still
locate the binary.

## Wiring

1. **`agency/integrations/__init__.py`** — add `"agency.copilot"` to the default
   module list in `load_integrations()`.
2. **`agency/integrations/integrations.yaml`** — add `"agency.copilot"` to the
   registered integrations list.
3. **`agency/app.py`** — add a badge color for `copilot` in
   `integration_badge_filter` (e.g. `"copilot": "bg-slate-100 text-slate-800"`).

## Testing

- Extend `tests/test_integration_sidecar.py` with `copilot` cases:
  - `detect()` true when `.copilot/` exists, true when `.github/` exists,
    false when neither exists (bare `AGENTS.md` folder)
  - identity round-trip (body → `AGENTS.md`, metadata → `.agency-meta.yaml`)
  - `run()` builds `copilot -p <prompt> --autopilot --experimental` with
    `cwd=agent_dir` (mock `subprocess.run`)
  - `prompt()` builds `copilot -p <text> --autopilot --experimental` and returns
    stdout (mock `subprocess.run`)
- The existing contract tests (`tests/test_integration_contract.py`) will
  automatically cover the new integration once it is registered.

## Documentation

- Add a `copilot` row to the "Shipped Integrations" table in `CLAUDE.md`:

  | Integration | Native File | Detect Signal | Execution | AI Backend |
  |-------------|------------|---------------|-----------|------------|
  | `copilot` | `AGENTS.md` | `.copilot/` or `.github/` dir exists | `copilot -p --autopilot --experimental` | Yes |

- Update `README.md` and `kb/integrations.md` if they enumerate integrations.

## Out of Scope

- Interactive Copilot sessions.
- Copilot-specific configuration beyond the standard `integration_config`.
- Any changes to the dispatch scheduler or workspace system.
