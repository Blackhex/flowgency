"""Durable atomic file-write helpers."""

import os
import tempfile
import time
from pathlib import Path


_WINDOWS_REPLACE_RETRIES = 20
_WINDOWS_REPLACE_DELAY_SECONDS = 0.01


def _fsync_parent_directory(path: Path) -> None:
    if os.name == "nt":
        return
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        _replace_with_retry(tmp_name, path)
        _fsync_parent_directory(path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(
    path: Path,
    content: str,
    encoding: str = "utf-8",
) -> None:
    atomic_write_bytes(Path(path), content.encode(encoding))


def _replace_with_retry(source: str, target: Path) -> None:
    if os.name != "nt":
        os.replace(source, target)
        return

    last_error = None
    for attempt in range(_WINDOWS_REPLACE_RETRIES):
        try:
            os.replace(source, target)
            return
        except PermissionError as error:
            last_error = error
            if getattr(error, "winerror", None) != 5:
                raise
            if attempt == _WINDOWS_REPLACE_RETRIES - 1:
                raise
            time.sleep(_WINDOWS_REPLACE_DELAY_SECONDS)
    if last_error is not None:
        raise last_error
