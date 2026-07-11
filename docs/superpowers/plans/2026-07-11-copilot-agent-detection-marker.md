# Copilot Agent Detection Marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure setup-generated and dashboard-created GitHub Copilot agent directories carry the `.copilot/` marker and resolve through `CopilotIntegration` rather than Codex, without narrowing detection compatibility for pre-existing repository roots.

**Architecture:** Add an integration preparation hook with a Copilot-specific marker implementation, call it from dashboard agent creation and Copilot identity writes, and require the same marker in Agency Setup. Keep filesystem-first detection and the unmarked `AGENTS.md` Codex fallback unchanged. Existing repository-root `.github/` detection remains a supported Copilot signal.

**Tech Stack:** Python 3.11+, pytest, FastAPI route handlers, Markdown-based VS Code skill, Windows PowerShell generation instructions.

## Global Constraints

- `.copilot/` is mandatory for setup-generated and dashboard-managed Copilot agent directories; it is not the exclusive Copilot detector.
- Existing repository-root `.github/` detection remains supported and is out of scope to remove or narrow.
- Marker creation is idempotent and filesystem errors must propagate.
- `AGENTS.md` without any accepted Copilot/OpenCode/Pi signal must continue to detect as Codex; `.github/` remains an accepted Copilot signal at repository roots.
- Do not change config-vs-filesystem integration priority.
- Do not rewrite existing identities, sidecars, memories, prompts, or config during migration.
- Do not create git commits unless the user explicitly requests them.

---

### Task 1: Integration Preparation Contract

**Files:**
- Modify: `agency/integrations/__init__.py`
- Modify: `agency/integrations/agency/copilot.py`
- Test: `tests/test_integration_sidecar.py`

**Interfaces:**
- Produces: `BaseIntegration.prepare_agent_dir(agent_dir: Path) -> None`
- Produces: `CopilotIntegration.prepare_agent_dir(agent_dir: Path) -> None`
- Preserves: `CopilotIntegration.write_identity(agent_dir, identity) -> None`

- [ ] **Step 1: Write the failing integration test**

Add to `TestCopilot` in `tests/test_integration_sidecar.py`:

```python
def test_write_identity_creates_detection_marker(self, integration, tmp_agent_dir):
    identity = AgentIdentity(
        display_name="Copilot Bot",
        title="Builder",
        emoji="",
        body="# Copilot Bot\n",
    )

    integration.write_identity(tmp_agent_dir, identity)

    assert (tmp_agent_dir / ".copilot").is_dir()
    assert detect_integration(tmp_agent_dir).name == "copilot"
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_integration_sidecar.py::TestCopilot::test_write_identity_creates_detection_marker -q
```

Expected: FAIL because `.copilot/` does not exist and detection returns `codex`.

- [ ] **Step 3: Add the preparation hook and Copilot implementation**

Add to `BaseIntegration` in `agency/integrations/__init__.py`:

```python
def prepare_agent_dir(self, agent_dir: Path) -> None:
    """Create integration-specific filesystem markers before identity writes."""
```

The base implementation returns without modifying the directory.

Add to `CopilotIntegration` in `agency/integrations/agency/copilot.py`:

```python
def prepare_agent_dir(self, agent_dir: Path) -> None:
    (agent_dir / ".copilot").mkdir(parents=True, exist_ok=True)
```

Call `self.prepare_agent_dir(agent_dir)` as the first line of
`CopilotIntegration.write_identity()`.

Do not change `CopilotIntegration.detect()`: its existing `.copilot/` or `.github/`
compatibility remains intentional.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_integration_sidecar.py::TestCopilot::test_detect_github_marker tests/test_integration_sidecar.py::TestCopilot::test_write_identity_creates_detection_marker tests/test_integrations.py::test_detect_integration_codex -q
```

Expected: PASS, including the unchanged repository-root `.github/` compatibility and
unmarked-Codex assertions.

---

### Task 2: Dashboard Agent Creation

**Files:**
- Modify: `agency/app.py`
- Create: `tests/test_admin_agent_create.py`

**Interfaces:**
- Consumes: `BaseIntegration.prepare_agent_dir(agent_dir: Path) -> None`
- Preserves: `admin_agent_create(request, org)` redirect and scaffold behavior

- [ ] **Step 1: Write the failing route test**

Create `tests/test_admin_agent_create.py`:

```python
import asyncio

import agency.app as app_mod
from agency.integrations import detect_integration


