# Configuration

Agency uses one authoritative strict-canonical YAML document. The top-level `schema_version: 2`, `agency`, and `groups` fields are required. `memory.channels` may be empty.

## Global paths

`agency.agent_library`, `agency.compilation_cache`, and `agency.memory_store` are required non-empty paths. Relative paths resolve against the directory containing the config. The library must exist and be readable; Agency may create cache and memory roots when their nearest parent is writable.

## Groups and instances

A group owns its workspace path, runtime defaults, dispatch limits, workspaces, and explicit instances. `default_integration` initializes new instances only. Every existing instance pins its own `blueprint` and `integration`.

Group runtime defaults include timeout, sandbox policy, and tool policy. Restricted sandbox roots are inherited. Instance `additional_roots` are additive and cannot remove a group root. An instance tool policy is a complete override with mode `all`, `allowlist`, or `none`; omission inherits the entire group policy.

Identity and `capabilities.write` live in the instance record. Omitted write capability is false.

## Routines and memory

`routines:` belongs to an instance. Each routine uses a stable `id`, selects one Agent Skill with `skill`, defines one `schedule`, and optionally provides arguments and memory. Schedules support `at`, `every`, and supported conditions.

Memory selectors are semantic: `run`, `routine`, `agent`, `group`, or declared global `channel`. An instance default cannot use routine scope. Example selectors include `scope: routine` and `scope: channel` with a channel key.

See [../config.yaml.example](../config.yaml.example) for a complete example.

## superseded v1 migration

The application does not auto-convert older files. Invoke `agency-migration`, then use the standalone commands:

```text
python tools/migrate_agent_model.py preview --config config.yaml --plan migration-plan.yaml
python tools/migrate_agent_model.py apply --plan migration-plan.yaml
python tools/migrate_agent_model.py verify --config config.yaml
python tools/migrate_agent_model.py rollback --plan migration-plan.yaml
```
