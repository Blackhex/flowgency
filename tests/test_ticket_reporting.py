from __future__ import annotations

from flowgency.tickets.reporting import (
    append_ticket_reporting_protocol,
    build_ticket_reporting_protocol,
)


def test_build_ticket_reporting_protocol_includes_ticket_tool_contract():
    protocol = build_ticket_reporting_protocol(
        workflows_available=True,
        tool_mode="allowlist",
        tool_names=("read", "search"),
    )

    assert "Use Flowgency ticket tools for work-item reporting." in protocol
    assert "Only an accepted transition changes ticket state." in protocol
    assert "Write memory only in the provided memory directory." in protocol
    assert "Your granted tool policy is an allowlist: read, search." in protocol
    assert "observations" not in protocol.lower()
    assert "proposals" not in protocol.lower()
    assert "decisions" not in protocol.lower()


def test_build_ticket_reporting_protocol_reports_when_no_workflows_are_available():
    protocol = build_ticket_reporting_protocol(
        workflows_available=False,
        tool_mode="none",
        tool_names=(),
    )

    assert "This team currently has no configured workflows." in protocol
    assert "You have been granted no tools." in protocol


def test_append_ticket_reporting_protocol_appends_once():
    first = append_ticket_reporting_protocol(
        "Complete the task.",
        workflows_available=True,
        tool_mode="all",
        tool_names=(),
    )
    second = append_ticket_reporting_protocol(
        first,
        workflows_available=True,
        tool_mode="all",
        tool_names=(),
    )

    assert second == first
    assert second.startswith("Complete the task.")


def test_append_ticket_reporting_protocol_uses_protocol_when_task_is_blank():
    appended = append_ticket_reporting_protocol(
        "   ",
        workflows_available=False,
        tool_mode="none",
        tool_names=(),
    )

    assert appended.startswith("## Flowgency ticket reporting protocol")