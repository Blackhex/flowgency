from __future__ import annotations

from agency.records.outbox import (
    OUTBOX_RELATIVE_MEMORY,
    OUTBOX_RELATIVE_OBSERVATIONS,
    OUTBOX_RELATIVE_PROPOSALS,
)
from agency.records.protocol import append_reporting_protocol, build_reporting_protocol


def test_protocol_names_every_outbox_directory():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert OUTBOX_RELATIVE_OBSERVATIONS in text
    assert OUTBOX_RELATIVE_PROPOSALS in text
    assert OUTBOX_RELATIVE_MEMORY in text


def test_protocol_states_that_agency_assigns_identity_fields():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "agent" in text
    assert "date" in text
    assert "status" in text


def test_protocol_reports_an_allowlisted_tool_policy():
    text = build_reporting_protocol(tool_mode="allowlist", tool_names=("read", "search"))

    assert "read, search" in text


def test_protocol_reports_an_unrestricted_tool_policy():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "all tools" in text


def test_append_places_the_protocol_after_the_task():
    combined = append_reporting_protocol(
        "Run the suite.", tool_mode="all", tool_names=()
    )

    assert combined.startswith("Run the suite.")
    assert OUTBOX_RELATIVE_OBSERVATIONS in combined


def test_append_to_blank_task_input_still_yields_the_protocol():
    combined = append_reporting_protocol("", tool_mode="all", tool_names=())

    assert OUTBOX_RELATIVE_OBSERVATIONS in combined


def test_append_is_idempotent():
    once = append_reporting_protocol("Run.", tool_mode="all", tool_names=())
    twice = append_reporting_protocol(once, tool_mode="all", tool_names=())

    assert once == twice
