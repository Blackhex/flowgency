"""Disposable feasibility probe: does the Copilot CLI expose a supported boundary
at which Flowgency may send ``/exit`` to an interactive setup session?

The probe reads only the CLI's static, documented surface (``--version``,
``--help``, ``help commands``, ``help monitoring``) in a scratch home. It never
starts an interactive model session, reads user session state, writes real
configuration, answers prompts or widens permissions. A boundary counts only if
it is machine-readable, works in the interactive session and has been measured;
screen text, silence and a documented ``/exit`` alone do not qualify.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROBE_VERSION = 1
HELP_TIMEOUT_SECONDS = 30
TOPICS = {
    "version": ("--version",),
    "help": ("--help",),
    "commands": ("help", "commands"),
    "monitoring": ("help", "monitoring"),
}
_VERSION = re.compile(r"(\d+\.\d+\.\d+(?:-\d+)?)")
_ENV_PASSTHROUGH = (
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "OS",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
)
# Flags that exist but cannot describe the interactive `-i` session Flowgency launches.
_NON_INTERACTIVE_SURFACES = {
    "-p/--prompt": "non-interactive mode; exits after one prompt",
    "--output-format": "applies to non-interactive output, not the interactive session",
    "--acp": "a different protocol that replaces the interactive session",
    "--usage-output-file": "final usage written at exit, not a boundary before it",
    "--share": "applies after non-interactive completion",
}


@dataclass
class Evaluation:
    boundary: str = "none"
    result: str = "unsupported"
    reason: str = ""
    unverified_candidates: list[str] = field(default_factory=list)
    non_boundary_surfaces: dict[str, str] = field(default_factory=dict)


def _scratch_environment(scratch: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in _ENV_PASSTHROUGH if name in os.environ}
    for name in ("TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "COPILOT_HOME"):
        env[name] = str(scratch)
    return env


def read_surface(prefix: tuple[str, ...], scratch: Path) -> dict[str, str]:
    surface: dict[str, str] = {}
    env = _scratch_environment(scratch)
    for topic, arguments in TOPICS.items():
        completed = subprocess.run(
            [*prefix, *arguments],
            cwd=scratch,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=HELP_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"copilot {' '.join(arguments)} exited {completed.returncode}")
        surface[topic] = completed.stdout.decode("utf-8", errors="replace")
    return surface


def evaluate(surface: dict[str, str]) -> Evaluation:
    evaluation = Evaluation()
    help_text = surface.get("help", "")
    monitoring = surface.get("monitoring", "")
    flag_markers = {
        "-p/--prompt": "--prompt",
        "--output-format": "--output-format",
        "--acp": "--acp",
        "--usage-output-file": "--usage-output-file",
        "--share": "--share",
    }
    for name, marker in flag_markers.items():
        if marker in help_text:
            evaluation.non_boundary_surfaces[name] = _NON_INTERACTIVE_SURFACES[name]
    if "COPILOT_OTEL_FILE_EXPORTER_PATH" in monitoring and "invoke_agent" in monitoring:
        evaluation.unverified_candidates.append("otel-file-exporter:invoke_agent-span-end")
    reasons = []
    # No candidate is ever verified here: measuring one needs a model session.
    if evaluation.unverified_candidates:
        reasons.append(
            "The documented OpenTelemetry file exporter reports agent-turn spans in "
            "interactive mode, but nothing documents that a finished span means an empty "
            "input field, no queued input and no pending prompt, and confirming it needs "
            "an interactive model session, which this probe must not start."
        )
    else:
        reasons.append("No machine-readable idle or completion signal is documented.")
    reasons.append(
        "Interactive commands, the status line and --output-format/--acp/-p do not "
        "describe the interactive session; /exit alone is not a boundary."
    )
    evaluation.reason = " ".join(reasons)
    return evaluation


def _cli_version(version_text: str) -> str:
    match = _VERSION.search(version_text)
    return match.group(1) if match else "unknown"


def run(evidence_dir: Path) -> dict[str, object]:
    from flowgency.integrations.errors import IntegrationError
    from flowgency.integrations.flowgency.copilot import CopilotIntegration

    evidence_dir.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, object] = {
        "probe_version": PROBE_VERSION,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "cli_version": "unknown",
        "boundary": "none",
        "attempted_exit_count": 0,
        "exit_code": None,
        "confirmed_cleanup": "n/a",
        "result": "unsupported",
        "reason": "",
        "unverified_candidates": [],
        "non_boundary_surfaces": {},
        "surface_sha256": {},
    }
    with tempfile.TemporaryDirectory(prefix="flowgency-exit-probe-") as scratch_name:
        scratch = Path(scratch_name)
        try:
            prefix = CopilotIntegration()._interactive_setup_command_prefix()
            surface = read_surface(prefix, scratch)
        except (IntegrationError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            receipt["reason"] = f"The CLI surface could not be read: {type(error).__name__}."
            _write(evidence_dir, receipt)
            return receipt
    evaluation = evaluate(surface)
    receipt.update(
        cli_version=_cli_version(surface["version"]),
        boundary=evaluation.boundary,
        result=evaluation.result,
        reason=evaluation.reason,
        unverified_candidates=evaluation.unverified_candidates,
        non_boundary_surfaces=evaluation.non_boundary_surfaces,
        surface_sha256={
            topic: hashlib.sha256(text.encode("utf-8")).hexdigest() for topic, text in surface.items()
        },
    )
    for topic, text in surface.items():
        (evidence_dir / f"surface-{topic}.txt").write_text(text, encoding="utf-8")
    _write(evidence_dir, receipt)
    return receipt


def _write(evidence_dir: Path, receipt: dict[str, object]) -> None:
    (evidence_dir / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    arguments = parser.parse_args()
    receipt = run(arguments.evidence_dir)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["result"] in {"supported", "unsupported"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
