from __future__ import annotations

import subprocess
from dataclasses import replace
import sys
import sysconfig
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from flowgency.setup_assets import copilot_discovery_root
from flowgency.workflows.library import WorkflowLibrary
from flowgency.web.dependencies import build_services


def _semantic_tree_snapshot(root: Path) -> tuple[tuple[str, bytes], ...]:
    if not root.exists():
        return ()
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.name == ".lock" or path.suffix == ".lock" or "locks" in path.parts:
            continue
        rows.append((path.relative_to(root).as_posix(), path.read_bytes()))
    return tuple(rows)


def test_workflow_reference_validation_rejects_missing_source(workflow_env):
    from flowgency.workflows.validation import validate_workflow_references

    snapshot = workflow_env.store.load()
    source = workflow_env.library.source_path("delivery")
    source.unlink()
    before_config = snapshot.path.read_bytes()

    issues = validate_workflow_references(snapshot)

    assert {issue.code for issue in issues} == {"missing-blueprint"}
    assert {issue.scope for issue in issues} == {
        "teams.newsletter.workflows.board-a",
        "teams.support.workflows.board-a",
    }
    assert all(issue.field == "blueprint" for issue in issues)
    assert snapshot.path.read_bytes() == before_config
    assert not source.exists()


def test_workflow_reference_validation_requires_contract_error_code(
    workflow_env, monkeypatch
):
    from flowgency.workflows.models import ContractError
    from flowgency.workflows.validation import validate_workflow_references

    def inspect(library, blueprint_id):
        raise ContractError("", "private-path-and-secret")

    monkeypatch.setattr(WorkflowLibrary, "inspect", inspect)

    with pytest.raises(ContractError) as failure:
        validate_workflow_references(workflow_env.store.load())

    assert failure.value.message == "private-path-and-secret"


@pytest.mark.parametrize(
    ("source_text", "expected_code"),
    [
        ("states: [", "invalid-workflow-definition"),
        ("schema_version: 1\nid: delivery\n", "invalid-workflow-definition"),
    ],
)
def test_workflow_reference_validation_rejects_invalid_source(
    workflow_env, source_text, expected_code
):
    from flowgency.workflows.validation import validate_workflow_references

    source = workflow_env.library.source_path("delivery")
    source.write_text(source_text, encoding="utf-8")

    issues = validate_workflow_references(workflow_env.store.load())

    assert {issue.code for issue in issues} == {expected_code}


def test_workflow_reference_validation_rejects_identity_mismatch(workflow_env):
    from flowgency.workflows.validation import validate_workflow_references
    from tests._ticket_helpers import delivery_definition

    definition = delivery_definition()
    definition["id"] = "another-delivery"
    workflow_env.library.source_path("delivery").write_text(
        yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
    )

    issues = validate_workflow_references(workflow_env.store.load())

    assert {issue.code for issue in issues} == {"workflow-identity-mismatch"}


def test_workflow_reference_validation_inspects_shared_blueprint_once(
    workflow_env, monkeypatch
):
    from flowgency.workflows.validation import validate_workflow_references

    inspected = []
    original = WorkflowLibrary.inspect

    def inspect(library, blueprint_id):
        inspected.append(blueprint_id)
        return original(library, blueprint_id)

    monkeypatch.setattr(WorkflowLibrary, "inspect", inspect)

    assert validate_workflow_references(workflow_env.store.load()) == ()
    assert inspected == ["delivery"]


def test_workflow_reference_validation_ignores_unused_invalid_source(workflow_env):
    from flowgency.workflows.validation import validate_workflow_references

    unused = workflow_env.library.root / "unused" / "workflow.yaml"
    unused.parent.mkdir()
    unused.write_text("states: [", encoding="utf-8")

    assert validate_workflow_references(workflow_env.store.load()) == ()


def test_workflow_reference_validation_accepts_no_workflows(tmp_path, raw_config):
    from flowgency.configuration import ConfigStore
    from flowgency.workflows.validation import validate_workflow_references

    store = ConfigStore(tmp_path / "config.yaml")
    store.create(raw_config)

    assert validate_workflow_references(store.load()) == ()


def test_workflow_reference_validation_rejects_oversized_source(workflow_env):
    from flowgency.workflows.models import MAX_BLUEPRINT_SOURCE_BYTES
    from flowgency.workflows.validation import validate_workflow_references

    workflow_env.library.source_path("delivery").write_bytes(
        b"x" * (MAX_BLUEPRINT_SOURCE_BYTES + 1)
    )

    issues = validate_workflow_references(workflow_env.store.load())

    assert {issue.code for issue in issues} == {"source-too-large"}


def test_workflow_reference_validation_sanitizes_read_failure(
    workflow_env, monkeypatch
):
    from flowgency.workflows.validation import validate_workflow_references

    source = workflow_env.library.source_path("delivery")
    original = Path.read_bytes

    def read_bytes(path):
        if path == source:
            raise PermissionError("private-path-and-secret")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    issues = validate_workflow_references(workflow_env.store.load())

    assert {issue.code for issue in issues} == {"unreadable-workflow-definition"}
    assert all("private-path-and-secret" not in issue.message for issue in issues)


