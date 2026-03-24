# Integration Marketplace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure integrations into author-namespaced subdirectories with config-driven loading, add an admin UI for registration/discovery, and provide contribution infrastructure.

**Architecture:** Move official integrations into `agency/integrations/agency/`, load integrations from `integrations.yaml` config, add admin page at `/admin/integrations` with auto-discovery of unregistered integrations, and provide a template file + contract test + developer guide.

**Tech Stack:** Python/FastAPI, Jinja2 templates, Tailwind CSS, PyYAML

**Spec:** `docs/superpowers/specs/2026-03-24-integration-marketplace-design.md`

---

### Task 1: Move integration files into `agency/` subdirectory

Create the `agency/integrations/agency/` author directory and move all 7 official integration files there. Update the hardcoded imports in `__init__.py` to use the new paths.

**Files:**
- Create: `agency/integrations/agency/__init__.py` (empty)
- Move: `agency/integrations/claude_code.py` → `agency/integrations/agency/claude_code.py`
- Move: `agency/integrations/codex.py` → `agency/integrations/agency/codex.py`
- Move: `agency/integrations/gemini.py` → `agency/integrations/agency/gemini.py`
- Move: `agency/integrations/aider.py` → `agency/integrations/agency/aider.py`
- Move: `agency/integrations/goose.py` → `agency/integrations/agency/goose.py`
- Move: `agency/integrations/script.py` → `agency/integrations/agency/script.py`
- Move: `agency/integrations/sdk.py` → `agency/integrations/agency/sdk.py`
- Modify: `agency/integrations/__init__.py:129-137` (import paths)

- [ ] **Step 1: Create the agency subdirectory**

```bash
mkdir -p agency/integrations/agency
touch agency/integrations/agency/__init__.py
```

- [ ] **Step 2: Move all integration files**

```bash
git mv agency/integrations/claude_code.py agency/integrations/agency/claude_code.py
git mv agency/integrations/codex.py agency/integrations/agency/codex.py
git mv agency/integrations/gemini.py agency/integrations/agency/gemini.py
git mv agency/integrations/aider.py agency/integrations/agency/aider.py
git mv agency/integrations/goose.py agency/integrations/agency/goose.py
git mv agency/integrations/script.py agency/integrations/agency/script.py
git mv agency/integrations/sdk.py agency/integrations/agency/sdk.py
```

- [ ] **Step 3: Update imports in `__init__.py`**

Replace lines 129-137 in `agency/integrations/__init__.py`:

```python
# Import all integrations to trigger registration.
# Each module calls _register() at import time.
from agency.integrations.agency.claude_code import ClaudeCodeIntegration  # noqa: E402, F401
from agency.integrations.agency.codex import CodexIntegration  # noqa: E402, F401
from agency.integrations.agency.gemini import GeminiIntegration  # noqa: E402, F401
from agency.integrations.agency.aider import AiderIntegration  # noqa: E402, F401
from agency.integrations.agency.goose import GooseIntegration  # noqa: E402, F401
from agency.integrations.agency.script import ScriptIntegration  # noqa: E402, F401
from agency.integrations.agency.sdk import SdkIntegration  # noqa: E402, F401
```

- [ ] **Step 4: Fix cross-references between integration files**

Both `sdk.py` and `script.py` import from `claude_code.py`:
```python
# In both agency/integrations/agency/sdk.py and agency/integrations/agency/script.py:
from agency.integrations.claude_code import _parse_frontmatter
```

Update both to:
```python
from agency.integrations.agency.claude_code import _parse_frontmatter
```

- [ ] **Step 5: Fix test imports**

These test files import integration modules directly and need path updates:

- `tests/test_integration_claude_code.py`: `from agency.integrations.claude_code import` → `from agency.integrations.agency.claude_code import`
- `tests/test_integration_sidecar.py`: `from agency.integrations.codex import` → `from agency.integrations.agency.codex import` (and similarly for gemini, aider, goose)
- `tests/test_integration_script.py`: `from agency.integrations.script import` → `from agency.integrations.agency.script import`
- `tests/test_integration_sdk.py`: `from agency.integrations.sdk import` → `from agency.integrations.agency.sdk import`

