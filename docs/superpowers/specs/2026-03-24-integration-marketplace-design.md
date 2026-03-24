# Integration Marketplace — Design Spec

> **Date:** 2026-03-24
> **Status:** Draft
> **Author:** Product Agent

## Problem

The integration system today requires contributors to edit `agency/integrations/__init__.py` to register new integrations — a code change in core files that creates friction for community contributions. There's no GUI for managing integrations, no template to start from, no test harness to validate against, and no documentation explaining how to build one. The growth strategy identifies the integration plugin loop as the primary flywheel for expanding Agency's user base.

## Solution

Restructure integrations into author-namespaced subdirectories, add config-driven loading, build an admin UI for registration, and provide contribution infrastructure (template, guide, test harness, issue template).

## Directory Structure

```
agency/integrations/
├── __init__.py              # BaseIntegration, REGISTRY, config-driven loading
├── integrations.yaml        # Which integrations are loaded (managed by admin UI)
├── _template.py             # Commented scaffolding for contributors
├── agency/                  # Official integrations
│   ├── __init__.py
│   ├── claude_code.py
│   ├── codex.py
│   ├── gemini.py
│   ├── aider.py
│   ├── goose.py
│   ├── script.py
│   └── sdk.py
└── {author}/                # Community integrations
    ├── __init__.py
    └── their_tool.py
```

### Author Folder Convention

- Official integrations live in `agency/integrations/agency/`
- Community integrations live in `agency/integrations/{author_name}/`
- Author name is lowercase, alphanumeric + hyphens (e.g., `johndoe`, `acme-corp`)
- Each author folder must contain an `__init__.py` (can be empty)

## Config-Driven Loading

### `integrations.yaml`

```yaml
integrations:
  - agency.claude_code
  - agency.codex
  - agency.gemini
  - agency.aider
  - agency.goose
  - agency.script
  - agency.sdk
```

Each entry is `{author}.{module_name}`, which maps to `agency/integrations/{author}/{module_name}.py`.

The file lives at `agency/integrations/integrations.yaml` (alongside `__init__.py`).

### Startup Loading

On app startup, `agency/integrations/__init__.py`:

1. Reads `integrations.yaml` from the integrations directory
2. For each entry, imports the module at `agency.integrations.{author}.{module_name}`
3. The module's `_register()` call at module level adds it to `REGISTRY`
4. If a module fails to import, log a warning and continue (don't crash the app)
5. If `integrations.yaml` doesn't exist, create it with the default official integrations list

### `_register()` Pattern

Unchanged. Each integration module still calls `_register(MyIntegration())` at module level. This is the contract — the loading system just controls which modules get imported.

## Admin Integrations Page

### Route

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/integrations` | Integrations management page |
| POST | `/admin/integrations/register` | Register an available integration |
| POST | `/admin/integrations/unregister` | Unregister an installed integration |

### Page Layout

**Section 1: Installed Integrations**

Table showing all currently registered integrations (from `REGISTRY`):

| Name | Author | Native File | Execution | AI Backend | Action |
|------|--------|-------------|-----------|------------|--------|
| claude-code | agency | CLAUDE.md | Yes | Yes | Unregister |
| codex | agency | AGENTS.md | Yes | Yes | Unregister |
| ... | ... | ... | ... | ... | ... |

"Unregister" removes the entry from `integrations.yaml`. Shows a warning that a restart is needed.

**Section 2: Available to Register**

Scanned from the integrations subdirectories. Shows `.py` files in author folders that aren't in `integrations.yaml`:

| Module | Author | Action |
|--------|--------|--------|
| johndoe.cursor | johndoe | Register |

"Register" adds the entry to `integrations.yaml`. Shows a notice that a restart is needed.

**Restart Banner**

When a register or unregister action has occurred, a yellow banner appears: "Integration changes require a service restart to take effect. Restart now?" with a "Restart Service" button that triggers `systemctl --user restart agency.service`.

### Discovery Logic

To populate "Available to Register":

1. Scan all subdirectories of `agency/integrations/` (skip `__pycache__`, files starting with `_`)
2. For each `.py` file in a subdirectory, check if `{dirname}.{stem}` is already in `integrations.yaml`
3. If not, try to detect if it contains a `BaseIntegration` subclass (simple text scan for `BaseIntegration` in the file content — no import needed)
4. Show matches in the "Available" section

### Admin Settings Page Change

The existing "Installed Integrations" table on `/admin/` settings page is replaced with a link: "Manage integrations →" pointing to `/admin/integrations`.

## Integration Template

### `agency/integrations/_template.py`

A copy-and-fill scaffolding file:

```python
"""
Integration template for Agency.

HOW TO USE:
1. Create your author directory: agency/integrations/{your-name}/
2. Add an empty __init__.py to your directory
3. Copy this file there and rename it: agency/integrations/{your-name}/your_tool.py
4. Fill in each method below (see comments for guidance)
5. Visit Admin → Integrations in the dashboard to register
6. Restart the service

TESTING:
  .venv/bin/python -m pytest tests/test_integration_contract.py -v -k "your_tool"
"""

from pathlib import Path
from agency.integrations import BaseIntegration, AgentIdentity, RunResult, _register


class YourToolIntegration(BaseIntegration):
    """Integration for YourTool CLI."""

    # Short identifier used in config.yaml and UI badges.
    # Example: 'cursor', 'windsurf', 'continue'
    name = "your-tool"

    # Display name shown in the admin UI.
    display_name = "Your Tool"

    # Can Agency invoke this tool to execute prompts?
    # True if the tool has a CLI that accepts a prompt/file.
    supports_execution = False

    # Can Agency use this tool as its own AI backbone?
    # True if the tool has a non-interactive prompt mode.
    supports_ai_backend = False

    # Lower number = checked first during auto-detection.
    # Use 100 (default) for most integrations.
    detect_priority = 100

    def identity_filename(self) -> str:
        """The identity/config file this tool uses natively.
        Agency reads/writes agent identity through this file.
        Example: 'CLAUDE.md', 'AGENTS.md', '.cursorrules'
        """
        return "YOUR_CONFIG_FILE"

    def detect(self, agent_dir: Path) -> bool:
        """Return True if agent_dir belongs to this tool.
        Usually: check if identity_filename exists in the directory.

        Example:
            return (agent_dir / self.identity_filename()).exists()
        """
        return (agent_dir / self.identity_filename()).exists()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        """Read the agent's identity from its native file.
        Return an AgentIdentity(display_name, title, emoji, body).

        For tools with YAML frontmatter in their native file,
        parse it directly. For tools without frontmatter support,
        read from .agency-meta.yaml sidecar file instead.
        See existing integrations for examples of both patterns.
        """
        # TODO: implement
        return AgentIdentity(display_name="", title="", emoji="", body="")

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        """Write agent identity back to the native file or sidecar.
        `identity` is an AgentIdentity dataclass with display_name,
        title, emoji, and body fields.
        """
        pass  # TODO: implement

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        """Execute the tool with a prompt file. Return a RunResult.
        Only needed if supports_execution is True.

        Example:
            import subprocess, time
            start = time.time()
            result = subprocess.run(
                ["your-tool", "--prompt", str(prompt_file)],
                cwd=str(agent_dir),
                capture_output=True, text=True, timeout=timeout
            )
            return RunResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=time.time() - start,
            )
        """
        raise NotImplementedError("This integration does not support execution")


# Register this integration — this line is required
_register(YourToolIntegration())
```

## Contribution Infrastructure

### Developer Guide: `kb/contributing-integrations.md`

Contents:
- What integrations are and how they work in Agency
- Directory structure and author folder convention
- Step-by-step: copy template → fill in methods → register via admin
- How each `BaseIntegration` method is called and what it should return
- The sidecar metadata pattern (for tools without frontmatter support)
- How to run the contract test
- How to submit (PR with your author folder)

### Contract Test: `tests/test_integration_contract.py`

A parametrized test that validates any registered integration:
- Has required class attributes (`name`, `supports_execution`, `supports_ai_backend`)
- `name` is a non-empty string
- `identity_filename()` returns a non-empty string
- `detect()` accepts a Path argument and returns bool
- `parse_identity()` accepts a Path and returns an `AgentIdentity` or None
- `write_identity()` is callable
- If `supports_execution` is True, `run()` is callable
- Can be imported without side effects beyond registration

Runs against all currently registered integrations by default. Can be filtered with `-k` to test a specific one.

### GitHub Issue Template: `.github/ISSUE_TEMPLATE/new-integration.md`

Fields:
- Tool name
- Tool's CLI command (if any)
- Tool's native config file
- Link to tool's documentation
- Would you like to contribute this integration? (yes/no)

## Migration

Moving existing integrations from `agency/integrations/*.py` to `agency/integrations/agency/*.py`:

1. Create `agency/integrations/agency/` directory with `__init__.py`
2. Move all 7 integration files into the subdirectory
3. Update all internal imports (integration files import from `agency.integrations` for base classes — these stay the same since `__init__.py` stays at the top level)
4. Create `integrations.yaml` with all 7 official integrations listed
5. Update `__init__.py` to load from config instead of hardcoded imports
6. Update tests that import specific integrations

## Impact on Existing Code

### `agency/integrations/__init__.py`

Major rewrite:
- Remove hardcoded integration imports at module level
- Add `load_integrations()` function that reads `integrations.yaml` and imports listed modules
- Add `scan_available()` function for admin page discovery
- Add `register_integration(module_path)` and `unregister_integration(module_path)` functions that edit `integrations.yaml`
- Keep `BaseIntegration`, `REGISTRY`, `get_integration()`, `detect_integration()`, `_register()` unchanged

### `agency/app.py`

- Call `load_integrations()` at startup
- Add 3 new routes (`/admin/integrations`, register, unregister)
- Update admin settings page to link to integrations page instead of showing inline table

### Templates

- New: `agency/templates/admin_integrations.html`
- Modify: `agency/templates/admin_settings.html` (remove integrations table, add link)

## Out of Scope

- Marketplace download/install (future — requires remote registry)
- Integration versioning
- Integration dependencies
- Hot-reloading integrations without restart
- Per-integration configuration UI (already handled by `integration_config` in config.yaml)
