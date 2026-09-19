import json
from pathlib import Path

import pytest

from flowgency.integrations.flowgency.copilot import CopilotIntegration


def event(kind, **data):
    return {"type": kind, "data": data}


@pytest.mark.parametrize("encoded", [False, True])
def test_arguments_preserve_messages_changes_and_attempts(encoded):
    arguments = {"path": str(Path("/repo") / "audit.txt")}
    if encoded:
        arguments = json.dumps(arguments)
    records = [
        event("assistant.message", content="Before"),
        event("tool.execution_start", toolCallId="write", toolName="create", arguments=arguments),
        event("tool.execution_complete", toolCallId="write", success=True,
              toolTelemetry={"metrics": {"linesAdded": 2}}),
        event("assistant.message", content="After"),
    ]
    text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(
        "\n".join(map(json.dumps, records)), Path("/repo")
    )
    assert text == "Before\nAfter"
    assert attempts == ["audit.txt"]
    assert [(change.path, change.status, change.lines_added) for change in changes] == [
        ("audit.txt", "added", 2)
    ]


@pytest.mark.parametrize("bad", [
    None, [], 5, "unexpected", {"type": []},
    {"type": "assistant.message", "data": "wrong"},
    event("assistant.message", content=["wrong"]),
    event("tool.execution_start", toolCallId=[], toolName="create", arguments={}),
    event("tool.execution_start", toolCallId="bad", toolName=[], arguments={}),
    event("tool.execution_start", toolCallId="bad", toolName="create", arguments="not-json"),
    event("tool.execution_start", toolCallId="bad", toolName="create", arguments="[]"),
    event("tool.execution_start", toolCallId="bad", toolName="create", arguments={"path": []}),
    event("tool.execution_complete", toolCallId=[], toolTelemetry="wrong"),
    {"type": "result", "usage": {"codeChanges": {"filesModified": [None, {}, 3]}}},
])
def test_bad_event_does_not_erase_other_information(bad):
    records = [
        event("assistant.message", content="Before"),
        event("tool.execution_start", toolCallId="write", toolName="edit",
              arguments={"path": "/repo/kept.txt"}),
        bad,
        event("tool.execution_complete", toolCallId="write", success=True,
              toolTelemetry={"metrics": {"linesAdded": "bad", "linesRemoved": 3}}),
        event("assistant.message_delta", deltaContent="NOT DISPLAYED"),
        event("assistant.reasoning", content="NOT DISPLAYED"),
        event("assistant.message", content="After"),
    ]
    raw = "\n".join(map(json.dumps, records)) + "\n{broken"
    text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(raw, Path("/repo"))
    assert text == "Before\nAfter"
    assert attempts == ["kept.txt"]
    assert [(change.path, change.lines_added, change.lines_removed) for change in changes] == [
        ("kept.txt", 0, 3)
    ]


@pytest.mark.parametrize("telemetry", [None, [], "bad", {"properties": [], "metrics": []}])
def test_bad_telemetry_keeps_known_write_attempt(telemetry):
    raw = "\n".join(map(json.dumps, [
        event("tool.execution_start", toolCallId="write", toolName="edit",
              arguments={"path": "/repo/kept.txt"}),
        event("tool.execution_complete", toolCallId="write", success=True, toolTelemetry=telemetry),
        event("assistant.message", content="Kept"),
    ]))
    text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(raw, Path("/repo"))
    assert text == "Kept"
    assert attempts == ["kept.txt"]
    assert [change.path for change in changes] == ["kept.txt"]


def test_denied_apply_patch_records_the_write_attempt_without_a_change():
    """A sandbox-denied apply_patch is a real, observed write attempt.

    Newer Copilot models write through ``apply_patch``, which names its target
    in the patch envelope rather than an ``arguments.path`` field. A denial
    (``success: false``, ``sandbox_denied``) must still surface as a write
    attempt, and must add nothing to the changed-file set.
    """
    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {Path('/repo') / 'blocked-note.txt'}\n"
        "+Flowgency workflow result reviewed.\n"
        "*** End Patch\n"
    )
    raw = "\n".join(map(json.dumps, [
        event("assistant.message", content="Making the single write attempt."),
        event("tool.execution_start", toolCallId="p1", toolName="apply_patch", arguments=patch),
        event(
            "tool.execution_complete",
            toolCallId="p1",
            success=False,
            error={"message": "Outside the sandbox's writable paths.", "code": "failure"},
            toolTelemetry={"properties": {"sandbox_denied": "true"}},
            sandboxed=True,
        ),
    ]))
    text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(raw, Path("/repo"))
    assert attempts == ["blocked-note.txt"]
    assert changes == []


