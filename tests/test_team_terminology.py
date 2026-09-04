from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml


REPO_ROOT = Path(__file__).parents[1]
ACTIVE_PATHS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "config.yaml.example",
    REPO_ROOT / "agency.service.example",
    *(REPO_ROOT / "kb").glob("*.md"),
    *(REPO_ROOT / "examples").glob("**/*.md"),
    *(REPO_ROOT / "agency" / "setup_assets").glob("**/*.md"),
)


def test_active_documents_use_v6_team_control_plane():
    text = "\n".join(path.read_text(encoding="utf-8") for path in ACTIVE_PATHS)

    assert "schema_version: 6" in text
    assert "default_team:" in text
    assert "teams:" in text
    assert "schema_version: 5" not in text
    assert "default_group:" not in text
    assert "\ngroups:\n" not in text
    assert "christag-agency config migrate" not in text
    assert "capabilities:" not in text
    assert "  sandbox:" not in text


def test_setup_assets_use_team_domain_terms():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "agency" / "setup_assets").glob("**/*.md")
    )

    assert "team display name" in text.lower()
    assert "teams.<team-id>" in text
    assert "<root>/teams/<team-id>" in text
    assert not re.search(r"\bgroup display name\b", text, re.IGNORECASE)
    assert not re.search(r"\bgroup concept\b", text, re.IGNORECASE)


def test_example_config_blocks_are_valid_v6(tmp_path: Path) -> None:
    from agency.configuration.models import validate_config

    example_readmes = [
        REPO_ROOT / "examples" / "code-review-team" / "README.md",
        REPO_ROOT / "examples" / "content-team" / "README.md",
    ]
    for readme in example_readmes:
        text = readme.read_text(encoding="utf-8")
        m = re.search(r"```yaml\n(.*?)```", text, re.DOTALL)
        assert m, f"No YAML block in {readme.name}"
        raw = yaml.safe_load(m.group(1))
        agency = raw.setdefault("agency", {})
        for key in ("agent_library", "compilation_cache", "memory_store", "prompt_store"):
            agency[key] = str(tmp_path / key)
        for team in raw.get("teams", {}).values():
            team["workspace_path"] = str(tmp_path / "workspace")
            team["path"] = str(tmp_path / "team-state")
        config_path = tmp_path / "config.yaml"
        issues = validate_config(raw, config_path)
        assert not issues, f"{readme.name}: {[i.message for i in issues]}"
