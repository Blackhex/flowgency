from __future__ import annotations
from pathlib import Path

import pytest

from flowgency.integrations import get_integration
from flowgency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedPermissionRule,
    RuntimeCapabilities,
    TicketToolLaunch,
)


@pytest.fixture
def ticket_request(tmp_path: Path) -> IntegrationRunRequest:
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
            mode="unrestricted",
            rules=(ResolvedPermissionRule(path=workspace, tools=("read", "search")),),
        ),
        ticket_tools=TicketToolLaunch(
            command="python",
            args=("-m", "flowgency.tickets.mcp_server"),
            env={
                "FLOWGENCY_TICKET_ENDPOINT": "http://127.0.0.1:9999",
                "FLOWGENCY_TICKET_TOKEN": "fixture-only-token",
            },
        ),
    )


@pytest.mark.parametrize("integration_name", ["aider", "gemini", "goose", "opencode", "pi"])
def test_unsupported_live_channel_is_not_an_outbox_fallback(
    integration_name: str,
    ticket_request: IntegrationRunRequest,
):
    integration = get_integration(integration_name)

    issues = integration.validate_run(ticket_request)

    assert any(issue.code == "unsupported-ticket-channel" for issue in issues)


def test_copilot_rejects_ticket_tools_when_runtime_capability_is_absent(
    monkeypatch,
    ticket_request: IntegrationRunRequest,
):
    integration = get_integration("copilot")
    monkeypatch.setattr(
        integration,
        "detect_runtime_capabilities",
        lambda: RuntimeCapabilities(
            permission_modes=frozenset({"restricted", "unrestricted"}),
            path_scopable_tools=frozenset({"write"}),
            live_ticket_transport=None,
        ),
    )
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "no-ticket-channel")
    integration.invalidate_capability_cache()

    issues = integration.validate_run(ticket_request)

    assert any(issue.code == "unsupported-ticket-channel" for issue in issues)


def test_copilot_reports_live_ticket_transport_only_when_probe_confirms_contract(monkeypatch):
    integration = get_integration("copilot")
    monkeypatch.setattr(type(integration), "_cli_version", lambda self: "1.0.78-2")
    monkeypatch.setattr(type(integration), "_ticket_tool_contract", lambda self, version: "mcp-stdio")
    integration.invalidate_capability_cache()

    try:
        assert integration.runtime_capabilities == RuntimeCapabilities(
            permission_modes=frozenset({"restricted", "unrestricted"}),
            path_scopable_tools=frozenset({"write"}),
            live_ticket_transport="mcp-stdio",
        )
    finally:
        integration.invalidate_capability_cache()


def test_copilot_does_not_guess_live_ticket_transport_for_unknown_versions(monkeypatch):
    integration = get_integration("copilot")
    monkeypatch.setattr(type(integration), "_cli_version", lambda self: None)
    monkeypatch.setattr(type(integration), "_ticket_tool_contract", lambda self, version: "mcp-stdio")
    integration.invalidate_capability_cache()

    try:
        assert integration.runtime_capabilities == RuntimeCapabilities(
            permission_modes=frozenset({"restricted", "unrestricted"}),
        )
    finally:
        integration.invalidate_capability_cache()


def test_copilot_capability_cache_key_includes_ticket_probe_result(monkeypatch):
    integration = get_integration("copilot")
    monkeypatch.setattr(type(integration), "_cli_version", lambda self: "1.0.84-1")
    monkeypatch.setattr(type(integration), "_ticket_tool_contract", lambda self, version: "mcp-stdio")

    assert integration._capability_cache_key() == "1.0.84-1|ticket:mcp-stdio"


def test_ticket_tool_launch_repr_redacts_secret_values(ticket_request: IntegrationRunRequest):
    text = repr(ticket_request.ticket_tools)

    assert "fixture-only-token" not in text
    assert "FLOWGENCY_TICKET_TOKEN" in text
