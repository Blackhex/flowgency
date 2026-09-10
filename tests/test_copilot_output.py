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
