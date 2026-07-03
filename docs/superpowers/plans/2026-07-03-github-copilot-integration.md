# GitHub Copilot Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `copilot` integration so Agency can run/dispatch GitHub Copilot CLI agents and use Copilot as its own AI backbone.

**Architecture:** A new `CopilotIntegration(BaseIntegration)` class following the existing sidecar pattern (`codex.py`/`opencode.py`). Identity lives in `.github/copilot-instructions.md` (plain markdown) with display metadata in the `.agency-meta.yaml` sidecar. Detection keys off the `.github/` directory. Execution and the AI-backbone `prompt()` both shell out to `copilot -p "<text>" --autopilot --experimental`. The integration is registered in `integrations.yaml` and the default module list, and gets a UI badge color.

**Tech Stack:** Python 3.11+, `subprocess`, pytest.

## Global Constraints

- Integration `name` MUST be `"copilot"`, `display_name` MUST be `"GitHub Copilot"`.
- Identity file: `.github/copilot-instructions.md` (nested under agent dir).
- Detection signal: presence of the `.github/` directory.
- `detect_priority = 7` (evaluated before broader `AGENTS.md`-based integrations).
- `supports_execution = True`, `supports_ai_backend = True`.
- Execution command: `copilot -p "<prompt>" --autopilot --experimental`, run with `cwd=agent_dir`.
- Backbone command: `copilot -p "<text>" --autopilot --experimental`, no working directory.
- CLI binary resolved via `self._resolve_cmd("copilot")`.
- Timeout → `RunResult(exit_code=124, stdout="", stderr="Timed out", ...)`. Missing CLI (`FileNotFoundError`) → `IntegrationError`.
- Config writes are not needed here; only add module strings to existing lists.

---

### Task 1: Create the CopilotIntegration class

**Files:**
- Create: `agency/integrations/agency/copilot.py`
- Test: `tests/test_integration_sidecar.py` (append `TestCopilot` class)

**Interfaces:**
- Consumes: `BaseIntegration`, `RunResult`, `AgentIdentity`, `IntegrationError`, `_register`, `read_sidecar`, `write_sidecar` from `agency.integrations`; helpers `_parse_sidecar_identity`, `_write_sidecar_identity`, `_resolve_cmd` inherited from `BaseIntegration`.
- Produces: `CopilotIntegration` with `name="copilot"`, `identity_filename() -> ".github/copilot-instructions.md"`, `detect(agent_dir) -> bool`, `parse_identity`, `write_identity`, `run(agent_dir, prompt_file, timeout) -> RunResult`, `prompt(text, timeout) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integration_sidecar.py`. First add the import near the top with the other integration imports:

```python
from agency.integrations.agency.copilot import CopilotIntegration
```

Then append this test class at the end of the file:

```python
class TestCopilot:
    @pytest.fixture
    def integration(self):
        return CopilotIntegration()

    def test_metadata(self, integration):
        assert integration.name == "copilot"
        assert integration.display_name == "GitHub Copilot"
        assert integration.supports_execution is True
        assert integration.supports_ai_backend is True

    def test_identity_filename(self, integration):
        assert integration.identity_filename() == ".github/copilot-instructions.md"

    def test_detect(self, integration, tmp_agent_dir):
        (tmp_agent_dir / ".github").mkdir()
        assert integration.detect(tmp_agent_dir) is True

    def test_detect_negative(self, integration, tmp_agent_dir):
        assert integration.detect(tmp_agent_dir) is False

    def test_parse_identity_body_from_native(self, integration, tmp_agent_dir):
        gh = tmp_agent_dir / ".github"
        gh.mkdir()
        (gh / "copilot-instructions.md").write_text("# Copilot Agent\nDo things.\n")
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity is not None
        assert "# Copilot Agent" in identity.body

    def test_parse_identity_metadata_from_sidecar(self, integration, tmp_agent_dir):
        gh = tmp_agent_dir / ".github"
        gh.mkdir()
        (gh / "copilot-instructions.md").write_text("# Agent\n")
        (tmp_agent_dir / ".agency-meta.yaml").write_text(
            "display_name: Copilot Bot\ntitle: CB\nemoji: \"🐙\"\n"
        )
        identity = integration.parse_identity(tmp_agent_dir)
        assert identity.display_name == "Copilot Bot"
        assert identity.title == "CB"

    def test_write_identity_creates_nested_file_and_sidecar(self, integration, tmp_agent_dir):
        identity = AgentIdentity(display_name="New", title="T", emoji="🐙", body="# New body")
        integration.write_identity(tmp_agent_dir, identity)
        assert "# New body" in (tmp_agent_dir / ".github" / "copilot-instructions.md").read_text()
        sidecar = (tmp_agent_dir / ".agency-meta.yaml").read_text()
        assert "display_name: New" in sidecar

    def test_missing_file(self, integration, tmp_agent_dir):
        assert integration.parse_identity(tmp_agent_dir) is None

    def test_run_builds_command(self, integration, tmp_agent_dir, monkeypatch):
        import agency.integrations.agency.copilot as mod
        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            return FakeCompleted()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        prompt_file = tmp_agent_dir / "prompt.md"
        prompt_file.write_text("Do the thing")
        result = integration.run(tmp_agent_dir, prompt_file, timeout=30)
        assert result.exit_code == 0
        assert captured["cmd"][:2] == [captured["cmd"][0], "-p"]
        assert "Do the thing" in captured["cmd"]
        assert "--autopilot" in captured["cmd"]
        assert "--experimental" in captured["cmd"]
        assert captured["cwd"] == str(tmp_agent_dir)

    def test_prompt_returns_stdout(self, integration, monkeypatch):
        import agency.integrations.agency.copilot as mod

        class FakeCompleted:
            returncode = 0
            stdout = "hello"
            stderr = ""

        def fake_run(cmd, **kwargs):
            fake_run.cmd = cmd
            return FakeCompleted()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        out = integration.prompt("hi there", timeout=10)
        assert out == "hello"
        assert "--autopilot" in fake_run.cmd
        assert "--experimental" in fake_run.cmd
        assert "hi there" in fake_run.cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_integration_sidecar.py -k Copilot -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agency.integrations.agency.copilot'`

