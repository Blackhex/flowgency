# Integrations

An integration adapts an explicit configured instance to one LLM runtime. Each instance pins one integration; filesystem contents do not override it.

Integrations declare executable support, enforceable sandbox/tool modes, a versioned runtime projector, native instruction and skill targets, and whether a selected skill can be activated non-interactively. Unsupported policy or activation fails before launch.

Runtime projectors consume standards-based Agent Library source plus prompt snapshots. They may relocate root `AGENTS.md`, whole `.agents/skills` directories, and saved prompts into native discovery paths, but must preserve canonical instruction, `SKILL.md`, and prompt bytes. Compiled artifacts are immutable and keyed by integration, projector version, and source digest.

Team sandbox roots form the baseline; instance `additional_roots` are additive. A present instance tool policy is a complete override. Integrations reject modes or names they cannot enforce rather than widening access.

## What is actually enforced

`copilot` is the only integration that enforces path rules. It writes a per-job `COPILOT_HOME` holding a sandbox filesystem policy, so a rule granting `read` on the workspace and the generated zone grants together mean a read-only agent can read its workspace and write nothing but its own outbox and memory. Enforcement is claimed only against a CLI version it has been measured against; where the version cannot be read the claim is withheld and the rules are rendered as a narrower global tool grant instead.

The policy is an allowlist, so it is switched on only when something is actually confined. An `unrestricted` policy naming no path is not sandboxed, because an empty allowlist would deny everything.

The boundary has real limits, and the job record names them rather than implying they are covered:

- Built-in file edits are policed in-process and cooperatively. Only shell commands are contained by the operating system, and the shell backend is unavailable on this platform, so `shell` is never claimed as path-scopable.
- Credentials carried in the environment are outside a path-based boundary entirely. The agent is given an explicit allowlist of environment variables rather than Flowgency's own environment, but anything on that list is visible to it.
- The Copilot sandbox requests git and `gh` credential injection through its `sandbox.auth.git` and `sandbox.auth.gh` keys, and only for an agent whose policy grants `write` on the workspace root itself, since a filesystem policy cannot stop a push: the write lands on the remote. The measured CLI (`1.0.84-3`) accepts those settings and documents injection only while the sandbox is enabled. That means Flowgency can request no git/gh token injection for a read-only ticket agent when the policy is confined, while an unconfined run leaves the sandbox off and the credentials cannot be scoped by it. The live evidence here is settings acceptance plus documented CLI semantics, not a separate direct git/gh denial probe.
- Where a rule cannot be expressed at all, the run says so on the job record instead of reporting the policy as applied.

The other integrations do not enforce path rules. They declare `unrestricted` only, so `mode: restricted` is rejected before launch; narrow rules written under `unrestricted` are not enforced by them. `claude-code` and `codex` previously disabled their own CLI permission models on every run, which is the only enforcement those integrations have. They now do so only when the policy grants write somewhere. Where the flag is withheld those CLIs may prompt, and such a run may not complete unattended -- the alternative was to keep over-granting silently.

`flowgency/integrations/integrations.yaml` controls which Python plugins are loadable. It is plugin discovery metadata, not team, instance, routine, identity, or memory configuration.

## Ticket tools

Agents report work through live Flowgency ticket tools, not by writing Markdown records into the workspace. The tools let an agent list configured workflows, inspect a ticket's current state, required transition inputs, and agent criteria, claim an unassigned ticket, and execute a transition by supplying required field values and a per-criterion assessment. Only an accepted transition changes ticket state.

Ticket transport is currently supplied by the `copilot` integration only, through a worker-owned authenticated loopback MCP HTTP endpoint. There is no Copilot stdio child bridge. Other integrations fail closed for ticket operations: they cannot execute ticket tools, so a workflow bound to an unsupported integration produces no ticket transport for its agents. A team with no configured workflows still runs; memory-only routines succeed when no ticket reporting is required.

The ticket tools are scoped and do not widen runtime authority. They grant no workspace write, no git or GitHub credentials, and no `shell` access. A read-only agent can inspect and transition tickets through the tools while still being unable to write the workspace, because ticket state lives in Flowgency-owned storage reached over an authenticated loopback tool endpoint, not through the filesystem policy. For restricted Copilot runs, `integration_config.allow_local_network` is a strict per-agent boolean that defaults to `false`; ticket-enabled runs require it to be explicitly `true` or Flowgency rejects the run before launch with `ticket-local-network-required`. The opt-in adds only per-job `allowLocalNetwork` for that agent. It does not imply `allowOutbound`, `sandboxMcpServers=false`, workspace write access, or a bypass of the generated launch-view zones. Anything an agent may write to the filesystem is still limited to the generated launch-view outbox and memory zones described above.

## Superseded layouts

Integration auto-detection, sidecar parsing, and directory-coupled runtime hints are not part of the current runtime. Native files under projected runtime layouts are generated output and never become control-plane authority.


## Runtime verification

Normal pytest runs automatically execute five live scenarios for each installed built-in AI CLI: basic execution, native root instructions, selected skill, write boundary, and launch zones. The launch-zone scenario skips for a runtime that cannot scope a write to a path. Missing CLIs do not create per-scenario skips; the installed-only collection omits absent executables instead of producing unavailable-case markers.

Installed runtimes are expected to be authenticated and operational. Live scenarios can consume configured CLI credentials, network access, model quota, and time. Authentication failures, network failures, quota limits, timeouts, and runtime process errors fail the test run; they are not skipped or xfailed.

To run only the live probes:

```text
python -m pytest -m real_runtime -v
```

To run the normal deterministic suite excluding live probes:

```text
python -m pytest -m "not real_runtime" -q
```

Deterministic integration and projector contracts continue to cover all eight built-in AI CLIs even when their executables are absent on the machine.
