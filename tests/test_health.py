from datetime import datetime, timedelta
import os

from agency.health import (
    AgentHealth,
    Lateness,
    RoutineSchedule,
    describe_agent_health,
    elapsed_coarse,
    elapsed_precise,
    evaluate_agent_health,
    grace_window,
    last_fired_at,
    routine_schedules,
    schedule_lateness,
    schedule_state,
)

NOW = datetime(2026, 7, 28, 12, 0, 0)
GRACE = timedelta(minutes=17)


def _logs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return logs


def _state(tmp_path, *schedules, now=NOW):
    return schedule_state(
        schedules,
        logs_root=_logs(tmp_path),
        agent_name="product",
        now=now,
        grace=GRACE,
    )


def _lateness(tmp_path, *schedules, now=NOW):
    return schedule_lateness(
        schedules,
        logs_root=_logs(tmp_path),
        agent_name="product",
        now=now,
        grace=GRACE,
    )



def _at(routine_id="r", at="09:00", enabled=True, conditional=False):
    return RoutineSchedule(routine_id=routine_id, at=at, every=None, enabled=enabled, conditional=conditional)


def _every(routine_id="r", every="7d", enabled=True, conditional=False):
    return RoutineSchedule(routine_id=routine_id, at=None, every=every, enabled=enabled, conditional=conditional)


def test_grace_window_is_the_interval_plus_two_minutes():
    assert grace_window(15) == timedelta(minutes=17)
    assert grace_window(60) == timedelta(minutes=62)


def test_no_schedules_produce_no_state(tmp_path):
    assert _state(tmp_path) is None


def test_at_before_its_time_produces_no_state(tmp_path):
    assert _state(tmp_path, _at(at="18:00")) is None


def test_at_inside_the_grace_window_is_due(tmp_path):
    at_time = (NOW - timedelta(minutes=5)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) == "due"


def test_at_past_the_grace_window_is_overdue(tmp_path):
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) == "overdue"


def test_at_with_todays_marker_produces_no_state(tmp_path):
    logs = _logs(tmp_path)
    day = NOW.strftime("%Y-%m-%d")
    (logs / day).mkdir()
    (logs / day / f".event-product-r-{day}").touch()
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) is None


def test_at_ignores_a_marker_from_another_day(tmp_path):
    logs = _logs(tmp_path)
    stale_day = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    (logs / stale_day).mkdir()
    (logs / stale_day / f".event-product-r-{stale_day}").touch()
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) == "overdue"


def test_malformed_at_produces_no_state(tmp_path):
    assert _state(tmp_path, _at(at="not-a-time")) is None


def test_every_without_a_marker_produces_no_state(tmp_path):
    assert _state(tmp_path, _every(every="1h")) is None


def test_every_before_the_interval_elapses_produces_no_state(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(minutes=30)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="6h")) is None


def test_every_inside_the_grace_window_is_due(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(hours=6, minutes=5)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="6h")) == "due"


def test_every_past_the_grace_window_is_overdue(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(hours=9)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="6h")) == "overdue"


def test_malformed_every_produces_no_state(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(days=90)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="soon")) is None


def test_disabled_routine_produces_no_state(tmp_path):
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time, enabled=False)) is None


def test_conditional_routine_produces_no_state(tmp_path):
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time, conditional=True)) is None


def test_overdue_wins_over_due_across_routines(tmp_path):
    due = (NOW - timedelta(minutes=5)).strftime("%H:%M")
    late = (NOW - timedelta(hours=3)).strftime("%H:%M")
    state = _state(tmp_path, _at(routine_id="a", at=due), _at(routine_id="b", at=late))
    assert state == "overdue"


def test_routine_schedules_reads_mappings():
    schedules = routine_schedules([
        {"id": "r", "schedule": {"at": "09:00"}, "enabled": True},
        {"id": "s", "schedule": {"every": "7d"}, "enabled": False, "condition": "pre-send"},
    ])
    assert schedules == (
        RoutineSchedule(routine_id="r", at="09:00", every=None, enabled=True, conditional=False),
        RoutineSchedule(routine_id="s", at=None, every="7d", enabled=False, conditional=True),
    )


def test_routine_schedules_reads_config_models():
    from agency.configuration.models import Routine

    routine = Routine(
        id="r",
        prompt={"scope": "blueprint", "name": "daily-review"},
        schedule={"at": "09:00"},
    )
    assert routine_schedules([routine]) == (
        RoutineSchedule(routine_id="r", at="09:00", every=None, enabled=True, conditional=False),
    )


def test_health_is_red_when_the_last_job_failed():
    assert evaluate_agent_health(has_run=True, last_job_failed=True, schedule=None) == "red"


def test_health_is_red_when_a_routine_is_overdue():
    assert evaluate_agent_health(has_run=True, last_job_failed=False, schedule="overdue") == "red"


def test_overdue_outranks_never_having_run():
    assert evaluate_agent_health(has_run=False, last_job_failed=False, schedule="overdue") == "red"


def test_health_is_amber_when_a_routine_is_due():
    assert evaluate_agent_health(has_run=True, last_job_failed=False, schedule="due") == "amber"


def test_health_is_gray_when_nothing_has_run():
    assert evaluate_agent_health(has_run=False, last_job_failed=False, schedule=None) == "gray"


def test_health_is_green_otherwise():
    assert evaluate_agent_health(has_run=True, last_job_failed=False, schedule=None) == "green"


def test_at_exact_occurrence_is_due(tmp_path):
    at_time = NOW.strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) == "due"