- [ ] **Step 3: Create the integration file**

Create `agency/integrations/agency/copilot.py`:

```python
"""GitHub Copilot CLI integration."""

import subprocess
import time
from pathlib import Path

from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
)


class CopilotIntegration(BaseIntegration):
    name = "copilot"
    display_name = "GitHub Copilot"
    supports_execution = True
    supports_ai_backend = True
    detect_priority = 7

    def identity_filename(self) -> str:
        return ".github/copilot-instructions.md"

    def _identity_file(self, agent_dir: Path) -> Path:
        return agent_dir / ".github" / "copilot-instructions.md"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".github").is_dir()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, self._identity_file(agent_dir))

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        (agent_dir / ".github").mkdir(parents=True, exist_ok=True)
        self._write_sidecar_identity(agent_dir, self._identity_file(agent_dir), identity)

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        prompt_text = prompt_file.read_text()
        cmd = self._find_cmd()
        start = time.monotonic()
        try:
            result = subprocess.run(
                [cmd, "-p", prompt_text, "--autopilot", "--experimental"],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(agent_dir),
            )
            duration = time.monotonic() - start
            return RunResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return RunResult(exit_code=124, stdout="", stderr="Timed out", duration_seconds=duration)
        except FileNotFoundError:
            raise IntegrationError(f"GitHub Copilot CLI not found. Looked for: {cmd}")

    def prompt(self, text: str, timeout: int = 60) -> str:
        cmd = self._find_cmd()
        try:
            result = subprocess.run(
                [cmd, "-p", text, "--autopilot", "--experimental"],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                raise IntegrationError(f"copilot exited with code {result.returncode}: {result.stderr}")
            return result.stdout
        except FileNotFoundError:
            raise IntegrationError(f"GitHub Copilot CLI not found. Looked for: {cmd}")
        except subprocess.TimeoutExpired:
            raise IntegrationError(f"copilot timed out after {timeout}s")

    def _find_cmd(self) -> str:
        return self._resolve_cmd("copilot")


_register(CopilotIntegration())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_integration_sidecar.py -k Copilot -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add agency/integrations/agency/copilot.py tests/test_integration_sidecar.py
git commit -m "feat: add GitHub Copilot integration class"
```

---

### Task 2: Register the integration

**Files:**
- Modify: `agency/integrations/__init__.py` (default module list in `load_integrations()`)
- Modify: `agency/integrations/integrations.yaml`

**Interfaces:**
- Consumes: `CopilotIntegration` (registered on import via `_register`).
- Produces: `"copilot"` present in `REGISTRY` at startup; contract tests in `tests/test_integration_contract.py` auto-cover it.

- [ ] **Step 1: Add to the default module list**

In `agency/integrations/__init__.py`, find the default `modules` list inside `load_integrations()`:

```python
        modules = [
            "agency.claude_code", "agency.codex", "agency.gemini",
            "agency.aider", "agency.goose", "agency.opencode", "agency.pi",
            "agency.script", "agency.sdk",
        ]
```

Replace it with:

```python
        modules = [
            "agency.claude_code", "agency.codex", "agency.gemini",
            "agency.aider", "agency.goose", "agency.opencode", "agency.pi",
            "agency.copilot", "agency.script", "agency.sdk",
        ]
```

