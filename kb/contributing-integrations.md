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
 - The plugin registers under a unique author namespace.
 - Projected instruction and `SKILL.md` bytes are unchanged.
 - Unsupported policies fail before launch.
 - Integrations must declare a `cli_command` and truthful runtime policies, and their projector must explicitly declare root-instruction discovery.
 - Integrations must truthfully indicate whether selected skills can activate non-interactively and whether a selected-skill scenario is supported.
 - Integrations must participate in the four static live verification scenarios when their CLI executable is installed: `basic`, `root-instructions`, `selected-skill`, and `write-boundary`.

Live scenarios may consume configured CLI credentials, network access, model quota, and time. Authentication, network, quota, timeout, or runtime execution failures fail the live suite; they are not opt-in or gated by environment variables.

To run only the live probes:

```text
python -m pytest -m real_runtime -v
```

To run the deterministic suite excluding live probes:

```text
python -m pytest -m "not real_runtime" -q
```
- Contract and normal test suites pass without requiring a live CLI.

## Superseded layouts

Native-file detection, sidecars, and identity parsing are outside runtime integration scope. New integrations must not reintroduce them.
