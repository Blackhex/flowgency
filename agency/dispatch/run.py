"""Agency dispatch runner — called by OS-native timer."""

import argparse
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from agency.clock import now as clock_now
from agency.configuration import resolve_team_paths
from agency.dispatch.schedule import (
    at_marker_path,
    catch_up_allows,
    every_marker_path,
    last_at_occurrence,
    last_every_occurrence,
    parse_catch_up,
    parse_every,
)
from agency.configuration.store import ConfigStore
from agency.health import grace_window
from agency.jobs import JobRequest, JobSubmissionError, JobValidationError, submit_job_request
from agency.jobs.authority import JobStore
from agency.jobs.queue import drain
from agency.jobs.store import _is_launched, read_job

log = logging.getLogger("agency.dispatch")

# Occurrences of one routine are at least a minute apart, so a second of slack
# separates them. The slack is needed because an `every` anchor round-trips
# through a float mtime, which can move a recomputed occurrence by a microsecond.
_OCCURRENCE_SLACK = timedelta(seconds=1)


def _same_occurrence(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= _OCCURRENCE_SLACK


def _occurrence_of(record) -> datetime | None:
    try:
        return datetime.fromisoformat(record.due_at)
    except (TypeError, ValueError):
        return None


def _never_ran(record) -> bool:
    """Whether this job was recorded as failed without ever reaching a worker."""
    return record.status == "failed" and not _is_launched(record)


def lost_occurrences(records) -> dict[tuple[str, str], datetime]:
    """The occurrence each routine still owes because its job never started.

    A marker records that an occurrence was *submitted*, not that it ran, and
    a job queued behind a full pool is launched by a later drain. When that
    launch fails the marker is left claiming work that never happened, so the
    runner reads the occurrence's outcome alongside its marker.

    A later job for the same routine that launched, or that is still pending,
    makes an earlier occurrence moot — the same rule the firing predicate
    already applies — which is what stops a retry from replaying.
    """
    lost: dict[tuple[str, str], datetime] = {}
    served: dict[tuple[str, str], datetime] = {}
    for record in records:
        spec = record.spec
        if spec.trigger != "scheduled_prompt" or not spec.routine_id:
            continue
        occurrence = _occurrence_of(record)
        if occurrence is None:
            continue
        key = (spec.agent_name, spec.routine_id)
        target = lost if _never_ran(record) else served
        newest = target.get(key)
        if newest is None or occurrence > newest:
            target[key] = occurrence
    return {
        key: occurrence
        for key, occurrence in lost.items()
        if key not in served or occurrence > served[key]
    }


def _team_job_records(memory_store: Path, team_key: str):
    try:
        team_dir = JobStore(memory_store).team_root(team_key)
    except (OSError, ValueError):
        return
    if not team_dir.is_dir():
        return
    for path in sorted(team_dir.glob("*.yaml")):
        try:
            yield read_job(path)
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
            log.warning("ignoring malformed job record %s: %s", path, error)


def _due_occurrence(
    routine,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    lost: datetime | None = None,
):
    """The occurrence this routine owes, with the marker that would record it.

    A marker of ``None`` means the rule cannot be read at all. An occurrence
    of ``None`` with a marker means the rule is readable and simply not due
    yet, which is the ordinary case and must not be reported as a fault.

    ``lost`` is an occurrence whose job never started. For ``every`` the
    anchor has already advanced to it, so it has to be offered again by name;
    the anchor itself is never rewound.
    """
    at_time = routine.schedule.at or ""
    every_value = routine.schedule.every or ""
    if at_time:
        occurrence = last_at_occurrence(at_time, now)
        if occurrence is None:
            return None, None
        marker = at_marker_path(
            logs_root, agent_name, routine.id, occurrence.strftime("%Y-%m-%d")
        )
        return occurrence, marker
    if every_value:
        period = parse_every(every_value)
        if period is None or period.total_seconds() <= 0:
            return None, None
        marker = every_marker_path(logs_root, agent_name, routine.id)
        try:
            anchor = datetime.fromtimestamp(marker.stat().st_mtime)
        except OSError:
            return now, marker
        occurrence = last_every_occurrence(anchor, every_value, now)
        if occurrence is None and _same_occurrence(lost, anchor):
            return lost, marker
        return occurrence, marker
    return None, None


def load_dispatch_config(config_path: str):
    """Load the canonical config snapshot."""
    return ConfigStore(Path(config_path)).load()


def run_dispatch_cycle(config, config_path: Path | str, launcher=None) -> None:
    """Run one full dispatch cycle across all enabled groups."""
    snapshot = config if hasattr(config, "config") else load_dispatch_config(str(config_path))
    resolved = snapshot.config
    interval = resolved.agency.dispatch.interval

    try:
        drain(
            resolved,
            memory_store=resolved.agency.memory_store,
            launcher=launcher,
            full_reconcile=True,
        )
    except Exception:
        log.exception("queue drain failed")

    for team_key, team in resolved.teams.items():
        if not team.dispatch.enabled:
            continue

        log.info("Processing team: %s", team_key)
        paths = resolve_team_paths(team)

        logs_root = paths.logs
        today = clock_now().strftime("%Y-%m-%d")
        log_dir = logs_root / today
        log_dir.mkdir(parents=True, exist_ok=True)
        lost = lost_occurrences(
            _team_job_records(resolved.agency.memory_store, team_key)
        )

        for agent_name, agent in team.agents.items():
            for routine in agent.routines:
                if not routine.enabled:
                    log.info("  SKIP: %s/%s is disabled", agent_name, routine.id)
                    continue

                if getattr(routine, "condition", None):
                    log.info(
                        "  SKIP: %s/%s has condition '%s' (requires group dispatch script)",
                        agent_name,
                        routine.id,
                        routine.condition,
                    )
                    continue

                now = clock_now()
                bound = parse_catch_up(getattr(routine.schedule, "catch_up", None))
                if bound is None:
                    log.warning(
                        "  SKIP: %s/%s has an invalid catch_up", agent_name, routine.id
                    )
                    continue
                unrun = lost.get((agent_name, routine.id))

                occurrence, marker = _due_occurrence(
                    routine, logs_root, agent_name, now, unrun
                )
                if marker is None:
                    log.warning(
                        "  WARNING: rule for %s/%s has no usable schedule",
                        agent_name,
                        routine.id,
                    )
                    continue
                if occurrence is None:
                    continue
                # A marker records a submission; a lost occurrence never ran.
                if (
                    routine.schedule.at
                    and marker.exists()
                    and not _same_occurrence(occurrence, unrun)
                ):
                    continue
                if not catch_up_allows(occurrence, now, bound, grace_window(interval)):
                    continue

                try:
                    request = JobRequest(
                        config_path=snapshot.path,
                        team_key=team_key,
                        agent_name=agent_name,
                        trigger="scheduled_prompt",
                        task_input="",
                        routine_id=routine.id,
                        due_at=occurrence.isoformat(),
                    )
                    submit_job_request(request, launcher)
                except (TypeError, ValueError, JobValidationError, JobSubmissionError, OSError) as error:
                    log.error("  ERROR: could not submit %s/%s: %s", agent_name, routine.id, error)
                    continue
                # Touch markers, stamped to the occurrence so late recovery does not drift subsequent ones
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
                stamp = occurrence.timestamp()
                os.utime(marker, (stamp, stamp))


def main():
    parser = argparse.ArgumentParser(description="Agency dispatch runner")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = load_dispatch_config(args.config)
    log.info("Dispatch started")
    run_dispatch_cycle(config, Path(args.config).resolve())
    log.info("Dispatch complete")


if __name__ == "__main__":
    main()
