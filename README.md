# Flowgency

**Ticket-driven orchestration for teams of AI agents.**

See agents take work, move tickets through predefined workflows, and keep every
transition inspectable from one local-first control plane.

![Flowgency workflow board overview with synthetic fixture data](screenshots/flowgency-board.png)

Fixture-backed board overview using synthetic data from the verified UI snapshot.

## How tickets flow

Agents discover open tickets, claim them, and advance them through workflow transitions using live ticket tools. Each transition records field inputs, optional agent assessments, and the criteria that were met. The board shows every ticket's current state as agents take and complete work; every transition is recorded and revisitable from the dashboard. Workflows are configurable: define states, fields, and transition criteria in a reusable blueprint, then attach named instances to teams with a Local ticket storage root. For Copilot, live ticket tools run through a worker-owned authenticated loopback MCP HTTP endpoint. Restricted ticket runs keep local-network access off by default and require an explicit per-agent `integration_config.allow_local_network: true` opt-in.

Read-only agents can execute ticket transitions without filesystem write access. Only agents may move tickets between states; users view and triage from the dashboard but do not advance the workflow directly.

## Supporting capabilities

- **Reusable blueprints** — Agent Library holds standard `AGENTS.md` and Agent
  Skills, separated from configured identity.
- **Explicit instances** — Each instance belongs to one team and pins one
  blueprint, one integration, and one permission policy.
- **Scheduled routines** — Each routine selects one saved prompt, one schedule,
  and optional semantic memory.
- **Semantic memory** — Selectors for run, routine, agent, team, or declared
  channel scope keep context focused without manual file management.
- **Local-first** — No cloud account required. The dashboard and all data live on
  your machine.

## Quick start

Flowgency requires Python 3.11 or newer.

```text
git clone https://github.com/Blackhex/flowgency.git
cd flowgency
python -m pip install -e .
flowgency serve
```

The dashboard opens at `http://127.0.0.1:8500`. Set `FLOWGENCY_CONFIG` to select
the one authoritative config file.

Start Flowgency, choose the Flowgency data root and supported AI integration, complete the flowgency-setup conversation, and return to the dashboard automatically.
Users may enter home syntax such as `~/Flowgency`; setup expands it to the
user's home directory before deriving canonical paths. The guided conversation asks
for the project workspace as its first question, then names your team and
proposes agent blueprints and instances. Setup proposes useful routines and
recommended schedules; approve them, request changes, or explicitly choose
manual-only operation. It writes one validated `config.yaml` with no individual
storage-path questions. If you approve restricted Copilot ticket workflows,
setup must ask separately before writing `integration_config.allow_local_network:
true`; declining leaves the value absent or `false`, and such runs fail
preflight with `ticket-local-network-required` rather than widening policy.

## Configuration

Flowgency uses one authoritative YAML document. The top-level `schema_version: 1`
and `flowgency` root are required:

```yaml
schema_version: 1
flowgency:
  title: My Project
  default_team: my-project
  agent_library: C:/Flowgency/agent-library
  compilation_cache: C:/Flowgency/compiled-agents
  memory_store: C:/Flowgency/memory
  prompt_store: C:/Flowgency/prompts
  workflow_library: C:/Flowgency/workflow-library
teams:
  my-project:
    name: My Project
    workspace_path: C:/Projects/my-project
    path: C:/Flowgency/teams/my-project
    default_integration: copilot
```

See [config.yaml.example](config.yaml.example) and [Configuration](kb/configuration.md)
for the complete reference. The [Flowgency Setup Skill](kb/setup-skill.md) generates
a validated config from a guided conversation.

## Scheduling

Setup proposes routines and schedules for approval. Schedule approval does not
enable automatic execution: setup separately asks whether to enable dispatch
before saving the configuration. You can keep approved routines inactive or
choose manual-only operation.

After approving activation, install the singleton dispatcher to run configured
routines on a platform timer. Installing the scheduler does not create routines.
Dispatch enablement and the scheduler's installed or running status are separate:

```text
flowgency dispatch install --config C:/Flowgency/config.yaml
flowgency dispatch status --config C:/Flowgency/config.yaml
```

## Integrations

Flowgency supports multiple AI runtimes via pluggable integrations. Each instance
pins one integration explicitly. See [Integrations](kb/integrations.md) for
supported runtimes and [Contributing Integrations](kb/contributing-integrations.md)
to add one.

## Local-first operation

Flowgency assumes trusted local access. The dashboard has no built-in user authentication.
The worker-owned authenticated loopback MCP HTTP endpoint used for Copilot ticket tools is
separate, private to the job, and not a public dashboard login surface. The dashboard,
config, blueprints, memory, and all records live on your local filesystem. Use a reverse
proxy (Traefik, nginx, Caddy) if you need access controls for the dashboard.

## Documentation

- [Getting Started](kb/getting-started.md)
- [Configuration](kb/configuration.md)
- [Directory Structure](kb/directory-structure.md)
- [Agent Identity](kb/agent-identity.md)
- [Integrations](kb/integrations.md)
- [Dispatch and Routines](kb/dispatch.md)
- [Data Formats](kb/data-formats.md)
- [Deployment](kb/deployment.md)
- [Flowgency Setup Skill](kb/setup-skill.md)
- [Contributing Integrations](kb/contributing-integrations.md)

## Development

```text
python -m pytest tests/ -q
```

Install test dependencies (pytest, httpx, Pillow) with:

```text
python -m pip install -e '.[test]'
```

Flowgency uses Python, FastAPI, Jinja2, and filesystem-backed YAML and Markdown.

### Rebuilding CSS

The dashboard's Tailwind CSS is compiled ahead of time into
[flowgency/static/tailwind.css](flowgency/static/tailwind.css) and shipped in
the wheel; startup and packaged installs never fetch Tailwind from a CDN or
require Node.js. After changing a template, an application source file that
produces utility classes, or [tailwind.config.cjs](tailwind.config.cjs), rebuild
the stylesheet:

```text
npm install
npm run build:css
```

This runs Tailwind 3 against [tools/tailwind.css](tools/tailwind.css) and the
content sources declared in `tailwind.config.cjs`, writing the minified result
to `flowgency/static/tailwind.css`. Commit the regenerated file; the build is
deterministic given unchanged inputs.

## Contributing

See [AGENTS.md](AGENTS.md) for the repository guide, development workflow, and
commit conventions. Contributions follow the development workflow described there:
feature branches in `.worktrees/`, conventional commits, and a full test suite
before integration.

## License

[AGPL-3.0](LICENSE)
