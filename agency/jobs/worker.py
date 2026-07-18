import argparse

from .authority import JobStore
from .execution import execute_job


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
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
