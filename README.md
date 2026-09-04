# Agency

Agency is a FastAPI control plane for reusable AI agent blueprints, team-owned agent instances, scheduled routines, semantic memory, durable jobs, and an observation-to-decision pipeline. It supports multiple LLM runtimes without making native project layouts authoritative.

## Install

Agency requires Python 3.11 or newer.

```text
pip install -e .
python -m agency.app
```

The dashboard listens on `http://127.0.0.1:8500` by default. Set `AGENCY_CONFIG` to select the one authoritative config.

## Current configuration model

Agency accepts one current `config.yaml` shape headed by `schema_version: 6`. `config.yaml` owns teams, explicit instances, runtime policy, routines, integration selection, identity, and semantic memory selectors. See [config.yaml.example](config.yaml.example).

Global paths separate reusable and mutable data:

- `agency.agent_library` contains standard blueprints.
- `agency.compilation_cache` contains disposable immutable runtime projections.
- `agency.memory_store` contains semantic mutable Markdown memory.
- `agency.prompt_store` contains canonical prompt files referenced by config.

Each team separates its source repository from Agency-owned state:

- `workspace_path` is the execution workspace and source repository.
- `path` is the Agency-owned team root.
- The team root is automatically available to restricted agents.
- Durable jobs live in `agency.memory_store/.jobs`.
- Operation locks live in `<team.path>/locks`.
- Agency never loads or creates `<workspace_path>/shared`.

Each immediate Agent Library child is a blueprint with `AGENTS.md` and optional Agent Skills under `.agents/skills/<skill>/SKILL.md`. An instance belongs to one team and explicitly selects one blueprint and integration. Runtime projectors create disposable native layouts without changing source bytes.

Runtime policy is a `permissions` block with a `mode` (`restricted` or `unrestricted`) and a `rules` list. Each rule binds a set of tools to a path. Instance rules are additive to team rules; the longest matching path governs. Agency adds generated rules for the launch view so the agent can read its instructions but cannot rewrite them.

Routines select prompt-backed execution. Each routine has a stable ID, selects one scoped prompt, defines one schedule, and may use semantic memory selectors with `run`, `routine`, `agent`, `team`, or declared `channel` scope.

The roster manual launcher uses the same effective prompt authority: users can run a saved blueprint or instance prompt from the catalog, or switch to a one-off task without changing config. Native integration prompt files remain generated output only.

## Product surfaces

- The Agents page lists team-owned instances.
- Agent Detail provides `Profile/Blueprint/Runtime/Routines/Prompts/Memory/Activity`. Profile identity is the config display name, title, and emoji.
- Agent Library manages standard `AGENTS.md` blueprint source and Agent Skills.
- Memory Channels and semantic selectors own mutable memory.
- Routines submit durable jobs; Jobs shows queued, waiting, running, completed, failed, and cancelled work.
- Team Settings manages defaults only. It does not discover folders, initialize physical agents, or own instance CRUD.
- Observations, proposals, decisions, logs, locks, and workspaces remain team-scoped.

Workspace launchers are optional frontends. They start configured instances in the team workspace and do not own configuration or source.

## Quick start

Start Agency, choose the Agency data root and supported AI integration, complete the agency-setup conversation, and return to the dashboard automatically. The launcher safely creates a missing root, attaches the bundled skill, and the guided conversation asks for the project workspace as its first question. The [Agency Setup Skill](kb/setup-skill.md) then owns team naming, blueprint source, instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write.

On first run, open `/setup` and choose the data root and supported integration to launch `agency-setup`. Users may enter home syntax such as `~/Agency`; setup expands it before deriving `agent-library`, `compiled-agents`, `memory`, `prompts`, and `teams/<team-id>` beneath the approved root. Advanced users can opt into one grouped path review; the default flow asks no individual storage-path questions.

## Pipeline and execution

Agents surface observations, converge them into proposals, and wait for human decisions. Approved decisions and scheduled routines become durable jobs. Every proposal names an explicit writable execution instance, and every job snapshots its blueprint, selected prompt source, runtime policy, task input, and memory selector before launch.

Agency installs one user-level platform scheduler for all teams:

```text
christag-agency dispatch install --config C:/Agency/config.yaml
christag-agency dispatch status --config C:/Agency/config.yaml
```

## Superseded layout cleanup

Runtime never parses or rewrites directory-coupled or sidecar-based authority. Older installations must be rewritten into the current config shape before Agency can load them.

Files such as native identity sidecars, prompt directories, physical memory files, `dispatch.agents`, or `tmux_config` are not consulted by runtime.

## Documentation

- [Getting Started](kb/getting-started.md)
- [Configuration](kb/configuration.md)
- [Directory Structure](kb/directory-structure.md)
- [Agent Identity](kb/agent-identity.md)
- [Integrations](kb/integrations.md)
- [Dispatch and Routines](kb/dispatch.md)
- [Data Formats](kb/data-formats.md)
- [Deployment](kb/deployment.md)
- [Agency Setup Skill](kb/setup-skill.md)
- [Contributing Integrations](kb/contributing-integrations.md)

## Development

```text
.venv/Scripts/python -m pytest tests/ -q
```

Agency uses Python, FastAPI, Jinja2, and filesystem-backed YAML and Markdown. See [LICENSE](LICENSE) for AGPL-3.0 terms.
