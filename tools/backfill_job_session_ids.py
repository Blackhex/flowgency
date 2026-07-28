"""One-time backfill of JobRecord.session_id from persisted stderr logs."""

from __future__ import annotations

import argparse
import re
from dataclasses import replace
from pathlib import Path

from agency.cli import _snapshot_read_only
from agency.fs.locks import exclusive_lock
from agency.jobs.authority import JobStore
from agency.jobs.store import job_lock_path, read_job, write_job


_RESUME = re.compile(r"--resume[= ]([A-Za-z0-9_-]{1,128})")


def _session_id_from_stderr(stderr_path: str | None) -> str | None:
    if not stderr_path:
        return None
    try:
        text = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _RESUME.search(text)
    return match.group(1) if match else None


def backfill(
    config_path: Path,
    group: str | None = None,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    snapshot = _snapshot_read_only(Path(config_path))
    store = JobStore(snapshot.config.agency.memory_store)
    group_ids = [group] if group else list(snapshot.config.groups)
    results: list[tuple[str, str]] = []
    for group_id in group_ids:
        for path in store.paths(group_id):
            try:
                record = read_job(path)
            except Exception:
                continue
            if record.session_id:
                results.append((record.spec.job_id, "skipped"))
                continue
            session_id = _session_id_from_stderr(record.stderr_path)
            if not session_id:
                results.append((record.spec.job_id, "unchanged"))
                continue
            results.append((record.spec.job_id, "filled"))
            if dry_run:
                continue
            with exclusive_lock(job_lock_path(path), wait=True):
                current = read_job(path)
                if current.session_id:
                    continue
                write_job(path, replace(current, session_id=session_id))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--group")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    results = backfill(args.config, args.group, args.dry_run)
    for job_id, outcome in results:
        print(f"{outcome:<10} {job_id}")
    filled = sum(1 for _, outcome in results if outcome == "filled")
    verb = "would fill" if args.dry_run else "filled"
    print(f"\n{verb} {filled} of {len(results)} record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
