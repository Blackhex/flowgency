from __future__ import annotations

import os
from pathlib import Path

import pytest

from agency.records.outbox import create_outbox
from agency.records.validation import (
    MAX_OUTBOX_ENTRIES_PER_KIND,
    MAX_RECORD_BYTES,
    MAX_RECORDS_PER_KIND,
    validate_outbox,
)

WRITABLE = frozenset({"paul"})


@pytest.fixture
def outbox(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()
    return create_outbox(launch, memory_files={})


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


OBSERVATION = "---\nstatus: open\n---\n\n**Suite is red.** Three tests fail.\n"

PROPOSAL = (
    "---\n"
    "execution_agent: paul\n"
    "questions:\n"
    "  - id: fix\n"
    "    prompt: Fix the failing tests?\n"
    "    type: boolean\n"
    "---\n\n"
    "**Fix the suite.**\n"
)


def test_valid_observation_is_accepted(outbox):
    write(outbox.observations / "a.md", OBSERVATION)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert result.ok
    assert [c.kind for c in result.accepted] == ["observation"]
    assert result.accepted[0].body.startswith("**Suite is red.**")


def test_valid_proposal_is_accepted(outbox):
    write(outbox.proposals / "p.md", PROPOSAL)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert result.ok
    assert [c.kind for c in result.accepted] == ["proposal"]


def test_empty_outbox_is_valid_and_empty(outbox):
    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert result.ok
    assert result.accepted == ()


def test_non_markdown_file_is_rejected(outbox):
    write(outbox.observations / "notes.txt", "hello")

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "not a markdown file" in result.rejected[0].reason


def test_subdirectory_is_rejected(outbox):
    (outbox.observations / "nested").mkdir()

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "subdirectories" in result.rejected[0].reason


def test_oversized_record_is_rejected(outbox):
    write(outbox.observations / "big.md", "x" * (MAX_RECORD_BYTES + 1))

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "exceeds" in result.rejected[0].reason


def test_too_many_records_are_rejected(outbox):
    for index in range(MAX_RECORDS_PER_KIND + 1):
        write(outbox.observations / f"r{index:02d}.md", OBSERVATION)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert any("too many" in item.reason for item in result.rejected)


def test_proposal_without_execution_agent_is_rejected(outbox):
    write(outbox.proposals / "p.md", PROPOSAL.replace("execution_agent: paul\n", ""))

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "execution_agent" in result.rejected[0].reason


def test_proposal_naming_a_non_writable_executor_is_rejected(outbox):
    write(outbox.proposals / "p.md", PROPOSAL.replace("paul", "gurney"))

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "not a writable" in result.rejected[0].reason


def test_record_with_unparsable_frontmatter_is_rejected(outbox):
    write(outbox.observations / "a.md", "---\n: : :\n---\n\nbody\n")

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "front matter" in result.rejected[0].reason


def test_record_with_empty_body_is_rejected(outbox):
    write(outbox.observations / "a.md", "---\nstatus: open\n---\n\n   \n")

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "empty" in result.rejected[0].reason


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlinked_record_is_rejected(outbox, tmp_path: Path):
    target = tmp_path / "outside.md"
    write(target, OBSERVATION)
    (outbox.observations / "link.md").symlink_to(target)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "not a regular file" in result.rejected[0].reason


def test_too_many_entries_with_non_markdown_files_are_rejected(outbox):
    # Create more than MAX_OUTBOX_ENTRIES_PER_KIND non-.md files
    # This tests the fix for memory exhaustion: the code should reject
    # the kind before materializing all entries, not after filtering to .md files
    for index in range(MAX_OUTBOX_ENTRIES_PER_KIND + 5):
        write(outbox.observations / f"entry_{index:03d}.txt", "not markdown")

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    # The entire kind should be rejected due to too many entries
    assert len([r for r in result.rejected if r.kind == "observation"]) == 1
    rejection = [r for r in result.rejected if r.kind == "observation"][0]
    assert "too many entries: more than" in rejection.reason
    # No records should be accepted
    assert len(result.accepted) == 0


def test_proposal_with_long_executor_name_is_truncated_in_rejection(outbox):
    long_executor = "a" * 150  # Executor name longer than 80 chars
    proposal = (
        f"---\n"
        f"execution_agent: {long_executor}\n"
        f"questions:\n"
        f"  - id: fix\n"
        f"    prompt: Fix?\n"
        f"    type: boolean\n"
        f"---\n\n"
        f"**Proposal body.**\n"
    )
    write(outbox.proposals / "p.md", proposal)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    reason = result.rejected[0].reason
    # The reason should contain a truncated executor (max 80 chars)
    assert "execution_agent" in reason
    # The truncated version should be in the reason
    assert "a" * 80 in reason
    # The full oversized executor should NOT be in the reason
    assert long_executor not in reason
    # Ensure it's actually truncated to 80 chars, not more
    assert reason.count("a" * 80) >= 1


def test_record_with_invalid_utf8_is_rejected(outbox):
    # Write a file with genuinely invalid UTF-8 bytes
    invalid_file = outbox.observations / "invalid.md"
    invalid_file.write_bytes(b"---\nx: 1\n---\n\n\xff\xfe body")

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "record is not valid UTF-8" in result.rejected[0].reason