class FakeRequest:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_admin_create_prepares_copilot_agent_dir(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {
            "grp": {
                "name": "Group",
                "path": str(agents_dir),
                "default_integration": "copilot",
                "agents": [],
            }
        },
    })
    app_mod.reload_groups()

    _run(app_mod.admin_agent_create(FakeRequest({"name": "reviewer"}), "grp"))

    agent_dir = agents_dir / "reviewer"
    assert (agent_dir / "AGENTS.md").is_file()
    assert (agent_dir / ".copilot").is_dir()
    assert detect_integration(agent_dir).name == "copilot"
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_admin_agent_create.py -q
```

Expected: FAIL because `admin_agent_create()` writes `AGENTS.md` without calling the
integration preparation hook.

- [ ] **Step 3: Invoke the selected integration hook**

In `admin_agent_create()` immediately after resolving `integration`, add:

```python
integration.prepare_agent_dir(agent_dir)
```

Keep identity and memory creation unchanged so other integrations retain their current
scaffolds.

- [ ] **Step 4: Run route and integration tests**

Run:

```powershell
python -m pytest tests/test_admin_agent_create.py tests/test_integration_sidecar.py::TestCopilot -q
```

Expected: PASS.

---

### Task 3: Agency Setup Marker Contract

**Files:**
- Modify: `skills/agency-setup/SKILL.md`
- Create: `tests/test_agency_setup_skill.py`

**Interfaces:**
- Produces: Copilot/Windows generation requirement for `agents/{agent}/.copilot/`
- Produces: post-generation Copilot detection verification requirement

- [ ] **Step 1: Write the failing skill contract test**

Create `tests/test_agency_setup_skill.py`:

```python
from pathlib import Path


SKILL_PATH = Path(__file__).parents[1] / "skills" / "agency-setup" / "SKILL.md"


def test_copilot_profile_requires_detection_marker():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert 'New-Item -ItemType Directory -Force "agents/$_/.copilot"' in skill
    assert "`agents/{agent}/.copilot/`" in skill
    assert "detect_integration(agent_dir).name" in skill
    assert "copilot" in skill
```

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py -q
```

Expected: FAIL because the skill creates no `.copilot/` marker and requires no detection
verification.

- [ ] **Step 3: Update Copilot/Windows generation instructions**

In Phase 4.1, change the Copilot/Windows loop to create both directories:

```powershell
$agentNames | ForEach-Object {
  New-Item -ItemType Directory -Force "agents/$_" | Out-Null
  New-Item -ItemType Directory -Force "agents/$_/.copilot" | Out-Null
}
```

In Phase 4.2 add:

```markdown
- Copilot/Windows: `agents/{agent}/.copilot/` — required detection marker that
    distinguishes setup-generated and dashboard-managed Copilot agent directories from
    Codex, which also uses `AGENTS.md`. This requirement does not replace the accepted
    `.github/` signal for pre-existing repository roots.
```

In Phase 4 generation verification require, when Agency's Python package is importable,
that `detect_integration(agent_dir).name == "copilot"` for each Copilot agent. Otherwise
verify that `.copilot/` and `AGENTS.md` both exist.

- [ ] **Step 4: Re-run the contract test and skill baseline**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py -q
```

Expected: PASS. Then re-run the read-only baseline scenario and confirm it lists
`.copilot/` and predicts `copilot` detection.

---

### Task 4: Existing Team Migration and Verification

**Files:**
- Create directories only: `agents/*/.copilot/` for the ten configured agents
- No identity, memory, prompt, sidecar, or config rewrites
- No migration of pre-existing repository roots that already detect through `.github/`

**Interfaces:**
- Consumes: existing `config.yaml` agent list
- Produces: all configured cards resolve to `CopilotIntegration`

- [ ] **Step 1: Create missing markers idempotently**

Use structured config parsing to enumerate `groups.agents.agents`. For each configured
name, create `<group path>/<agent>/.copilot/`. Do not scan or modify `shared/`.
This migration targets the existing setup-generated team only; do not treat it as a
detector migration or remove support for repository-root `.github/` signals.

- [ ] **Step 2: Verify all configured agents detect as Copilot**

Run:

```powershell
python -c 'from pathlib import Path; import yaml; from agency.integrations import detect_integration; c=yaml.safe_load(open("config.yaml", encoding="utf-8")); g=c["groups"]["agents"]; names=[a["name"] if isinstance(a,dict) else a for a in g["agents"]]; results={n:detect_integration(Path(g["path"])/n).name for n in names}; assert set(results.values())=={"copilot"},results; print(results)'
```

Expected: all ten values are `copilot`.

- [ ] **Step 3: Verify the live dashboard**

Fetch `http://localhost:8500/agents/agents` and assert all ten concrete cards contain the
`GitHub Copilot` integration badge and none contains `OpenAI Codex`.

- [ ] **Step 4: Run full verification**

Run:

```powershell
python -m pytest tests/ -q
git diff --check
```

Expected: all tests pass with only the existing environment-dependent skip, and no
whitespace errors are reported.
