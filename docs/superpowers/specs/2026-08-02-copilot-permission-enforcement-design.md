# Copilot permission enforcement — design

Phase 3 of the permission work. Phase 1 gave every agent a way to report. Phase 2
defined what an agent may do, as a list of rules pairing a tool with a path. This
phase makes an integration actually enforce that, starting with Copilot, and
stops the other integrations quietly granting more than they were asked to.

## Problem

Phase 2 shipped a model nothing enforces.

### The zone grants are advisory

Agency generates three rules for every job — `instructions` readable,
`.agency/outbox` and `.agency/memory` writable. They are excluded from capability
negotiation, so no integration has to reject a policy on their account. Copilot
then excludes them from rendering too, because its tool permissions are global
and it cannot grant `write` for the outbox without granting it everywhere.

The result is that nothing prevents an agent rewriting the instruction file it is
executing under. That is the hole phase 1 identified and phase 2 described but
did not close.

### A read-only agent cannot report

Reporting is unconditional in policy and impossible in practice. An agent whose
rules grant only `read` gets no `write` tool at all, so it cannot write the
outbox that phase 1 built for it. The two phases contradict each other on the one
case they were both designed around.

### Capabilities are declared, not discovered

`runtime_capabilities` is a static class attribute. What Copilot can enforce
depends on the CLI version, the operating system build, and the ACLs on every
`PATH` directory. A constant cannot express that, so today it lies in the safe
direction: `path_scopable_tools` is empty everywhere, which is why nothing
enforces anything.

### Eight integrations were never audited

Copilot carried two defects found only in the final review: a policy granting no
tools fell through to `--allow-all-tools --autopilot`, and generated zone rules
leaked into the global allowlist so an agent authored `read` launched with
`--allow-tool write`. Both were rendering mistakes, not model mistakes. The other
eight integrations were never checked for either.

## Design

### Capability detection belongs to the integration

`runtime_capabilities` stops being a class attribute and becomes something the
integration determines from the environment it finds — principally the CLI
version it detects, and whatever that version can be relied on to enforce.

An integration that cannot detect its tool reports the capabilities it can
guarantee without it, which for every integration means enforcing nothing beyond
the current behaviour. Detection failure narrows; it never widens.

The result is cached against the detected version, so the steady-state cost is
nothing and the cache invalidates exactly when the thing it describes changes.

### Copilot renders the model into sandbox filesystem policy

Copilot has two independent gates and an operation needs both: tool permission,
which is global, and filesystem policy, which is per path. Phase 2 already mirrors
that shape. This phase starts using the second gate.

Each job gets its own `COPILOT_HOME`, because `settings.json` is the only place a
sandbox policy can be expressed and a repository-level file will not accept a
`sandbox` key. Into it Agency writes:

| Setting | Value | Why |
|---|---|---|
| `sandbox.enabled` | `true` | the boundary |
| `sandbox.allowBypass` | `false` | defaults true; left alone the boundary is decorative |
| `sandbox.addCurrentWorkingDirectory` | `false` | the launch view is granted explicitly, not implicitly |
| `userPolicy.filesystem.readonlyPaths` | rules granting read but not write | |
| `userPolicy.filesystem.readwritePaths` | rules granting write | |

`deniedPaths` is not used. Windows ignores it, so the policy grants narrowly
rather than granting broadly and carving out.

The generated zone rules render here rather than being dropped:
`<launch>/instructions` becomes read-only and the outbox and memory directories
become read/write. A read-only agent can then read its workspace and write
nothing but its own outbox — which is what both earlier phases were reaching for.
At that point Copilot can honestly declare `write` in `path_scopable_tools`, and
the zone grants stop being advisory.

### Enforcement is best effort, and the gaps are stated

Agency applies as much of the policy as the integration can enforce and says
plainly what it could not.

The reason is empirical. Enabling the sandbox on a non-Insiders Windows build
disables every sandboxed subprocess — `search`, `glob`, `grep` and `shell` all
fail `backend_unavailable`, because the container backend needs an Insiders build
and its fallback needs `WRITE_DAC` on directories a normal user does not own.
In-process file edits still honour the policy. So on that platform the boundary is
real for file writes and absent for shell.

Refusing to launch would make the common case unusable. Pretending the boundary
holds would be worse. So a run reports which rules were enforced and which were
not, the unenforced ones are recorded against the job where an operator sees them
rather than only in a log, and the run proceeds.

Enforcement is also asymmetric in a second way worth stating: only shell commands
are contained by the operating system. Built-in file edits follow the same policy
on a best-effort, in-process basis. The file boundary is strong and cooperative,
not a cage.

### Git and GitHub tokens follow executor eligibility

`gitAuth` and `ghAuth` default to true and inject credentials into sandboxed
commands. A filesystem policy cannot stop a `git push`, because the write happens
on the remote.

They are therefore enabled only for an agent that may execute decisions — one
whose policy grants `write` on the group's `workspace_path` itself. An agent that
may not change the project may not push it either. This reuses the eligibility
rule phase 2 derived instead of adding a second notion of trust.

### Relocating `COPILOT_HOME` moves more than settings

The whole configuration directory moves, which has three consequences the
implementation must handle rather than discover:

- A fresh home has no credentials. `config.json` is seeded from the real home, or
  the CLI cannot start.
- `session-state/` moves with it, so `--resume` must be given the same home it was
  launched under.
- `logs/` and `permissions-config.json` move too, and the existing usage-summary
  reader already consults `COPILOT_HOME`.

### The other eight integrations are audited

For `aider`, `claude_code`, `codex`, `gemini`, `goose`, `opencode`, `pi` and
`script`, each is checked for the two defects Copilot had:

- a policy granting **no** tools must not fall through to a permissive branch
- generated zone rules must not leak into a global grant

Each gets tests asserting its rendered invocation for `tools=()`, `tools=None`, an
explicit narrow list with zone rules present, and `restricted` mode. Where an
integration cannot express something, it under-grants and says so; it never
over-grants.

## Out of scope

- Sandboxing for integrations other than Copilot. The audit fixes over-granting;
  it does not add enforcement.
- Network policy. `allowOutbound` and `allowLocalNetwork` stay at their defaults.
- MCP and LSP sandboxing settings.
- Any change to the permission model itself. Rules, merge semantics, negotiation
  and eligibility are settled.

## Verification

- A Copilot agent whose rules grant only `read` on the workspace can read it,
  cannot write it, and **can** write its outbox and memory.
- An attempt to write `<launch>/instructions` is refused by sandbox policy.
- `allowBypass` is false in every generated `settings.json`.
- An agent that is not an executor runs with `gitAuth` and `ghAuth` disabled.
- Capability detection reports narrower capabilities when the CLI is absent or
  its version is unknown, and never wider.
- A run on a platform where the container backend is unavailable still launches,
  and the rules it could not enforce are recorded against the job.
- For each of the nine integrations, a policy granting no tools produces no tool
  grant, and generated zone rules never appear in a global allowlist.
- `--resume` works against a job launched under a relocated `COPILOT_HOME`.
