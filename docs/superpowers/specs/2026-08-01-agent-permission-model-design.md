# Agent permission model

Date: 2026-08-01
Status: approved

## Problem

Agency describes what an agent may do with three separate, overlapping
settings, and none of them says what an operator actually wants to say.

`runtime.sandbox` lists paths. `runtime.tools` lists tools. `capabilities.write`
is a boolean bolted onto the side. Nothing connects them, so the model cannot
express "read the source but write only the tests", "read this directory but
never touch that one file", or "this tool here but not there". The permission an
operator wants to grant is a tool acting on a path, and that pair has no home.

The gaps this produces are concrete.

### Writability is all-or-nothing

`_resolve_sandbox` in `agency/configuration/effective.py` ends with

```python
return mode, roots, (roots if may_write else ()), not may_write
```

Every root is writable or none is. An agent that should write its own scratch
directory must be granted the whole workspace.

### The group root is writable by accident

The phase-1 reporting-protocol specification states that group records stay
read-only for every agent, because Agency holds the pen and validates records on
ingest. The code does not do that: `sandbox_roots` includes `paths.group_root`,
so any agent with `capabilities.write` true also gets write access to
`observations/`, `proposals/` and `decisions/`. The decision prompt merely asks
it not to. Nothing enforces the boundary the phase-1 design is built on.

### Agency's own grants are invisible

The phase-1 contract says the launch view is "implicitly writable for every
agent" and never appears in configuration. That is an invisible clause: an
operator reading an agent's configuration cannot see that the agent may write
its outbox and its memory, and cannot see that it may also rewrite the
instruction file it is currently executing, because the launch view is the
working directory and the whole of it is writable.

### `capabilities.write` is a hand-maintained duplicate

It is also the gate for decision execution — `execution_agent_options()` filters
on it and proposal validation requires a writable executor. So a flag that is
supposed to describe workspace writability doubles as an authorisation decision,
and can disagree with the sandbox and tool policy beside it.

### Two settings are dead or nearly so

`tools.mode: none` is declared by no shipped integration, so any policy using it
fails validation everywhere. `restricted` and `allowlist` are declared only by
Copilot; the other eight integrations accept neither, so they must refuse any
restriction at all.

## Design

One section replaces `runtime.sandbox`, `runtime.tools` and `capabilities`.

```yaml
runtime:
  timeout: 1800
  permissions:
    mode: restricted
    rules:
      - tools: [fetch]                                       # no path: pathless tool
      - path: C:/Projekty/christag-agency
        tools: [read, search]
      - path: C:/Projekty/christag-agency/tests
        tools: [read, search, write]
      - path: C:/Projekty/christag-agency/config.yaml
        tools: []                                            # reachable, untouchable
```

A rule is `{path?, tools?}`. With a `path` it governs that path; without one it
governs tools that do not act on a path at all. Every tool has exactly one rule
that governs it, so there is no outer gate to keep in sync and no second list to
contradict the first.

`tools` has three states, and the distinction carries weight:

| `tools` | Meaning |
|---|---|
| omitted | every tool the integration offers |
| `[read, search]` | exactly those tools |
| `[]` | no tool may act here |

Omission is what makes "all tools" expressible without Agency enumerating an
integration's tool names, which it has no way to know.

Rules are a list rather than a mapping keyed by path. Windows paths are hostile
as YAML keys, and a list keeps ordering explicit and merging predictable.

When several rules match a path, the rule with the longest matching `path`
governs it. That is what makes a narrow rule a real carve-out: `tools: []` on a
single file overrides a broader grant covering its directory.

### The same schema everywhere

A group and an agent instance carry the identical `runtime.permissions` block.
There is no agent-only shorthand and no separate capability flag.

```yaml
agents:
  - name: duncan
    blueprint: test-engineer
    integration: copilot
    runtime:
      permissions:
        rules:
          - path: C:/Projekty/christag-agency/tests
            tools: [read, search, write]
          - path: C:/Projekty/christag-agency/config.yaml
            tools: []
```

