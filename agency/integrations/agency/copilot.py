"""GitHub Copilot CLI integration."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agency.config import SandboxSpec
from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
)


class CopilotIntegration(BaseIntegration):
    name = "copilot"
    display_name = "GitHub Copilot"
    supports_execution = True
    supports_ai_backend = True
    supports_sandbox = True
    detect_priority = 7

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def _identity_file(self, agent_dir: Path) -> Path:
        return agent_dir / "AGENTS.md"

    def detect(self, agent_dir: Path) -> bool:
        return (agent_dir / ".copilot").is_dir() or (agent_dir / ".github").is_dir()

    def parse_identity(self, agent_dir: Path) -> AgentIdentity | None:
        return self._parse_sidecar_identity(agent_dir, self._identity_file(agent_dir))

    def write_identity(self, agent_dir: Path, identity: AgentIdentity) -> None:
        self._write_sidecar_identity(agent_dir, self._identity_file(agent_dir), identity)

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int,
            *, sandbox_root: "SandboxSpec | None" = None) -> RunResult:
        prompt_text = prompt_file.read_text()
        cmd = self._resolve_real_cmd(self._find_cmd())

        # Least-privilege builder. `roots` empty => --allow-all-paths; `tools`
        # empty => blanket --allow-all-tools. --autopilot is emitted ONLY with
        # blanket tools: it is incompatible with explicit --allow-tool grants,
        # which under autopilot perform a permission round-trip that fails
        # closed mid-session (github/copilot-cli#2971). Explicit grants were
        # validated denial-free by a real-session probe on 2026-07-09.
        spec = sandbox_root or SandboxSpec()
        roots, tools = spec.roots, spec.allowed_tools

        cmd_args = [
            cmd, "-p", prompt_text,
            "--no-custom-instructions",
            "--no-ask-user",
            "--no-color",
            "--experimental",
        ]

        if roots:
            for p in roots:
                cmd_args += ["--add-dir", str(p)]
            work_dir = str(roots[0])
        else:
            cmd_args += ["--allow-all-paths"]
            work_dir = str(agent_dir)

        if tools:
            for t in tools:
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
                capture_output=True, text=True, timeout=timeout,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            duration = time.monotonic() - start
            return RunResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return RunResult(exit_code=124, stdout="", stderr="Timed out", duration_seconds=duration)
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
        # Re-search PATH with the wrapper's own directory removed, mirroring
        # the wrapper's Find-RealCopilot logic, to locate the real binary. Pass
        # the pruned path explicitly (rather than mutating os.environ) so the
        # lookup is thread-safe under concurrent dispatch.
        search = os.pathsep.join(
            d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and Path(d) != wrapper.parent
        )
        real = shutil.which("copilot", path=search)
        if real and Path(real).suffix.lower() == ".exe":
            return real
        return cmd


_register(CopilotIntegration())
