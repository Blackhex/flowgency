from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agency.records.frontmatter import parse_frontmatter
from agency.records.ingest import ingest_records
from agency.records.validation import OutboxValidation, RecordCandidate

NOW = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def dirs(tmp_path: Path):
    observations = tmp_path / "observations"
    proposals = tmp_path / "proposals"
    observations.mkdir()
    proposals.mkdir()
    return observations, proposals


def candidate(kind="observation", meta=None, body="**Suite is red.** Details."):
    return RecordCandidate(
        kind=kind,
        source_name="a.md",
        meta=dict(meta or {}),
        body=body,
    )


def ingest(dirs, *candidates, agent_name="duncan"):
    observations, proposals = dirs
    return ingest_records(
        OutboxValidation(accepted=tuple(candidates), rejected=()),
        observations_dir=observations,
        proposals_dir=proposals,
        agent_name=agent_name,
        now=NOW,
        job_id="job123",
    )


def test_observation_lands_in_the_observations_directory(dirs):
    observations, _ = dirs

    written = ingest(dirs, candidate())

    assert len(written) == 1
    assert written[0].path.parent == observations
    assert written[0].path.name == "2026-07-31-suite-is-red.md"


def test_agency_stamps_agent_date_and_status(dirs):
    written = ingest(dirs, candidate(meta={"agent": "someone-else", "date": "1999-01-01"}))

    meta, _ = parse_frontmatter(written[0].path.read_text(encoding="utf-8"))
    assert meta["agent"] == "duncan"
    assert meta["date"] == "2026-07-31"
    assert meta["status"] == "open"


def test_author_supplied_fields_other_than_the_stamped_ones_survive(dirs):
    written = ingest(dirs, candidate(meta={"ttl_days": 14, "float": True}))

    meta, _ = parse_frontmatter(written[0].path.read_text(encoding="utf-8"))
    assert meta["ttl_days"] == 14
    assert meta["float"] is True


def test_explicit_slug_is_used_when_valid(dirs):
    written = ingest(dirs, candidate(meta={"slug": "custom-name"}))

    assert written[0].path.name == "2026-07-31-custom-name.md"


def test_invalid_slug_falls_back_to_the_title(dirs):
    written = ingest(dirs, candidate(meta={"slug": "../escape"}))

    assert written[0].path.name == "2026-07-31-suite-is-red.md"


def test_untitled_record_falls_back_to_the_job_id(dirs):
    written = ingest(dirs, candidate(body="plain text with no bold"))

    assert written[0].path.name == "2026-07-31-job123.md"


def test_colliding_names_gain_a_numeric_suffix(dirs):
    written = ingest(dirs, candidate(), candidate(), candidate())

    assert [item.path.name for item in written] == [
        "2026-07-31-suite-is-red.md",
        "2026-07-31-suite-is-red-2.md",
        "2026-07-31-suite-is-red-3.md",
    ]


def test_collision_with_a_preexisting_file_is_avoided(dirs):
    observations, _ = dirs
    (observations / "2026-07-31-suite-is-red.md").write_text("old", encoding="utf-8")

    written = ingest(dirs, candidate())

    assert written[0].path.name == "2026-07-31-suite-is-red-2.md"
    assert (observations / "2026-07-31-suite-is-red.md").read_text(encoding="utf-8") == "old"


def test_proposal_lands_in_the_proposals_directory_with_open_status(dirs):
    _, proposals = dirs

    written = ingest(dirs, candidate(kind="proposal", meta={"execution_agent": "paul"}))

    assert written[0].path.parent == proposals
    meta, _ = parse_frontmatter(written[0].path.read_text(encoding="utf-8"))
    assert meta["status"] == "open"
    assert meta["execution_agent"] == "paul"


def test_ingest_creates_missing_target_directories(tmp_path: Path):
    written = ingest_records(
        OutboxValidation(accepted=(candidate(),), rejected=()),
        observations_dir=tmp_path / "fresh" / "observations",
        proposals_dir=tmp_path / "fresh" / "proposals",
        agent_name="duncan",
        now=NOW,
        job_id="job123",
    )

    assert written[0].path.is_file()


def test_reservation_reserves_the_filename_atomically(dirs):
    """Verify that choosing a destination reserves it, preventing concurrent overwrite."""
    observations, _ = dirs
    
    # Ingest two candidates with the same slug in the same call
    written = ingest(dirs, candidate(), candidate())
    
    # First should get the base name, second should get -2 (not reuse the base)
    assert written[0].path.name == "2026-07-31-suite-is-red.md"
    assert written[1].path.name == "2026-07-31-suite-is-red-2.md"
    
    # Both files should exist and contain content
    assert written[0].path.exists()
    assert written[1].path.exists()


def test_exhausting_collision_cap_raises_runtime_error(dirs):
    """Verify that exhausting the collision suffix cap raises RuntimeError."""
    from agency.records.ingest import _MAX_COLLISION_SUFFIX
    
    observations, _ = dirs
    base_name = "2026-07-31-suite-is-red"
    
    # Pre-create the base name and all suffixes up to _MAX_COLLISION_SUFFIX
    (observations / f"{base_name}.md").write_text("existing", encoding="utf-8")
    for i in range(2, _MAX_COLLISION_SUFFIX + 1):
        (observations / f"{base_name}-{i}.md").write_text("existing", encoding="utf-8")
    
    # Trying to ingest should raise RuntimeError
    with pytest.raises(RuntimeError, match="all.*collision suffixes exhausted"):
        ingest(dirs, candidate())


def test_slug_with_leading_or_trailing_hyphen_falls_back_to_title(dirs):
    """Verify that slugs with leading or trailing hyphens are treated as invalid."""
    written_leading = ingest(dirs, candidate(meta={"slug": "-invalid"}))
    assert written_leading[0].path.name == "2026-07-31-suite-is-red.md"
    
    written_trailing = ingest(dirs, candidate(meta={"slug": "invalid-"}))
    assert written_trailing[0].path.name == "2026-07-31-suite-is-red-2.md"


def test_successful_ingest_leaves_no_empty_placeholders(dirs):
    """Verify that a successful ingest leaves no empty placeholder files."""
    observations, _ = dirs
    
    written = ingest(dirs, candidate(), candidate(), candidate())
    
    # All ingested paths should have non-empty content
    for ingested in written:
        content = ingested.path.read_text(encoding="utf-8")
        assert len(content) > 0, f"Placeholder file {ingested.path} has empty content"
        # Verify it contains YAML frontmatter
        assert content.startswith("---"), f"File {ingested.path} does not start with frontmatter"

