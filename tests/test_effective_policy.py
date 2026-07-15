from pathlib import Path

import pytest


def test_agent_roots_are_additive_and_ordered(canonical_raw_config, canonical_paths):
    from agency.configuration import parse_config_canonical
    from agency.configuration.effective import resolve_effective_policy

    group = canonical_raw_config["groups"]["newsletter"]
    group["path"] = "C:/Projects/newsletter"
    group["runtime"] = {
        "sandbox": {
            "mode": "restricted",
            "roots": ["C:/Projects/newsletter", "C:/projects/newsletter", "C:/Shared/research"],
        }
    }
    agent = group["agents"][0]
    agent["name"] = "advisor"
    agent["integration"] = "copilot"
    agent["runtime"] = {
        "sandbox": {
            "mode": "restricted",
            "additional_roots": ["C:/Research/editorial", "c:/shared/RESEARCH"],
        }
    }

    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    policy = resolve_effective_policy(parsed.resolved, "newsletter", "advisor")

    assert policy.sandbox_mode == "restricted"
    assert policy.sandbox_roots == (
        Path("C:/Projects/newsletter").resolve(strict=False),
        Path("C:/Shared/research").resolve(strict=False),
        Path("C:/Research/editorial").resolve(strict=False),
    )


def test_agent_tool_policy_replaces_group(canonical_raw_config, canonical_paths):
    from agency.configuration import parse_config_canonical
    from agency.configuration.effective import resolve_effective_policy

    group = canonical_raw_config["groups"]["newsletter"]
    group["runtime"] = {
        "tools": {
            "mode": "allowlist",
            "names": ["read", "search"],
        }
    }
    agent = group["agents"][0]
    agent["name"] = "advisor"
    agent["integration"] = "copilot"
    agent["runtime"] = {
        "tools": {
            "mode": "allowlist",
            "names": ["read", "search", "write"],
        }
    }

    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    policy = resolve_effective_policy(parsed.resolved, "newsletter", "advisor")

    assert policy.tools.mode == "allowlist"
    assert policy.tools.names == ("read", "search", "write")


def test_agent_omits_tools_and_inherits_group(canonical_raw_config, canonical_paths):
    from agency.configuration import parse_config_canonical
    from agency.configuration.effective import resolve_effective_policy

    group = canonical_raw_config["groups"]["newsletter"]
    group["runtime"] = {
        "tools": {
            "mode": "allowlist",
            "names": ["read", "search"],
        }
    }
    agent = group["agents"][0]
    agent["name"] = "advisor"
    agent["integration"] = "copilot"

    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    policy = resolve_effective_policy(parsed.resolved, "newsletter", "advisor")

    assert policy.tools.mode == "allowlist"
    assert policy.tools.names == ("read", "search")


def test_timeout_override_precedence_is_job_then_agent_then_group(canonical_raw_config, canonical_paths):
    from agency.configuration import parse_config_canonical
    from agency.configuration.effective import resolve_effective_policy

    group = canonical_raw_config["groups"]["newsletter"]
    group["runtime"] = {"timeout": 900}
    agent = group["agents"][0]
    agent["name"] = "advisor"
    agent["integration"] = "copilot"
    agent["runtime"] = {"timeout": 1200}

    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    inherited = resolve_effective_policy(parsed.resolved, "newsletter", "advisor")
    overridden = resolve_effective_policy(parsed.resolved, "newsletter", "advisor", timeout_override=1800)

    assert inherited.timeout == 1200
    assert overridden.timeout == 1800


def test_agent_additional_roots_cannot_make_unrestricted_policy_ambiguous(canonical_raw_config, canonical_paths):
    from agency.configuration import parse_config_canonical
    from agency.configuration import ValidationFailed
    from agency.configuration.effective import resolve_effective_policy

    group = canonical_raw_config["groups"]["newsletter"]
    agent = group["agents"][0]
    agent["name"] = "advisor"
    agent["integration"] = "copilot"
    agent["runtime"] = {
        "sandbox": {
            "additional_roots": ["C:/Research/editorial"],
        }
    }

    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    with pytest.raises(ValidationFailed) as excinfo:
        resolve_effective_policy(parsed.resolved, "newsletter", "advisor")

    assert len(excinfo.value.issues) == 1
    issue = excinfo.value.issues[0]
    assert issue.code == "sandbox-contradiction"
    assert issue.scope == "groups.newsletter.agents.advisor"
    assert issue.field == "runtime.sandbox.additional_roots"
    assert issue.message == "Unrestricted sandbox cannot add roots."
    assert issue.corrective_hint == "Remove additional roots or switch to restricted mode."


def test_unknown_integration_fails_closed(canonical_raw_config, canonical_paths):
    from agency.configuration import ValidationFailed, parse_config_canonical
    from agency.configuration.effective import resolve_effective_policy

    agent = canonical_raw_config["groups"]["newsletter"]["agents"][0]
    agent["name"] = "advisor"
    agent["integration"] = "missing-runtime"

    parsed = parse_config_canonical(canonical_raw_config, canonical_paths["config_path"])

    with pytest.raises(ValidationFailed) as excinfo:
        resolve_effective_policy(parsed.resolved, "newsletter", "advisor")

    assert {issue.code for issue in excinfo.value.issues} == {"unknown-integration"}