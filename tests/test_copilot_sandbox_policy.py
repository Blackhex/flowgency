from __future__ import annotations

from pathlib import Path

from flowgency.integrations.flowgency.copilot_sandbox import build_sandbox_settings
from flowgency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


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
            rule(launch / ".flowgency" / "outbox", ("read", "write"), generated=True),
        )
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert str(launch / "instructions") in fs["readonlyPaths"]
    assert str(launch / ".flowgency" / "outbox") in fs["readwritePaths"]


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


def test_build_sandbox_settings_sets_allow_local_network_only_when_opted_in(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",))),
        allow_local_network=True,
    )

    assert settings["sandbox"]["userPolicy"]["network"] == {
        "allowLocalNetwork": True
    }


def test_build_sandbox_settings_omits_allow_local_network_when_not_opted_in(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",))),
    )

    assert "network" not in settings["sandbox"]["userPolicy"]


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


def test_an_unrestricted_policy_naming_nothing_is_not_sandboxed():
    """An allowlist cannot say everything, and an empty one says nothing."""
    settings, _ = build_sandbox_settings(policy(mode="unrestricted"))
    sandbox = settings["sandbox"]

    assert sandbox["enabled"] is False
    assert sandbox["userPolicy"]["filesystem"]["readonlyPaths"] == []
    assert sandbox["userPolicy"]["filesystem"]["readwritePaths"] == []


def test_a_restricted_policy_naming_nothing_stays_sandboxed():
    """Restricted with no rule reaches nothing; denying everything is the point."""
    settings, _ = build_sandbox_settings(policy(mode="restricted"))

    assert settings["sandbox"]["enabled"] is True


def test_an_unrestricted_policy_that_names_a_path_is_sandboxed(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",)), mode="unrestricted")
    )

    assert settings["sandbox"]["enabled"] is True
    assert str(tmp_path / "ws") in settings["sandbox"]["userPolicy"]["filesystem"]["readonlyPaths"]


def test_a_denial_dropped_by_disabling_the_sandbox_is_reported(tmp_path):
    denied = rule(tmp_path / "secret", ())
    settings, unenforced = build_sandbox_settings(policy(denied, mode="unrestricted"))

    assert settings["sandbox"]["enabled"] is False
    assert unenforced == (denied,)


def test_generated_zones_alone_do_not_confine_an_unrestricted_policy(tmp_path):
    """Every job carries zone grants; counting them would sandbox every job.

    The launch arguments say allow-all-paths for this policy, so an allowlist
    holding only the zones would deny the agent its own workspace.
    """
    launch = tmp_path / "launch"
    settings, _ = build_sandbox_settings(
        policy(
            rule(launch / "instructions", ("read",), generated=True),
            rule(launch / ".flowgency" / "outbox", ("read", "write"), generated=True),
            mode="unrestricted",
        )
    )

    assert settings["sandbox"]["enabled"] is False


# ── git / gh credentials ─────────────────────────────────────────────────────
#
# The installed CLI (1.0.84-3) documents credential injection under
# ``sandbox.auth.git`` / ``sandbox.auth.gh`` and ignores the older top-level
# ``gitAuth`` / ``ghAuth`` keys entirely ("Ignoring unknown top-level key(s)
# ... 'gitAuth', 'ghAuth'. They ... have no effect."). The settings must carry
# the nested form so the intended grant or denial actually takes effect, and
# tokens are only injected while the sandbox is enabled.


def test_credentials_use_the_nested_sandbox_auth_schema(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read", "write"))),
        workspace_root=tmp_path / "ws",
    )

    assert settings["sandbox"]["auth"] == {"git": True, "gh": True}
    assert "gitAuth" not in settings
    assert "ghAuth" not in settings


def test_reader_is_denied_git_and_gh_under_the_nested_schema(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read",))),
        workspace_root=tmp_path / "ws",
    )

    assert settings["sandbox"]["enabled"] is True
    assert settings["sandbox"]["auth"] == {"git": False, "gh": False}


def test_root_writer_is_granted_git_and_gh_under_the_nested_schema(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read", "write"))),
        workspace_root=tmp_path / "ws",
    )

    assert settings["sandbox"]["auth"] == {"git": True, "gh": True}


def test_subdirectory_write_does_not_earn_credentials_under_nested_schema(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(
            rule(tmp_path / "ws", ("read",)),
            rule(tmp_path / "ws" / "scratch", ("read", "write")),
        ),
        workspace_root=tmp_path / "ws",
    )

    assert settings["sandbox"]["auth"] == {"git": False, "gh": False}


def test_unknown_workspace_root_earns_no_credentials(tmp_path):
    settings, _ = build_sandbox_settings(
        policy(rule(tmp_path / "ws", ("read", "write"))),
    )

    assert settings["sandbox"]["auth"] == {"git": False, "gh": False}


def test_unconfined_policy_still_names_the_credential_keys(tmp_path):
    """With the sandbox disabled the CLI cannot enforce the denial, but the
    emitted schema must still be the recognised nested one, never the ignored
    top-level keys."""
    settings, _ = build_sandbox_settings(
        policy(mode="unrestricted"),
        workspace_root=tmp_path / "ws",
    )

    assert settings["sandbox"]["enabled"] is False
    assert "auth" in settings["sandbox"]
    assert "gitAuth" not in settings
    assert "ghAuth" not in settings


def test_an_authored_rule_confines_even_alongside_generated_zones(tmp_path):
    launch = tmp_path / "launch"
    settings, _ = build_sandbox_settings(
        policy(
            rule(launch / ".flowgency" / "outbox", ("read", "write"), generated=True),
            rule(tmp_path / "ws", ("read",)),
            mode="unrestricted",
        )
    )
    fs = settings["sandbox"]["userPolicy"]["filesystem"]

    assert settings["sandbox"]["enabled"] is True
    assert str(tmp_path / "ws") in fs["readonlyPaths"]
    assert str(launch / ".flowgency" / "outbox") in fs["readwritePaths"]
