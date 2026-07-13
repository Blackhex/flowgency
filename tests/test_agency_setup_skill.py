from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = REPO_ROOT / "skills" / "agency-setup"
DISCOVERY_SKILL_DIR = REPO_ROOT / ".github" / "skills" / "agency-setup"
SKILL_PATH = CANONICAL_SKILL_DIR / "SKILL.md"
DISPATCH_TEMPLATES_PATH = CANONICAL_SKILL_DIR / "references" / "dispatch-templates.md"


def test_copilot_skill_discovery_resolves_to_canonical_source():
    assert DISCOVERY_SKILL_DIR.resolve() == CANONICAL_SKILL_DIR.resolve()


def test_copilot_profile_requires_detection_marker():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    verification = skill.split(
        "After generation, verify every Copilot agent is detectable.", maxsplit=1
    )[1].split("### 4.3 Dispatch Prompts", maxsplit=1)[0]

    assert 'New-Item -ItemType Directory -Force "agents/$_/.copilot"' in skill
    assert "`agents/{agent}/.copilot/`" in skill
    assert 'detect_integration(agent_dir).name == "copilot"' in verification
    assert "`agents/{agent}/.copilot/`" in verification
    assert "`agents/{agent}/AGENTS.md`" in verification


def test_copilot_profile_verifies_real_executable_before_completion():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    verification = skill.split(
        "After generation, verify every Copilot agent is detectable.", maxsplit=1
    )[1].split("### 4.3 Dispatch Prompts", maxsplit=1)[0]

    assert "Get-Command copilot -All" in verification
    assert "copilot.exe" in verification
    assert "--version" in verification


def test_registration_revalidates_disk_after_dashboard_reload():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    registration = skill.split("### 4.7 Agency Registration", maxsplit=1)[1].split(
        "### 4.8 Singleton Scheduler Setup", maxsplit=1
    )[0]

    assert "parse the config from disk again" in registration
    assert "preserve concurrent changes" in registration
    assert "stale pre-reload object" in registration


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


def test_setup_writes_schedule_rules_directly_from_assignments():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    registration = skill.split("### 4.7 Agency Registration", maxsplit=1)[1].split(
        "### 4.8 Singleton Scheduler Setup",
        maxsplit=1,
    )[0]
    assert 'at: "07:00"' in registration
    assert 'at: "21:00"' in registration
    assert "Phase 2 dispatch assignment" in registration
    assert "generated platform dispatch script" not in registration


def test_windows_launcher_still_resolves_real_copilot_executable():
    templates = DISPATCH_TEMPLATES_PATH.read_text(encoding="utf-8")
    launcher = templates.split("## Windows Terminal Launch Script Template", maxsplit=1)[1]
    assert "Get-Command copilot -All" in launcher
    assert "-ieq '.exe'" in launcher
    assert "-EncodedCommand" in launcher
    assert "Invoke-Expression" not in launcher


def test_registration_skipped_when_user_declines_config_selection():
    """When multiple configs exist and user declines/skips/invalid, skip registration and scheduler setup."""
    skill = SKILL_PATH.read_text(encoding="utf-8")
    registration = skill.split("### 4.7 Agency Registration", maxsplit=1)[1].split(
        "### 4.8 Singleton Scheduler Setup", maxsplit=1
    )[0]
    # Normalize whitespace for assertions that may span line breaks
    normalized = " ".join(registration.split())

    # Must ask when multiple valid configs exist
    assert "If multiple remaining candidates are valid" in normalized
    assert "ask which" in normalized
    # Must skip registration when user declines/invalid
    assert "If the user declines, skips, or supplies an invalid selection" in normalized
    assert "skip registration and scheduler setup" in normalized
    # Never pick implicitly
    assert "never pick one implicitly" in normalized


def test_scheduler_setup_only_after_registration_verification():
    """Phase 4.8 scheduler setup offered only after registration and on-disk verification succeed."""
    skill = SKILL_PATH.read_text(encoding="utf-8")
    scheduler = skill.split("### 4.8 Singleton Scheduler Setup", maxsplit=1)[1]
    # Normalize whitespace for assertions that may span line breaks
    normalized = " ".join(scheduler.split())

    # Must enforce registration prerequisite
    assert "Only offer scheduler setup after registration and on-disk verification succeed" in normalized
    # Must report when registration was skipped
    assert "If no authoritative Agency config was found" in normalized
    assert "registration and scheduling were not completed" in normalized
    assert "do not create a fallback project scheduler" in normalized


def test_condition_rules_skipped_by_python_dispatcher():
    """Rules with condition field are skipped by Python dispatcher, run by external code only."""
    skill = SKILL_PATH.read_text(encoding="utf-8")
    registration = skill.split("### 4.7 Agency Registration", maxsplit=1)[1].split(
        "### 4.8 Singleton Scheduler Setup", maxsplit=1
    )[0]

    # Must explain condition rules are skipped by Python dispatcher
    assert "condition:" in registration
    assert "skipped by" in registration or "runs only when triggered by external" in registration
    # Must say they remain read-only in UI
    assert "read-only" in registration


def test_registration_writes_explicit_fail_closed_agent_capabilities():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    registration = skill.split("### 4.7 Agency Registration", maxsplit=1)[1].split(
        "### 4.8 Singleton Scheduler Setup", maxsplit=1
    )[0]
    normalized = " ".join(registration.split())

    assert "capabilities.write: true" in normalized
    assert "capabilities.write: false" in normalized
    assert "Never infer write authority for an existing agent" in normalized
    assert "ask the user when a newly generated role is ambiguous" in normalized