- [ ] **Step 2: Add to integrations.yaml**

Open `agency/integrations/integrations.yaml`. Add `- agency.copilot` to the `integrations:` list (place it right before `- agency.script`). Example resulting list:

```yaml
integrations:
- agency.claude_code
- agency.codex
- agency.gemini
- agency.aider
- agency.goose
- agency.opencode
- agency.pi
- agency.copilot
- agency.script
- agency.sdk
```

> Note: match the existing file's exact formatting. If entries are quoted or ordered differently, insert `agency.copilot` consistently before `agency.script`.

- [ ] **Step 3: Verify registration and contract tests pass**

Run: `python -m pytest tests/test_integration_contract.py -v`
Expected: PASS — includes parametrized cases for `copilot`.

Also verify it is registered:

Run: `python -c "from agency.integrations import REGISTRY; print('copilot' in REGISTRY)"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add agency/integrations/__init__.py agency/integrations/integrations.yaml
git commit -m "feat: register GitHub Copilot integration"
```

---

### Task 3: Add UI badge color and docs

**Files:**
- Modify: `agency/app.py` (`integration_badge_filter`)
- Modify: `CLAUDE.md` (Shipped Integrations table)
- Modify: `kb/integrations.md` (if it enumerates integrations)

**Interfaces:**
- Consumes: `integration_badge_filter` colors dict.
- Produces: a `copilot` badge color; documentation row.

- [ ] **Step 1: Add badge color**

In `agency/app.py`, find the `colors` dict inside `integration_badge_filter`:

```python
    colors = {
        "claude-code": "bg-orange-100 text-orange-800",
        "codex": "bg-green-100 text-green-800",
        "gemini": "bg-blue-100 text-blue-800",
        "aider": "bg-purple-100 text-purple-800",
        "goose": "bg-yellow-100 text-yellow-800",
        "script": "bg-gray-100 text-gray-800",
        "sdk": "bg-indigo-100 text-indigo-800",
    }
```

Add a `copilot` entry:

```python
    colors = {
        "claude-code": "bg-orange-100 text-orange-800",
        "codex": "bg-green-100 text-green-800",
        "gemini": "bg-blue-100 text-blue-800",
        "aider": "bg-purple-100 text-purple-800",
        "goose": "bg-yellow-100 text-yellow-800",
        "copilot": "bg-slate-100 text-slate-800",
        "script": "bg-gray-100 text-gray-800",
        "sdk": "bg-indigo-100 text-indigo-800",
    }
```

- [ ] **Step 2: Update CLAUDE.md integration table**

In `CLAUDE.md`, find the "Shipped Integrations" table. Add this row after the `pi` row:

```markdown
| `copilot` | `.github/copilot-instructions.md` | `.github/` dir exists | `copilot -p --autopilot --experimental` | Yes |
```

- [ ] **Step 3: Update kb/integrations.md if applicable**

Open `kb/integrations.md`. If it contains a list/table of shipped integrations, add a GitHub Copilot entry consistent with the existing format (native file `.github/copilot-instructions.md`, detect via `.github/` directory, execution `copilot -p "<prompt>" --autopilot --experimental`, AI backend: yes). If the file does not enumerate integrations, skip this step.

- [ ] **Step 4: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (all tests, including the new Copilot and contract cases).

- [ ] **Step 5: Commit**

```bash
git add agency/app.py CLAUDE.md kb/integrations.md
git commit -m "docs: document GitHub Copilot integration and add UI badge"
```

---

## Self-Review

**Spec coverage:**
- New `copilot.py` class with all attributes/methods → Task 1 ✓
- Detection via `.github/` → Task 1 (`detect`, tests) ✓
- Identity `.github/copilot-instructions.md` + sidecar, nested mkdir edge case → Task 1 (`write_identity`, test) ✓
- Execution `run()` command → Task 1 (impl + `test_run_builds_command`) ✓
- AI backbone `prompt()` command → Task 1 (impl + `test_prompt_returns_stdout`) ✓
- CLI resolution via `_resolve_cmd` → Task 1 ✓
- Register in `__init__.py` default list + `integrations.yaml` → Task 2 ✓
- Badge color in `app.py` → Task 3 ✓
- Docs in CLAUDE.md / kb → Task 3 ✓
- Contract tests auto-cover → Task 2 Step 3 ✓

**Placeholder scan:** No TBD/TODO. The only conditional step (kb/integrations.md) has explicit skip criteria. Code steps all contain full code.

**Type consistency:** `CopilotIntegration` name, `identity_filename()` return `".github/copilot-instructions.md"`, `_identity_file` helper, `run`/`prompt` signatures, and `RunResult`/`AgentIdentity`/`IntegrationError` usages are consistent across tasks and match the `BaseIntegration` API and existing `codex.py` pattern.
