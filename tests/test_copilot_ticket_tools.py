from __future__ import annotations

import dataclasses
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from flowgency.configuration import ValidationFailed
from flowgency.integrations.flowgency.copilot import CopilotIntegration
from flowgency.integrations.errors import IntegrationError
from flowgency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedPermissionRule,
    TicketToolLaunch,
)
from flowgency.jobs.processes import (
    CompletedRuntimeProcess,
    ProcessStopEvidence,
    RuntimeProcessLifecycle,
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


def _patch_common(monkeypatch) -> None:
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "copilot")
    monkeypatch.setattr(CopilotIntegration, "_cli_version", lambda self: "1.0.78-2")
    monkeypatch.setattr(CopilotIntegration, "_ticket_tool_contract", lambda self, version: "mcp-http")
    monkeypatch.setattr(CopilotIntegration, "_supports_required_isolation", lambda self: True)
    monkeypatch.setattr(
        CopilotIntegration,
        "_prepare_copilot_home",
        lambda self, request, settings: (None, "fixture-shared-home"),
    )


def _ok_completion() -> CompletedRuntimeProcess:
    return CompletedRuntimeProcess(
        exit_code=0,
        stdout='{"type":"message","content":"ok"}\n',
        stderr="",
        duration_seconds=0.01,
        process_stop_evidence=ProcessStopEvidence(
            job_id="job-1",
            generation="gen-1",
            confirmed=True,
            reason="exited",
        ),
    )


def _capture_supervised(monkeypatch) -> dict:
    """Fake ``run_supervised`` capturing argv/env/config for ticket launches."""
    import flowgency.integrations.flowgency.copilot as copilot_mod

    captured: dict[str, object] = {}

    def fake_supervised(argv, *, cwd, env, timeout, lifecycle):
        argv_list = list(argv)
        captured["argv"] = argv_list
        captured["cwd"] = cwd
        captured["env"] = dict(env)
        captured["timeout"] = timeout
        captured["lifecycle"] = lifecycle
        prompt_index = argv_list.index("-p") + 1
        captured["prompt_text"] = argv_list[prompt_index]
        if "--additional-mcp-config" in argv_list:
            config_index = argv_list.index("--additional-mcp-config") + 1
            config_path = Path(argv_list[config_index][1:])
            captured["config_path"] = config_path
            captured["config_payload"] = json.loads(config_path.read_text(encoding="utf-8"))
        return _ok_completion()

    monkeypatch.setattr(copilot_mod, "run_supervised", fake_supervised)
    monkeypatch.setattr(
        copilot_mod.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ticket launch with lifecycle must not use subprocess.run")
        ),
    )
    _patch_common(monkeypatch)
    return captured


def _ticket_integration() -> CopilotIntegration:
    return CopilotIntegration({"allow_local_network": True})


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
    _patch_common(monkeypatch)

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


def _launch_with_lifecycle() -> TicketToolLaunch:
    launch = build_ticket_tool_launch(_endpoint())
    return dataclasses.replace(
        launch,
        lifecycle=RuntimeProcessLifecycle(job_id="job-1", generation="gen-1"),
    )


def _expected_http_launch() -> TicketToolLaunch:
    return build_ticket_tool_launch(_endpoint())


def test_build_ticket_tool_launch_uses_http_contract():
    launch = build_ticket_tool_launch(_endpoint())

    assert launch.url == "http://127.0.0.1:9999/mcp"
    assert launch.headers == {"Authorization": "Bearer fixture-only-token"}
    assert launch.server_name == "flowgency-tickets"
    assert launch.lifecycle is None


def test_write_copilot_ticket_config_writes_expected_json(tmp_path: Path):
    config_path = tmp_path / "ticket-tools.json"
    launch = _expected_http_launch()
    write_copilot_ticket_config(launch, config_path)

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "mcpServers": {
            "flowgency-tickets": {
                "type": "http",
                "url": launch.url,
                "headers": dict(launch.headers),
                "tools": ["*"],
            }
        }
    }


