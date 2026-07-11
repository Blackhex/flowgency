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
        "### 4.8 Scheduler Setup", maxsplit=1
    )[0]

    assert "parse the config from disk again" in registration
    assert "preserve concurrent changes" in registration
    assert "stale pre-reload object" in registration


def test_windows_templates_enumerate_real_copilot_executable():
    templates = DISPATCH_TEMPLATES_PATH.read_text(encoding="utf-8")

    assert templates.count("Get-Command copilot -All") >= 2
    assert templates.count("-ieq '.exe'") >= 2
    assert templates.count("Start-Process -FilePath $copilotExe") >= 1
    assert "Invoke-Expression" not in templates