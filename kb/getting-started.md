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

The setup wizard creates a strict-canonical control plane. Choose the authoritative config path and provide the global Agent Library, compilation cache, and memory store paths. Add a group with a display name and project workspace path. The wizard writes `schema_version: 2`; it does not scan folders, create physical agent directories, or infer instances.

Next, create reusable blueprints and Agent Skills in Agent Library. Open the group's Agents page to add explicit instances that select a blueprint and integration. Configure identity, runtime overrides, routines, and semantic memory from Agent Detail.

## Core concepts

### Blueprints and instances

A blueprint is reusable standard source in the global Agent Library: one `AGENTS.md` plus optional Agent Skills. An instance belongs to one group and stores its stable name, blueprint, integration, display identity, capability, runtime overrides, routines, and default memory selector in config.

Runtime projectors compile blueprint source into disposable native layouts for each integration. Do not edit the compilation cache.

### Groups and settings

A group owns a project workspace, runtime defaults, dispatch limits, workspaces, and explicit instances. Group Settings changes defaults only. The Agents page owns the roster; Agent Detail exposes `Profile/Blueprint/Runtime/Routines/Memory/Activity`.

### Routines, jobs, and memory

A routine selects one Agent Skill, schedule, optional arguments, and optional semantic memory. Routine and decision submissions create durable jobs. Memory uses selectors such as `scope: routine`, `scope: agent`, or `scope: channel`; Memory Channels define named cross-instance memory.

### Pipeline

Agency links observations to proposals, human decisions, durable execution jobs, and verification. Proposal execution requires an explicit instance whose integration supports execution and whose `capabilities.write` is true.

## Development reload

```text
christag-agency serve --reload
```

Reload watches application code, templates, static assets, themes, and control-plane configuration. Runtime records under group workspaces do not trigger reload.

## Next steps

- Read [Configuration](configuration.md) for the strict-canonical schema.
- Read [Directory Structure](directory-structure.md) before choosing global paths.
- Use [Agency Setup Skill](setup-skill.md) to propose blueprints and explicit instances.
- Use [Dispatch and Routines](dispatch.md) to install the singleton scheduler.

## superseded v1 migration

If an existing install has physical agent definitions, prompt schedules, or file-based memory, stop and invoke `agency-migration`. Runtime does not import that layout.

```text
python tools/migrate_agent_model.py preview --config config.yaml --plan migration-plan.yaml
python tools/migrate_agent_model.py apply --plan migration-plan.yaml
python tools/migrate_agent_model.py verify --config config.yaml
python tools/migrate_agent_model.py rollback --plan migration-plan.yaml
```