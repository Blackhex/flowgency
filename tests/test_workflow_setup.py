from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path
from zipfile import ZipFile

import yaml

from flowgency.setup_assets import copilot_discovery_root
from flowgency.workflows.library import WorkflowLibrary

REPO_ROOT = Path(__file__).parents[1]
KB = REPO_ROOT / "kb"


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


def test_kb_data_formats_documents_ticket_workflow_model():
    text = (KB / "data-formats.md").read_text(encoding="utf-8")
    lowered = text.lower()
    # The old work-item pipeline is retired as the documented data model.
    assert "## observation format" not in lowered
    assert "## proposal format" not in lowered
    assert "## decision format" not in lowered
    assert "clickable pipeline banners" not in lowered
    # The current ticket workflow contract is documented instead.
    for term in ("ticket", "workflow", "state", "transition", "field", "assessment"):
        assert term in lowered, term
    assert "workflowdefinition" in lowered


def test_kb_getting_started_documents_ticket_workflows():
    text = (KB / "getting-started.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "### pipeline" not in lowered
    assert "links observations to proposals" not in lowered
    for term in ("ticket", "workflow", "transition"):
        assert term in lowered, term


def test_kb_integrations_documents_live_ticket_tools():
    text = (KB / "integrations.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "ticket tool" in lowered
    # Copilot transport is implemented; other integrations fail closed.
    assert "fail closed" in lowered or "fail-closed" in lowered
    # Live tools do not widen filesystem, Git/GitHub, or shell authority.
    assert "workspace write" in lowered or "write access" in lowered


def test_kb_directory_structure_lists_ticket_storage_not_pipeline_dirs():
    text = (KB / "directory-structure.md").read_text(encoding="utf-8")
    assert "observations/" not in text
    assert "proposals/" not in text
    assert "decisions/" not in text
    lowered = text.lower()
    assert "workflow-library" in lowered
    assert "ticket" in lowered


def _local_workflow_instances(document: str) -> list[dict]:
    data = yaml.safe_load(document)
    instances: list[dict] = []
    for team in (data.get("teams") or {}).values():
        for instance in (team.get("workflows") or {}).values():
            instances.append(instance)
    return instances


def test_documented_config_examples_declare_local_workflow_instances():
    example = (REPO_ROOT / "config.yaml.example").read_text(encoding="utf-8")
    data = yaml.safe_load(example)
    assert "workflow_library" in data["flowgency"]
    instances = _local_workflow_instances(example)
    assert instances, "config.yaml.example must document at least one workflow instance"
    for instance in instances:
        assert set(instance) >= {"name", "blueprint", "integration"}
        assert instance["integration"] == "local"
        assert instance["integration_config"]["root"]


def test_installed_distribution_validates_shipped_workflow_examples(tmp_path):
    """Load and validate the examples from a built wheel, not the worktree tree."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(tmp_path),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(tmp_path.glob("flowgency-*.whl"))
    assert len(wheels) == 1

    extract_dir = tmp_path / "installed"
    with ZipFile(wheels[0]) as archive:
        archive.extractall(extract_dir)

    # Isolate: -S skips site processing so the editable finder for the worktree
    # package is never registered; the installed tree resolves first, deps follow.
    deps = [sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]]
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(extract_dir)!r})\n"
        f"sys.path.extend({deps!r})\n"
        "import flowgency\n"
        f"assert flowgency.__file__.startswith({str(extract_dir)!r}), flowgency.__file__\n"
        "from flowgency.setup_assets import workflow_example_root\n"
        "from flowgency.workflows.library import WorkflowLibrary\n"
        "root = workflow_example_root()\n"
        f"assert str(root).startswith({str(extract_dir)!r}), root\n"
        "items = WorkflowLibrary(root).list()\n"
        "assert all(not i.issues for i in items), [(i.blueprint_id, i.issues) for i in items]\n"
        "names = {i.snapshot.definition.name for i in items}\n"
        "assert names == {'Software delivery', 'Research'}, names\n"
        "for i in items:\n"
        "    d = i.snapshot.definition\n"
        "    assert d.state(d.initial_state) is not None\n"
        "    assert 'evidence_required' not in d.model_dump_json()\n"
        "print('OK')\n"
    )
    proof = subprocess.run(
        [sys.executable, "-S", "-c", script],
        capture_output=True,
        text=True,
    )
    assert proof.returncode == 0, proof.stdout + proof.stderr
    assert proof.stdout.strip().endswith("OK")


def test_kb_configuration_documents_generated_outbox_and_memory_zones():
    """kb/configuration.md must list both generated write zones; omitting outbox
    contradicts AGENTS.md, kb/integrations.md and the live zones.py/launch_view.py."""
    text = (KB / "configuration.md").read_text(encoding="utf-8")
    # Both generated zones must appear as read+write; instructions stay read-only.
    assert "<launch>/.flowgency/outbox" in text, (
        "kb/configuration.md is missing the generated <launch>/.flowgency/outbox zone"
    )
    assert "<launch>/.flowgency/memory" in text
    assert "`<launch>/instructions` is `read` only" in text


def test_skill_does_not_propose_workflow_instance_id_to_user():
    """SKILL.md must not ask users to name or approve technical workflow instance IDs;
    stable hidden IDs must be generated for both custom blueprint definitions and
    configured instances from approved display names only."""
    from flowgency.setup_assets import copilot_discovery_root

    skill = (
        copilot_discovery_root()
        / ".github/skills/flowgency-setup/SKILL.md"
    ).read_text(encoding="utf-8")
    # The proposal list must not include the instance ID.
    assert "the workflow instance ID" not in skill, (
        "SKILL.md proposes 'the workflow instance ID' to the user; IDs must be hidden"
    )
    # The skill must state that hidden IDs cover both blueprint definitions and instances.
    assert "blueprint" in skill.lower() and "instance" in skill.lower()
    normalized = " ".join(skill.split()).lower()
    assert "stable hidden" in normalized, (
        "SKILL.md must state that stable hidden IDs are generated"
    )
