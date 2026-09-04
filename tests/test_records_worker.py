from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agency.configuration.models import parse_config
from agency.integrations import RunResult
from agency.integrations.models import EffectiveRuntimePolicy, IntegrationRunRequest
from agency.jobs.execution import MAX_SUMMARY_REASONS, execute_job
from agency.records.validation import writable_agent_names
from test_job_execution import _authority as _job_authority, queued_job


def build_config(raw_config, config_paths, agents):
    raw_config["teams"]["newsletter"]["agents"] = agents
    return parse_config(raw_config, config_paths["config_path"]).resolved


def test_only_writable_agents_are_returned(raw_config, config_paths):
    ws = str(config_paths["workspace_path"])
    config = build_config(
        raw_config,
        config_paths,
        [
            {
                "name": "paul",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "runtime": {
                    "permissions": {
                        "rules": [{"path": ws, "tools": ["read", "write"]}],
                    },
                },
            },
            {
                "name": "gurney",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "runtime": {
                    "permissions": {
                        "rules": [{"path": ws, "tools": ["read"]}],
                    },
                },
            },
        ],
    )

    assert writable_agent_names(config, "newsletter") == frozenset({"paul"})


def test_agents_without_a_capabilities_block_are_not_writable(raw_config, config_paths):
    config = build_config(
        raw_config,
        config_paths,
        [
            {
                "name": "gurney",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
            }
        ],
    )

    assert writable_agent_names(config, "newsletter") == frozenset()


def test_unknown_group_yields_an_empty_set(raw_config, config_paths):
    config = build_config(raw_config, config_paths, [])

    assert writable_agent_names(config, "no-such-group") == frozenset()


# ---------------------------------------------------------------------------
# Worker wiring tests
# ---------------------------------------------------------------------------


def _fake_context(group_root: Path, integration):
    return SimpleNamespace(
        workspace_root=group_root,
        integration=integration,
        timeout=30,
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
        group_root=group_root,
    )


def _obs_integration(filename: str, content: str):
    """Return an integration that writes one file into outbox/observations/."""

    class _Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest) -> RunResult:
            dest = (
                request.launch_dir
                / ".agency"
                / "outbox"
                / "observations"
                / filename
            )
            dest.write_text(content, encoding="utf-8")
            return RunResult(0, "done", "", 0.1)

    return _Integration()


def test_execute_job_files_valid_observation(tmp_path, monkeypatch):
    _, spec = queued_job(tmp_path)
    group_root = tmp_path / "group"
    obs = "---\ntitle: Test Finding\n---\n\nSomething notable occurred.\n"
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: _fake_context(group_root, _obs_integration("obs.md", obs)),
    )

    result = execute_job(_job_authority(spec))

    assert result.status == "complete"
    filed = list((group_root / "observations").glob("*.md"))
    assert len(filed) == 1
    _, fm, _ = filed[0].read_text().split("---", 2)
    meta = yaml.safe_load(fm)
    assert meta["agent"] == spec.agent_name
    assert meta["date"]
    assert "1 record" in (result.execution_summary or "")


def test_execute_job_rejects_invalid_observation_retains_artifacts(tmp_path, monkeypatch):
    _, spec = queued_job(tmp_path)
    group_root = tmp_path / "group"
    # Empty body is rejected by validate_outbox.
    obs = "---\ntitle: Bad Record\n---\n\n"
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: _fake_context(group_root, _obs_integration("bad.md", obs)),
    )

    result = execute_job(_job_authority(spec))

    assert result.status == "failed"
    assert result.memory_publication is not None
    assert result.memory_publication.get("failed_artifacts")
    assert "record body is empty" in (result.execution_summary or "")


def test_observation_survives_publication_failure(tmp_path, monkeypatch):
    """ingest_records runs before prepare_publication; a pub failure cannot lose records."""
    from agency.memory.publication import MemoryPublicationError

    _, spec = queued_job(tmp_path)
    group_root = tmp_path / "group"
    obs = "---\ntitle: Durable\n---\n\nThis record must survive a publication failure.\n"
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: _fake_context(group_root, _obs_integration("obs.md", obs)),
    )

    def _fail(*args, **kwargs):
        raise MemoryPublicationError("simulated")

    monkeypatch.setattr("agency.jobs.execution.prepare_publication", _fail)

    result = execute_job(_job_authority(spec))

    assert result.status == "failed"
    # Record was written by ingest_records before prepare_publication was called.
    filed = list((group_root / "observations").glob("*.md"))
    assert len(filed) == 1


# ---------------------------------------------------------------------------
# writable_agent_names filtering
# ---------------------------------------------------------------------------


def test_agents_on_a_non_executable_integration_are_not_writable(raw_config, config_paths):
    config = build_config(
        raw_config,
        config_paths,
        [
            {
                "name": "leto",
                "blueprint": "builder-blueprint",
                "integration": "sdk",
            }
        ],
    )

    assert writable_agent_names(config, "newsletter") == frozenset()


