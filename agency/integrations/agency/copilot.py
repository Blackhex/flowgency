"""GitHub Copilot CLI integration."""

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Sequence

from agency.blueprints.projectors import get_projector
from agency.fs.atomic import atomic_write_text
from agency.integrations import (
    AgentIdentity,
    BaseIntegration,
    FileChange,
    IntegrationError,
    RunResult,
    _register,
    format_command_with_environment,
    format_interactive_command,
    spawn_interactive_terminal,
    terminal_available,
)
from agency.integrations.agency.copilot_sandbox import build_sandbox_settings
from agency.integrations.models import (
    IntegrationRunRequest,
    InteractiveSetupRequest,
    InteractiveSetupResult,
    RuntimeCapabilities,
)


logger = logging.getLogger(__name__)


def _intersect_grants(
    grants: Iterable[tuple[str, ...] | None],
) -> tuple[str, ...] | None:
    """What every grant permits, in first-seen order.

    None means every tool, so it is the identity: a rule that omits `tools`
    narrows nothing. An empty tuple means no tool and absorbs everything.
    """
    result: tuple[str, ...] | None = None
    for grant in grants:
        if grant is None:
            continue
        if result is None:
            result = tuple(dict.fromkeys(grant))
        else:
            result = tuple(name for name in result if name in grant)
    return result