Instance rules are **additive** to the group's: the two lists are concatenated,
never replaced. An instance can widen or refine what its group grants but cannot
delete a group rule — the same one-way relationship `roots` and
`additional_roots` already have, now expressed once instead of twice.

Where a group rule and an instance rule name the exact same path, their tools are
unioned. Where they name different paths, longest match decides, so an instance
refines its group by naming a narrower path rather than by overriding a broader
one.

### `mode`

The mode decides one thing: what happens to a path no rule covers.

| Mode | An uncovered path |
|---|---|
| `restricted` | is forbidden — what is not allowed is denied |
| `unrestricted` | is allowed — what is not forbidden is permitted |

Rules mean the same thing under both. Under `unrestricted` they read as
restrictions, because anything they do not narrow is already permitted; under
`restricted` they read as grants. A `tools: []` rule forbids its path under
either mode, which is how an otherwise-open agent is kept away from one secret
file.

`mode` is retained for one reason that is not obvious: it is how an integration
declares that it cannot restrict paths at all. Eight of the nine shipped
integrations are in exactly that position, and without the mode the negotiation
that makes them refuse has nothing to key on.

### Agency's own grants become rules

The launch view stops being an implicit, undocumented write grant. Agency
contributes generated rules into the same table, so an agent's whole permission
set is one list:

| Generated rule | Tools |
|---|---|
| `<launch>/instructions` | `read` |
| `<launch>/.agency/outbox` | `read`, `write` |
| `<launch>/.agency/memory` | `read`, `write` |

These are generated, not authored — an operator cannot remove them — but they
are visible, which the phase-1 arrangement was not.

Generated rules are excluded from capability negotiation. `scoped_tools`
considers only authored rules when determining whether an integration can
enforce the operator's policy, because the generated grants are Agency's own
intent rather than a demand the operator placed on the integration. The zone
rules stay in the effective policy: an integration that declares `write` in
`path_scopable_tools` will honour them. Until an integration exists that can
scope writes per path, the zones are advisory — they express the intended
boundary but nothing at runtime enforces it. Making integrations actually
enforce them is a follow-up feature (phase 3), not part of this branch.

### Compilation becomes a per-instance projection

Those generated rules need something to point at, and today nothing is rendered
per instance. `_entry_path` in `agency/blueprints/cache.py` is
`<integration>/<projector_version>/<source_digest>`, so an artifact is a
projection of *blueprint × integration* and two instances sharing a blueprint
share one directory.

Compilation becomes a projection of *blueprint × integration × the instance
properties that affect what is rendered*. The entry path gains one component:

```
<cache>/<integration>/<projector_version>/<blueprint_digest>/<instance_digest>
```

`instance_digest` covers exactly those instance properties, and nothing else:

- **identity** — `display_name`, `title`, `emoji` — because it is projected into
  the instruction file;
- **the permission model** — the mode and the resolved rules.

Deliberately excluded, because they change nothing that is rendered: `timeout`,
the memory selector and channel, routines and schedules, and prompt
registrations, since private prompts are projected into the launch view per job
rather than into the artifact.

Putting the permission model in the key rather than in a manifest keeps the
artifact genuinely immutable, which is what `AGENTS.md` claims of the
compilation cache. A policy edit yields a different digest and therefore a
different directory, so nothing is ever rewritten under a running job and a job
launched against one policy can never read another.

`BlueprintRef` on the job spec carries the instance digest, so a recovered or
resumed job resolves the entry it was launched against rather than whatever the
configuration says at the time it resumes.

### The rendered instance has access zones

The launch view stops being one flat copy. It is rendered as zones whose access
levels are exactly the generated rules above:

| Zone | Contents | Rule |
|---|---|---|
| `instructions/` | projected instruction file, skills, prompts | `read` |
| `.agency/outbox/` | `observations/`, `proposals/` | `read`, `write` |
| `.agency/memory/` | the seeded memory directory | `read`, `write` |

