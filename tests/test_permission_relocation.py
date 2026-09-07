from copy import deepcopy

import pytest

from flowgency.configuration.effective import resolve_effective_policy
from flowgency.configuration.issues import ValidationFailed
from flowgency.configuration.models import parse_config


def test_sibling_policy_keeps_union_and_timeout(raw_config, tmp_path):
    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["runtime"] = {"timeout": 1800}
    team["permissions"] = {
        "mode": "unrestricted",
        "rules": [{"path": ".", "tools": ["read", "search"]}],
    }
    team["agents"] = [{
        "name": "advisor", "blueprint": "advisor", "integration": "copilot",
        "runtime": {"timeout": 2400},
        "permissions": {"rules": [{"path": ".", "tools": ["write"]}]},
    }]
    config = parse_config(raw, tmp_path / "config.yaml").resolved
    policy = resolve_effective_policy(config, "newsletter", "advisor")
    assert policy.timeout == 2400
    assert policy.mode == "unrestricted"
    assert set(policy.rules[0].tools) == {"read", "search", "write"}
    assert policy.rules[0].path == config.teams["newsletter"].workspace_path
    assert not hasattr(config.teams["newsletter"].runtime, "permissions")


@pytest.mark.parametrize("level", ["team", "agent"])
@pytest.mark.parametrize("also_sibling", [False, True])
def test_nested_policy_never_silently_ignored(raw_config, tmp_path, level, also_sibling):
    raw = deepcopy(raw_config)
    team = raw["teams"]["newsletter"]
    team["runtime"] = {"timeout": 1800}
    team.pop("permissions", None)
    team["agents"] = [{"name": "advisor", "blueprint": "advisor", "integration": "copilot"}]
    owner = team if level == "team" else team["agents"][0]
    owner.setdefault("runtime", {})["permissions"] = {"mode": "restricted"}
    if also_sibling:
        owner["permissions"] = {"mode": "unrestricted"}
    with pytest.raises(ValidationFailed) as caught:
        parse_config(raw, tmp_path / "config.yaml")
    expected_scope = "teams.newsletter" if level == "team" else "teams.newsletter.agents.advisor"
    expected_field = f"{expected_scope}.runtime.permissions"
    expected_hint = (
        f"Move {expected_scope}.runtime.permissions to {expected_scope}.permissions; "
        "keep timeout in runtime."
    )

    assert any(
        issue.code == "relocated-permissions"
        and issue.scope == expected_scope
        and issue.field == expected_field
        and issue.corrective_hint == expected_hint
        for issue in caught.value.issues
    )