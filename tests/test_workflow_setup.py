from __future__ import annotations

from pathlib import Path

from flowgency.setup_assets import copilot_discovery_root
from flowgency.workflows.library import WorkflowLibrary


def test_shipped_workflows_are_valid_and_local_only():
    from flowgency.setup_assets import workflow_example_root

    library = WorkflowLibrary(workflow_example_root())
    examples = library.list()
    assert all(not item.issues for item in examples), [
        (item.blueprint_id, item.issues) for item in examples if item.issues
    ]
    assert {item.snapshot.definition.name for item in examples} == {
        "Software delivery",
        "Research",
    }
    for item in examples:
        definition = item.snapshot.definition
        assert definition.state(definition.initial_state) is not None
        assert "evidence_required" not in definition.model_dump_json()


def test_setup_uses_ticket_reporting_without_native_authority():
    skill = (
        copilot_discovery_root() / ".github/skills/flowgency-setup/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "flowgency.workflow_library" in skill
    assert "ticket-workflow-steps.md" in skill
    assert "observation-system-steps.md" not in skill
    assert "one authoritative canonical Flowgency config" in skill
