"""Agency dispatch runner — called by OS-native timer."""

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path

from agency.clock import now as clock_now
from agency.configuration import resolve_group_paths
from agency.dispatch.schedule import (
    at_marker_path,
    catch_up_allows,
    every_marker_path,
    last_at_occurrence,
    last_every_occurrence,
    parse_catch_up,
)
from agency.configuration.store import ConfigStore
from agency.health import grace_window
from agency.jobs import JobRequest, JobSubmissionError, JobValidationError, submit_job_request
from agency.jobs.queue import drain

log = logging.getLogger("agency.dispatch")


def _due_occurrence(routine, logs_root: Path, agent_name: str, now: datetime):
    """The occurrence this routine owes, with the marker that would record it."""
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
        marker = every_marker_path(logs_root, agent_name, routine.id)
        try:
            anchor = datetime.fromtimestamp(marker.stat().st_mtime)
        except OSError:
            return now, marker
        return last_every_occurrence(anchor, every_value, now), marker
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
        drain(resolved, memory_store=resolved.agency.memory_store)
    except Exception:
        log.exception("queue drain failed")

    for group_key, group in resolved.groups.items():
        if not group.dispatch.enabled:
            continue

        log.info("Processing group: %s", group_key)
        paths = resolve_group_paths(group)

        logs_root = paths.logs
        today = clock_now().strftime("%Y-%m-%d")
        log_dir = logs_root / today
        log_dir.mkdir(parents=True, exist_ok=True)

        for agent_name, agent in group.agents.items():
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

                occurrence, marker = _due_occurrence(
                    routine, logs_root, agent_name, now
                )
                if occurrence is None or marker is None:
                    log.warning(
                        "  WARNING: rule for %s/%s has no usable schedule",
                        agent_name,
                        routine.id,
                    )
                    continue
                if routine.schedule.at and marker.exists():
                    continue
                if not catch_up_allows(occurrence, now, bound, grace_window(interval)):
                    continue

                try:
                    request = JobRequest(
                        config_path=snapshot.path,
                        group_key=group_key,
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
