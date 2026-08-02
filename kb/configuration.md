# Configuration

Agency uses one authoritative YAML document. The top-level `schema_version: 5`, `agency`, and `groups` fields are required. `memory.channels` may be empty. A document declaring `schema_version: 4` is rejected; run `christag-agency config migrate` to convert it.

## Global paths

`agency.agent_library`, `agency.compilation_cache`, `agency.memory_store`, and
`agency.prompt_store` are required non-empty paths. Relative paths resolve against
the directory containing the config. The library must exist and be readable; Agency
may create cache, memory, and prompt-store roots when their nearest parent is writable.

`agency.jobs.pool` caps the number of concurrently running workers across the whole
installation. The default is 4; the minimum is 1. See [dispatch.md](dispatch.md)
for queue behaviour.

## Groups and instances

A group owns its execution `workspace_path`, Agency-owned state `path`, runtime defaults, dispatch limits, workspaces, and explicit instances. `workspace_path` is the execution workspace and source repository; `path` is the Agency-owned group root. `default_integration` initializes new instances only. Every existing instance pins its own `blueprint` and `integration`.

Group runtime defaults include timeout and permission policy. A permission is a **tool acting on a path**; `runtime.permissions` holds a `mode` and a `rules` list. Each rule is `{path?, tools?}`: with a `path` it governs that path; without one it governs tools that do not act on a path. `tools` omitted means every tool the integration offers; `[]` means none; a list means exactly those tools. The rule with the longest matching path governs a given path. Instance rules are **additive** to group rules — the two lists are concatenated, never replaced; the same path in both unions its tools.

`mode` decides what happens to a path no rule covers: `restricted` forbids it, `unrestricted` allows it. Relative rule paths resolve against the group workspace. Only the `copilot` integration currently supports `mode: restricted`.

Agency contributes generated rules for the launch view that configuration cannot widen: `<launch>/instructions` is `read` only; `<launch>/.agency/outbox` and `<launch>/.agency/memory` are `read` and `write`. An agent cannot rewrite the instructions it is executing under.

Executor eligibility is derived, not stored: an agent may execute decisions when its effective permissions grant `write` on a rule whose `path` is the group's `workspace_path` itself — not a subdirectory.

Compilation is a per-instance projection keyed on blueprint × integration × projector version × instance digest, where the digest covers identity and the permission model. Timeout, memory selector, routines, and prompt registrations are excluded from the digest.

The group root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`. Durable jobs live in `agency.memory_store/.jobs`, and operation locks live in `<group.path>/locks`.

## Routines and memory

`routines:` belongs to an instance. Each routine uses a stable `id`, selects one
saved prompt with `prompt.scope` plus `prompt.name`, defines one `schedule`, and
optionally provides arguments and memory. Schedules support `at`, `every`, and
supported conditions.

`schedule.catch_up` controls how far back the runner recovers a missed occurrence.
Accepted values are `none`, `today`, `always`, or a duration in the same grammar
as `every` (e.g. `8h`, `7d`). Absent means `today`. See
[dispatch.md](dispatch.md) for recovery semantics.

The effective prompt catalog is the union of blueprint-shared prompts from `.agents/prompts/*.prompt.md` and instance-private prompt names registered in config and stored under `agency.prompt_store`. Manual launches may use a saved catalog prompt or a one-off task; one-off input is runtime-only and does not create prompt authority.

Memory selectors are semantic: `run`, `routine`, `agent`, `group`, or declared global `channel`. An instance default cannot use routine scope. Example selectors include `scope: routine` and `scope: channel` with a channel key.

See [../config.yaml.example](../config.yaml.example) for a complete example.

## Superseded layouts

The application does not auto-load directory-coupled agent state, sidecars, prompt schedules, or per-agent memory files. Native integration prompt files are generated from canonical prompt authority and are never edited as source. A `config.yaml` declaring `schema_version: 4` is rejected; the `runtime.sandbox`, `runtime.tools`, and `capabilities` keys from that version are not accepted.
