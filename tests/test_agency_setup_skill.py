from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = REPO_ROOT / "skills" / "agency-setup"
DISCOVERY_SKILL_DIR = REPO_ROOT / ".github" / "skills" / "agency-setup"
SKILL_PATH = CANONICAL_SKILL_DIR / "SKILL.md"
DISPATCH_TEMPLATES_PATH = CANONICAL_SKILL_DIR / "references" / "dispatch-templates.md"
SETUP_KB_PATH = REPO_ROOT / "kb" / "setup-skill.md"


def test_copilot_skill_discovery_resolves_to_canonical_source():
    assert DISCOVERY_SKILL_DIR.resolve() == CANONICAL_SKILL_DIR.resolve()


def test_setup_creates_standard_global_agent_library_blueprints():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "agency.agent_library" in skill
    assert "{agent_library}/{blueprint}/AGENTS.md" in skill
    assert "{agent_library}/{blueprint}/.agents/skills/{skill}/SKILL.md" in skill
    assert "standard Agent Skills" in skill
    for forbidden in ("agents/{agent}/CLAUDE.md", "agents/{agent}/memory.md", "agents/{agent}/.copilot/"):
        assert forbidden not in skill


def test_setup_registers_explicit_canonical_instances_routines_and_memory():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "one authoritative canonical Agency config" in skill
    assert "agency.agent_library" in skill
    assert "blueprint:" in skill
    assert "integration:" in skill
    assert "routines:" in skill
    assert "skill:" in skill
    assert "default_memory:" in skill
    assert "scope: agent" in skill
    assert "scope: routine" in skill
    assert "scope: channel" in skill
    assert "additional_roots" in skill
    assert "complete override" in skill
    assert "dispatch.agents" not in skill


def test_setup_accepts_only_canonical_configs_without_conversion_or_secondary_skills():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    kb = SETUP_KB_PATH.read_text(encoding="utf-8")
    combined = f"{skill}\n{kb}".lower()
    for phrase in (
        "accepts only the canonical config shape",
        "creates the config when absent",
        "reports validation errors",
        "never invoke another skill",
        "never scan or convert superseded authority",
    ):
        assert phrase in combined
    assert "agency-migration" not in combined
    assert "tools/migrate_agent_model.py" not in combined


def test_setup_maintains_one_authoritative_strict_canonical_config():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "one authoritative" in skill
    assert "canonical Agency config" in skill
    assert "revision" in skill
    assert "atomically" in skill


def test_setup_uses_official_singleton_scheduler_cli():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "christag-agency dispatch install --config" in skill
    assert "christag-agency dispatch status --config" in skill
    assert "exactly one Agency dashboard" in skill
    assert "do not create a fallback project scheduler" in skill


def test_setup_does_not_generate_project_scheduler_artifacts():
    combined = SKILL_PATH.read_text(encoding="utf-8") + DISPATCH_TEMPLATES_PATH.read_text(encoding="utf-8")
    forbidden = [
        "agents/shared/dispatch.ps1",
        "agents/shared/install-dispatch.ps1",
        "agents/shared/dispatch.sh",
        "## Windows Scheduled Task Installer Template",
        "## Systemd Timer Template",
        "## Systemd Service Template",
    ]
    for text in forbidden:
        assert text not in combined


def test_setup_writes_routines_directly_from_assignments():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert 'at: "07:00"' in skill
    assert 'at: "21:00"' in skill
    assert "Phase 2 routine assignment" in skill
    assert "generated platform dispatch script" not in skill


def test_windows_launcher_still_resolves_real_copilot_executable():
    templates = DISPATCH_TEMPLATES_PATH.read_text(encoding="utf-8")
    launcher = templates.split("## Windows Terminal Launch Script Template", maxsplit=1)[1]
    assert "Get-Command copilot -All" in launcher
    assert "-ieq '.exe'" in launcher
    assert "-EncodedCommand" in launcher
    assert "Invoke-Expression" not in launcher


def test_registration_writes_explicit_fail_closed_agent_capabilities():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "capabilities.write: true" in normalized
    assert "capabilities.write: false" in normalized
    assert "Never infer write authority for an existing agent" in normalized
    assert "ask the user when a newly generated role is ambiguous" in normalized


def test_docs_clarify_execution_agent_blocks_not_skips():
    """kb/data-formats.md and CLAUDE.md must state that a missing, invalid, non-executable,
    or non-writable execution_agent blocks the decide form and POST until corrected — not
    that it silently creates a skipped decision. The prohibited superseded-skip row must be
    absent. The substantive-input and no-boolean-questions execution rules must be stated."""
    data_formats = (REPO_ROOT / "kb" / "data-formats.md").read_text(encoding="utf-8")
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    # Required: blocking language in kb/data-formats.md
    assert "blocks the decide form" in data_formats, \
        "data-formats.md must say missing/invalid execution_agent blocks the decide form"
    assert "blocked until corrected" in data_formats, \
        "data-formats.md must say the form is blocked until the executor is corrected"

    # Required: substantive non-boolean input causes execution despite all booleans declined
    assert "substantive" in data_formats, \
        "data-formats.md must describe the substantive non-boolean input rule"

    # Required: questionnaires with no boolean questions execute after validation
    assert "no `boolean` questions" in data_formats, \
        "data-formats.md must state that questionnaires with no boolean questions execute"

    # Required: blocking language in CLAUDE.md
    assert "blocks the decide form" in claude_md, \
        "CLAUDE.md Pipeline Relationships must say missing/invalid execution_agent blocks the decide form"

    # Prohibited: superseded-skip row implying missing executor creates a skipped decision
    assert "No writable `execution_agent` is available | `skipped`" not in data_formats, \
        "data-formats.md must not contain the inaccurate superseded-skip table row"
