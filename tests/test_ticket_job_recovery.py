from __future__ import annotations

import shutil

import pytest

from flowgency.jobs import JobSubmissionError
from flowgency.jobs.tickets import TicketReservationError
from flowgency.tickets.models import TicketEvent, TicketOperation, TicketRef
from flowgency.workflows.configuration import resolve_workflow_binding

from tests._ticket_helpers import SEED_TIME, make_ticket_job_environment


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
    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")
    assert reservation is not None
    assert reservation.status == "retryable"


def test_retry_after_missing_job_reuses_original_job_id_and_version(
    ticket_job_env,
    monkeypatch,
):
    import flowgency.jobs.submission as submission_module

    env = ticket_job_env
    ticket = env.create_assigned("builder")
    original_submit_resolved = submission_module._submit_resolved

    monkeypatch.setattr(
        submission_module,
        "_submit_resolved",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("submit failed")),
    )

    with pytest.raises(RuntimeError, match="submit failed"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")
    assert reservation is not None
    assert env.read(ticket.ref).record.pending_run is None

    monkeypatch.setattr(submission_module, "_submit_resolved", original_submit_resolved)

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")

    assert handle.job_id == reservation.job_id
    current = env.read(ticket.ref).record
    assert current.pending_run is not None
    assert current.pending_run.job_id == reservation.job_id
    assert sum(1 for event in current.events if event.kind == "ticket-run-reserved") == 1
    assert sum(1 for event in current.events if event.kind == "ticket-run-reconciled") == 2


def test_submit_clears_pending_reservation_when_never_started_job_fails(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")
    env.launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    current = env.read(ticket.ref).record
    assert current.pending_run is None
    assert current.assignee == "builder"
    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")
    assert reservation is not None
    failed = env.jobs.read(
        type("Handle", (), {"path": env.job_store.path(env.team_id, reservation.job_id), "job_id": reservation.job_id})
    )
    assert failed.status == "failed"


def test_retry_after_durable_job_creation_adopts_verified_authority(ticket_job_env):
    env = ticket_job_env
    ticket = env.create_assigned("builder")
    env.launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")
    assert reservation is not None
    assert reservation.authority_digest is not None

    env.launcher.launch.side_effect = None
    handle = env.coordinator.submit(env.user, ticket.version, "run-request")

    assert handle.job_id == reservation.job_id
    assert handle.path == env.job_store.path(env.team_id, reservation.job_id)


def test_retry_rejects_stray_job_at_reserved_path(ticket_job_env, monkeypatch):
    import flowgency.jobs.submission as submission_module

    env = ticket_job_env
    ticket = env.create_assigned("builder")
    original_submit_resolved = submission_module._submit_resolved

    monkeypatch.setattr(
        submission_module,
        "_submit_resolved",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("submit failed")),
    )

    with pytest.raises(RuntimeError, match="submit failed"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")
    assert reservation is not None
    env.running_job("builder", reservation.job_id)

    monkeypatch.setattr(submission_module, "_submit_resolved", original_submit_resolved)

    with pytest.raises(TicketReservationError, match="ticket target|reservation"):
        env.coordinator.submit(env.user, ticket.version, "run-request")


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


def test_reconcile_uses_original_binding_and_cancels_stale_job_after_switch(ticket_job_env):
    import flowgency.jobs.submission as submission_module

    env = ticket_job_env
    ticket = env.create_assigned("builder")
    real_submit = env.coordinator.submitter

    def switching_submit(request):
        handle = real_submit(request)
        env.set_storage_root(env.root_b)
        return handle

    env.coordinator.submitter = switching_submit

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")

    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")
    assert reservation is not None
    assert reservation.status == "stale"
    assert env.provider.read(ticket.ref).pending_run is None
    assert env.jobs.read(handle).status == "cancelled"


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


def test_submit_preserves_original_failure_when_original_storage_is_unavailable_before_job_creation(
    ticket_job_env,
):
    env = ticket_job_env
    ticket = env.create_assigned("builder")
    backup = env.tmp_path / "root-a-backup"
    original_submitter = env.coordinator.submitter

    def unavailable_submit(request):
        env.root_a.rename(backup)
        raise RuntimeError("submit failed")

    env.coordinator.submitter = unavailable_submit

    with pytest.raises(RuntimeError, match="submit failed"):
        env.coordinator.submit(env.user, ticket.version, "run-request")

    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")
    assert reservation is not None
    assert reservation.status == "recovery-pending"
    assert reservation.recovery is not None
    assert reservation.recovery.kind == "original-storage-unavailable"

    backup.rename(env.root_a)

    recovery = env.coordinator.reconcile_pending_submission(
        env.team_id,
        ticket.ref,
        "run-request",
    )

    assert recovery is not None
    assert recovery.status == "retryable"

    env.coordinator.submitter = original_submitter

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")

    assert handle.job_id == reservation.job_id


def test_reconcile_pending_submission_retries_original_storage_without_touching_current_destination(
    ticket_job_env,
):
    env = ticket_job_env
    ticket = env.create_assigned("builder")
    backup = env.tmp_path / "root-a-backup"
    real_submit = env.coordinator.submitter

    def unavailable_after_create(request):
        handle = real_submit(request)
        env.root_a.rename(backup)
        return handle

    env.coordinator.submitter = unavailable_after_create

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")
    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")

    assert reservation is not None
    assert reservation.status == "recovery-pending"
    assert reservation.authority_digest is not None

    env.set_storage_root(env.root_b)
    current_binding = resolve_workflow_binding(
        env.store.load(),
        env.team_id,
        env.workflow_id,
    ).storage
    current_provider = env.current_provider()
    current_ref = TicketRef.from_binding(current_binding, ticket.ref.ticket_id)
    shadow = ticket.record.with_ref(current_ref).model_copy(
        update={
            "number": 0,
            "revision": 1,
            "pending_run": None,
            "active_run": None,
            "events": (
                TicketEvent(kind="opened", actor="system", summary="Shadow ticket"),
            ),
            "receipts": (),
            "created_at": SEED_TIME,
            "updated_at": SEED_TIME,
        }
    )
    current_provider.create(
        shadow,
        TicketOperation(operation_id="seed-shadow", request_digest="seed-shadow"),
    )

    backup.rename(env.root_a)

    recovery = env.coordinator.reconcile_pending_submission(
        env.team_id,
        ticket.ref,
        "run-request",
    )

    assert recovery is not None
    assert recovery.status == "stale"
    assert recovery.handle is not None
    assert recovery.handle.job_id == handle.job_id
    assert env.provider.read(ticket.ref).pending_run is None
    assert env.jobs.read(handle).status == "cancelled"

    shadow_current = current_provider.read(current_ref)

    assert shadow_current.revision == 1
    assert shadow_current.pending_run is None
    assert all(event.kind != "ticket-run-reconciled" for event in shadow_current.events)