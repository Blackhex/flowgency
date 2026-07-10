import argparse
from pathlib import Path

from .execution import execute_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute one Agency job")
    parser.add_argument("job_path", type=Path)
    args = parser.parse_args(argv)
    result = execute_job(args.job_path.resolve())
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())