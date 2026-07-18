from __future__ import annotations

from pathlib import Path


def pick_directory(initial: Path | None = None) -> Path | None:
    try:
        from tkinter import TclError, filedialog
    except ImportError:
        return None

    initialdir = str(Path(initial).expanduser().resolve()) if initial is not None else None
    try:
        selection = filedialog.askdirectory(initialdir=initialdir)
    except (OSError, TclError):
        return None
    if not selection:
        return None
    return Path(selection).expanduser().resolve(strict=True)
