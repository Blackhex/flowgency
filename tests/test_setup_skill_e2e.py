from __future__ import annotations

from pathlib import Path
import re

import yaml

from agency import cli
from agency.web.dependencies import build_services


REPO_ROOT = Path(__file__).parents[1]
TEMPLATES_PATH = REPO_ROOT / "skills" / "agency-setup" / "references" / "templates.md"

SUBSTITUTIONS = {
    "{ROLE_NAME}": "Reviewer",
    "{LANGUAGE_OR_DOMAIN}": "Python",
    "{REUSABLE_ROLE_MISSION}": "Review change sets for defects.",
    "{RESPONSIBILITY}": "Report findings with file and line citations.",
    "{skill}": "diff-review",
    "{Skill Title}": "Diff Review",
    "{CONCRETE_TRIGGER_CONDITION}": "reviewing a change set",
    "{EXPECTED_RESULT}": "Findings recorded through the configured pipeline.",
    "{TASK}": "the review",
    "{TASK_SPECIFIC_BOUNDARY}": "Ignore style and formatting.",
    "{prompt}": "diff-review",
    "{Prompt Title}": "Diff Review",
    "{ONE_LINE_PURPOSE}": "Review the current change set for defects.",
    "{OPTIONAL_ARGUMENT_SUMMARY}": "Optional review focus",
    "{TASK_INSTRUCTIONS}": "Review the current change set and report findings.",
}


def _template_block(document: str, heading: str, expected_token: str) -> str:
    marker = f"\n## {heading}\n"
    start = document.index(marker) + len(marker)
    section = document[start:]
    # Do NOT truncate at the first \n## — template bodies contain ## headings inside the fence.
    fence = "```markdown\n"
    open_at = section.index(fence) + len(fence)
    close_at = section.index("\n```", open_at)
    block = section[open_at:close_at] + "\n"
    assert expected_token in block, (
        f"Template block for '{heading}' does not contain expected token {expected_token!r}; "
        f"this likely means the extraction targeted the wrong fenced block"
    )
    return block


def _render(block: str) -> str:
    rendered = block
    for placeholder, value in SUBSTITUTIONS.items():
        rendered = rendered.replace(placeholder, value)
    leftover = re.search(r"\{[A-Za-z_ ]+\}", rendered)
    assert leftover is None, f"Unsubstituted placeholder {leftover.group(0)!r}"
    return rendered


def _materialize(tmp_path: Path) -> Path:
    document = TEMPLATES_PATH.read_text(encoding="utf-8")
    library_root = tmp_path / "agent-library"
    blueprint = library_root / "reviewer"
    skill_dir = blueprint / ".agents" / "skills" / "diff-review"
    prompt_dir = blueprint / ".agents" / "prompts"
    skill_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(
        _render(_template_block(document, "Blueprint AGENTS.md", "{REUSABLE_ROLE_MISSION}")),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        _render(_template_block(document, "Standard Agent Skill", "{CONCRETE_TRIGGER_CONDITION}")),
        encoding="utf-8",
    )
    (prompt_dir / "diff-review.prompt.md").write_text(
        _render(_template_block(document, "Standard Task Prompt", "{ONE_LINE_PURPOSE}")),
        encoding="utf-8",
    )
    return library_root


def _write_config(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    group_root = tmp_path / "groups" / "reviewers"
    workspace.mkdir(parents=True, exist_ok=True)
    group_root.mkdir(parents=True, exist_ok=True)
    raw = {
        "schema_version": 4,
        "agency": {
            "title": "Agency",
            "default_group": "reviewers",
            "ai_backend": "copilot",
            "agent_library": str(tmp_path / "agent-library"),
            "compilation_cache": str(tmp_path / "compiled-agents"),
            "memory_store": str(tmp_path / "memory-store"),
            "prompt_store": str(tmp_path / "prompts"),
        },
        "groups": {
            "reviewers": {
                "name": "Reviewers",
                "workspace_path": str(workspace),
                "path": str(group_root),
                "default_integration": "copilot",
                "agents": [
                    {
                        "name": "reviewer",
                        "blueprint": "reviewer",
                        "integration": "copilot",
                        "routines": [
                            {
                                "id": "diff-review",
                                "prompt": {"scope": "blueprint", "name": "diff-review"},
                                "schedule": {"at": "09:00"},
                            }
                        ],
                    }
                ],
            }
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def test_setup_templates_produce_a_library_the_app_accepts(tmp_path):
    _materialize(tmp_path)
    config_path = _write_config(tmp_path)

    services = build_services(config_path)

    assert services.startup_error is None
    assert services.prompt_issues == ()

    inspection = services.blueprint_library.inspect("reviewer")
    assert inspection.title == "Reviewer"
    assert list(inspection.skills) == ["diff-review"]
    prompt = next(item for item in inspection.prompts if item.name == "diff-review")
    assert prompt.description == "Review the current change set for defects."
    assert prompt.argument_hint == "Optional review focus"
    assert prompt.body.strip()


def test_validate_accepts_a_library_built_from_the_templates(tmp_path, capsys):
    _materialize(tmp_path)
    config_path = _write_config(tmp_path)

    exit_code = cli.run(["--config", str(config_path), "validate"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No validation issues found." in captured.out


def test_validate_rejects_the_same_library_without_prompt_frontmatter(tmp_path, capsys):
    library_root = _materialize(tmp_path)
    prompt_path = library_root / "reviewer" / ".agents" / "prompts" / "diff-review.prompt.md"
    body = prompt_path.read_text(encoding="utf-8").split("\n---\n", 1)[1].lstrip("\n")
    prompt_path.write_text(body, encoding="utf-8")
    config_path = _write_config(tmp_path)

    exit_code = cli.run(["--config", str(config_path), "validate"])
    captured = capsys.readouterr()

    assert exit_code == 3
    assert "Prompt markdown frontmatter is incomplete" in captured.err
    assert "Terminate the YAML frontmatter before the prompt body." in captured.err


def test_both_skill_copies_expose_identical_templates():
    canonical = REPO_ROOT / "skills" / "agency-setup" / "references" / "templates.md"
    discovery = REPO_ROOT / ".github" / "skills" / "agency-setup" / "references" / "templates.md"
    assert discovery.read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8")
