# Integrations

An integration adapts an explicit configured instance to one LLM runtime. Each instance pins one integration; filesystem contents do not override it.

Integrations declare executable support, enforceable sandbox/tool modes, a versioned runtime projector, native instruction and skill targets, and whether a selected skill can be activated non-interactively. Unsupported policy or activation fails before launch.

Runtime projectors consume standards-based Agent Library source plus prompt snapshots. They may relocate root `AGENTS.md`, whole `.agents/skills` directories, and saved prompts into native discovery paths, but must preserve canonical instruction, `SKILL.md`, and prompt bytes. Compiled artifacts are immutable and keyed by integration, projector version, and source digest.

Group sandbox roots form the baseline; instance `additional_roots` are additive. A present instance tool policy is a complete override. Integrations reject modes or names they cannot enforce rather than widening access.

`agency/integrations/integrations.yaml` controls which Python plugins are loadable. It is plugin discovery metadata, not group, instance, routine, identity, or memory configuration.

## Superseded layouts

Integration auto-detection, sidecar parsing, and directory-coupled runtime hints are not part of the current runtime. Native files under projected runtime layouts are generated output and never become control-plane authority.


## Runtime verification

Normal pytest runs automatically execute four live scenarios for each installed built-in AI CLI: basic execution, native root instructions, selected skill, and write boundary. Missing CLIs do not create per-scenario skips; the installed-only collection omits absent executables instead of producing unavailable-case markers.

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
