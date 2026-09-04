from __future__ import annotations

from pathlib import Path
import re


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


def test_setup_assets_use_team_domain_terms():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "agency" / "setup_assets").glob("**/*.md")
    )

    assert "team display name" in text.lower()
    assert "teams.<team-id>" in text
    assert "<root>/teams/<team-id>" in text
    assert not re.search(r"\bgroup display name\b", text, re.IGNORECASE)
