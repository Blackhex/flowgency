from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from flowgency.integrations.flowgency.copilot import CopilotIntegration
from flowgency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedPermissionRule,
    TicketToolLaunch,
)
from flowgency.integrations.ticket_tools import (
    build_ticket_tool_launch,
    write_copilot_ticket_config,
)
from flowgency.tickets.models import AgentTicketContext, LiveTicketEndpoint, TicketAccessGrant


class _FakeCompleted:
    returncode = 0
    stdout = "ok"
    stderr = ""


@dataclass(frozen=True)
class CapturedLaunch:
    argv: list[str]
    env: dict[str, str]
    tool_grants: list[str]
    config_path: Path | None
    config_payload: dict[str, object] | None
    prompt_text: str


@pytest.fixture
def copilot_request(tmp_path: Path) -> IntegrationRunRequest:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    launch_dir = tmp_path / "launch" / "runtime"
    launch_dir.mkdir(parents=True)
    task_file = tmp_path / "task.prompt"
    task_file.write_text("inspect tickets", encoding="utf-8")
    return IntegrationRunRequest(
        workspace_root=workspace,
        launch_dir=launch_dir,
        task_file=task_file,
        timeout=60,
        runtime_policy=EffectiveRuntimePolicy(
            timeout=60,
            mode="restricted",
            rules=(ResolvedPermissionRule(path=workspace, tools=("read", "search")),),
        ),
    )


def capture_copilot_launch(monkeypatch) -> CapturedLaunch:
    import flowgency.integrations.flowgency.copilot as copilot_mod

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        argv = list(args)
        captured["argv"] = argv
        captured["env"] = dict(kwargs["env"])
        prompt_index = argv.index("-p") + 1
        captured["prompt_text"] = argv[prompt_index]
        if "--additional-mcp-config" in argv:
            config_index = argv.index("--additional-mcp-config") + 1
            config_ref = argv[config_index]
            config_path = Path(config_ref[1:])
            captured["config_path"] = config_path
            captured["config_payload"] = json.loads(config_path.read_text(encoding="utf-8"))
        return _FakeCompleted()

    monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "copilot")
    monkeypatch.setattr(CopilotIntegration, "_cli_version", lambda self: "1.0.78-2")
    monkeypatch.setattr(CopilotIntegration, "_ticket_tool_contract", lambda self, version: "mcp-stdio")
    monkeypatch.setattr(CopilotIntegration, "_prepare_copilot_home", lambda self, request, settings: (None, "fixture-shared-home"))

    def finish() -> CapturedLaunch:
        argv = captured["argv"]
        assert isinstance(argv, list)
        env = captured["env"]
        assert isinstance(env, dict)
        prompt_text = captured["prompt_text"]
        assert isinstance(prompt_text, str)
        return CapturedLaunch(
            argv=argv,
            env=env,
            tool_grants=[argv[i + 1] for i, value in enumerate(argv) if value == "--allow-tool"],
            config_path=captured.get("config_path"),
            config_payload=captured.get("config_payload"),
            prompt_text=prompt_text,
        )

    return finish


def _endpoint() -> LiveTicketEndpoint:
    return LiveTicketEndpoint(
        url="http://127.0.0.1:9999",
        grant=TicketAccessGrant(
            session_id="session-1",
            token="fixture-only-token",
            context=AgentTicketContext(
                job_id="job-1",
                team_id="team-1",
                agent_name="advisor",
                session_id="session-1",
            ),
        ),
    )


def test_build_ticket_tool_launch_uses_bridge_contract():
    launch = build_ticket_tool_launch(_endpoint())

    assert launch.command == sys.executable
    assert launch.args == ("-m", "flowgency.tickets.mcp_server")
    assert launch.server_name == "flowgency-tickets"
    assert launch.env == {
        "FLOWGENCY_TICKET_ENDPOINT": "http://127.0.0.1:9999",
        "FLOWGENCY_TICKET_TOKEN": "fixture-only-token",
    }