class CopilotIntegration(BaseIntegration):
    name = "copilot"
    display_name = "GitHub Copilot"
    cli_command = "copilot"
    supports_execution = True
    supports_ai_backend = True
    supports_sandbox = True
    detect_priority = 7
    projector = get_projector("copilot")
    declared_runtime_capabilities = RuntimeCapabilities(
        permission_modes=frozenset({"restricted", "unrestricted"}),
        path_scopable_tools=frozenset({"write"}),
    )
    _WINDOWS_SHELL_HOSTS = ("powershell.exe", "pwsh.exe")
    _WINDOWS_SCRIPT_EXTENSIONS = (".ps1",)
    _WINDOWS_WRAPPER_EXTENSIONS = (".bat", ".cmd")
    # Measured on 1.0.78-2: a write into a `readonlyPaths` entry was refused
    # with "Edit (sandbox policy)" and left no file on disk, while a write into
    # a `readwritePaths` entry succeeded.
    _SANDBOX_MEASURED_VERSION = (1, 0, 78, 2)
    # A major bump is the only signal the publisher gives that behaviour may
    # change, so the claim stops there until it is measured again.
    _SANDBOX_UNMEASURED_VERSION = (2, 0, 0, 0)
    # A build suffix marks a prerelease of its patch, so the plain release is
    # NEWER than any of them. Sorting a missing suffix last says that.
    _RELEASE_BUILD = float("inf")
    # Anchored: an unanchored search would take the first dotted triple in any
    # banner text, and a date-like prefix could land inside the trusted window.
    _VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-(\d+))?")
    # The probe runs on request paths, so it must not be able to stall one for
    # long. Reporting a version is a local read; seconds is already generous.
    _VERSION_PROBE_TIMEOUT = 5

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def _probe_cli_version(self, command: str) -> str | None:
        """The version the installed CLI reports, or None when it will not say."""
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=self._VERSION_PROBE_TIMEOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return None
        return (result.stdout or result.stderr or "").strip() or None

    @staticmethod
    def _executable_stamp(command: str) -> object:
        """Identity of the installed binary, so an in-place upgrade re-probes."""
        try:
            info = os.stat(command)
        except OSError:
            return command
        return (command, info.st_mtime_ns, info.st_size)

    def _cli_version(self) -> str | None:
        """The installed CLI version, probed once per installed binary.

        Capabilities are read on request paths, so the subprocess runs only
        when the binary behind `copilot` has changed.
        """
        command = self.resolve_executable()
        stamp = self._executable_stamp(command) if command else None
        cached = getattr(self, "_version_cache", None)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        try:
            version = self._probe_cli_version(command) if command else None
        except Exception:
            # Losing the claim is the safe outcome, but losing it silently is
            # undiagnosable: enforcement would just quietly stop.
            logger.warning(
                "copilot: could not read the CLI version from %s; "
                "path-scoped enforcement is not claimed",
                command,
                exc_info=True,
            )
            version = None
        self._version_cache = (stamp, version)
        return version

    @classmethod
    def _sandbox_enforces_writes(cls, reported: str | None) -> bool:
        """Is this a CLI whose sandbox was measured to refuse a denied write?"""
        if reported is None:
            return False
        # The CLI answers with a banner ("GitHub Copilot CLI 1.0.78-2.") and
        # may add an upgrade notice below it. Only the first line states the
        # installed version, and a line offering a newer one must never be
        # read as the version in hand -- so a first line carrying more than
        # one version is ambiguous and is refused rather than guessed at.
        first_line = reported.strip().splitlines()[0] if reported.strip() else ""
        found = cls._VERSION_PATTERN.findall(first_line)
        if len(found) != 1:
            return False
        major, minor, patch, build = found[0]
        version = (
            int(major),
            int(minor),
            int(patch),
            # The suffix counts pre-release builds, so a bare 1.0.78 ships
            # after 1.0.78-2 rather than before it.
            int(build) if build else cls._RELEASE_BUILD,
        )
        return cls._SANDBOX_MEASURED_VERSION <= version < cls._SANDBOX_UNMEASURED_VERSION

    def _capability_cache_key(self) -> str | None:
        return self._cli_version()

    def detect_runtime_capabilities(self) -> RuntimeCapabilities:
        declared = self.declared_runtime_capabilities
        if self._sandbox_enforces_writes(self._cli_version()):
            return declared
        return RuntimeCapabilities(permission_modes=declared.permission_modes)

    def invalidate_capability_cache(self) -> None:
        super().invalidate_capability_cache()
        self._version_cache = None

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
        if not terminal_available():
            return False
        try:
            self._interactive_setup_command_prefix()
        except IntegrationError:
            return False
        return True

    @staticmethod
    def _command_exists(cmd: str) -> bool:
        path = Path(cmd)
        return path.exists() or shutil.which(cmd) is not None

    @classmethod
    def _resolve_windows_shell_host(cls) -> str | None:
        for candidate in cls._WINDOWS_SHELL_HOSTS:
            resolved = shutil.which(candidate, path=os.environ.get("PATH", ""))
            if resolved and Path(resolved).suffix.lower() == ".exe":
                return resolved
        return None

    @classmethod
    def _resolve_windows_wrapper_script(cls, cmd: str) -> str | None:
        wrapper = Path(cmd)
        suffix = wrapper.suffix.lower()
        if suffix in cls._WINDOWS_SCRIPT_EXTENSIONS and wrapper.is_file():
            return str(wrapper)
        if suffix not in cls._WINDOWS_WRAPPER_EXTENSIONS:
            return None
        script = wrapper.with_suffix(".ps1")
        if script.is_file():
            return str(script)
        return None

    def _interactive_setup_command_prefix(self) -> tuple[str, ...]:
        cmd = self._resolve_real_cmd(self._find_cmd())
        if platform.system() != "Windows":
            if not self._command_exists(cmd):
                raise IntegrationError(
                    f"GitHub Copilot CLI not found. Looked for: {cmd}"
                )
            return (cmd,)
        if Path(cmd).suffix.lower() == ".exe":
            if not self._command_exists(cmd):
                raise IntegrationError(
                    f"GitHub Copilot CLI not found. Looked for: {cmd}"
                )
            return (cmd,)
        script = self._resolve_windows_wrapper_script(cmd)
        if script is None:
            raise IntegrationError(
                "GitHub Copilot interactive setup requires copilot.exe "
                "or a copilot.ps1 wrapper with PowerShell."
            )
        shell_host = self._resolve_windows_shell_host()
        if shell_host is None:
            raise IntegrationError(
                "GitHub Copilot interactive setup requires powershell.exe "
                "or pwsh.exe to run copilot.ps1."
            )
        return (
            shell_host,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script,
        )

    def _interactive_setup_command(
        self,
        request: InteractiveSetupRequest,
    ) -> Sequence[str]:
        project_dir = request.project_dir.resolve(strict=True)
        return (
            *self._interactive_setup_command_prefix(),
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
    def _parse_jsonl_output_details(
        raw: str,
        root: "Path | None",
    ) -> "tuple[str, list[FileChange], list[str]]":
        """Parse Copilot --output-format json (JSONL) into text, changes, attempts.

        Reconstructs human-readable text from assistant messages and extracts
        per-file changes from native file-edit tool calls. Records normalized
        write-attempt paths for native write tools that reached
        tool.execution_start, even when completion later fails. Any structural
        problem falls back to (raw, [], []); a run must never break on parsing.
        """
        try:
            tool_names: dict[str, str] = {}
            tool_paths: dict[str, str] = {}
            # path -> {"status": str, "added": int, "removed": int}
            files: dict[str, dict] = {}
            texts: list[str] = []
            write_attempts: list[str] = []
            seen_attempts: set[str] = set()
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
                    tool_name = data.get("toolName", "")
                    if tcid:
                        tool_names[tcid] = tool_name
                        path = (data.get("arguments") or {}).get("path")
                        if path:
                            tool_paths[tcid] = path
                            if tool_name in CopilotIntegration._WRITE_TOOLS:
                                rel = CopilotIntegration._relativize(path, root)
                                if rel not in seen_attempts:
                                    seen_attempts.add(rel)
                                    write_attempts.append(rel)
                elif etype == "tool.execution_complete":
                    tcid = data.get("toolCallId")
                    telemetry = data.get("toolTelemetry") or {}
                    props = telemetry.get("properties") or {}
                    metrics = telemetry.get("metrics") or {}
                    command = props.get("command") or tool_names.get(tcid, "")
                    if command not in CopilotIntegration._WRITE_TOOLS:
                        continue
                    if data.get("success") is False:
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
                return raw, [], []

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
            return text, changes, write_attempts
        except Exception:
            return raw, [], []

    @staticmethod
    def _parse_jsonl_output(raw: str, root: "Path | None") -> "tuple[str, list[FileChange]]":
        """Parse Copilot --output-format json (JSONL) into (text, changes)."""
        text, changes, _write_attempts = CopilotIntegration._parse_jsonl_output_details(
            raw,
            root,
        )
        return text, changes

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
    def _parse_session_id(raw: str) -> str | None:
        """Return the sessionId of the last result event, or None."""
        session_id: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict) or event.get("type") != "result":
                continue
            candidate = event.get("sessionId")
            if isinstance(candidate, str) and candidate:
                session_id = candidate
        return session_id

    def resume_command(self, session_id: str) -> tuple[str, ...] | None:
        return (self.cli_command, "--resume", session_id)

    def _prepare_copilot_home(
        self,
        request: IntegrationRunRequest,
        settings: dict,
    ) -> tuple[Path | None, str | None]:
        """Create a per-job COPILOT_HOME holding sandbox settings and auth.

        Returns ``(home, None)`` on success or ``(None, reason)`` when the
        caller should fall back to the shared home.
        """
        real_home = Path(
            os.environ.get("COPILOT_HOME", Path.home() / ".copilot")
        )
        source_config = real_home / "config.json"
        if not source_config.is_file():
            return None, "copilot credentials not found; using shared home"

        job_home = request.launch_dir.parent / ".copilot"
        job_home.mkdir(parents=True, exist_ok=True)

        atomic_write_text(
            job_home / "config.json",
            source_config.read_text(encoding="utf-8"),
        )

        atomic_write_text(
            job_home / "settings.json",
            json.dumps(settings, indent=2) + "\n",
        )

        return job_home, None

    @staticmethod
    def _usage_summary(raw: str, *, copilot_home: Path | None = None, cmd: str | None = None) -> str:
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

        home = copilot_home or Path(
            os.environ.get("COPILOT_HOME", Path.home() / ".copilot")
        )
        events_path = (
            home / "session-state" / result["sessionId"] / "events.jsonl"
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

        executable = cmd or "copilot"
        resume_cmd = format_command_with_environment(
            [executable, f"--resume={result['sessionId']}"],
            {"COPILOT_HOME": str(copilot_home)} if copilot_home is not None else {},
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
            f"Resume     {resume_cmd}"
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

    @staticmethod
    def _build_runtime_prompt(
        task_text: str,
        skill: str | None,
        skill_arguments: Sequence[str],
    ) -> str:
        if not skill:
            return task_text

        parts = [
            f"Explicitly invoke and use the '{skill}' skill before completing this task.",
            "Do not complete the task until that selected skill has been applied.",
        ]
        if skill_arguments:
            parts.append(f"Skill arguments: {', '.join(skill_arguments)}")
        return "\n".join(parts) + "\n\n" + task_text

    def run(self, request: IntegrationRunRequest) -> RunResult:
        self.require_valid_run(request)
        task_text = request.task_file.read_text()
        prompt_text = self._build_runtime_prompt(
            task_text,
            request.skill,
            request.skill_arguments,
        )
        cmd = self.require_executable()

        policy = request.runtime_policy
        # Only what the operator authored is rendered. Agency's generated
        # launch-zone rules grant write on the outbox and memory, but Copilot's
        # allowlist is global: honouring them would hand the agent write on
        # every reachable path. They stay advisory until Copilot can scope a
        # tool to a path.
        authored = tuple(rule for rule in policy.rules if not rule.generated)
        roots = [rule.path for rule in authored if rule.path is not None]
        # The sandbox has to be decided before the tool allowlist is: a tool may
        # only be granted globally on the strength of the sandbox confining it,
        # so we must know the sandbox is actually in force first.
        settings, _unenforceable = build_sandbox_settings(policy)
        job_home, degraded = self._prepare_copilot_home(request, settings)
        sandbox_confines = job_home is not None and settings["sandbox"]["enabled"]
        # A single global allowlist can only grant what every reachable rule
        # permits, so the grants intersect rather than union. None is the
        # identity: a rule that omits `tools` narrows nothing.
        granted: tuple[str, ...] | None
        if roots:
            granted = _intersect_grants(rule.tools for rule in authored)
            if granted is not None and sandbox_confines:
                # Copilot's two gates intersect: the tool gate decides whether a
                # tool may be used at all, the sandbox decides where its effects
                # may land. A tool the sandbox scopes per path can therefore be
                # granted globally -- the sandbox is what holds it to the rules.
                # Without this the tool gate would veto the sandbox's own zone
                # grants, and a read-only agent could not write its outbox.
                granted = granted + tuple(
                    tool
                    for tool in sorted(self.runtime_capabilities.path_scopable_tools)
                    if tool not in granted
                    and any(
                        rule.tools is None or tool in rule.tools
                        for rule in policy.rules
                    )
                )
        elif policy.mode == "unrestricted":
            granted = None
        else:
            # Restricted with no rule reaches nothing and grants nothing.
            granted = ()

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
        elif policy.mode == "unrestricted":
            cmd_args += ["--allow-all-paths"]

        if granted is None:
            cmd_args += ["--allow-all-tools", "--autopilot"]
        else:
            for t in granted:
                cmd_args += ["--allow-tool", t]

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
        run_env = (
            {**os.environ, "COPILOT_HOME": str(job_home)}
            if job_home is not None
            else None
        )
        try:
            result = subprocess.run(
                cmd_args,
                capture_output=True, text=True, timeout=request.timeout,
                cwd=str(request.launch_dir),
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                env=run_env,
            )
            duration = time.monotonic() - start
            parse_root = request.workspace_root
            parsed_text, changed_files, write_attempts = self._parse_jsonl_output_details(
                result.stdout,
                parse_root,
            )
            usage_summary = self._usage_summary(
                result.stdout, copilot_home=job_home, cmd=cmd,
            )
            stderr = result.stderr
            if degraded:
                warning = f"sandbox: {degraded}\n"
                stderr = f"{warning}{stderr}" if stderr else warning
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
                write_attempts=write_attempts,
                session_id=self._parse_session_id(result.stdout),
                copilot_home=str(job_home) if job_home is not None else None,
            )
        except subprocess.TimeoutExpired as error:
            duration = time.monotonic() - start
            partial_stdout = error.stdout or ""
            partial_stderr = error.stderr or ""
            if isinstance(partial_stdout, bytes):
                partial_stdout = partial_stdout.decode(errors="replace")
            if isinstance(partial_stderr, bytes):
                partial_stderr = partial_stderr.decode(errors="replace")
            parse_root = request.workspace_root
            parsed_text, changed_files, write_attempts = self._parse_jsonl_output_details(
                partial_stdout,
                parse_root,
            )
            timeout_message = f"Timed out after {request.timeout} seconds."
            stderr = (
                f"{partial_stderr.rstrip()}\n{timeout_message}"
                if partial_stderr
                else timeout_message
            )
            if degraded:
                stderr = f"sandbox: {degraded}\n{stderr}"
            return RunResult(
                exit_code=124,
                stdout=parsed_text,
                stderr=stderr,
                duration_seconds=duration,
                changed_files=changed_files,
                write_attempts=write_attempts,
                session_id=self._parse_session_id(partial_stdout),
                copilot_home=str(job_home) if job_home is not None else None,
            )
        except FileNotFoundError:
            raise IntegrationError(f"GitHub Copilot CLI not found. Looked for: {cmd}")

    def prompt(self, text: str, timeout: int = 60) -> str:
        cmd = self.require_executable()
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

    def _resolve_launch_command(self, command: str) -> str:
        return self._resolve_real_cmd(command)

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