def test_at_exact_grace_boundary_is_due(tmp_path):
    at_time = (NOW - GRACE).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) == "due"


def test_every_exact_period_boundary_is_due(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(hours=6)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="6h")) == "due"


def test_every_exact_grace_boundary_is_due(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(hours=6, minutes=17)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="6h")) == "due"


def test_amber_outranks_gray_when_schedule_is_due():
    assert evaluate_agent_health(has_run=False, last_job_failed=False, schedule="due") == "amber"


def test_lateness_is_none_when_nothing_is_late(tmp_path):
    assert _lateness(tmp_path, _at(at="18:00")) is None


def test_lateness_names_the_routine_and_its_due_time(tmp_path):
    result = _lateness(tmp_path, _at(routine_id="suite-health", at="08:00"))
    assert result == Lateness(
        routine_id="suite-health",
        state="overdue",
        due_at=datetime(2026, 7, 28, 8, 0),
    )


def test_overdue_outranks_due_regardless_of_order(tmp_path):
    due_at = (NOW - timedelta(minutes=5)).strftime("%H:%M")
    result = _lateness(
        tmp_path,
        _at(routine_id="soon", at=due_at),
        _at(routine_id="late", at="08:00"),
    )
    assert result.routine_id == "late"
    assert result.state == "overdue"


def test_earliest_due_wins_within_one_state(tmp_path):
    result = _lateness(
        tmp_path,
        _at(routine_id="nine", at="09:00"),
        _at(routine_id="eight", at="08:00"),
    )
    assert result.routine_id == "eight"


def test_exact_tie_breaks_on_configured_order(tmp_path):
    result = _lateness(
        tmp_path,
        _at(routine_id="first", at="08:00"),
        _at(routine_id="second", at="08:00"),
    )
    assert result.routine_id == "first"


def test_schedule_state_still_reports_only_severity(tmp_path):
    assert _state(tmp_path, _at(at="08:00")) == "overdue"
    assert _state(tmp_path, _at(at="18:00")) is None


def test_elapsed_coarse_uses_one_unit():
    assert elapsed_coarse(timedelta(seconds=30)) == "0m"
    assert elapsed_coarse(timedelta(minutes=46)) == "46m"
    assert elapsed_coarse(timedelta(hours=3, minutes=46)) == "3h"
    assert elapsed_coarse(timedelta(days=2, hours=3)) == "2d"


def test_elapsed_precise_adds_the_next_smaller_unit():
    assert elapsed_precise(timedelta(seconds=12)) == "12s"
    assert elapsed_precise(timedelta(minutes=3, seconds=55)) == "3m 55s"
    assert elapsed_precise(timedelta(hours=3, minutes=46)) == "3h 46m"
    assert elapsed_precise(timedelta(days=2, hours=3)) == "2d 3h"


def test_elapsed_precise_omits_a_zero_remainder():
    assert elapsed_precise(timedelta(hours=3)) == "3h"
    assert elapsed_precise(timedelta(minutes=5)) == "5m"


def test_last_fired_at_is_none_without_a_marker(tmp_path):
    assert last_fired_at(
        _at(), logs_root=_logs(tmp_path), agent_name="product", now=NOW
    ) is None


def test_last_fired_at_reads_the_every_marker(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.write_text("", encoding="utf-8")
    stamp = datetime(2026, 7, 27, 6, 12).timestamp()
    os.utime(marker, (stamp, stamp))

    assert last_fired_at(
        _every(), logs_root=logs, agent_name="product", now=NOW
    ) == datetime(2026, 7, 27, 6, 12)


def _describe(has_run=True, last_job_failed=False, lateness=None):
    return describe_agent_health(
        has_run=has_run,
        last_job_failed=last_job_failed,
        lateness=lateness,
        now=NOW,
    )


def _late(state="overdue", routine_id="suite-health", hours=3):
    return Lateness(
        routine_id=routine_id,
        state=state,
        due_at=NOW - timedelta(hours=hours),
    )


def test_failed_job_is_red_and_named():
    assert _describe(last_job_failed=True) == AgentHealth("red", "job_failed", None, None, None)


def test_overdue_carries_the_routine_and_how_late_it_is():
    result = _describe(lateness=_late())
    assert result.color == "red"
    assert result.kind == "overdue"
    assert result.routine_id == "suite-health"
    assert result.due_at == NOW - timedelta(hours=3)
    assert result.late == timedelta(hours=3)


def test_due_is_amber_and_named():
    result = _describe(lateness=_late(state="due", hours=0))
    assert result.color == "amber"
    assert result.kind == "due"


def test_a_failed_job_outranks_an_overdue_routine():
    result = _describe(last_job_failed=True, lateness=_late())
    assert result.kind == "job_failed"
    assert result.routine_id is None


def test_no_run_on_record_is_gray_and_named():
    assert _describe(has_run=False) == AgentHealth("gray", "never_run", None, None, None)


def test_a_quiet_agent_that_has_run_is_healthy():
    assert _describe() == AgentHealth("green", "healthy", None, None, None)


def test_kind_never_contradicts_colour():
    cases = [
        _describe(last_job_failed=True),
        _describe(lateness=_late()),
        _describe(lateness=_late(state="due", hours=0)),
        _describe(has_run=False),
        _describe(),
    ]
    expected = {
        "job_failed": "red",
        "overdue": "red",
        "due": "amber",
        "never_run": "gray",
        "healthy": "green",
    }
    for result in cases:
        assert result.color == expected[result.kind]

