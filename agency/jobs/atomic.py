"""Shared atomic file-write helper.

Lives under ``agency.jobs`` (not ``agency.app``) so it can be imported by both
the web app and worker-side job execution code without a circular import —
``agency.app`` already imports from ``agency.jobs``, so ``agency.jobs`` must
never import ``agency.app``.
"""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to a temp file in the same directory as ``path`` (so the rename is
    on the same filesystem/volume) then swaps it into place with
    ``os.replace``. This guarantees readers never observe a partially written
    file, even if the process is interrupted mid-write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