Search each file for `from agency.integrations.` imports and add `.agency` after `integrations`.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/
git commit -m "refactor: move integrations into agency/ author subdirectory"
```

---

### Task 2: Add config-driven loading with `integrations.yaml`

Replace the hardcoded imports in `__init__.py` with config-driven loading from `integrations.yaml`.

**Files:**
- Create: `agency/integrations/integrations.yaml`
- Modify: `agency/integrations/__init__.py:101-137` (registry + imports section)
- Test: `tests/test_integrations.py`

- [ ] **Step 1: Create `integrations.yaml`**

Create `agency/integrations/integrations.yaml`:

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

- [ ] **Step 2: Write test for config-driven loading**

Add to `tests/test_integrations.py`:

```python
def test_load_integrations_from_config(tmp_path):
    """Config-driven loading populates the registry."""
    from agency.integrations import load_integrations, REGISTRY
    # Registry should already be populated from app startup
    assert len(REGISTRY) >= 7

def test_integrations_yaml_exists():
    """integrations.yaml config file exists."""
    from agency.integrations import INTEGRATIONS_DIR
    config_path = INTEGRATIONS_DIR / "integrations.yaml"
    assert config_path.exists()
```

- [ ] **Step 3: Rewrite the loading section of `__init__.py`**

Replace lines 101-137 with:

```python
# ── Registry ──────────────────────────────────────────────────────────────────

REGISTRY: dict[str, BaseIntegration] = {}
INTEGRATIONS_DIR = Path(__file__).parent


def _register(integration: BaseIntegration) -> None:
    """Register an integration instance."""
    REGISTRY[integration.name] = integration


def get_integration(name: str) -> BaseIntegration:
    """Get integration by name. Raises KeyError if not found."""
    return REGISTRY[name]


def detect_integration(agent_dir: Path) -> BaseIntegration | None:
    """Auto-detect which integration an agent directory belongs to.

    Checks in detect_priority order (lower first). The sdk integration
    (priority 999) is the fallback. script never auto-detects.
    """
    candidates = sorted(REGISTRY.values(), key=lambda i: i.detect_priority)
    for integration in candidates:
        if integration.detect(agent_dir):
            return integration
    return None


def _get_config_path() -> Path:
    """Path to integrations.yaml."""
    return INTEGRATIONS_DIR / "integrations.yaml"


def _read_config() -> list[str]:
    """Read the list of integration module paths from config."""
    config_path = _get_config_path()
    if not config_path.exists():
        return []
    data = yaml.safe_load(config_path.read_text()) or {}
    return data.get("integrations", [])


def _write_config(modules: list[str]) -> None:
    """Write the integration module list to config."""
    config_path = _get_config_path()
    data = {"integrations": modules}
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)
    config_path.write_text(content)


def load_integrations() -> None:
    """Load integrations from integrations.yaml config."""
    import importlib
    import logging
    logger = logging.getLogger("agency.integrations")

    modules = _read_config()
    if not modules:
        # First run or missing config — create default
        modules = [
            "agency.claude_code", "agency.codex", "agency.gemini",
            "agency.aider", "agency.goose", "agency.script", "agency.sdk",
        ]
        _write_config(modules)

    for module_path in modules:
        # module_path is like "agency.claude_code" → import "agency.integrations.agency.claude_code"
        full_module = f"agency.integrations.{module_path}"
        try:
            importlib.import_module(full_module)
        except Exception as e:
            logger.warning(f"Failed to load integration '{module_path}': {e}")