def test_successful_apply_patch_records_add_update_and_delete_changes():
    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {Path('/repo') / 'new.txt'}\n"
        "+one\n"
        "+two\n"
        f"*** Update File: {Path('/repo') / 'edit.txt'}\n"
        "@@ ctx\n"
        " keep\n"
        "-old\n"
        "+fresh\n"
        f"*** Delete File: {Path('/repo') / 'gone.txt'}\n"
        "*** End Patch\n"
    )
    raw = "\n".join(map(json.dumps, [
        event("tool.execution_start", toolCallId="p2", toolName="apply_patch", arguments=patch),
        event("tool.execution_complete", toolCallId="p2", success=True,
              toolTelemetry={"properties": {"command": "apply_patch"}}),
    ]))
    text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(raw, Path("/repo"))
    assert sorted(attempts) == ["edit.txt", "gone.txt", "new.txt"]
    assert {
        change.path: (change.status, change.lines_added, change.lines_removed)
        for change in changes
    } == {
        "new.txt": ("added", 2, 0),
        "edit.txt": ("modified", 1, 1),
        "gone.txt": ("deleted", 0, 0),
    }


@pytest.mark.parametrize("other_cwd_name", ["controller-a", "controller-b"])
def test_relative_write_path_resolves_against_launch_dir_not_process_cwd(
    tmp_path, monkeypatch, other_cwd_name
):
    """A relative CLI path is relative to the CLI's own cwd (launch_dir).

    Parametrizing over two different controller cwds proves the result no
    longer depends on Flowgency's own process cwd.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    launch_dir = tmp_path / "runtime"
    launch_dir.mkdir()
    other_cwd = tmp_path / other_cwd_name
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    relative_path = str(Path("..") / "workspace" / "write-probe.txt")
    records = [
        event("tool.execution_start", toolCallId="w1", toolName="create",
              arguments={"path": relative_path}),
        event("tool.execution_complete", toolCallId="w1", success=False,
              toolTelemetry={"properties": {"sandbox_denied": "true"}}),
    ]
    _text, _changes, attempts = CopilotIntegration._parse_jsonl_output_details(
        "\n".join(map(json.dumps, records)), workspace_root, launch_dir,
    )
    assert attempts == ["write-probe.txt"]


def test_absolute_and_launch_relative_forms_of_same_target_agree(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    launch_dir = tmp_path / "runtime"
    launch_dir.mkdir()
    absolute_path = str(workspace_root / "same.txt")
    relative_path = str(Path("..") / "workspace" / "same.txt")

    def attempts_for(path):
        records = [
            event("tool.execution_start", toolCallId="w", toolName="create",
                  arguments={"path": path}),
        ]
        _text, _changes, attempts = CopilotIntegration._parse_jsonl_output_details(
            "\n".join(map(json.dumps, records)), workspace_root, launch_dir,
        )
        return attempts

    assert attempts_for(absolute_path) == attempts_for(relative_path) == ["same.txt"]


def test_launch_relative_target_outside_workspace_keeps_original_identity(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    launch_dir = tmp_path / "runtime"
    launch_dir.mkdir()
    relative_path = str(Path("..") / "outside" / "secret.txt")
    records = [
        event("tool.execution_start", toolCallId="w", toolName="create",
              arguments={"path": relative_path}),
    ]
    _text, _changes, attempts = CopilotIntegration._parse_jsonl_output_details(
        "\n".join(map(json.dumps, records)), workspace_root, launch_dir,
    )
    assert attempts == [relative_path]


def test_apply_patch_relative_target_resolves_against_launch_dir():
    workspace_root = Path("/repo/workspace")
    launch_dir = Path("/repo/runtime")
    relative_target = str(Path("..") / "workspace" / "patched.txt")
    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {relative_target}\n"
        "+line\n"
        "*** End Patch\n"
    )
    records = [
        event("tool.execution_start", toolCallId="p1", toolName="apply_patch", arguments=patch),
        event("tool.execution_complete", toolCallId="p1", success=True,
              toolTelemetry={"properties": {"command": "apply_patch"}}),
    ]
    _text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(
        "\n".join(map(json.dumps, records)), workspace_root, launch_dir,
    )
    assert attempts == ["patched.txt"]
    assert changes[0].path == "patched.txt"


def test_omitted_launch_dir_keeps_prior_direct_call_behavior():
    """Direct callers that don't pass launch_dir keep resolving against cwd."""
    records = [
        event("tool.execution_start", toolCallId="w", toolName="create",
              arguments={"path": str(Path("/repo") / "kept.txt")}),
    ]
    _text, _changes, attempts = CopilotIntegration._parse_jsonl_output_details(
        "\n".join(map(json.dumps, records)), Path("/repo"),
    )
    assert attempts == ["kept.txt"]
