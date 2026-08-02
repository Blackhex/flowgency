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
        policy(rule(tmp_path / "ws", ("read",))), launch_dir=tmp_path / "launch"
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") in fs["readonlyPaths"]
    assert str(tmp_path / "ws") not in fs["readwritePaths"]


def test_write_rule_becomes_a_readwrite_path(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read", "write"))), launch_dir=tmp_path / "launch"
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") in fs["readwritePaths"]


def test_generated_zone_rules_are_rendered(tmp_path):
    launch = tmp_path / "launch"
    settings, _ = build_sandbox_settings(
        policy(
            rule(launch / "instructions", ("read",), generated=True),
            rule(launch / ".agency" / "outbox", ("read", "write"), generated=True),
        ),
        launch_dir=launch,
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(launch / "instructions") in fs["readonlyPaths"]
    assert str(launch / ".agency" / "outbox") in fs["readwritePaths"]


def test_omitted_tools_is_writable(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", None)), launch_dir=tmp_path / "launch"
    )

    assert str(tmp_path / "ws") in settings["sandbox"]["userPolicy"]["filesystem"]["readwritePaths"]


def test_empty_tools_grants_neither(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ())), launch_dir=tmp_path / "launch"
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(tmp_path / "ws") not in fs["readonlyPaths"]
    assert str(tmp_path / "ws") not in fs["readwritePaths"]


def test_denied_paths_is_never_used(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ())), launch_dir=tmp_path / "launch"
    )

    assert "deniedPaths" not in settings["sandbox"]["userPolicy"]["filesystem"]


def test_bypass_is_disabled_and_cwd_is_not_implicit(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",))), launch_dir=tmp_path / "launch"
    )
    sandbox = settings["sandbox"]

    assert sandbox["enabled"] is True
    assert sandbox["allowBypass"] is False
    assert sandbox["addCurrentWorkingDirectory"] is False


def test_a_pathless_rule_cannot_be_expressed_and_is_reported(tmp_path):
    _, unenforced = build_sandbox_settings(
        policy(ResolvedPermissionRule(path=None, tools=("fetch",))),
        launch_dir=tmp_path / "launch",
    )

    assert len(unenforced) == 1