def scan_available() -> list[dict]:
    """Scan subdirectories for integration files not yet in config.

    Returns list of dicts: {"module_path": "author.name", "author": "author", "filename": "name.py"}
    """
    registered = set(_read_config())
    available = []

    for subdir in sorted(INTEGRATIONS_DIR.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith(("_", ".")):
            continue
        if subdir.name == "__pycache__":
            continue
        for py_file in sorted(subdir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_path = f"{subdir.name}.{py_file.stem}"
            if module_path in registered:
                continue
            # Check if file likely contains a BaseIntegration subclass
            try:
                content = py_file.read_text()
                if "BaseIntegration" in content and "_register" in content:
                    available.append({
                        "module_path": module_path,
                        "author": subdir.name,
                        "filename": py_file.name,
                    })
            except (OSError, UnicodeDecodeError):
                continue

    return available


def register_integration(module_path: str) -> None:
    """Add an integration to integrations.yaml."""
    modules = _read_config()
    if module_path not in modules:
        modules.append(module_path)
        _write_config(modules)


def unregister_integration(module_path: str) -> None:
    """Remove an integration from integrations.yaml."""
    modules = _read_config()
    if module_path in modules:
        modules.remove(module_path)
        _write_config(modules)


# Load integrations on import
load_integrations()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add agency/integrations/__init__.py agency/integrations/integrations.yaml tests/test_integrations.py
git commit -m "feat: config-driven integration loading from integrations.yaml"
```

---

### Task 3: Add admin integrations page and routes

Create the `/admin/integrations` page with installed and available-to-register sections. Update admin settings to link to it.

**Files:**
- Modify: `agency/app.py:1200-1219` (admin settings route — remove integrations table)
- Modify: `agency/app.py` (add 3 new routes)
- Create: `agency/templates/admin_integrations.html`
- Modify: `agency/templates/admin_settings.html:42-80` (replace table with link)
- Modify: `agency/templates/base.html:259-265` (add nav link)

- [ ] **Step 1: Add integrations routes to app.py**

Add these routes after the existing admin routes (after line 1219):

```python
def _read_integration_config():
    """Read integration module list from config."""
    from agency.integrations import _read_config
    return _read_config()


@app.get("/admin/integrations", response_class=HTMLResponse)
async def admin_integrations_page(request: Request):
    """Admin integrations management page."""
    from agency.integrations import scan_available

    # Build reverse map: module_name → author from config
    config_modules = _read_integration_config()
    module_to_author = {}
    for mod in config_modules:
        parts = mod.split(".")
        if len(parts) == 2:
            module_to_author[parts[1]] = parts[0]  # e.g., claude_code → agency

    installed = []
    for name, i in REGISTRY.items():
        module_name = name.replace("-", "_")
        author = module_to_author.get(module_name, "unknown")
        installed.append({
            "name": name,
            "display_name": i.display_name,
            "module_path": f"{author}.{module_name}",
            "supports_execution": i.supports_execution,
            "supports_ai_backend": i.supports_ai_backend,
            "identity_file": i.identity_filename() if hasattr(i, 'identity_filename') and callable(i.identity_filename) else "—",
            "author": author,
        })

    available = scan_available()

    return templates.TemplateResponse("admin_integrations.html", {
        "request": request,
        **admin_context("integrations"),
        "installed": installed,
        "available": available,
        "restart_needed": request.query_params.get("restart") == "1",
    })


@app.post("/admin/integrations/register", response_class=HTMLResponse)
async def admin_integrations_register(request: Request):
    """Register an available integration."""
    from agency.integrations import register_integration
    form = await request.form()
    module_path = form.get("module_path", "")
    if module_path:
        register_integration(module_path)
    return RedirectResponse("/admin/integrations?restart=1", status_code=303)


@app.post("/admin/integrations/unregister", response_class=HTMLResponse)
async def admin_integrations_unregister(request: Request):
    """Unregister an installed integration."""
    from agency.integrations import unregister_integration
    form = await request.form()
    module_path = form.get("module_path", "")
    if module_path:
        unregister_integration(module_path)
    return RedirectResponse("/admin/integrations?restart=1", status_code=303)
```

- [ ] **Step 2: Create admin_integrations.html template**

Create `agency/templates/admin_integrations.html`:

```html
{% extends "base.html" %}
{% block title %}Integrations — {{ agency_title }}{% endblock %}
{% block content %}
<div class="max-w-3xl">
  <h1 class="text-2xl font-bold text-gray-900 mb-6">Integrations</h1>

  {% if restart_needed %}
  <div class="mb-6 p-4 bg-amber-50 border border-amber-200 rounded-lg flex items-center justify-between">
    <div class="text-sm text-amber-800">Integration changes require a service restart to take effect.</div>
    <form method="POST" action="/admin/integrations/restart" class="inline">
      <button type="submit" class="px-3 py-1.5 text-xs font-medium text-white bg-amber-600 rounded-lg hover:bg-amber-700 transition-colors"
              onclick="return confirm('Restart the Agency service?')">
        Restart Service
      </button>
    </form>
  </div>
  {% endif %}

  <!-- Installed Integrations -->
  <div class="mb-8">
    <h2 class="text-lg font-semibold text-gray-900 mb-3">Installed</h2>
    <div class="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
            <th class="px-4 py-3">Integration</th>
            <th class="px-4 py-3">Author</th>
            <th class="px-4 py-3">Identity File</th>
            <th class="px-4 py-3">Execution</th>
            <th class="px-4 py-3">AI Backend</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          {% for i in installed %}
          <tr>
            <td class="px-4 py-3">{{ i.name | integration_badge }}</td>
            <td class="px-4 py-3 text-xs text-gray-500">{{ i.author }}</td>
            <td class="px-4 py-3 font-mono text-xs text-gray-600">{{ i.identity_file }}</td>
            <td class="px-4 py-3">
              {% if i.supports_execution %}<span class="text-emerald-600">&#10003;</span>{% else %}<span class="text-gray-300">—</span>{% endif %}
            </td>
            <td class="px-4 py-3">
              {% if i.supports_ai_backend %}<span class="text-emerald-600">&#10003;</span>{% else %}<span class="text-gray-300">—</span>{% endif %}
            </td>
            <td class="px-4 py-3 text-right">
              <form method="POST" action="/admin/integrations/unregister" class="inline">
                <input type="hidden" name="module_path" value="{{ i.module_path }}">
                <button type="submit" class="text-xs text-red-600 hover:text-red-800"
                        onclick="return confirm('Unregister {{ i.name }}?')">Unregister</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Available to Register -->
  {% if available %}
  <div class="mb-8">
    <h2 class="text-lg font-semibold text-gray-900 mb-3">Available to Register</h2>
    <p class="text-sm text-gray-500 mb-3">Integration files found in subdirectories that aren't registered yet.</p>
    <div class="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="bg-gray-50 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
            <th class="px-4 py-3">Module</th>
            <th class="px-4 py-3">Author</th>
            <th class="px-4 py-3">File</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          {% for a in available %}
          <tr>
            <td class="px-4 py-3 font-mono text-xs">{{ a.module_path }}</td>
            <td class="px-4 py-3 text-xs text-gray-500">{{ a.author }}</td>
            <td class="px-4 py-3 font-mono text-xs text-gray-600">{{ a.filename }}</td>
            <td class="px-4 py-3 text-right">
              <form method="POST" action="/admin/integrations/register" class="inline">
                <input type="hidden" name="module_path" value="{{ a.module_path }}">
                <button type="submit" class="px-3 py-1 text-xs font-medium text-white bg-indigo-600 rounded hover:bg-indigo-700 transition-colors">Register</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% else %}
  <div class="mb-8">
    <h2 class="text-lg font-semibold text-gray-900 mb-3">Available to Register</h2>
    <div class="bg-white rounded-xl border border-gray-200 p-6 text-center text-gray-400 text-sm">
      No unregistered integrations found. Drop a <code>.py</code> file in a subdirectory of <code>agency/integrations/</code> to add one.
    </div>
  </div>
  {% endif %}

  <!-- Contribution link -->
  <div class="text-sm text-gray-500">
    <a href="#" class="text-indigo-600 hover:text-indigo-800">How to create an integration →</a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Update admin_settings.html — replace integrations table with link**

In `agency/templates/admin_settings.html`, replace lines 42-80 (the `{% if all_integrations_info %}` block) with:

```html
  <div class="mt-8">
    <a href="/admin/integrations" class="inline-flex items-center gap-2 text-sm text-indigo-600 hover:text-indigo-800 font-medium">
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 8l4 4m0 0l-4 4m4-4H3"/></svg>
      Manage Integrations ({{ installed_count }} installed)
    </a>
  </div>
```

Update the admin settings route in `app.py` to pass `installed_count` instead of `all_integrations_info`:

```python
    return templates.TemplateResponse("admin_settings.html", {
        "request": request,
        **admin_context("settings"),
        "integrations": {name: i.display_name for name, i in REGISTRY.items() if i.supports_ai_backend},
        "ai_backend": CONFIG.get("agency", {}).get("ai_backend", "claude-code"),
        "installed_count": len(REGISTRY),
    })
```

- [ ] **Step 4: Add nav link in base.html**

In `agency/templates/base.html`, add an "Integrations" nav link after the "Agent Groups" link (after line 265):

```html
        <a href="/admin/integrations" class="nav-item {% if admin_page == 'integrations' %}active{% endif %}">
          <span class="flex items-center gap-2">
            <svg class="nav-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z"/></svg>
            Integrations
          </span>
        </a>
```

- [ ] **Step 5: Add restart route**

Add after the unregister route in `app.py`:

```python
@app.post("/admin/integrations/restart", response_class=HTMLResponse)
async def admin_integrations_restart(request: Request):
    """Restart the agency service to apply integration changes."""
    import subprocess
    try:
        subprocess.Popen(["systemctl", "--user", "restart", "agency.service"])
    except Exception:
        pass  # Best effort — the service will restart and kill this process
    return RedirectResponse("/admin/integrations", status_code=303)
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add agency/app.py agency/templates/admin_integrations.html agency/templates/admin_settings.html agency/templates/base.html
git commit -m "feat: add admin integrations page with register/unregister/restart"
```

---

### Task 4: Create template file and contract test (was Task 5)

Add the `_template.py` scaffolding file and a contract test that validates integrations against the `BaseIntegration` API.

**Files:**
- Create: `agency/integrations/_template.py`
- Create: `tests/test_integration_contract.py`

- [ ] **Step 1: Create `_template.py`**

Create `agency/integrations/_template.py`:

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
  .venv/bin/python -m pytest tests/test_integration_contract.py -v
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

- [ ] **Step 2: Create contract test**

Create `tests/test_integration_contract.py`:

```python
"""Contract tests: validate all registered integrations meet the BaseIntegration API."""
import pytest
from pathlib import Path
from agency.integrations import REGISTRY, BaseIntegration, AgentIdentity


def all_integration_names():
    """Return names of all registered integrations for parametrize."""
    return list(REGISTRY.keys())


@pytest.fixture(params=all_integration_names())
def integration(request):
    """Fixture that yields each registered integration."""
    return REGISTRY[request.param]


class TestIntegrationContract:
    def test_has_name(self, integration):
        assert isinstance(integration.name, str)
        assert len(integration.name) > 0

    def test_has_display_name(self, integration):
        assert isinstance(integration.display_name, str)

    def test_identity_filename_returns_string(self, integration):
        result = integration.identity_filename()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_detect_accepts_path_returns_bool(self, integration, tmp_path):
        result = integration.detect(tmp_path)
        assert isinstance(result, bool)

    def test_parse_identity_accepts_path(self, integration, tmp_path):
        # Should not raise — may return None for empty dir
        result = integration.parse_identity(tmp_path)
        assert result is None or isinstance(result, AgentIdentity)

    def test_supports_execution_is_bool(self, integration):
        assert isinstance(integration.supports_execution, bool)

    def test_supports_ai_backend_is_bool(self, integration):
        assert isinstance(integration.supports_ai_backend, bool)

    def test_detect_priority_is_int(self, integration):
        assert isinstance(integration.detect_priority, int)

    def test_run_callable_if_execution_supported(self, integration):
        if integration.supports_execution:
            assert callable(integration.run)

    def test_is_base_integration_subclass(self, integration):
        assert isinstance(integration, BaseIntegration)
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_integration_contract.py -v`
Expected: All tests pass for all 7 official integrations.

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add agency/integrations/_template.py tests/test_integration_contract.py
git commit -m "feat: add integration template and contract test harness"
```

---

### Task 5: Create developer guide and GitHub issue template

Write the contribution guide and issue template.

**Files:**
- Create: `kb/contributing-integrations.md`
- Create: `.github/ISSUE_TEMPLATE/new-integration.md`

- [ ] **Step 1: Create developer guide**

Create `kb/contributing-integrations.md`:

```markdown
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
```

- [ ] **Step 2: Create GitHub issue template**

Create `.github/ISSUE_TEMPLATE/new-integration.md`:

```markdown
---
name: New Integration Request
about: Suggest a new LLM tool integration for Agency
title: "Integration: [Tool Name]"
labels: enhancement, integration
---

## Tool Information

**Tool name:**

**Tool's CLI command** (if any):

**Tool's native config file** (e.g., `.cursorrules`, `MYCONFIG.md`):

**Link to tool's documentation:**

## Would you like to contribute this integration?

- [ ] Yes, I'd like to build it (see `kb/contributing-integrations.md`)
- [ ] No, just requesting it
```

- [ ] **Step 3: Commit**

```bash
git add kb/contributing-integrations.md .github/ISSUE_TEMPLATE/new-integration.md
git commit -m "docs: add integration contribution guide and issue template"
```

---

### Task 6: Update CLAUDE.md documentation

Update the root CLAUDE.md to document the new integration directory structure, config-driven loading, and admin integrations page.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update Integration System section**

In `CLAUDE.md`, find the "Integration System" section and update it to reflect:
- The new directory structure (`agency/integrations/agency/` for official, `{author}/` for community)
- `integrations.yaml` config-driven loading
- The admin integrations page at `/admin/integrations`
- The `_template.py` file and `kb/contributing-integrations.md` guide

- [ ] **Step 2: Update the Route Structure table**

Add the new admin routes:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/integrations` | Integration management page |
| POST | `/admin/integrations/register` | Register an available integration |
| POST | `/admin/integrations/unregister` | Unregister an installed integration |
| POST | `/admin/integrations/restart` | Restart service to apply changes |

- [ ] **Step 3: Update the Project Structure tree**

Update the integrations directory in the tree to show the new structure.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for integration marketplace"
```
