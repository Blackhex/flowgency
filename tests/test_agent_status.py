import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agency.app import is_agent_running


def _group(tmp_path):
    shared = tmp_path / "shared"
    (shared / "logs").mkdir(parents=True)
    return {"key": "grp", "path": tmp_path, "shared": shared}


def test_running_marker_fresh(tmp_path):
    g = _group(tmp_path)
    (g["shared"] / "logs" / ".running-product").touch()
    assert is_agent_running(g, "product", timeout=1800) is True


def test_running_marker_stale(tmp_path):
    g = _group(tmp_path)
    marker = g["shared"] / "logs" / ".running-product"
    marker.touch()
    old = time.time() - 3600  # 1h ago, older than 1800s timeout
    os.utime(marker, (old, old))
    assert is_agent_running(g, "product", timeout=1800) is False


def test_running_marker_absent(tmp_path):
    g = _group(tmp_path)
    assert is_agent_running(g, "product", timeout=1800) is False
