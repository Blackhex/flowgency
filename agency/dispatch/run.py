"""Agency dispatch runner — called by OS-native timer."""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from agency.jobs import JobSpec, JobSubmissionError, JobValidationError, submit_job

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
    match = re.fullmatch(r"(\d+)(m|h)", interval_str)
    if not match:
        log.warning("Invalid every interval: %s", interval_str)
        return False
    val = int(match.group(1))
    unit = match.group(2)
    seconds = val * 60 if unit == "m" else val * 3600
    if not marker_file.exists():
        return True
    elapsed = time.time() - marker_file.stat().st_mtime
    return elapsed >= seconds


def load_dispatch_config(config_path: str) -> dict:
    """Load config.yaml."""
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def run_dispatch_cycle(config: dict, config_path: Path | str, launcher=None) -> None:
    """Run one full dispatch cycle across all enabled groups."""
    agency_cfg = config.get("agency", {})
    dispatch_cfg = agency_cfg.get("dispatch", {})
    interval = dispatch_cfg.get("interval", 15)

    groups = config.get("groups", {})
    for group_key, g in groups.items():
        d = g.get("dispatch", {})
        if not d.get("enabled", False):
            continue

        log.info("Processing group: %s", group_key)
        group_path = Path(g["path"])
        timeout = d.get("timeout", 1800)
        daily_limit = d.get("daily_limit", 20)

        logs_root = group_path / "shared" / "logs"
        today = datetime.now().strftime("%Y-%m-%d")
        log_dir = logs_root / today
        log_dir.mkdir(parents=True, exist_ok=True)

        # Daily limit
        out_count = len(list(log_dir.glob("*.out")))
        if out_count >= daily_limit:
            log.info("  SKIP: daily limit reached (%d/%d)", out_count, daily_limit)
            continue

        dispatch_agents = d.get("agents", {})
        for agent_name, rules in dispatch_agents.items():
            if not rules:
                continue
            for rule in rules:
                prompt = rule.get("prompt", "")
                at_time = rule.get("at", "")
                every_val = rule.get("every", "")
                condition = rule.get("condition", "")

                if not prompt:
                    log.warning("  WARNING: rule for %s missing 'prompt'", agent_name)
                    continue

                # Skip condition rules
                if condition:
                    log.info("  SKIP: %s/%s has condition '%s' (requires group dispatch script)",
                             agent_name, prompt, condition)
                    continue

                stem = prompt.removesuffix(".md")

                # Re-check daily limit
                out_count = len(list(log_dir.glob("*.out")))
                if out_count >= daily_limit:
                    log.info("  SKIP: daily limit reached")
                    break

                should_run = False
                if at_time:
                    event_marker = log_dir / f".event-{agent_name}-{stem}"
                    if event_marker.exists():
                        continue
                    if check_at_rule(at_time, interval=interval):
                        should_run = True
                elif every_val:
                    every_marker = logs_root / f".last-{agent_name}-{stem}"
                    if check_every_rule(every_marker, every_val):
                        should_run = True
                else:
                    log.warning("  WARNING: rule for %s/%s has no 'at' or 'every'", agent_name, prompt)
                    continue

                if should_run:
                    agent_timeout = timeout
                    prompt_path = group_path / "shared" / "prompts" / prompt
                    try:
                        prompt_content = prompt_path.read_text()
                        spec = JobSpec.create(
                            config_path=Path(config_path),
                            group_key=group_key,
                            agent_name=agent_name,
                            trigger="scheduled_prompt",
                            prompt_source={"type": "saved_prompt", "path": str(prompt_path)},
                            prompt_content=prompt_content,
                            timeout_override=agent_timeout,
                        )
                        submit_job(spec, launcher)
                    except (TypeError, ValueError, JobValidationError, JobSubmissionError, OSError) as error:
                        log.error("  ERROR: could not submit %s/%s: %s", agent_name, prompt, error)
                        continue
                    # Touch markers
                    if at_time:
                        (log_dir / f".event-{agent_name}-{stem}").touch()
                    elif every_val:
                        (logs_root / f".last-{agent_name}-{stem}").touch()


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
