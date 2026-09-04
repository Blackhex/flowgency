from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from agency.configuration.effective import resolve_effective_policy
from agency.configuration.store import ConfigStore
from agency.integrations.models import (
    ANY_TOOL,
    EffectiveRuntimePolicy,
    ResolvedPermissionRule,
)
from agency.permissions.zones import ZONE_INSTRUCTIONS, ZONE_MEMORY, ZONE_OUTBOX


def policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(timeout=60, mode=mode, rules=tuple(rules))


def rule(path, tools):
    return ResolvedPermissionRule(path=None if path is None else Path(path), tools=tools)


def test_longest_match_governs():
    p = policy(rule("/ws", ("read",)), rule("/ws/tests", ("read", "write")))

    assert p.tools_for(Path("/ws/src/a.py")) == ("read",)
    assert p.tools_for(Path("/ws/tests/t.py")) == ("read", "write")


def test_empty_tools_is_a_carve_out():
    p = policy(rule("/ws", ("read", "write")), rule("/ws/.env", ()))

    assert p.tools_for(Path("/ws/.env")) == ()


def test_omitted_tools_means_every_tool():
    p = policy(rule("/ws", None))

    assert p.tools_for(Path("/ws/a")) is None


def test_uncovered_path_is_forbidden_when_restricted():
    p = policy(rule("/ws", ("read",)), mode="restricted")

    assert p.tools_for(Path("/elsewhere")) == ()


def test_uncovered_path_is_permitted_when_unrestricted():
    p = policy(rule("/ws", ("read",)), mode="unrestricted")

    assert p.tools_for(Path("/elsewhere")) is None


def test_scoped_tools_names_tools_whose_grant_differs():
    p = policy(rule("/ws", ("read",)), rule("/ws/tests", ("read", "write")))

    assert p.scoped_tools == frozenset({"write"})


def test_scoped_tools_is_empty_when_every_rule_agrees():
    p = policy(rule("/a", ("read",)), rule("/b", ("read",)))

    assert p.scoped_tools == frozenset()


def test_scoped_tools_none_plus_explicit_reports_explicit_names():
    # A None rule grants everything; an explicit tuple is narrower — every
    # named tool in the explicit rule is potentially scoped differently.
    p = policy(rule("/ws", None), rule("/ws/sub", ("read",)))

    assert p.scoped_tools == frozenset({"read"})


def test_scoped_tools_none_plus_none_is_empty():
    p = policy(rule("/ws", None), rule("/ws/sub", None))

    assert p.scoped_tools == frozenset()


def test_scoped_tools_explicit_plus_explicit_standard_diff():
    p = policy(rule("/a", ("read",)), rule("/b", ("read", "write")))

    assert p.scoped_tools == frozenset({"write"})


def test_path_prefix_boundary():
    # /ws-other must NOT be treated as inside /ws (classic startswith bug).
    p = policy(rule("/ws", ("read",)), mode="restricted")

    assert p.tools_for(Path("/ws-other/a")) == ()


def test_pathless_rule_is_ignored_by_tools_for():
    # path=None rules are skipped and never match any concrete path.
    p = policy(rule(None, ("read", "write")), rule("/ws", ("search",)))

    assert p.tools_for(Path("/ws/a")) == ("search",)

    p2 = policy(rule(None, ("read",)), mode="restricted")
    assert p2.tools_for(Path("/ws/a")) == ()


def test_pathless_rule_excluded_from_scoped_tools():
    # A single path-bearing rule plus a pathless rule — not enough to scope.
    p = policy(rule(None, ("read",)), rule("/ws", ("write",)))

    assert p.scoped_tools == frozenset()


def test_unrestricted_carve_out_leaves_the_remainder_unbounded():
    # `mode` governs only uncovered paths. A carve-out narrows the path it
    # names; everything outside it still grants every tool.
    p = policy(rule("/ws", ("read",)), mode="unrestricted")

    assert p.tools_for(Path("/ws/a")) == ("read",)
    assert p.tools_for(Path("/elsewhere")) is None


def test_unrestricted_deny_carve_out_forbids_only_its_own_path():
    p = policy(rule("/ws/.env", ()), mode="unrestricted")

    assert p.tools_for(Path("/ws/.env")) == ()
    assert p.tools_for(Path("/ws/src")) is None


def test_restricted_deny_rule_beside_blanket_rule_is_scoped():
    # /ws grants everything, /ws/.env grants nothing: a real difference that
    # no tool name can express, so the sentinel stands in for it. Returning an
    # empty set here would tell negotiation there is nothing to scope.
    p = policy(rule("/ws", None), rule("/ws/.env", ()), mode="restricted")

    assert p.scoped_tools == frozenset({ANY_TOOL})


def test_restricted_single_rule_still_needs_no_scoping():
    # Under restricted the uncovered remainder is outside the sandbox
    # entirely, so one rule is enforceable by path confinement alone.
    p = policy(rule("/ws", ("read",)), mode="restricted")

    assert p.scoped_tools == frozenset()


def test_launch_zones_are_appended(tmp_path: Path):
    zoned = policy(rule("/ws", ("read",))).with_launch_zones(tmp_path)

    assert zoned.tools_for(tmp_path / ZONE_INSTRUCTIONS / "AGENTS.md") == ("read",)
    assert zoned.tools_for(tmp_path / ZONE_OUTBOX / "observations") == ("read", "write")
    assert zoned.tools_for(tmp_path / ZONE_MEMORY / "memory.md") == ("read", "write")


def test_launch_zones_cannot_be_widened_by_configuration(tmp_path: Path):
    authored = policy(rule(str(tmp_path / ZONE_INSTRUCTIONS), ("read", "write")))

    zoned = authored.with_launch_zones(tmp_path)

    assert zoned.tools_for(tmp_path / ZONE_INSTRUCTIONS / "AGENTS.md") == ("read",)


def _config(tmp_path: Path, raw_config, *, group_rules, agent_rules):
    raw = deepcopy(raw_config)
    raw["schema_version"] = 6
    raw["teams"]["newsletter"]["runtime"] = {
        "permissions": {"mode": "restricted", "rules": group_rules}
    }
    # agents is a list in raw_config; strip any stale capability/sandbox keys
    agent = raw["teams"]["newsletter"]["agents"][0]
    agent.pop("capabilities", None)
    # Use copilot so that restricted mode is accepted by the integration validator.
    agent["integration"] = "copilot"
    agent_runtime = {"permissions": {"rules": agent_rules}}
    agent["runtime"] = agent_runtime
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


def test_instance_rules_are_additive(tmp_path, raw_config):
    config = _config(
        tmp_path,
        raw_config,
        group_rules=[{"path": "C:/ws", "tools": ["read"]}],
        agent_rules=[{"path": "C:/ws/tests", "tools": ["read"]}],
    )

    resolved = resolve_effective_policy(config, "newsletter", "builder")
    paths = [r.path for r in resolved.rules if r.path is not None]

    assert Path("C:/ws") in paths
    assert Path("C:/ws/tests") in paths


def test_same_path_in_team_and_instance_unions_tools(tmp_path, raw_config):
    config = _config(
        tmp_path,
        raw_config,
        group_rules=[{"path": "C:/ws", "tools": ["read"]}],
        agent_rules=[{"path": "C:/ws", "tools": ["search"]}],
    )

    resolved = resolve_effective_policy(config, "newsletter", "builder")

    assert set(resolved.tools_for(Path("C:/ws/a"))) == {"read", "search"}


