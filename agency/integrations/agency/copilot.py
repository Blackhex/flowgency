"""GitHub Copilot CLI integration."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from agency.blueprints.projectors import get_projector
from agency.integrations import (
    AgentIdentity,
    BaseIntegration,
    FileChange,
    IntegrationError,
    RunResult,
    _register,
    format_interactive_command,
    spawn_interactive_terminal,
    terminal_available,
)
from agency.integrations.models import (
    IntegrationRunRequest,
    InteractiveSetupRequest,
    InteractiveSetupResult,
    RuntimeCapabilities,
)


class CopilotIntegration(BaseIntegration):
    name = "copilot"
    display_name = "GitHub Copilot"
    supports_execution = True
    supports_ai_backend = True
    supports_sandbox = True
    detect_priority = 7
    projector = get_projector("copilot")
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"restricted", "unrestricted"}),
        tool_modes=frozenset({"all", "allowlist"}),
    )

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def _identity_file(self, agent_dir: Path) -> Path:
        return agent_dir / "AGENTS.md"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".copilot").is_dir() or (agent_dir / ".github").is_dir()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, self._identity_file(agent_dir))

    def prepare_agent_dir(self, agent_dir: Path) -> None:
        (agent_dir / ".copilot").mkdir(parents=True, exist_ok=True)

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        self.prepare_agent_dir(agent_dir)
        self._write_sidecar_identity(agent_dir, self._identity_file(agent_dir), identity)

    def interactive_setup_available(self) -> bool:
        return terminal_available() and Path(self._find_cmd()).exists()

    def _interactive_setup_command(
        self,
        request: InteractiveSetupRequest,
    ) -> Sequence[str]:
        project_dir = request.project_dir.resolve(strict=True)
        return (
            self._find_cmd(),
            "-C",
            str(project_dir),
            "-i",
            request.prompt,
            "--name",
            "Agency setup",
        )

    def launch_interactive_setup(self, request: InteractiveSetupRequest) -> InteractiveSetupResult:
        project_dir = request.project_dir.resolve(strict=True)
        command = self._interactive_setup_command(request)
        fallback_command = spawn_interactive_terminal(command, project_dir)
        return InteractiveSetupResult(fallback_command=fallback_command)

    def interactive_setup_fallback_command(
        self,
        request: InteractiveSetupRequest,
    ) -> str:
        return format_interactive_command(self._interactive_setup_command(request))

    # Copilot native file-edit tools that mutate the filesystem. Read-only
    # tools like "view" are intentionally excluded. Shell edits are not
    # tracked by the CLI and cannot be recovered here.
    _WRITE_TOOLS = {"create", "edit", "str_replace", "delete"}
    _STATUS_BY_COMMAND = {
        "create": "added",
        "edit": "modified",
        "str_replace": "modified",
        "delete": "deleted",
    }

    @staticmethod
    def _parse_jsonl_output(raw: str, root: "Path | None") -> "tuple[str, list[FileChange]]":
        """Parse Copilot --output-format json (JSONL) into (text, changes).

        Reconstructs human-readable text from assistant messages and extracts
        per-file changes from native file-edit tool calls. Any structural
        problem falls back to (raw, []); a run must never break on parsing.
        """
        try:
            tool_names: dict[str, str] = {}
            tool_paths: dict[str, str] = {}
            # path -> {"status": str, "added": int, "removed": int}
            files: dict[str, dict] = {}
            texts: list[str] = []
            saw_json = False

            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    continue
                saw_json = True
                etype = obj.get("type")
                data = obj.get("data") or {}

                if etype == "tool.execution_start":
                    tcid = data.get("toolCallId")
                    if tcid:
                        tool_names[tcid] = data.get("toolName", "")
                        path = (data.get("arguments") or {}).get("path")
                        if path:
                            tool_paths[tcid] = path
                elif etype == "tool.execution_complete":
                    tcid = data.get("toolCallId")
                    telemetry = data.get("toolTelemetry") or {}
                    props = telemetry.get("properties") or {}
                    metrics = telemetry.get("metrics") or {}
                    command = props.get("command") or tool_names.get(tcid, "")
                    if command not in CopilotIntegration._WRITE_TOOLS:
                        continue
                    path = tool_paths.get(tcid)
                    if not path:
                        continue
                    rel = CopilotIntegration._relativize(path, root)
                    entry = files.setdefault(rel, {"status": None, "added": 0, "removed": 0})
                    entry["added"] += int(metrics.get("linesAdded") or 0)
                    entry["removed"] += int(metrics.get("linesRemoved") or 0)
                    new_status = CopilotIntegration._STATUS_BY_COMMAND.get(command, "modified")
                    # "added" wins (a file created this run stays added); then
                    # "deleted"; otherwise "modified".
                    if entry["status"] is None:
                        entry["status"] = new_status
                    elif entry["status"] != "added" and new_status == "added":
                        entry["status"] = "added"
                    elif entry["status"] == "modified" and new_status == "deleted":
                        entry["status"] = "deleted"
                elif etype == "assistant.message":
                    content = data.get("content")
                    if content:
                        texts.append(content)
                elif etype == "result":
                    # Fallback source for file list if no per-tool edits parsed.
                    usage = obj.get("usage") or data.get("usage") or {}
                    code_changes = usage.get("codeChanges") or {}
                    for p in code_changes.get("filesModified") or []:
                        rel = CopilotIntegration._relativize(p, root)
                        if rel not in files:
                            files[rel] = {"status": "modified", "added": 0, "removed": 0}

            if not saw_json:
                return raw, []

            changes = [
                FileChange(
                    path=path,
                    status=info["status"] or "modified",
                    lines_added=info["added"],
                    lines_removed=info["removed"],
                )
                for path, info in files.items()
            ]
            text = "\n".join(texts) if texts else raw
            return text, changes
        except Exception:
            return raw, []

    @staticmethod
    def _compact_number(value: int | float) -> str:
        if value < 1000:
            return f"{value:g}"
        if value < 1_000_000:
            return f"{value / 1000:.1f}k"
        return f"{value / 1_000_000:.1f}m"

    @staticmethod
    def _format_duration(milliseconds: int | float) -> str:
        seconds = max(0, round(milliseconds / 1000))
        minutes, seconds = divmod(seconds, 60)
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    @staticmethod
    def _usage_summary(raw: str) -> str:
        result = None
        for line in raw.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if event.get("type") == "result":
                result = event
        if not result or not result.get("sessionId"):
            return ""

        copilot_home = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))
        events_path = (
            copilot_home / "session-state" / result["sessionId"] / "events.jsonl"
        )
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""

        shutdown = None
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if event.get("type") == "session.shutdown":
                shutdown = event.get("data") or {}
                break
        if shutdown is None:
            return ""

        token_details = shutdown.get("tokenDetails") or {}
        input_tokens = (token_details.get("input") or {}).get("tokenCount", 0)
        cached_tokens = (token_details.get("cache_read") or {}).get("tokenCount", 0)
        written_tokens = (token_details.get("cache_write") or {}).get("tokenCount", 0)
        output_tokens = (token_details.get("output") or {}).get("tokenCount", 0)
        total_input = input_tokens + cached_tokens + written_tokens
        reasoning_tokens = sum(
            ((metrics.get("usage") or {}).get("reasoningTokens") or 0)
            for metrics in (shutdown.get("modelMetrics") or {}).values()
        )
        changes = shutdown.get("codeChanges") or {}
        usage = result.get("usage") or {}
        credits = shutdown.get("totalPremiumRequests", usage.get("premiumRequests", 0))
        duration = CopilotIntegration._format_duration(
            usage.get("sessionDurationMs", 0)
        )

        return (
            f"Changes    +{changes.get('linesAdded', 0)} "
            f"-{changes.get('linesRemoved', 0)}\n"
            f"AI Credits {credits:g} ({duration})\n"
            f"Tokens     \u2191 {CopilotIntegration._compact_number(total_input)} "
            f"({CopilotIntegration._compact_number(cached_tokens)} cached, "
            f"{CopilotIntegration._compact_number(written_tokens)} written) "
            f"\u2022 \u2193 {CopilotIntegration._compact_number(output_tokens)} "
            f"({CopilotIntegration._compact_number(reasoning_tokens)} reasoning)\n"
            f"Resume     copilot --resume={result['sessionId']}"
        )

    @staticmethod
    def _relativize(path: str, root: "Path | None") -> str:
        """Return path relative to root when possible, else the original."""
        if not root:
            return path
        try:
            return str(Path(path).resolve().relative_to(Path(root).resolve()))
        except (ValueError, OSError):
            return path

    def run(self, request: IntegrationRunRequest) -> RunResult:
        self.require_valid_run(request)
        prompt_text = request.task_file.read_text()
        cmd = self._resolve_real_cmd(self._find_cmd())

        roots = request.runtime_policy.sandbox_roots
        tools = request.runtime_policy.tools

        cmd_args = [
            cmd, "-p", prompt_text,
            "--no-ask-user",
            "--no-color",
            "--experimental",
            "--output-format", "json",
        ]

        if roots:
            for p in roots:
                cmd_args += ["--add-dir", str(p)]
        else:
            cmd_args += ["--allow-all-paths"]

        if tools.mode == "allowlist":
            for t in tools.names:
                cmd_args += ["--allow-tool", t]
        else:
            cmd_args += ["--allow-all-tools", "--autopilot"]

        start = time.monotonic()
        # On Windows `copilot` resolves to a .bat wrapper that spawns
        # powershell -> copilot.ps1 -> the real copilot.exe. That chain
        # re-allocates a console for the grandchild exe, so the CLI decides it
        # is interactive and tries to prompt for tool permission, which fails
        # closed in headless dispatch with "Permission denied and could not
        # request permission from user" (github/copilot-cli#2971) even with
        # --allow-all-tools set. _resolve_real_cmd() bypasses the wrapper and
        # returns copilot.exe directly; CREATE_NO_WINDOW then actually
        # suppresses the console for that process, so the CLI stays
        # non-interactive -- matching the proven no-console Start-Job launch
        # used by production dispatchers.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                cmd_args,
                capture_output=True, text=True, timeout=request.timeout,
                cwd=str(request.launch_dir),
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            duration = time.monotonic() - start
            parse_root = request.workspace_dir
            parsed_text, changed_files = self._parse_jsonl_output(result.stdout, parse_root)
            usage_summary = self._usage_summary(result.stdout)
            stderr = result.stderr
            if usage_summary:
                stderr = (
                    f"{stderr.rstrip()}\n\n{usage_summary}"
                    if stderr
                    else usage_summary
                )
            return RunResult(
                exit_code=result.returncode,
                stdout=parsed_text,
                stderr=stderr,
                duration_seconds=duration,
                changed_files=changed_files,
            )
        except subprocess.TimeoutExpired as error:
            duration = time.monotonic() - start
            partial_stdout = error.stdout or ""
            partial_stderr = error.stderr or ""
            if isinstance(partial_stdout, bytes):
                partial_stdout = partial_stdout.decode(errors="replace")
            if isinstance(partial_stderr, bytes):
                partial_stderr = partial_stderr.decode(errors="replace")
            parse_root = request.workspace_dir
            parsed_text, changed_files = self._parse_jsonl_output(partial_stdout, parse_root)
            timeout_message = f"Timed out after {request.timeout} seconds."
            stderr = (
                f"{partial_stderr.rstrip()}\n{timeout_message}"
                if partial_stderr
                else timeout_message
            )
            return RunResult(
                exit_code=124,
                stdout=parsed_text,
                stderr=stderr,
                duration_seconds=duration,
                changed_files=changed_files,
            )
        except FileNotFoundError:
            raise IntegrationError(f"GitHub Copilot CLI not found. Looked for: {cmd}")

    def prompt(self, text: str, timeout: int = 60) -> str:
        cmd = self._find_cmd()
        try:
            result = subprocess.run(
                [cmd, "-p", text, "--autopilot", "--experimental"],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                raise IntegrationError(f"copilot exited with code {result.returncode}: {result.stderr}")
            return result.stdout
        except FileNotFoundError:
            raise IntegrationError(f"GitHub Copilot CLI not found. Looked for: {cmd}")
        except subprocess.TimeoutExpired:
            raise IntegrationError(f"copilot timed out after {timeout}s")

    def _find_cmd(self) -> str:
        return self._resolve_cmd("copilot")

    @staticmethod
    def _resolve_real_cmd(cmd: str) -> str:
        """On Windows, resolve the real copilot.exe behind the wrapper.

        `shutil.which("copilot")` returns a .bat/.cmd/.ps1 bootstrapper that
        launches powershell + the real copilot.exe, re-allocating a console
        that makes the CLI think it is interactive. Invoking the .exe directly
        lets CREATE_NO_WINDOW keep the process console-free and headless.

        On non-Windows platforms (or when no wrapper is detected) the original
        command is returned unchanged.
        """
        if not sys.platform.startswith("win"):
            return cmd
        wrapper = Path(cmd)
        if wrapper.suffix.lower() not in (".bat", ".cmd", ".ps1"):
            return cmd
        # Search for the exact executable name so any number of package-manager
        # .bat/.cmd/.ps1 shims earlier on PATH are skipped without mutating the
        # process environment.
        real = shutil.which("copilot.exe", path=os.environ.get("PATH", ""))
        if real and Path(real).suffix.lower() == ".exe":
            return real
        return cmd


_register(CopilotIntegration())
