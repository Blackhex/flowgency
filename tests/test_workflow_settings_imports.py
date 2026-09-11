from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _run_python(
    *args: str,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=timeout,
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
    assert "Failed to load integration" not in cli_first.stderr

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
                "from flowgency.jobs import (",
                "    JobSubmissionError,",
                "    JobValidationError,",
                "    resolve_job_request,",
                "    submit_job_request,",
                ")",
                "from flowgency.jobs.resolution import (",
                "    JobValidationError as concrete_job_validation_error,",
                "    resolve_job_request as concrete_resolve_job_request,",
                ")",
                "from flowgency.jobs.submission import (",
                "    JobSubmissionError as concrete_job_submission_error,",
                "    submit_job_request as concrete_submit_job_request,",
                ")",
                "assert JobValidationError is concrete_job_validation_error",
                "assert resolve_job_request is concrete_resolve_job_request",
                "assert JobSubmissionError is concrete_job_submission_error",
                "assert submit_job_request is concrete_submit_job_request",
                "print('imports-ok')",
            ]
        ),
        cwd=repo_root,
        env=env,
    )

    assert jobs_first.returncode == 0, jobs_first.stderr
    assert jobs_first.stdout.strip() == "imports-ok"
    assert "Failed to load integration" not in jobs_first.stderr

    help_result = _run_python(
        "-m",
        "flowgency.cli",
        "--help",
        cwd=repo_root,
        env=env,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert "Flowgency - AI Agent Management" in help_result.stdout
    assert "Failed to load integration" not in help_result.stderr


def test_workflow_settings_effective_policy_imports_before_registry(tmp_path):
    config_path = tmp_path / "import-config.yaml"
    env = os.environ.copy()
    env["FLOWGENCY_CONFIG"] = str(config_path)
    repo_root = Path(__file__).resolve().parents[1]

    resolver_first = _run_python(
        "-c",
        "\n".join(
            [
                "import flowgency.configuration.effective",
                "from flowgency.integrations import REGISTRY",
                "import flowgency.integrations.flowgency.copilot",
                "from flowgency.configuration.effective import resolve_effective_policy",
                "assert 'copilot' in REGISTRY, sorted(REGISTRY)",
                "assert callable(resolve_effective_policy)",
                "print('resolver-imports-ok')",
            ]
        ),
        cwd=repo_root,
        env=env,
    )

    assert resolver_first.returncode == 0, resolver_first.stderr
    assert resolver_first.stdout.strip() == "resolver-imports-ok"
    assert "Failed to load integration" not in resolver_first.stderr