import argparse
import logging
from pathlib import Path

from agency.configuration.store import ConfigStore
from agency.jobs.queue import drain

from .authority import JobStore
from .execution import execute_job
from .store import read_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute one Agency job")
    parser.add_argument("--store-root", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--immutable-digest", required=True)
    args = parser.parse_args(argv)
    store = JobStore.from_store_root(args.store_root)
    reference = store.reference(
        args.group_id,
        args.job_id,
        args.immutable_digest,
    )
    result = execute_job(reference)
    try:
        config = ConfigStore(Path(read_job(reference.path).spec.config_path)).load().config
        drain(config, memory_store=store.memory_store)
    except Exception:
        logging.getLogger("agency.jobs.worker").exception("drain after job failed")
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
