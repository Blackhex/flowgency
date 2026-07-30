"""Agency dispatch runner — called by OS-native timer."""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

from agency.configuration import resolve_group_paths
from agency.dispatch.schedule import at_marker_path, every_marker_path, parse_every
from agency.configuration.store import ConfigStore
from agency.jobs import JobRequest, JobSubmissionError, JobValidationError, submit_job_request

log = logging.getLogger("agency.dispatch")


def check_at_rule(target_time: str, now_epoch: float | None = None, interval: int = 15) -> bool:
    """Check if current time is within (interval+2) minutes of an 'at' target."""
    now = datetime.now()
    if now_epoch is not None:
        now = datetime.fromtimestamp(now_epoch)
    today = now.strftime("%Y-%m-%d")
    try:
        target = datetime.strptime(f"{today} {target_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        log.warning("Invalid at time: %s", target_time)
        return False
    diff = (now - target).total_seconds()
    window = (interval + 2) * 60
    return 0 <= diff < window


def check_every_rule(marker_file: Path, interval_str: str) -> bool:
    """Check if enough time has elapsed since marker file mtime."""
    period = parse_every(interval_str)
    if period is None:
        log.warning("Invalid every interval: %s", interval_str)
        return False
    if not marker_file.exists():
        return True
    elapsed = time.time() - marker_file.stat().st_mtime
    return elapsed >= period.total_seconds()


def load_dispatch_config(config_path: str):
    """Load the canonical config snapshot."""
    return ConfigStore(Path(config_path)).load()


def run_dispatch_cycle(config, config_path: Path | str, launcher=None) -> None:
    """Run one full dispatch cycle across all enabled groups."""
    snapshot = config if hasattr(config, "config") else load_dispatch_config(str(config_path))
    resolved = snapshot.config
    interval = resolved.agency.dispatch.interval

    for group_key, group in resolved.groups.items():
        if not group.dispatch.enabled:
            continue

        log.info("Processing group: %s", group_key)
        paths = resolve_group_paths(group)

        logs_root = paths.logs
        today = datetime.now().strftime("%Y-%m-%d")
        log_dir = logs_root / today
        log_dir.mkdir(parents=True, exist_ok=True)

        for agent_name, agent in group.agents.items():
            for routine in agent.routines:
                if not routine.enabled:
                    log.info("  SKIP: %s/%s is disabled", agent_name, routine.id)
                    continue
                at_time = routine.schedule.at or ""
                every_val = routine.schedule.every or ""

                if getattr(routine, "condition", None):
                    log.info(
                        "  SKIP: %s/%s has condition '%s' (requires group dispatch script)",
                        agent_name,
                        routine.id,
                        routine.condition,
                    )
                    continue

                should_run = False
                if at_time:
                    event_marker = at_marker_path(logs_root, agent_name, routine.id, today)
                    if event_marker.exists():
                        continue
                    if check_at_rule(at_time, interval=interval):
                        should_run = True
                elif every_val:
                    every_marker = every_marker_path(logs_root, agent_name, routine.id)
                    if check_every_rule(every_marker, every_val):
                        should_run = True
                else:
                    log.warning("  WARNING: rule for %s/%s has no 'at' or 'every'", agent_name, routine.id)
                    continue

                if should_run:
                    try:
                        request = JobRequest(
                            config_path=snapshot.path,
                            group_key=group_key,
                            agent_name=agent_name,
                            trigger="scheduled_prompt",
                            task_input="",
                            routine_id=routine.id,
                        )
                        submit_job_request(request, launcher)
                    except (TypeError, ValueError, JobValidationError, JobSubmissionError, OSError) as error:
                        log.error("  ERROR: could not submit %s/%s: %s", agent_name, routine.id, error)
                        continue
                    # Touch markers
                    if at_time:
                        at_marker_path(logs_root, agent_name, routine.id, today).touch()
                    elif every_val:
                        every_marker_path(logs_root, agent_name, routine.id).touch()


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
