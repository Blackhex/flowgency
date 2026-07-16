# Agency

Agency is a FastAPI control plane for reusable AI agent blueprints, group-owned agent instances, scheduled routines, semantic memory, durable jobs, and an observation-to-decision pipeline. It supports multiple LLM runtimes without making native project layouts authoritative.

## Install

Agency requires Python 3.11 or newer.

```text
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m agency.app
```

On POSIX, use `.venv/bin/python`. The dashboard listens on `http://127.0.0.1:8500` by default. Set `AGENCY_CONFIG` to select the one authoritative config.

## Strict canonical model

Agency accepts only `schema_version: 2`. `config.yaml` owns groups, explicit instances, runtime policy, routines, integration selection, identity, capabilities, and semantic memory selectors. See [config.yaml.example](config.yaml.example).

Global paths separate reusable and mutable data:

- `agency.agent_library` contains standard blueprints.
- `agency.compilation_cache` contains disposable immutable runtime projections.
- `agency.memory_store` contains semantic mutable Markdown memory.

Each immediate Agent Library child is a blueprint with `AGENTS.md` and optional Agent Skills under `.agents/skills/<skill>/SKILL.md`. An instance belongs to one group and explicitly selects one blueprint and integration. Runtime projectors create disposable native layouts without changing source bytes.

Group runtime values are defaults. Instance `runtime.sandbox.additional_roots` are additive to group roots. A present instance tool policy is a complete override and is never merged with the group tool list.

Routines replace prompt files and per-agent schedule maps. Each routine has a stable ID, selects one Agent Skill, defines one schedule, and may use semantic memory selectors with `run`, `routine`, `agent`, `group`, or declared `channel` scope.

## Product surfaces

- The Agents page lists group-owned instances.
- Agent Detail provides `Profile/Blueprint/Runtime/Routines/Memory/Activity`. Profile identity is the config display name, title, and emoji.
- Agent Library manages standard `AGENTS.md` blueprint source and Agent Skills.
- Memory Channels and semantic selectors own mutable memory.
- Routines submit durable jobs; Jobs shows queued, waiting, running, completed, failed, and cancelled work.
- Group Settings manages defaults only. It does not discover folders, initialize physical agents, or own instance CRUD.
- Observations, proposals, decisions, logs, and workspaces remain group-scoped.

Workspace launchers are optional frontends. They start configured instances in the group workspace and do not own configuration or source.

## Quick start

Start Agency and open `http://127.0.0.1:8500`. On first run, the setup wizard creates strict-canonical control-plane paths for the Agent Library, compilation cache, and memory store, plus an explicit group workspace. It does not scan an agent directory or create agent folders.

Create reusable blueprints and Agent Skills in Agent Library, then create explicit instances from the group roster and assign routines and semantic memory. The optional [Agency Setup Skill](kb/setup-skill.md) can perform this workflow after team approval.

## Pipeline and execution

Agents surface observations, converge them into proposals, and wait for human decisions. Approved decisions and scheduled routines become durable jobs. Every proposal names an explicit writable execution instance, and every job snapshots its blueprint, selected skill, runtime policy, task input, and memory selector before launch.

Agency installs one user-level platform scheduler for all groups:

```text
christag-agency dispatch install --config C:/Agency/config.yaml
christag-agency dispatch status --config C:/Agency/config.yaml
```

## superseded v1 migration

Runtime never parses or rewrites superseded authority. Invoke `agency-migration` and review a standalone migration plan before applying it:

```text
python tools/migrate_agent_model.py preview --config config.yaml --plan migration-plan.yaml
python tools/migrate_agent_model.py apply --plan migration-plan.yaml
python tools/migrate_agent_model.py verify --config config.yaml
python tools/migrate_agent_model.py rollback --plan migration-plan.yaml
```

Only migration may inspect old native identity definitions, `.agency-meta.yaml`, prompt directories, physical memory files, `dispatch.agents`, or `tmux_config`. It leaves source directories untouched; strict-canonical runtime does not consult them.

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