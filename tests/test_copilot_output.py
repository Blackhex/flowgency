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