def test_workflow_reference_validation_rejects_reparse_source(
    workflow_env, monkeypatch
):
    import flowgency.workflows.library as library_module
    from flowgency.workflows.validation import validate_workflow_references

    source = workflow_env.library.source_path("delivery")
    original = library_module.is_symlink_or_reparse
    monkeypatch.setattr(
        library_module,
        "is_symlink_or_reparse",
        lambda path: path == source or original(path),
    )

    issues = validate_workflow_references(workflow_env.store.load())

    assert {issue.code for issue in issues} == {"unsafe-blueprint"}


def test_workflow_reference_validation_rejects_missing_workflow_library(workflow_env):
    from flowgency.workflows.validation import validate_workflow_references

    snapshot = workflow_env.store.load()
    raw = yaml.safe_load(snapshot.path.read_text(encoding="utf-8"))
    raw["flowgency"].pop("workflow_library", None)
    config = snapshot.config.model_copy(
        update={
            "flowgency": snapshot.config.flowgency.model_copy(
                update={"workflow_library": None}
            )
        }
    )

    issues = validate_workflow_references(
        replace(snapshot, raw=raw, config=config)
    )

    assert {issue.code for issue in issues} == {"missing-workflow-library"}
    assert {issue.scope for issue in issues} == {
        "teams.newsletter.workflows.board-a",
        "teams.support.workflows.board-a",
    }


def test_setup_status_blocks_missing_definition_without_blocking_dashboard(
    workflow_web_env,
):
    snapshot = workflow_web_env.store.load()
    source = workflow_web_env.library.source_path("delivery")
    original = source.read_bytes()
    config_before = snapshot.path.read_bytes()
    tickets_before = (
        _semantic_tree_snapshot(workflow_web_env.root_a),
        _semantic_tree_snapshot(workflow_web_env.root_b),
    )
    source.unlink()

    status = workflow_web_env.client.get("/setup/status").json()
    assert status["state"] == "incomplete"
    assert "redirect" not in status
    assert "Board A" in status["message"]
    assert workflow_web_env.client.get("/newsletter/").status_code == 200

    repeated = workflow_web_env.client.get("/setup/status").json()
    assert repeated == status

    inspection = build_services(snapshot.path)
    assert inspection.startup_error is None

    source.write_bytes(original)
    ready = workflow_web_env.client.get("/setup/status").json()
    assert ready["state"] == "ready"
    assert ready["redirect"] == "/"

    assert snapshot.path.read_bytes() == config_before
    assert source.read_bytes() == original
    assert _semantic_tree_snapshot(workflow_web_env.root_a) == tickets_before[0]
    assert _semantic_tree_snapshot(workflow_web_env.root_b) == tickets_before[1]


def test_setup_status_recovers_after_malformed_definition(workflow_web_env):
    snapshot = workflow_web_env.store.load()
    source = workflow_web_env.library.source_path("delivery")
    original = source.read_bytes()
    config_before = snapshot.path.read_bytes()
    tickets_before = (
        _semantic_tree_snapshot(workflow_web_env.root_a),
        _semantic_tree_snapshot(workflow_web_env.root_b),
    )
    source.write_text("states: [", encoding="utf-8")

    status = workflow_web_env.client.get("/setup/status").json()
    assert status["state"] == "incomplete"
    assert "redirect" not in status
    assert "definition is invalid" in status["message"]

    repeated = workflow_web_env.client.get("/setup/status").json()
    assert repeated == status

    source.write_bytes(original)
    ready = workflow_web_env.client.get("/setup/status").json()
    assert ready["state"] == "ready"
    assert ready["redirect"] == "/"

    assert snapshot.path.read_bytes() == config_before
    assert source.read_bytes() == original
    assert _semantic_tree_snapshot(workflow_web_env.root_a) == tickets_before[0]
    assert _semantic_tree_snapshot(workflow_web_env.root_b) == tickets_before[1]


def test_setup_status_accepts_configs_with_no_workflows(workflow_web_env):
    snapshot = workflow_web_env.store.load()
    workflow_web_env.create("No workflow fixture")
    support_ticket = (
        workflow_web_env.root_b
        / "support"
        / "board-a"
        / "tickets"
        / "seeded-ticket.md"
    )
    support_ticket.parent.mkdir(parents=True, exist_ok=True)
    support_ticket.write_text("support ticket contents\n", encoding="utf-8")
    raw = yaml.safe_load(snapshot.path.read_text(encoding="utf-8"))
    raw["flowgency"].pop("workflow_library", None)
    for team in raw["teams"].values():
        team["workflows"] = {}
    snapshot.path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    config_before = snapshot.path.read_bytes()
    source_before = workflow_web_env.library.source_path("delivery").read_bytes()
    tickets_before = (
        _semantic_tree_snapshot(workflow_web_env.root_a),
        _semantic_tree_snapshot(workflow_web_env.root_b),
    )

    status = workflow_web_env.client.get("/setup/status").json()
    repeated = workflow_web_env.client.get("/setup/status").json()

    assert status["state"] == "ready"
    assert status["redirect"] == "/"
    assert repeated == status
    assert build_services(snapshot.path).startup_error is None
    assert snapshot.path.read_bytes() == config_before
    assert workflow_web_env.library.source_path("delivery").read_bytes() == source_before
    assert _semantic_tree_snapshot(workflow_web_env.root_a) == tickets_before[0]
    assert _semantic_tree_snapshot(workflow_web_env.root_b) == tickets_before[1]


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


