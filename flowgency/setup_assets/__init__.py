from __future__ import annotations

from pathlib import Path


def copilot_discovery_root() -> Path:
    """Return the stable package-owned root passed to Copilot --add-dir."""
    return (Path(__file__).resolve().parent / "copilot").resolve()


def workflow_example_root() -> Path:
    """Return the package-owned directory that holds shipped blueprint examples."""
    return (Path(__file__).resolve().parent / "workflows").resolve()
