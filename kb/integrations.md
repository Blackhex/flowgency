# Integrations

An integration adapts an explicit configured instance to one LLM runtime. Each instance pins one integration; filesystem contents do not override it.

Integrations declare executable support, enforceable sandbox/tool modes, a versioned runtime projector, native instruction and skill targets, and whether a selected skill can be activated non-interactively. Unsupported policy or activation fails before launch.

Runtime projectors consume standards-based Agent Library source. They may relocate root `AGENTS.md` and whole `.agents/skills` directories into native discovery paths, but must preserve instruction and `SKILL.md` bytes. Compiled artifacts are immutable and keyed by integration, projector version, and source digest.

Group sandbox roots form the baseline; instance `additional_roots` are additive. A present instance tool policy is a complete override. Integrations reject modes or names they cannot enforce rather than widening access.

`agency/integrations/integrations.yaml` controls which Python plugins are loadable. It is plugin discovery metadata, not group, instance, routine, identity, or memory configuration.

## Superseded layouts

Integration auto-detection, sidecar parsing, and directory-coupled runtime hints are not part of the current runtime.
