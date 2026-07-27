from __future__ import annotations

from pathlib import Path

import yaml

from agency.blueprints import BlueprintLibrary
from agency.configuration import ConfigStore
from agency.prompts import PromptStore, validate_prompt_catalogs


VALID_PROMPT = "---\nname: diff-review\ndescription: Review the change set.\n---\n\nReview it.\n"
NO_FRONTMATTER_PROMPT = "# Diff Review\n\nReview the change set.\n"


def _write_blueprint(library_root: Path, key: str, prompt_source: str) -> None:
    blueprint = library_root / key
    prompts = blueprint / ".agents" / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {key.title()}\n", encoding="utf-8")
    (prompts / "diff-review.prompt.md").write_text(prompt_source, encoding="utf-8")


def _write_config(tmp_path: Path, agents: list[dict]) -> Path:
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
                "agents": agents,
            }
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def _validate(tmp_path: Path, config_path: Path):
    snapshot = ConfigStore(config_path).load()
    library = BlueprintLibrary(tmp_path / "agent-library")
    store = PromptStore(tmp_path / "prompts")
    return validate_prompt_catalogs(snapshot, library, store)


def _agent(name: str, blueprint: str) -> dict:
    return {"name": name, "blueprint": blueprint, "integration": "copilot"}


def test_malformed_blueprint_prompt_keeps_its_own_message_and_hint(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", NO_FRONTMATTER_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    issues = _validate(tmp_path, config_path)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == "invalid-prompt-frontmatter"
    assert issue.field == ".agents/prompts/diff-review.prompt.md"
    assert issue.message == (
        "Prompt markdown frontmatter is incomplete: .agents/prompts/diff-review.prompt.md."
    )
    assert issue.corrective_hint == "Terminate the YAML frontmatter before the prompt body."
    assert issue.scope == "groups.reviewers.agents.reviewer"


def test_one_broken_blueprint_shared_by_two_agents_reports_once(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", NO_FRONTMATTER_PROMPT)
    config_path = _write_config(
        tmp_path,
        [_agent("first", "reviewer"), _agent("second", "reviewer")],
    )

    issues = _validate(tmp_path, config_path)

    assert len(issues) == 1


def test_valid_blueprint_prompt_reports_no_issues(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    assert _validate(tmp_path, config_path) == ()


def test_name_collision_across_scopes_keeps_its_own_code_and_hint(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    private = tmp_path / "prompts" / "reviewers" / "reviewer"
    private.mkdir(parents=True, exist_ok=True)
    (private / "diff-review.prompt.md").write_text(VALID_PROMPT, encoding="utf-8")
    agent = _agent("reviewer", "reviewer")
    agent["prompts"] = ["diff-review"]
    config_path = _write_config(tmp_path, [agent])

    issues = _validate(tmp_path, config_path)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == "invalid-prompt-catalog"
    assert issue.scope == "groups.reviewers.agents.reviewer"
    assert issue.field == "prompts"
    assert issue.corrective_hint == "Use unique prompt names across blueprint and instance scopes."


def test_missing_instance_prompt_still_reports_its_own_code(tmp_path):
    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    agent = _agent("reviewer", "reviewer")
    agent["prompts"] = ["absent"]
    config_path = _write_config(tmp_path, [agent])

    issues = _validate(tmp_path, config_path)

    assert len(issues) == 1
    assert issues[0].code == "missing-instance-prompt"


def test_build_services_reports_prompt_issues_without_failing_startup(tmp_path):
    from agency.web.dependencies import build_services

    _write_blueprint(tmp_path / "agent-library", "reviewer", NO_FRONTMATTER_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    services = build_services(config_path)

    assert services.startup_error is None
    assert services.instances is not None
    assert services.blueprint_library is not None
    assert [issue.code for issue in services.prompt_issues] == ["invalid-prompt-frontmatter"]


def test_build_services_reports_no_prompt_issues_for_a_valid_library(tmp_path):
    from agency.web.dependencies import build_services

    _write_blueprint(tmp_path / "agent-library", "reviewer", VALID_PROMPT)
    config_path = _write_config(tmp_path, [_agent("reviewer", "reviewer")])

    services = build_services(config_path)

    assert services.startup_error is None
    assert services.prompt_issues == ()
