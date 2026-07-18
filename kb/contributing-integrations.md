# Contributing an Integration

An integration adapts a configured current instance to one LLM runtime. Config selects the integration explicitly; project files do not select it.

## Quick start

1. Create `agency/integrations/<author>/__init__.py` and copy `agency/integrations/_template.py` to that package.
2. Implement execution, policy support, and a versioned runtime projector following the template and existing official adapters.
3. Register the plugin in `agency/integrations/integrations.yaml` through Admin > Integrations.
4. Run the integration and projector contract suites.

```text
.venv/bin/python -m pytest tests/test_integration_contract.py tests/test_runtime_projectors.py -v
```

## Contract

An integration declares whether it can execute, which sandbox and tool policies it enforces, where projected instructions and skills must be placed, and whether selected Agent Skills can activate non-interactively. It returns structured execution results and fails closed when it cannot enforce requested policy.

Projectors consume a blueprint's standard `AGENTS.md` and complete `.agents/skills` tree. They may relocate those files into the runtime's discovery layout, but must preserve bytes, write only to the compilation cache, and key output by integration, projector version, and source digest. Config identity and mutable semantic memory never enter blueprint source.

## Submission checklist

- The plugin registers under a unique author namespace.
- Projected instruction and `SKILL.md` bytes are unchanged.
- Unsupported policies fail before launch.
- Compatible skill discovery has an opt-in live runtime probe.
- Contract and normal test suites pass without requiring a live CLI.

## Superseded layouts

Native-file detection, sidecars, and identity parsing are outside runtime integration scope. New integrations must not reintroduce them.