def test_config_example_documents_ticket_opt_in_without_silent_network_grant():
    example = (REPO_ROOT / "config.yaml.example").read_text(encoding="utf-8")
    normalized = " ".join(example.split())

    assert "allow_local_network: false" in example
    assert "ticket-local-network-required" in example
    assert "Allow local-network access" in example
    assert "Allows connections to local services and LAN hosts, not only Flowgency." in example
    assert "true by default" not in normalized.lower()


def test_example_team_guides_document_restricted_ticket_opt_in_for_copilot():
    documents = {
        "content": (REPO_ROOT / "examples" / "content-team" / "README.md").read_text(
            encoding="utf-8"
        ),
        "code-review": (REPO_ROOT / "examples" / "code-review-team" / "README.md").read_text(
            encoding="utf-8"
        ),
    }

    for document_name, text in documents.items():
        assert "allow_local_network: false" in text, document_name
        assert "Allow local-network access" in text, document_name
        assert "ticket-local-network-required" in text, document_name


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


@pytest.mark.parametrize(
    ("blueprint_id", "transition_id", "expected_outputs"),
    [
        ("research", "begin-synthesis", {"findings": True}),
        ("research", "close", {"conclusion": True}),
        ("software-delivery", "submit-review", {"notes": True}),
        ("software-delivery", "approve", {"review-notes": True}),
        ("software-delivery", "return-to-progress", {"review-notes": True}),
    ],
)
def test_shipped_result_transitions_require_durable_outputs(
    blueprint_id, transition_id, expected_outputs
):
    from flowgency.setup_assets import workflow_example_root

    definition = WorkflowLibrary(workflow_example_root()).inspect(blueprint_id).definition
    transition = definition.transition(transition_id)
    assert {use.field_id: use.required for use in transition.outputs} == expected_outputs
    assert not (set(expected_outputs) & {use.field_id for use in transition.inputs})


def test_shipped_transitions_without_results_still_declare_no_outputs():
    from flowgency.setup_assets import workflow_example_root

    research = WorkflowLibrary(workflow_example_root()).inspect("research").definition
    assert research.transition("start-exploration").outputs == ()
    assert research.transition("reopen").outputs == ()

    delivery = WorkflowLibrary(workflow_example_root()).inspect("software-delivery").definition
    approve = delivery.transition("approve")
    assert {use.field_id: use.required for use in approve.inputs} == {"approved": True}
    assert approve.preconditions[0].field_id == "approved"
    assert approve.preconditions[0].operator == "equals"
    assert approve.preconditions[0].value is True


def test_research_close_requires_output_even_when_input_is_supplied():
    from flowgency.setup_assets import workflow_example_root
    from flowgency.workflows.models import ContractError
    from flowgency.workflows.rules import evaluate_transition

    definition = WorkflowLibrary(workflow_example_root()).inspect("research").definition
    with pytest.raises(ContractError) as failure:
        evaluate_transition(
            definition, "close", "synthesizing", {},
            {"conclusion": "Attempt context only"}, {}, (),
        )
    assert failure.value.field_id == "conclusion"
    accepted = evaluate_transition(
        definition, "close", "synthesizing", {}, {},
        {"conclusion": "Durable result"}, (),
    )
    assert dict(accepted.effective_outputs) == {"conclusion": "Durable result"}


def test_research_close_rejects_type_invalid_required_output():
    from flowgency.setup_assets import workflow_example_root
    from flowgency.workflows.models import ContractError
    from flowgency.workflows.rules import evaluate_transition

    definition = WorkflowLibrary(workflow_example_root()).inspect("research").definition
    with pytest.raises(ContractError) as failure:
        evaluate_transition(
            definition, "close", "synthesizing", {}, {}, {"conclusion": 5}, (),
        )
    assert failure.value.code == "invalid-type"
    assert failure.value.field_id == "conclusion"


@pytest.mark.parametrize("relative", ["SKILL.md", "references/ticket-workflow-steps.md"])
def test_packaged_ticket_guidance_matches_discovery_source(relative):
    packaged = copilot_discovery_root() / ".github/skills/flowgency-setup" / relative
    discovery = REPO_ROOT / ".github/skills/flowgency-setup" / relative
    assert packaged.read_bytes() == discovery.read_bytes()
