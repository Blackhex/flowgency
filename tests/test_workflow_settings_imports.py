from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _run_python(*args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def test_workflow_settings_related_modules_import_in_fresh_process(tmp_path):
    config_path = tmp_path / "import-config.yaml"
    env = os.environ.copy()
    env["FLOWGENCY_CONFIG"] = str(config_path)
    repo_root = Path(__file__).resolve().parents[1]

    cli_first = _run_python(
        "-c",
        "\n".join(
            [
                "import flowgency.cli",
                "from flowgency.integrations import REGISTRY",
                "assert 'copilot' in REGISTRY, sorted(REGISTRY)",
                "assert flowgency.cli.build_parser().prog == 'flowgency'",
                "print('cli-imports-ok')",
            ]
        ),
        cwd=repo_root,
        env=env,
    )

    assert cli_first.returncode == 0, cli_first.stderr
    assert cli_first.stdout.strip() == "cli-imports-ok"
    assert "Failed to load integration flowgency.copilot" not in cli_first.stderr

    jobs_first = _run_python(
        "-c",
        "\n".join(
            [
                "import flowgency.jobs",
                "import flowgency.tickets.access",
                "import flowgency.workflows.configuration",
                "import flowgency.app",
                "from flowgency.integrations import REGISTRY",
                "assert 'copilot' in REGISTRY, sorted(REGISTRY)",
                "print('imports-ok')",
            ]
        ),
        cwd=repo_root,
        env=env,
    )

    assert jobs_first.returncode == 0, jobs_first.stderr
    assert jobs_first.stdout.strip() == "imports-ok"
    assert "Failed to load integration flowgency.copilot" not in jobs_first.stderr

    help_result = _run_python(
        "-m",
        "flowgency.cli",
        "--help",
        cwd=repo_root,
        env=env,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert "Flowgency - AI Agent Management" in help_result.stdout
    assert "Failed to load integration flowgency.copilot" not in help_result.stderr