def test_copilot_ticket_tools_use_http_config(copilot_request, monkeypatch):
    request = dataclasses.replace(copilot_request, ticket_tools=_launch_with_lifecycle())
    captured = _capture_supervised(monkeypatch)

    _ticket_integration().run(request)

    argv = captured["argv"]
    assert "--additional-mcp-config" in argv
    tool_grants = [argv[i + 1] for i, value in enumerate(argv) if value == "--allow-tool"]
    assert "flowgency-tickets" in tool_grants
    assert "fixture-only-token" not in " ".join(argv)
    assert captured["config_payload"]["mcpServers"]["flowgency-tickets"]["type"] == "http"


def test_copilot_rejects_missing_local_network_consent_before_prompt_read_or_launch(
    copilot_request,
    monkeypatch,
):
    request = dataclasses.replace(copilot_request, ticket_tools=_launch_with_lifecycle())
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path == request.task_file:
            raise AssertionError("run() must validate before reading the task prompt")
        return original_read_text(path, *args, **kwargs)

    import flowgency.integrations.flowgency.copilot as copilot_mod

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(
        copilot_mod,
        "run_supervised",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("run() must validate before launching the supervised process")
        ),
    )
    monkeypatch.setattr(
        copilot_mod.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("run() must validate before launching the subprocess")
        ),
    )
    _patch_common(monkeypatch)

    with pytest.raises(ValidationFailed) as excinfo:
        CopilotIntegration().run(request)

    assert any(issue.code == "ticket-local-network-required" for issue in excinfo.value.issues)


def test_ticket_tools_do_not_grant_shell(copilot_request, monkeypatch):
    request = dataclasses.replace(
        copilot_request,
        ticket_tools=TicketToolLaunch(
            url="http://127.0.0.1:9999/mcp",
            headers={"Authorization": "Bearer fixture-only-token"},
            lifecycle=RuntimeProcessLifecycle(job_id="job-1", generation="gen-1"),
        ),
    )
    captured = _capture_supervised(monkeypatch)

    _ticket_integration().run(request)

    args = captured["argv"]
    assert isinstance(args, list)
    assert "--additional-mcp-config" in args
    tool_grants = [args[i + 1] for i, value in enumerate(args) if value == "--allow-tool"]
    assert "flowgency-tickets" in tool_grants
    assert not any(value.startswith("shell") for value in tool_grants)
    assert "fixture-only-token" not in " ".join(args)


def test_ticket_tools_use_private_ephemeral_config_and_delete_it_on_success(
    copilot_request,
    monkeypatch,
):
    request = dataclasses.replace(copilot_request, ticket_tools=_launch_with_lifecycle())
    captured = _capture_supervised(monkeypatch)

    _ticket_integration().run(request)

    config_path = captured["config_path"]
    assert isinstance(config_path, Path)
    assert config_path.parent == request.launch_dir
    assert captured["config_payload"] == {
        "mcpServers": {
            "flowgency-tickets": {
                "type": "http",
                "url": request.ticket_tools.url,
                "headers": dict(request.ticket_tools.headers),
                "tools": ["*"],
            }
        }
    }
    assert not config_path.exists()


def test_ticket_tools_do_not_enter_prompt_or_copilot_environment(copilot_request, monkeypatch):
    request = dataclasses.replace(copilot_request, ticket_tools=_launch_with_lifecycle())
    captured = _capture_supervised(monkeypatch)

    _ticket_integration().run(request)

    assert "fixture-only-token" not in str(captured["prompt_text"])
    env = captured["env"]
    assert isinstance(env, dict)
    assert "FLOWGENCY_TICKET_TOKEN" not in env
    assert "FLOWGENCY_TICKET_ENDPOINT" not in env
    assert not any("fixture-only-token" in value for value in env.values())


def test_ticket_tools_can_use_launch_instructions_config_without_other_launch_deltas(
    copilot_request,
    monkeypatch,
):
    request = dataclasses.replace(copilot_request, ticket_tools=_launch_with_lifecycle())

    baseline = _capture_supervised(monkeypatch)
    _ticket_integration().run(request)

    baseline_path = baseline["config_path"]
    assert isinstance(baseline_path, Path)
    assert baseline_path == request.launch_dir / "ticket-tools.mcp.json"

    import flowgency.integrations.flowgency.copilot as copilot_mod

    def write_under_instructions(launch, path):
        return write_copilot_ticket_config(
            launch,
            path.parent / "instructions" / path.name,
        )

    monkeypatch.setattr(copilot_mod, "write_copilot_ticket_config", write_under_instructions)
    patched = _capture_supervised(monkeypatch)

    _ticket_integration().run(request)

    patched_path = patched["config_path"]
    assert isinstance(patched_path, Path)
    assert patched_path == request.launch_dir / "instructions" / "ticket-tools.mcp.json"

    def normalized(argv):
        items = list(argv)
        items[items.index("-p") + 1] = "<prompt>"
        items[items.index("--additional-mcp-config") + 1] = "@<config>"
        return items

    assert normalized(baseline["argv"]) == normalized(patched["argv"])
    assert baseline["env"] == patched["env"]
    assert baseline["config_payload"] == patched["config_payload"]


