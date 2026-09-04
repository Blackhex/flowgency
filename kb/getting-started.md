# Getting Started

## Install

```text
git clone https://github.com/christag/agency.git
cd agency
python -m pip install -e .
christag-agency serve
```

Open `http://127.0.0.1:8500`.

## First run

Start Agency, choose the Agency data root and supported AI integration, complete the agency-setup conversation, and return to the dashboard automatically. The launcher safely creates a missing root, attaches the bundled skill, and the guided conversation asks for the project workspace as its first question. The Agency Setup Skill owns team naming, storage paths, blueprint source, instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write.

On first run, open `/setup` and choose the data root and supported integration to launch `agency-setup`. After setup, create reusable blueprints, Agent Skills, and shared prompts in Agent Library. Open the team's Agents page to add explicit instances that select a blueprint and integration. Configure identity, private prompts, runtime overrides, routines, and semantic memory from Agent Detail.

## Core concepts

### Blueprints and instances

A blueprint is reusable standard source in the global Agent Library: one `AGENTS.md`, optional Agent Skills, and optional shared prompts. An instance belongs to one team and stores its stable name, blueprint, integration, display identity, capability, runtime overrides, registered private prompts, routines, and default memory selector in config.

Runtime projectors compile blueprint and prompt source into disposable native layouts for each integration. Do not edit the compilation cache or projected native prompt files.

### Teams and settings

A team owns a project workspace, runtime defaults, dispatch limits, workspaces, and explicit instances. Team Settings changes defaults only. The Agents page owns the roster; Agent Detail exposes `Profile/Blueprint/Runtime/Routines/Prompts/Memory/Activity`.

`workspace_path` is the execution workspace and source repository. `path` is the Agency-owned team root, which is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`. Durable jobs live in `agency.memory_store/.jobs`; operation locks live in `<team.path>/locks`.

### Routines, jobs, and memory

A routine selects one saved prompt, schedule, optional arguments, and optional semantic memory. Routine and decision submissions create durable jobs. The roster manual launcher can run saved prompts or one-off tasks. Memory uses selectors such as `scope: routine`, `scope: agent`, or `scope: channel`; Memory Channels define named cross-instance memory.

### Pipeline

Agency links observations to proposals, human decisions, durable execution jobs, and verification. Proposal execution requires an explicit instance whose integration supports execution and whose runtime permissions grant write access.

## Development reload

```text
christag-agency serve --reload
```

Reload watches application code, templates, static assets, themes, and control-plane configuration. Runtime records under group workspaces do not trigger reload.

## Next steps

- Read [Configuration](configuration.md) for the current config schema.
- Read [Directory Structure](directory-structure.md) before choosing global paths.
- Use [Agency Setup Skill](setup-skill.md) to propose blueprints and explicit instances.
- Use [Dispatch and Routines](dispatch.md) to install the singleton scheduler.

## Superseded layouts

If an existing install depends on physical agent definitions, prompt schedules, or file-based memory, rewrite it into the current config shape before starting Agency.
