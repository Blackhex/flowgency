from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]
CANONICAL_SKILL_DIR = REPO_ROOT / "skills" / "agency-setup"
DISCOVERY_SKILL_DIR = REPO_ROOT / ".github" / "skills" / "agency-setup"
SKILL_PATH = CANONICAL_SKILL_DIR / "SKILL.md"


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