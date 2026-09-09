from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_workflow_settings_related_modules_import_in_fresh_process(tmp_path):
    config_path = tmp_path / "import-config.yaml"
    env = os.environ.copy()
    env["FLOWGENCY_CONFIG"] = str(config_path)
    script = "\n".join(
        [
            "import flowgency.jobs",
            "import flowgency.tickets.access",
            "import flowgency.workflows.configuration",
            "import flowgency.app",
            "print('imports-ok')",
        ]
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imports-ok"