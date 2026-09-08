from __future__ import annotations

import shutil

import pytest

from flowgency.jobs import JobSubmissionError

from tests._ticket_helpers import make_ticket_job_environment


@pytest.fixture
def ticket_job_env(tmp_path, raw_config, monkeypatch):
    return make_ticket_job_environment(tmp_path, raw_config, monkeypatch)


def test_cleanup_keeps_active_work_when_stop_is_unknown(ticket_job_env):
    from flowgency.jobs.processes import ProcessStopEvidence

    env = ticket_job_env
    authority, targets = env.start_two_tickets(agent="builder", job_id="run-a")

    result = env.coordinator.cleanup(
        authority,
        ProcessStopEvidence(
            job_id="run-a",
            generation=env.generation,
            confirmed=False,
            reason="unknown",
        ),
    )

    assert result.pending_cleanup
    for ref in targets:
        assert env.read(ref).record.active_run is not None


def test_submit_clears_partial_reservation_when_job_was_never_created(
    ticket_job_env,
    monkeypatch,
):
    import flowgency.jobs.submission as submission_module

    env = ticket_job_env
    ticket = env.create_assigned("builder")

    monkeypatch.setattr(
        submission_module,
        "_submit_resolved",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("submit failed")),
    )

    with pytest.raises(RuntimeError, match="submit failed"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    assert env.read(ticket.ref).record.pending_run is None


def test_submit_keeps_reservation_when_job_record_exists_before_failure(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")
    env.launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    current = env.read(ticket.ref).record
    assert current.pending_run is not None
    failed = env.jobs.read(
        type("Handle", (), {"path": env.job_store.path(env.team_id, current.pending_run.job_id), "job_id": current.pending_run.job_id})
    )
    assert failed.status == "failed"


def test_cleanup_uses_original_target_after_storage_switch(ticket_job_env):
    from flowgency.jobs.processes import ProcessStopEvidence

    env = ticket_job_env
    authority, targets = env.start_two_tickets(agent="builder", job_id="run-a")
    env.set_storage_root(env.root_b)

    env.coordinator.cleanup(
        authority,
        ProcessStopEvidence(
            job_id="run-a",
            generation=env.generation,
            confirmed=True,
            reason="exited",
        ),
    )

    for ref in targets:
        record = env.provider.read(ref)
        assert record.assignee == "builder"
        assert record.active_run is None


def test_cleanup_keeps_active_work_for_later_generation(ticket_job_env):
    from flowgency.jobs.processes import ProcessStopEvidence

    env = ticket_job_env
    authority, targets = env.start_two_tickets(agent="builder", job_id="run-a")

    result = env.coordinator.cleanup(
        authority,
        ProcessStopEvidence(
            job_id="run-a",
            generation=f"{env.generation}-later",
            confirmed=True,
            reason="exited",
        ),
    )

    assert set(result.pending_cleanup) == set(targets)
    for ref in targets:
        assert env.provider.read(ref).active_run is not None


def test_cleanup_reports_pending_when_original_storage_is_unavailable(ticket_job_env):
    from flowgency.jobs.processes import ProcessStopEvidence

    env = ticket_job_env
    authority, targets = env.start_two_tickets(agent="builder", job_id="run-a")
    shutil.rmtree(env.root_a)

    result = env.coordinator.cleanup(
        authority,
        ProcessStopEvidence(
            job_id="run-a",
            generation=env.generation,
            confirmed=True,
            reason="exited",
        ),
    )

    assert set(result.pending_cleanup) == set(targets)