from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).parents[1]
PREVIOUS_TERMS = (
    "".join(("a", "gency")),
    "".join(("chris", "tag")),
)


def _tracked_paths() -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return tuple(
        path for path in completed.stdout.decode("utf-8").split("\0") if path
    )


def test_tracked_paths_omit_previous_brand_terms():
    matches = [
        path
        for path in _tracked_paths()
        if any(term in path.casefold() for term in PREVIOUS_TERMS)
    ]
    assert not matches, "\n".join(matches)


def test_tracked_text_and_symlink_targets_omit_previous_brand_terms():
    matches: list[str] = []
    for relative in _tracked_paths():
        path = REPO_ROOT / relative
        if path.is_symlink():
            text = os.readlink(path)
        elif path.is_file():
            payload = path.read_bytes()
            if b"\0" in payload:
                continue
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            continue
        lowered = text.casefold()
        for term in PREVIOUS_TERMS:
            if term in lowered:
                matches.append(f"{relative}: {term}")
    assert not matches, "\n".join(matches)
