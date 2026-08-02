from __future__ import annotations

from pathlib import Path

from agency.integrations.agency.copilot_sandbox import build_sandbox_settings
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def rule(path, tools, generated=False):
    return ResolvedPermissionRule(path=Path(path), tools=tools, generated=generated)


def policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(timeout=60, mode=mode, rules=tuple(rules))


def test_read_only_rule_becomes_a_readonly_path(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",)))
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") in fs["readonlyPaths"]
    assert str(tmp_path / "ws") not in fs["readwritePaths"]


def test_write_rule_becomes_a_readwrite_path(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read", "write")))
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") in fs["readwritePaths"]
    assert str(tmp_path / "ws") not in fs["readonlyPaths"]


def test_generated_zone_rules_are_rendered(tmp_path):
    launch = tmp_path / "launch"
    settings, _ = build_sandbox_settings(
        policy(
            rule(launch / "instructions", ("read",), generated=True),
            rule(launch / ".agency" / "outbox", ("read", "write"), generated=True),
        )
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(launch / "instructions") in fs["readonlyPaths"]
    assert str(launch / ".agency" / "outbox") in fs["readwritePaths"]


def test_omitted_tools_is_writable(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", None))
    )

    assert str(tmp_path / "ws") in settings["sandbox"]["userPolicy"]["filesystem"]["readwritePaths"]


def test_empty_tools_grants_neither(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ()))
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") not in fs["readonlyPaths"]
    assert str(tmp_path / "ws") not in fs["readwritePaths"]


def test_denied_paths_is_never_used(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ()))
    )

    assert "deniedPaths" not in settings["sandbox"]["userPolicy"]["filesystem"]


def test_bypass_is_disabled_and_cwd_is_not_implicit(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",)))
    )
    sandbox = settings["sandbox"]

    assert sandbox["enabled"] is True
    assert sandbox["allowBypass"] is False
    assert sandbox["addCurrentWorkingDirectory"] is False


def test_overlapping_path_in_read_and_write_rules_appears_only_in_readwrite(tmp_path):
    p = str(tmp_path / "ws")
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",)), rule(tmp_path / "ws", ("read", "write")))
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert p in fs["readwritePaths"]
    assert p not in fs["readonlyPaths"]


def test_a_pathless_rule_cannot_be_expressed_and_is_reported(tmp_path):
    pathless = ResolvedPermissionRule(path=None, tools=("fetch",))
    _, unenforced = build_sandbox_settings(policy(pathless))

    assert len(unenforced) == 1
    assert unenforced[0] is pathless
