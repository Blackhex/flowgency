from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from agency.configuration.models import parse_config
from agency.integrations import RunResult
from agency.integrations.models import IntegrationRunRequest
from agency.jobs.execution import execute_job
from agency.records.validation import writable_agent_names
from test_job_execution import _authority as _job_authority, queued_job


def build_config(raw_config, config_paths, agents):
    raw_config["groups"]["newsletter"]["agents"] = agents
    return parse_config(raw_config, config_paths["config_path"]).resolved


def test_only_writable_agents_are_returned(raw_config, config_paths):
    config = build_config(
        raw_config,
        config_paths,
        [
            {
                "name": "paul",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "capabilities": {"write": True},
            },
            {
                "name": "gurney",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "capabilities": {"write": False},
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
        sandbox_root=None,
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