def test_agents_on_an_unregistered_integration_are_not_writable(raw_config, config_paths):
    config = build_config(
        raw_config,
        config_paths,
        [
            {
                "name": "leto",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
            }
        ],
    )
    object.__setattr__(
        config.groups["newsletter"].agents["leto"],
        "integration",
        "no-such-integration",
    )

    assert writable_agent_names(config, "newsletter") == frozenset()


# ---------------------------------------------------------------------------
# Unsafe outbox contents driven through the worker
# ---------------------------------------------------------------------------

VALID_OBSERVATION = "---\ntitle: Real Finding\n---\n\n**Real finding.** Something notable occurred.\n"
EMPTY_BODY_OBSERVATION = "---\ntitle: Bad Record\n---\n\n"


def _outbox_integration(populate):
    """Return an integration that populates the launch view's outbox."""

    class _Integration:
        supports_execution = True
        name = "fake"

        def run(self, request: IntegrationRunRequest) -> RunResult:
            populate(request.launch_dir / ".agency" / "outbox")
            return RunResult(0, "done", "", 0.1)

    return _Integration()


def _run_with_outbox(tmp_path, monkeypatch, populate):
    _, spec = queued_job(tmp_path)
    group_root = tmp_path / "group"
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: _fake_context(group_root, _outbox_integration(populate)),
    )
    return execute_job(_job_authority(spec)), group_root


def _artifact_names(result):
    return {
        artifact["name"]
        for artifact in (result.memory_publication or {}).get("failed_artifacts", [])
    }


def test_unretainable_outbox_entries_keep_the_per_record_reasons(tmp_path, monkeypatch):
    """A non-.md file and a subdirectory must not blow up retention."""

    def populate(outbox: Path) -> None:
        (outbox / "observations" / "notes.txt").write_text("junk", encoding="utf-8")
        (outbox / "observations" / "nested").mkdir()
        (outbox / "observations" / "bad.md").write_text(
            EMPTY_BODY_OBSERVATION, encoding="utf-8"
        )

    result, _ = _run_with_outbox(tmp_path, monkeypatch, populate)

    assert result.status == "failed"
    summary = result.execution_summary or ""
    assert summary.startswith("Rejected agent records:")
    assert "Execution error" not in summary
    assert "notes.txt: not a markdown file" in summary
    assert "nested: subdirectories are not allowed" in summary
    assert "bad.md: record body is empty" in summary
    # The retainable record is kept; the entries that caused the rejection are skipped.
    assert _artifact_names(result) == {"observations-bad.md"}


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlinked_record_keeps_the_per_record_reasons(tmp_path, monkeypatch):
    target = tmp_path / "outside.md"
    target.write_text("secret", encoding="utf-8")

    def populate(outbox: Path) -> None:
        (outbox / "observations" / "link.md").symlink_to(target)
        (outbox / "observations" / "bad.md").write_text(
            EMPTY_BODY_OBSERVATION, encoding="utf-8"
        )

    result, _ = _run_with_outbox(tmp_path, monkeypatch, populate)

    assert result.status == "failed"
    summary = result.execution_summary or ""
    assert summary.startswith("Rejected agent records:")
    assert "link.md: not a regular file" in summary
    assert _artifact_names(result) == {"observations-bad.md"}


def test_a_rejected_proposal_body_is_retained(tmp_path, monkeypatch):
    def populate(outbox: Path) -> None:
        (outbox / "proposals" / "p.md").write_text(
            "---\nexecution_agent: nobody\nquestions: []\n---\n\nA proposal body.\n",
            encoding="utf-8",
        )

    result, _ = _run_with_outbox(tmp_path, monkeypatch, populate)

    assert result.status == "failed"
    assert _artifact_names(result) == {"proposals-p.md"}


def test_valid_records_are_ingested_alongside_rejected_ones(tmp_path, monkeypatch):
    def populate(outbox: Path) -> None:
        (outbox / "observations" / "good.md").write_text(
            VALID_OBSERVATION, encoding="utf-8"
        )
        (outbox / "observations" / "bad.md").write_text(
            EMPTY_BODY_OBSERVATION, encoding="utf-8"
        )

    result, group_root = _run_with_outbox(tmp_path, monkeypatch, populate)

    assert result.status == "failed"
    filed = list((group_root / "observations").glob("*.md"))
    assert [item.name.endswith("-real-finding.md") for item in filed] == [True]
    summary = result.execution_summary or ""
    assert "bad.md: record body is empty" in summary
    assert "Filed 1 valid record." in summary


def test_the_rejection_summary_is_bounded(tmp_path, monkeypatch):
    long_stem = "z" * 100

    def populate(outbox: Path) -> None:
        for index in range(20):
            (outbox / "observations" / f"{long_stem}{index:02d}.md").write_text(
                EMPTY_BODY_OBSERVATION, encoding="utf-8"
            )

    result, _ = _run_with_outbox(tmp_path, monkeypatch, populate)

    assert result.status == "failed"
    summary = result.execution_summary or ""
    assert len(summary) < 2 * MAX_SUMMARY_REASONS
    assert summary.endswith("… (truncated)")
