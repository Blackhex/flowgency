"""Custom script integration."""

import subprocess
import time
from pathlib import Path

import yaml

from agency.configuration.issues import ValidationIssue
from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
    parse_identity_frontmatter as _parse_frontmatter,
)
from agency.integrations.models import RuntimeCapabilities
from agency.integrations.models import IntegrationRunRequest


class ScriptIntegration(BaseIntegration):
    name = "script"
    display_name = "Custom Script"
    supports_execution = True
    supports_ai_backend = False
    detect_priority = 1000  # Never auto-detects
    projector = BaseIntegration._default_projector("agent.md")
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"unrestricted"}),
        tool_modes=frozenset({"all"}),
    )

    def __init__(self, integration_config: dict | None = None):
        self._config = integration_config or {}

    def with_config(self, integration_config: dict) -> "ScriptIntegration":
        """Return a new instance with the given config. Used by dispatch/execution resolvers."""
        return ScriptIntegration(integration_config)

    def identity_filename(self) -> str:
        return "agent.md"

    def detect(self, agent_dir: Path) -> bool:
        return False  # Must be configured explicitly

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        path = agent_dir / "agent.md"
        if not path.is_file():
            return None
        meta, body = _parse_frontmatter(path.read_text())
        return AgentIdentity(
            display_name=meta.get("display_name"),
            title=meta.get("title"),
            emoji=meta.get("emoji"),
            body=body,
        )

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        path = agent_dir / "agent.md"
        meta = {}
        if identity.display_name:
            meta["display_name"] = identity.display_name
        if identity.title:
            meta["title"] = identity.title
        if identity.emoji:
            meta["emoji"] = identity.emoji
        if meta:
            front = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
            path.write_text(f"---\n{front}\n---\n\n{identity.body}")
        else:
            path.write_text(identity.body)

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        if not config.get("command"):
            errors.append("'command' is required for the script integration")
        return errors

    def validate_run(self, request: IntegrationRunRequest):
        issues = list(super().validate_run(request))
        command = self._config.get("command", "")
        for obsolete in ("{workspace_dir}", "{agent_dir}"):
            if obsolete in command:
                issues.append(
                    ValidationIssue(
                        code="script-obsolete-placeholder",
                        scope="integrations.script",
                        field="integration_config.command",
                        message=f"Script integration does not support {obsolete}.",
                        corrective_hint="Use {workspace_root} instead.",
                    )
                )
        required = ("{runtime_dir}", "{workspace_root}", "{skill}")
        if request.skill is not None and not all(token in command for token in required):
            issues.append(
                ValidationIssue(
                    code="script-missing-runtime-placeholders",
                    scope="integrations.script",
                    field="integration_config.command",
                    message="Script integration requires runtime_dir, workspace_root, and skill placeholders for routine skill activation.",
                    corrective_hint="Add {runtime_dir}, {workspace_root}, and {skill} placeholders or run without a routine skill.",
                )
            )
        return tuple(issues)

    def run(self, request: IntegrationRunRequest) -> RunResult:
        self.require_valid_run(request)
        errors = self.validate_config(self._config)
        if errors:
            raise IntegrationError("; ".join(errors))
        command = self._config.get("command", "")
        if not command:
            raise IntegrationError("No command configured for script integration")
        command = command.replace("{prompt_file}", str(request.task_file))
        command = command.replace("{runtime_dir}", str(request.launch_dir))
        command = command.replace("{workspace_root}", str(request.workspace_root))
        command = command.replace("{skill}", request.skill or "")
        start = time.monotonic()
        try:
            result = subprocess.run(
                command, shell=True,
                capture_output=True, text=True, timeout=request.timeout,
                cwd=str(request.launch_dir),
            )
            return RunResult(
                exit_code=result.returncode, stdout=result.stdout,
                stderr=result.stderr, duration_seconds=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired:
            return RunResult(exit_code=124, stdout="", stderr="Timed out",
                           duration_seconds=time.monotonic() - start)


_register(ScriptIntegration())
