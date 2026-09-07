from __future__ import annotations

from copy import deepcopy

import yaml


def test_summary_sources_follow_same_path_union(raw_config, tmp_path, monkeypatch):
    from flowgency.configuration.models import parse_config
    from flowgency.integrations import get_integration
    from flowgency.integrations.models import RuntimeCapabilities
    from flowgency.permissions.presentation import present_permissions

    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["default_integration"] = "copilot"
    team["permissions"] = {
        "mode": "unrestricted",
        "rules": [{"path": ".", "tools": ["read"]}],
    }
    team["agents"] = [
        {
            "name": "advisor",
            "blueprint": "advisor",
            "integration": "copilot",
            "permissions": {"rules": [{"path": ".", "tools": ["write"]}]},
        }
    ]
    integration = get_integration("copilot")
    monkeypatch.setattr(
        type(integration),
        "runtime_capabilities",
        property(
            lambda self: RuntimeCapabilities(
                permission_modes=frozenset({"restricted", "unrestricted"}),
                path_scopable_tools=frozenset({"write"}),
            )
        ),
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    before = config_path.read_bytes()
    config = parse_config(raw, config_path).resolved

    summary = present_permissions(config, "newsletter", "advisor")

    assert summary.workspace_write is True
    assert summary.mode == "unrestricted"
    assert summary.mode_source == "team"
    assert summary.team_settings_href == "/admin/teams/newsletter/edit"
    assert summary.scopes[0].path == str(config.teams["newsletter"].workspace_path).replace("\\", "/")
    assert [(grant.name, grant.sources) for grant in summary.scopes[0].grants] == [
        ("read", ("team",)),
        ("write", ("agent",)),
    ]
    assert summary.scopes[0].all_tools_sources == ()
    assert config_path.read_bytes() == before


def test_summary_keeps_pathless_unbounded_sources_separate_from_child_paths(
    raw_config, tmp_path, monkeypatch
):
    from flowgency.configuration.models import parse_config
    from flowgency.integrations import get_integration
    from flowgency.integrations.models import RuntimeCapabilities
    from flowgency.permissions.presentation import present_permissions

    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    workspace = team["workspace_path"]
    team["default_integration"] = "copilot"
    team["permissions"] = {
        "mode": "restricted",
        "rules": [{"tools": None}, {"path": ".", "tools": ["read"]}],
    }
    team["agents"] = [
        {
            "name": "advisor",
            "blueprint": "advisor",
            "integration": "copilot",
            "permissions": {
                "rules": [
                    {"path": ".", "tools": ["write"]},
                    {"path": f"{workspace}/nested", "tools": ["read", "search"]},
                ]
            },
        }
    ]
    integration = get_integration("copilot")
    monkeypatch.setattr(
        type(integration),
        "runtime_capabilities",
        property(
            lambda self: RuntimeCapabilities(
                permission_modes=frozenset({"restricted", "unrestricted"}),
                path_scopable_tools=frozenset({"read", "write", "search"}),
            )
        ),
    )

    config = parse_config(raw, tmp_path / "config.yaml").resolved

    summary = present_permissions(config, "newsletter", "advisor")

    assert summary.mode == "restricted"
    assert summary.mode_source == "team"
    assert summary.scopes[0].path is None
    assert summary.scopes[0].grants == ()
    assert summary.scopes[0].all_tools_sources == ("team",)

    workspace_scope = next(
        scope
        for scope in summary.scopes
        if scope.path == str(config.teams["newsletter"].workspace_path).replace("\\", "/")
    )
    assert [(grant.name, grant.sources) for grant in workspace_scope.grants] == [
        ("read", ("team",)),
        ("write", ("agent",)),
    ]
    nested_scope = next(scope for scope in summary.scopes if scope.path and scope.path.endswith("/nested"))
    assert [(grant.name, grant.sources) for grant in nested_scope.grants] == [
        ("read", ("agent",)),
        ("search", ("agent",)),
    ]


def test_summary_prefers_agent_mode_override(raw_config, tmp_path, monkeypatch):
    from flowgency.configuration.models import parse_config
    from flowgency.integrations import get_integration
    from flowgency.integrations.models import RuntimeCapabilities
    from flowgency.permissions.presentation import present_permissions

    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["default_integration"] = "copilot"
    team["permissions"] = {"mode": "restricted", "rules": []}
    team["agents"] = [
        {
            "name": "advisor",
            "blueprint": "advisor",
            "integration": "copilot",
            "permissions": {"mode": "unrestricted", "rules": []},
        }
    ]
    integration = get_integration("copilot")
    monkeypatch.setattr(
        type(integration),
        "runtime_capabilities",
        property(
            lambda self: RuntimeCapabilities(
                permission_modes=frozenset({"restricted", "unrestricted"}),
                path_scopable_tools=frozenset(),
            )
        ),
    )

    config = parse_config(raw, tmp_path / "config.yaml").resolved

    summary = present_permissions(config, "newsletter", "advisor")

    assert summary.mode == "unrestricted"
    assert summary.mode_source == "agent"
    assert summary.scopes == ()
    assert summary.workspace_write is False