def test_ticket_tools_delete_private_config_on_timeout(copilot_request, monkeypatch):
    request = dataclasses.replace(copilot_request, ticket_tools=_launch_with_lifecycle())
    import flowgency.integrations.flowgency.copilot as copilot_mod

    captured: dict[str, Path] = {}

    def fake_supervised(argv, *, cwd, env, timeout, lifecycle):
        argv = list(argv)
        config_index = argv.index("--additional-mcp-config") + 1
        captured["config_path"] = Path(argv[config_index][1:])
        return CompletedRuntimeProcess(
            exit_code=124,
            stdout="",
            stderr="",
            duration_seconds=0.01,
            process_stop_evidence=ProcessStopEvidence(
                job_id="job-1",
                generation="gen-1",
                confirmed=False,
                reason="io-drain-incomplete",
            ),
            outcome="timeout",
        )

    monkeypatch.setattr(copilot_mod, "run_supervised", fake_supervised)
    monkeypatch.setattr(
        copilot_mod.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ticket launch with lifecycle must not use subprocess.run")
        ),
    )
    _patch_common(monkeypatch)

    result = _ticket_integration().run(request)

    assert result.exit_code == 124
    assert not captured["config_path"].exists()


def test_ticket_launch_uses_supervised_runtime_with_lifecycle(copilot_request, monkeypatch):
    request = dataclasses.replace(
        copilot_request,
        ticket_tools=TicketToolLaunch(
            url="http://127.0.0.1:9999/mcp",
            headers={"Authorization": "Bearer fixture-only-token"},
            lifecycle=RuntimeProcessLifecycle(job_id="job-1", generation="gen-1"),
        ),
    )
    captured = _capture_supervised(monkeypatch)

    result = _ticket_integration().run(request)

    assert result.exit_code == 0
    assert result.process_stop_evidence == ProcessStopEvidence(
        job_id="job-1",
        generation="gen-1",
        confirmed=True,
        reason="exited",
    )
    assert captured["lifecycle"] == RuntimeProcessLifecycle(job_id="job-1", generation="gen-1")
    assert request.ticket_tools is not None
    assert "fixture-only-token" not in " ".join(captured["argv"])


def test_ticket_launch_without_lifecycle_rejects_before_config_or_runner(
    copilot_request,
    monkeypatch,
):
    request = dataclasses.replace(
        copilot_request,
        ticket_tools=TicketToolLaunch(
            url="http://127.0.0.1:9999/mcp",
            headers={"Authorization": "Bearer fixture-only-token"},
        ),
    )
    import flowgency.integrations.flowgency.copilot as copilot_mod

    monkeypatch.setattr(
        copilot_mod,
        "write_copilot_ticket_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ticket config must not be written without a lifecycle")
        ),
    )
    monkeypatch.setattr(
        copilot_mod,
        "run_supervised",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ticket launch without lifecycle must not start supervised runtime")
        ),
    )
    monkeypatch.setattr(
        copilot_mod.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ticket launch without lifecycle must not fall back to subprocess.run")
        ),
    )
    _patch_common(monkeypatch)

    with pytest.raises(IntegrationError, match="trusted lifecycle"):
        _ticket_integration().run(request)


def test_non_ticket_launch_keeps_plain_subprocess_path(copilot_request, monkeypatch):
    import flowgency.integrations.flowgency.copilot as copilot_mod

    finish = capture_copilot_launch(monkeypatch)
    monkeypatch.setattr(
        copilot_mod,
        "run_supervised",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("non-ticket launch must not use supervision")
        ),
    )

    CopilotIntegration().run(copilot_request)

    captured = finish()
    assert captured.argv[0] == "copilot"
