# Getting Started

## Install

```text
git clone https://github.com/Blackhex/flowgency.git
cd flowgency
python -m pip install -e .
flowgency serve
```

Open `http://127.0.0.1:8500`. Set `FLOWGENCY_CONFIG` to select the one authoritative config file.

## First run

Start Flowgency, choose the Flowgency data root and supported AI integration, complete the flowgency-setup conversation, and return to the dashboard automatically. The launcher safely creates a missing root, attaches the bundled skill, and the guided conversation asks for the project workspace as its first question. The Flowgency Setup Skill owns team naming, storage paths, blueprint source, instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write.

On first run, open `/setup` and choose the data root and supported integration to launch `flowgency-setup`. After setup, create reusable blueprints, Agent Skills, and shared prompts in Agent Library. Open the team's Agents page to add explicit instances that select a blueprint and integration. Configure identity, private prompts, runtime overrides, routines, and semantic memory from Agent Detail.

## Core concepts

### Blueprints and instances

A blueprint is reusable standard source in the global Agent Library: one `AGENTS.md`, optional Agent Skills, and optional shared prompts. An instance belongs to one team and stores its stable name, blueprint, integration, display identity, capability, runtime overrides, registered private prompts, routines, and default memory selector in config.

Runtime projectors compile blueprint and prompt source into disposable native layouts for each integration. Do not edit the compilation cache or projected native prompt files.

### Teams and settings

A team owns a project workspace, runtime defaults, dispatch limits, workspaces, and explicit instances. Team Settings changes defaults only. The Agents page owns the roster; Agent Detail exposes `Profile/Blueprint/Runtime/Routines/Prompts/Memory/Activity`.

`workspace_path` is the execution workspace and source repository. `path` is the Flowgency-owned team root, which is automatically available to restricted agents. Flowgency never loads or creates `<workspace_path>/shared`. Durable jobs live in `flowgency.memory_store/.jobs`; operation locks live in `<team.path>/locks`.

### Routines, jobs, and memory

A routine selects one saved prompt, schedule, optional arguments, and optional semantic memory. Routine submissions, ticket runs, and decision submissions create durable jobs. The roster manual launcher can run saved prompts or one-off tasks. Memory uses selectors such as `scope: routine`, `scope: agent`, or `scope: channel`; Memory Channels define named cross-instance memory.

### Ticket workflows

Work is tracked as tickets that move through a configurable workflow. A reusable blueprint in `flowgency.workflow_library` defines states, a field catalog, and transitions; each transition declares required inputs and optional agent criteria. A team attaches named workflow instances that bind a blueprint to a Local ticket storage root. Agents discover open tickets, claim one, and advance it by executing a transition through the live ticket tools, supplying required field values and a per-criterion assessment. Only agents move tickets between states; assignment is persistent ownership and sign-off is optional. Read-only agents can transition tickets without workspace write access. Inspect boards and tickets from the CLI with `flowgency workflows`, `flowgency tickets`, and `flowgency ticket show|create|assign|unassign|run`.

## Development reload

```text
flowgency serve --reload
```

Reload watches application code, templates, static assets, themes, and control-plane configuration. Runtime records under group workspaces do not trigger reload.

## Next steps

- Read [Configuration](configuration.md) for the current config schema.
- Read [Directory Structure](directory-structure.md) before choosing global paths.
- Read [Data Formats](data-formats.md) for the ticket workflow contract.
- Use [Flowgency Setup Skill](setup-skill.md) to propose blueprints and explicit instances.
- Use [Dispatch and Routines](dispatch.md) to install the singleton scheduler.

## Superseded layouts

If an existing install depends on physical agent definitions, prompt schedules, or file-based memory, rewrite it into the current config shape before starting Flowgency.