An agent can therefore read exactly what it was told to do — which is what lets
it report its own constraints accurately instead of guessing, the failure that
began this work — while being unable to alter them.

### Effective policy

`EffectiveRuntimePolicy` drops `sandbox_roots`, `writable_roots`,
`writes_narrowed` and the `narrows_writes` property, and carries one resolved
table of rules instead. Those four fields were added three commits before this
specification; they were the wrong shape, and keeping them beside a rule table
would recreate the duplication this design removes.

### Capability negotiation

`RuntimeCapabilities` declares:

- `permission_modes` — which of `restricted` and `unrestricted` it can honour,
  replacing `path_modes`.
- `path_scopable_tools` — the tools whose grant it can vary from one path to
  another.

`tool_modes` is removed. `all` and `allowlist` are now expressed by the mode and
the rules, and `none` was accepted by no integration.

`enforces_write_boundary` is removed as a separate flag. It is derivable:
an integration enforces the write boundary exactly when `write` is in
`path_scopable_tools`.

`validate_runtime_policy` fails closed, as it does today:

- the policy's `mode` must be in `permission_modes`;
- any tool whose grant **differs between rules** must be in
  `path_scopable_tools`. A policy that grants the same tools everywhere needs no
  per-path scoping and any integration can honour it as a flat allowlist.

The rejection names the integration, the tool, and the paths that differ, so the
operator learns which rule cannot be honoured rather than that "something" is
unsupported.

### Executor eligibility is derived

`capabilities.write` disappears, so "may execute a decision" stops being a flag
and becomes a question about the rules: an agent is eligible when its effective
permissions grant `write` on a rule whose `path` is the group's `workspace_path`
itself. A `write` grant on a subdirectory does not confer eligibility — an agent
allowed to write only its own scratch directory is not thereby trusted to
implement a decision across the workspace.

This is the question `execution_agent_options()` and phase 1's
`writable_agent_names` were approximating. Deriving it removes the possibility
that the flag and the permissions disagree.

### Schema version

`schema_version` becomes `5`. A configuration declaring `4` is rejected with a
message naming the migration command.

Agency does not read the superseded keys. `AGENTS.md` states that only the
current control-plane shape is accepted at runtime and that superseded layouts
must not be loaded, and a translation layer inside the loader would make the one
document that forbids superseded shapes carry a permanent exception to itself. A
migration command is the right home for that logic.

## Migration

A CLI command rewrites a `schema_version: 4` configuration in place, translating:

| Superseded | Becomes |
|---|---|
| `sandbox.mode: unrestricted` | `permissions.mode: unrestricted` |
| `sandbox.mode: restricted` | `permissions.mode: restricted` |
| `sandbox.roots`, `sandbox.additional_roots` | one rule per root |
| `tools.mode: all` | `tools` omitted on every rule |
| `tools.mode: allowlist`, `tools.names` | those tools on each rule |
| `tools.mode: none` | `tools: []` on every rule |
| `capabilities.write: true` | `write` present on the workspace path's rule |
| `capabilities.write: false` | `write` absent from every rule |

Under `sandbox.mode: unrestricted` there are no roots and therefore no path
rules; the tool policy becomes a single pathless rule.

Migrating `tools.mode: all` to an omitted `tools` list is what avoids having to
enumerate an integration's tool names during migration.

The command writes through the same locked, revision-checked, atomic path as
every other configuration write, and refuses to run when the file is already at
version 5.

## Verification

Test-driven, as the repository requires. The cases that matter are the ones the
old shape could not express or got wrong:

- A rule granting `write` on one path and not another resolves to exactly that,
  rather than to all-or-nothing.
- `tools` omitted grants every tool; `tools: []` grants none; an explicit list
  grants exactly itself.
- Longest match governs: `tools: []` on a file overrides a broader grant on its
  directory, under both modes.