def test_write_copilot_ticket_config_writes_expected_json(tmp_path: Path):
    config_path = tmp_path / "ticket-tools.json"
    write_copilot_ticket_config(
        TicketToolLaunch(
            command=sys.executable,
            args=("-m", "flowgency.tickets.mcp_server"),
            env={
                "FLOWGENCY_TICKET_ENDPOINT": "http://127.0.0.1:9999",
                "FLOWGENCY_TICKET_TOKEN": "fixture-only-token",
            },
        ),
        config_path,
    )

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "mcpServers": {
            "flowgency-tickets": {
                "command": sys.executable,
                "args": ["-m", "flowgency.tickets.mcp_server"],
                "env": {
                    "FLOWGENCY_TICKET_ENDPOINT": "http://127.0.0.1:9999",
                    "FLOWGENCY_TICKET_TOKEN": "fixture-only-token",
                },
                "tools": ["*"],
            }
        }
    }


def test_ticket_tools_do_not_grant_shell(copilot_request, monkeypatch, tmp_path):
    request = dataclasses.replace(
        copilot_request,
        ticket_tools=TicketToolLaunch(
            command=sys.executable,
            args=("-m", "flowgency.tickets.mcp_server"),
            env={
                "FLOWGENCY_TICKET_ENDPOINT": "http://127.0.0.1:9999",
                "FLOWGENCY_TICKET_TOKEN": "fixture-only-token",
            },
        ),
    )
    finish = capture_copilot_launch(monkeypatch)

    CopilotIntegration().run(request)
        
    captured = finish()
    args = captured.argv
    assert "--additional-mcp-config" in args
    assert "flowgency-tickets" in captured.tool_grants
    assert not any(value.startswith("shell") for value in captured.tool_grants)
    assert "fixture-only-token" not in " ".join(args)


def test_ticket_tools_use_private_ephemeral_config_and_delete_it_on_success(
    copilot_request,
    monkeypatch,
):
    request = dataclasses.replace(copilot_request, ticket_tools=build_ticket_tool_launch(_endpoint()))
    finish = capture_copilot_launch(monkeypatch)

    CopilotIntegration().run(request)

    captured = finish()
    assert captured.config_path is not None
    assert captured.config_path.parent == request.launch_dir
    assert captured.config_payload == {
        "mcpServers": {
            "flowgency-tickets": {
                "command": sys.executable,
                "args": ["-m", "flowgency.tickets.mcp_server"],
                "env": {
                    "FLOWGENCY_TICKET_ENDPOINT": "http://127.0.0.1:9999",
                    "FLOWGENCY_TICKET_TOKEN": "fixture-only-token",
                },
                "tools": ["*"],
            }
        }
    }
    assert not captured.config_path.exists()


def test_ticket_tools_do_not_enter_prompt_or_copilot_environment(copilot_request, monkeypatch):
    request = dataclasses.replace(copilot_request, ticket_tools=build_ticket_tool_launch(_endpoint()))
    finish = capture_copilot_launch(monkeypatch)

    CopilotIntegration().run(request)

    captured = finish()
    assert "fixture-only-token" not in captured.prompt_text
    assert "FLOWGENCY_TICKET_TOKEN" not in captured.env
    assert "FLOWGENCY_TICKET_ENDPOINT" not in captured.env


def test_ticket_tools_delete_private_config_on_timeout(copilot_request, monkeypatch):
    request = dataclasses.replace(copilot_request, ticket_tools=build_ticket_tool_launch(_endpoint()))
    import flowgency.integrations.flowgency.copilot as copilot_mod

    captured: dict[str, Path] = {}

    def fake_run(args, **kwargs):
        argv = list(args)
        config_index = argv.index("--additional-mcp-config") + 1
        captured["config_path"] = Path(argv[config_index][1:])
        raise subprocess.TimeoutExpired(argv, timeout=60, output="", stderr="")

    monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "copilot")
    monkeypatch.setattr(CopilotIntegration, "_cli_version", lambda self: "1.0.78-2")
    monkeypatch.setattr(CopilotIntegration, "_ticket_tool_contract", lambda self, version: "mcp-stdio")
    monkeypatch.setattr(CopilotIntegration, "_prepare_copilot_home", lambda self, request, settings: (None, "fixture-shared-home"))

    result = CopilotIntegration().run(request)

    assert result.exit_code == 124
    assert not captured["config_path"].exists()
