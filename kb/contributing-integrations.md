# Contributing an Integration

Agency uses a plugin system to support different LLM tools. Each integration is a Python class that teaches Agency how to interact with a specific tool.

## Quick Start

1. **Create your author directory:**
   ```bash
   mkdir -p agency/integrations/{your-name}
   touch agency/integrations/{your-name}/__init__.py
   ```

2. **Copy the template:**
   ```bash
   cp agency/integrations/_template.py agency/integrations/{your-name}/your_tool.py
   ```

3. **Fill in the methods** — see the template comments for guidance on each method.

4. **Test your integration:**
   ```bash
   .venv/bin/python -m pytest tests/test_integration_contract.py -v
   ```

5. **Register via the dashboard:**
   Visit Admin → Integrations. Your integration will appear in "Available to Register." Click Register, then restart the service.

## Directory Structure

```
agency/integrations/
├── agency/           # Official integrations
│   ├── claude_code.py
│   ├── codex.py
│   └── ...
├── {your-name}/      # Your integration
│   ├── __init__.py
│   └── your_tool.py
├── _template.py      # Start here
└── integrations.yaml # Auto-managed by the admin UI
```

## What Each Method Does

| Method | When It's Called | What to Return |
|--------|-----------------|----------------|
| `identity_filename()` | Determining which file to read/write for agent identity | The filename (e.g., `'CLAUDE.md'`, `'.cursorrules'`) |
| `detect(agent_dir)` | Auto-detecting which tool an agent uses | `True` if the directory belongs to your tool |
| `parse_identity(agent_dir)` | Reading agent name/title/emoji from the native file | An `AgentIdentity` dataclass, or `None` |
| `write_identity(agent_dir, identity)` | Saving identity changes from the profile page | Write fields to the native file or sidecar |
| `run(agent_dir, prompt_file, timeout)` | Executing the tool with a prompt (dispatch, decisions) | A `RunResult` with exit code, stdout, stderr, duration |

## Two Identity Patterns

**Frontmatter tools** (like Claude Code with `CLAUDE.md`): Parse YAML frontmatter from the identity file directly. See `agency/integrations/agency/claude_code.py`.

**Sidecar tools** (like Codex, Gemini): The native file doesn't support YAML frontmatter, so Agency stores metadata in `.agency-meta.yaml` next to the native file. See `agency/integrations/agency/codex.py` and use the `read_sidecar()`/`write_sidecar()` helpers.

## Submitting

Open a PR with your author directory. Make sure:
- [ ] All contract tests pass
- [ ] `_register()` is called at module level
- [ ] `__init__.py` exists in your directory