- Under `restricted` an uncovered path is forbidden; under `unrestricted` it is
  permitted.
- Instance rules are additive: a group rule cannot be deleted by an instance,
  rules for the same path union their tools, and a narrower instance path
  refines a broader group one.
- The generated launch-view rules are present in every effective policy, grant
  `read` on the instructions and `read`/`write` on the outbox and memory, and
  cannot be removed or widened by configuration. They are excluded from
  capability negotiation, so their varying grants do not trigger tool-scoping
  rejection on integrations that cannot scope per path.
- The rendered instance places each zone where its generated rule says. The
  instructions zone is intended to be non-writable, but that boundary is
  advisory until an integration declares path-scoped write enforcement.
- Two instances of one blueprint that differ only in identity, or only in their
  permission rules, resolve to different cache entries; two that differ only in
  `timeout` or memory selector resolve to the same one.
- A job spec carries the instance digest and resolves the entry it was launched
  against after a configuration change.
- An integration lacking `write` in `path_scopable_tools` rejects a policy whose
  authored rules differ in `write`, and the message names the tool and the
  differing paths.
- A policy granting identical tools on every authored rule is accepted by an
  integration with empty `path_scopable_tools`.
- Executor eligibility follows the rules: an agent granted `write` on the
  workspace is eligible, one without it is not, and no `capabilities` key is
  consulted.
- A `schema_version: 4` configuration is rejected with the migration message.
- Migration produces, for each superseded combination in the table above, a
  version-5 configuration whose effective policy matches what the version-4
  configuration produced — except where version 4 was wrong, namely the group
  root, which becomes read-only.

## Out of scope

- **The Copilot CLI implementation of this model.** Copilot continues to declare
  no per-path scoping, so behaviour is unchanged and an agent whose rules narrow
  a tool still cannot run — the accepted state phase 1 established. Rendering the
  model into Copilot's sandbox policy and `settings.json`, relocating its
  configuration directory per instance, and deriving the capability from a
  runtime probe are the next specification's subject.
- **Cache pruning.** Nothing collects unreferenced compilation entries today and
  every blueprint edit already strands one, so this is a pre-existing defect
  rather than one this change introduces. Re-keying multiplies the rate at which
  entries accumulate without changing the nature of the problem.
- **The working-directory ancestry weakness** recorded in the phase-1
  specification.

## Recorded for phase 3

Testing against GitHub Copilot CLI 1.0.77 on non-Insiders Windows established
constraints that phase 3 cannot design around:

- Copilot's own model is two independent gates — tool permission, which is
  global, and filesystem policy, which is per-path. An operation succeeds only
  when both allow it. The filesystem policy is default-deny.
- Per-path read/write scoping is expressible only through the sandbox's
  `readonlyPaths` / `readwritePaths`, which live in `settings.json` in the
  configuration directory. Repository-level settings do not accept a `sandbox`
  key, so relocating that directory per instance is the only route.
- Tool-permission path scoping (`write(<path>)`) matches by trailing path
  components, so it can protect or permit individual **files** but cannot scope
  a directory subtree. Wildcards are not supported yet.
- The access-zone arrangement works: a read-only zone stays readable, writes to
  it are refused by sandbox policy, a read/write subdirectory works, and the CLI
  runs with a non-writable working directory. Until an integration actually
  declares path-scoped write enforcement, the zone grants remain advisory at the
  Agency layer.
- **Enabling the sandbox on this platform disables every sandboxed subprocess.**
  `search`, `glob`, `grep` and `shell` fail with `backend_unavailable`: the MXC
  ProcessContainer backend requires a Windows Insiders build, and its DACL
  fallback needs `WRITE_DAC` on every `PATH` directory, which a non-elevated
  user does not have. In-process file tools still honour the policy. So on this
  platform the boundary is enforceable only for agents that need no subprocess
  tools — and a read-only advisor usually needs `search`.

Phase 3 must therefore derive the capability from a runtime probe and the
agent's own rules rather than declare it statically.
