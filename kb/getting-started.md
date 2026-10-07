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

Start Flowgency, choose the Flowgency data root and supported AI integration, and complete the flowgency-setup conversation. The launcher safely creates a missing root, attaches the bundled skill, and the guided conversation asks for the project workspace as its first question. The Flowgency Setup Skill owns team naming, storage paths, blueprint source, instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write.

Open setup from a browser on the same computer as Flowgency. On supported
POSIX hosts, GitHub Copilot setup runs in the page's terminal; refresh or
reopen the page to reconnect. A ready configuration alone does not finish
setup: Flowgency opens the dashboard only after the guided session explicitly
reports completion, meaning every question is answered and the summary is
delivered. Unrelated process exit, silence, terminal text, Stop, or a crash
never count as completion. Choosing not to install the scheduler still
completes setup. A failed or unknown scheduler result completes only after the
limitation is acknowledged. Enabling dispatch in the saved configuration is
separate from an observed scheduler installation, and setup reports the two
independently.

Automatic return also requires the completion environment supplied by
Flowgency. If an external terminal cannot receive it, or you run a copyable
fallback command manually, finish the conversation and open the dashboard
yourself. The displayed command never includes completion credentials.

The installed Copilot CLI offers no verified way to exit automatically, so the
terminal may stay open after setup completes. This is the explicit fallback and
the current measured behavior. Use the dashboard's View terminal link to open
the terminal in an inspection view that never redirects, or Stop to end the
session.
The setup CLI has the server user's access to files and tools; configured
agent sandbox permissions do not apply before the first team exists. On
native Windows, Copilot setup runs in this browser's terminal when the
contained ConPTY backend is available. If it cannot prove process cleanup,
Stop blocks another launch; a separate console with a copyable fallback
command is offered only after safe cleanup. On POSIX, use the external
terminal if the PTY cannot start. Closing the tab does not Stop the
server-owned session, but a server restart ends its in-memory connection and
transcript.
This release's native Windows checks do not qualify POSIX connected mode;
validate it on a native POSIX host before relying on it in production.

On first run, open `/setup` and choose the data root and supported integration to launch `flowgency-setup`. After setup, create reusable blueprints, Agent Skills, and shared prompts in Agent Library. Open the team's Agents page to add explicit instances that select a blueprint and integration. Configure identity, private prompts, runtime overrides, routines, and semantic memory from Agent Detail.

## Core concepts

### Blueprints and instances

A blueprint is reusable standard source in the global Agent Library: one `AGENTS.md`, optional Agent Skills, and optional shared prompts. An instance belongs to one team and stores its stable name, blueprint, integration, display identity, capability, runtime overrides, registered private prompts, routines, and default memory selector in config.

Runtime projectors compile blueprint and prompt source into disposable native layouts for each integration. Do not edit the compilation cache or projected native prompt files.

### Teams and settings

A team owns a project workspace, runtime defaults, dispatch limits, workspaces, and explicit instances. Team Settings changes defaults only. The Agents page owns the roster; Agent Detail exposes `Profile/Blueprint/Runtime/Routines/Prompts/Memory/Activity`.

`workspace_path` is the execution workspace and source repository. `path` is the Flowgency-owned team root, which is automatically available to restricted agents. Flowgency never loads or creates `<workspace_path>/shared`. Durable jobs live in `flowgency.memory_store/.jobs`; operation locks live in `<team.path>/locks`.

### Routines, jobs, and memory

A routine selects one saved prompt, schedule, optional arguments, and optional semantic memory. Routine submissions and ticket runs create durable jobs. The roster manual launcher can run saved prompts or one-off tasks. Memory uses selectors such as `scope: routine`, `scope: agent`, or `scope: channel`; Memory Channels define named cross-instance memory.

### Ticket workflows

Work is tracked as tickets that move through a configurable workflow. A reusable blueprint in `flowgency.workflow_library` defines states, a field catalog, and transitions; each transition declares required inputs and optional agent criteria. A team attaches named workflow instances that bind a blueprint to a Local ticket storage root. Agents discover open tickets, claim one, and advance it by executing a transition through the live ticket tools, supplying required field values and a per-criterion assessment. Only agents move tickets between states; assignment is persistent ownership and sign-off is optional. Read-only agents can transition tickets without workspace write access. For restricted Copilot agents, ticket runs use a worker-owned authenticated loopback MCP HTTP endpoint and require explicit per-agent `integration_config.allow_local_network: true`; otherwise Flowgency stops the run before launch with `ticket-local-network-required`. Inspect boards and tickets from the CLI with `flowgency workflows`, `flowgency tickets`, and `flowgency ticket show|create|assign|unassign|run`.

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
