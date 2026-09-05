"""Focused tests proving dispatch/run and jobs/resolution use config.teams."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from flowgency.configuration.models import parse_config
from flowgency.configuration.store import ConfigSnapshot


def test_dispatch_cycle_iterates_config_teams(raw_config, config_paths, monkeypatch):
    """run_dispatch_cycle reads config.teams, not config.groups."""
    from flowgency.dispatch.run import run_dispatch_cycle

    parsed = parse_config(raw_config, config_paths["config_path"])
    snapshot = SimpleNamespace(config=parsed.resolved, path=config_paths["config_path"])

    monkeypatch.setattr("flowgency.dispatch.run.drain", lambda *a, **kw: None)
    # dispatch.enabled is False by default in conftest, so no job submission occurs
    run_dispatch_cycle(snapshot, config_paths["config_path"])


def test_resolve_job_request_accesses_config_teams(raw_config, config_paths, monkeypatch):
    """resolve_job_request dereferences config.teams, not config.groups."""
    from flowgency.jobs.models import JobRequest
    from flowgency.jobs.resolution import JobValidationError, resolve_job_request

    parsed = parse_config(raw_config, config_paths["config_path"])
    snapshot = ConfigSnapshot(
        path=config_paths["config_path"],
        revision="test",
        raw=raw_config,
        config=parsed.resolved,
    )

    monkeypatch.setattr("flowgency.jobs.resolution.validate_resolved_paths", lambda c: [])

    request = JobRequest(
        config_path=config_paths["config_path"],
        team_key="nonexistent",
        agent_name="builder",
        trigger="manual",
        task_input="test",
    )

    with pytest.raises(JobValidationError, match="Unknown team"):
        resolve_job_request(
            request,
            config_store=None,
            library=None,
            cache=None,
            prompt_store=None,
            integrations={},
            snapshot=snapshot,
        )
