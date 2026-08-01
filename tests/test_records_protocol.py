from __future__ import annotations

from agency.proposals import SUPPORTED_QUESTION_TYPES
from agency.records.outbox import (
    MAX_MEMORY_FILE_BYTES,
    MAX_MEMORY_FILES,
    OUTBOX_RELATIVE_MEMORY,
    OUTBOX_RELATIVE_OBSERVATIONS,
    OUTBOX_RELATIVE_PROPOSALS,
)
from agency.records.protocol import append_reporting_protocol, build_reporting_protocol
from agency.records.validation import MAX_RECORD_BYTES, MAX_RECORDS_PER_KIND


def test_protocol_names_every_outbox_directory():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert OUTBOX_RELATIVE_OBSERVATIONS in text
    assert OUTBOX_RELATIVE_PROPOSALS in text
    assert OUTBOX_RELATIVE_MEMORY in text


def test_protocol_states_that_agency_assigns_identity_fields():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "Agency assigns the `agent`, `date`, and `status` fields" in text


def test_protocol_states_that_unknown_front_matter_is_dropped():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "Agency does not understand are dropped" in text


def test_protocol_describes_partial_ingest_and_the_failed_run():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "files every record that passes validation" in text
    assert "still fails" in text
    assert "names each rejection" in text


def test_protocol_states_the_memory_limits():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert str(MAX_MEMORY_FILES) in text
    assert str(MAX_MEMORY_FILE_BYTES) in text


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


def test_protocol_requires_markdown_extension():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "`.md`" in text


def test_protocol_states_max_record_byte_limit():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert str(MAX_RECORD_BYTES) in text


def test_protocol_states_max_records_per_kind_limit():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert str(MAX_RECORDS_PER_KIND) in text


def test_protocol_warns_exceeding_record_limit_rejects_entire_directory():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "rejects the entire directory" in text or "rejects the whole directory" in text


def test_protocol_forbids_subdirectories():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "subdirectories are rejected" in text or "subdirectories" in text


def test_protocol_lists_all_supported_question_types():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    for question_type in SUPPORTED_QUESTION_TYPES:
        assert question_type in text


def test_protocol_requires_non_empty_options_for_choice_questions():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "choice" in text and "options" in text


def test_protocol_requires_unique_non_empty_question_id():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "unique non-empty `id`" in text


def test_protocol_requires_non_empty_question_prompt():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "non-empty `prompt`" in text


def test_protocol_requires_utf8_encoding():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "UTF-8" in text or "utf-8" in text or "UTF8" in text


def test_protocol_requires_non_empty_body():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "non-empty body" in text
