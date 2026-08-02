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

### Eight integrations enforce nothing

Copilot's rendering is careful: a policy granting no tools produces no grant, and
Agency's generated zone rules are kept out of the global allowlist. The other
eight translate no rule into any argument, and two of them — `claude_code` and
`codex` — unconditionally pass a flag that disables their own permission model.
An operator writing narrow rules for those agents is writing documentation.

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
| `sandbox.enabled` | `true` only when the policy confines something | see below |
| `sandbox.allowBypass` | `false` | defaults true; left alone the boundary is decorative |
| `sandbox.addCurrentWorkingDirectory` | `false` | the launch view is granted explicitly, not implicitly |
| `userPolicy.filesystem.readonlyPaths` | rules granting read but not write | |
| `userPolicy.filesystem.readwritePaths` | rules granting write | |

The filesystem policy is an **allowlist**: a path nobody names is denied. So
`enabled: true` with empty path lists denies everything, which is the exact
inverse of an unrestricted policy that names no path — and the CLI hangs on it
rather than reporting an error. The sandbox is therefore switched on only when
the policy actually confines something: a `restricted` policy, or an
`unrestricted` one carrying an operator-authored rule that names a path. The
generated zone rules do not count towards that decision; they are attached to
every job, so counting them would sandbox every job including the default
configuration that is meant to be unconfined.

`deniedPaths` is not used. Windows ignores it, so the policy grants narrowly
rather than granting broadly and carving out.

Sandboxing is an **experimental** feature. The `/sandbox` command is registered
only when experimental features are on, so Agency passes `--experimental` on
every sandboxed launch. An integration that renders a policy without it produces
a settings file the CLI will not act on — the worst possible outcome, because it
looks enforced and is not. Detection therefore treats the absence of the flag as
an absence of the capability.

The generated zone rules render here rather than being dropped:
`<launch>/instructions` becomes read-only and the outbox and memory directories
become read/write. A read-only agent can then read its workspace and write
nothing but its own outbox — which is what both earlier phases were reaching for.
At that point Copilot can honestly declare `write` in `path_scopable_tools`, and
the zone grants stop being advisory.

For that to be more than a declaration, the tool gate has to stop vetoing it.
The gates intersect, so a tool the global allowlist withholds is unreachable no
matter what the filesystem policy grants. A tool the sandbox scopes per path is
therefore granted globally when any reachable rule asks for it — the sandbox,
not the allowlist, is what holds it to those paths. This is what makes the
outbox writable to a read-only agent, and it is why two authored rules that
disagree about `write` are now rendered as a global `write` bounded to the
granting path rather than as their intersection.

The union is conditional on the sandbox actually being in force: it applies only
when the settings file was written. Where credentials are missing the job falls
back to the shared home, no policy is written, and nothing would confine the
grant — so the claim lapses with it and the allowlist reverts to the
intersection. A capability that is asserted rather than checked is exactly the
failure this phase exists to remove.

### Enforcement is best effort, and the gaps are stated

Agency applies as much of the policy as the integration can enforce and says
plainly what it could not.

The reason is empirical, and was re-measured against the installed CLI
(1.0.78-2) rather than carried over from the earlier probe. Enabling the sandbox
on a non-Insiders Windows build disables every sandboxed subprocess — `echo hello`
through the shell tool fails `backend_unavailable`, because the container backend
needs an Insiders build and its DACL fallback needs `WRITE_DAC` on the
Store-installed PowerShell directory, which a normal user does not have.

The file boundary, measured in the same session, holds exactly as designed: a
read from the read-only zone succeeded, a write into it was refused with
`Edit (sandbox policy)` and produced no file on disk, and a write into the
read/write zone succeeded. So on this platform the boundary is real for file
writes and absent for shell.

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

This control covers git and `gh` only. The sandbox inherits the rest of the shell
environment apart from a fixed blocklist, so any other credential already present
when Agency launches — a cloud access key, a registry token — remains visible to
the agent regardless of its rules. A permission model expressed in paths cannot
reach that. The launch environment is therefore reduced to what a job needs, and
the spec states plainly that environment-borne secrets are outside the boundary.

### Relocating `COPILOT_HOME` moves more than settings

The whole configuration directory moves, which has three consequences the
implementation must handle rather than discover:

- A fresh home has no credentials. `config.json` is seeded from the real home, or
  the CLI cannot start.
- `session-state/` moves with it, so `--resume` must be given the same home it was
  launched under.
- `logs/` and `permissions-config.json` move too, and the existing usage-summary
  reader already consults `COPILOT_HOME`.

### The other eight integrations disarm themselves

Reading them established something worse than the defects this section was
originally written to hunt. None of the eight translates a permission rule into
a command-line argument at all. The two rendering defects Copilot carried
therefore cannot exist in them — there is no rendering.

What they do instead is give the agent everything, twice over:

- `claude_code` passes `--dangerously-skip-permissions` on every run
- `codex` passes `--yolo` on every run

Both are unconditional. Each disables the CLI's *own* permission model, so the
one enforcement mechanism these integrations still had is switched off before
the agent starts. The remaining six pass no policy-derived flag either, but at
least leave their tool's defaults intact.

The only thing standing between an operator's rules and an unrestricted agent is
that all eight declare `unrestricted` alone, so negotiation rejects a
`restricted` policy before launch. That is a refusal, not an enforcement: an
operator who writes narrow rules under `unrestricted` gets them silently ignored.

This phase does not give these integrations sandboxes. It does two smaller
things. The unconditional bypass flags stop being unconditional — an integration
may only disarm its own safety when the policy it was handed actually grants
that much. And each grows the same rendered-invocation tests Copilot has, so a
future change that introduces the empty-tools or generated-rule defect is caught
rather than shipped.

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
- No integration disables its own permission model unless the policy it was
  handed grants that much.
- `--resume` works against a job launched under a relocated `COPILOT_HOME`.
- A sandboxed launch passes `--experimental`; without it the rendered policy is
  treated as unenforced rather than assumed to be in effect.
