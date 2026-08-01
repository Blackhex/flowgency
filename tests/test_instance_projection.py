from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agency.blueprints.cache import CacheRef, _entry_path, instance_digest
from agency.configuration.models import AgentIdentity
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def policy(*rules, timeout=60, mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=timeout,
        mode=mode,
        # Pre-resolve so paths are absolute on every platform.
        rules=tuple(ResolvedPermissionRule(path=Path(p).resolve(), tools=t) for p, t in rules),
    )


IDENTITY = AgentIdentity(display_name="Duncan", title="Test Engineer")


def test_identity_changes_the_digest():
    other = AgentIdentity(display_name="Gurney", title="Test Engineer")

    assert instance_digest(IDENTITY, policy()) != instance_digest(other, policy())


def test_permission_rules_change_the_digest():
    a = policy(("/ws", ("read",)))
    b = policy(("/ws", ("read", "write")))

    assert instance_digest(IDENTITY, a) != instance_digest(IDENTITY, b)


def test_mode_changes_the_digest():
    a = policy(("/ws", ("read",)), mode="restricted")
    b = policy(("/ws", ("read",)), mode="unrestricted")

    assert instance_digest(IDENTITY, a) != instance_digest(IDENTITY, b)


def test_timeout_does_not_change_the_digest():
    a = policy(("/ws", ("read",)), timeout=60)
    b = policy(("/ws", ("read",)), timeout=1800)

    assert instance_digest(IDENTITY, a) == instance_digest(IDENTITY, b)


def test_digest_is_stable_across_calls():
    assert instance_digest(IDENTITY, policy()) == instance_digest(IDENTITY, policy())


def test_tools_none_and_empty_tuple_differ():
    # tools=None means every tool; tools=() means no tools — opposite permissions.
    all_tools = policy(("/ws", None))
    no_tools = policy(("/ws", ()))

    assert instance_digest(IDENTITY, all_tools) != instance_digest(IDENTITY, no_tools)


def test_emoji_changes_the_digest():
    plain = AgentIdentity(display_name="Duncan", title="Test Engineer", emoji="")
    with_emoji = AgentIdentity(display_name="Duncan", title="Test Engineer", emoji="🚀")

    assert instance_digest(plain, policy()) != instance_digest(with_emoji, policy())


def test_digest_is_stable_across_processes():
    # Runs in a subprocess with PYTHONHASHSEED=0 to prove hash() is not in the path.
    expected = instance_digest(IDENTITY, policy(("/ws", ("read",))))

    code = "\n".join([
        "from pathlib import Path",
        "from agency.blueprints.cache import instance_digest",
        "from agency.configuration.models import AgentIdentity",
        "from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule",
        "rules = (ResolvedPermissionRule(path=Path('/ws').resolve(), tools=('read',)),)",
        "p = EffectiveRuntimePolicy(timeout=60, mode='restricted', rules=rules)",
        "identity = AgentIdentity(display_name='Duncan', title='Test Engineer')",
        "print(instance_digest(identity, p))",
    ])
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_entry_path_includes_the_instance_digest(tmp_path: Path):
    ref = CacheRef(
        integration="copilot",
        projector_version="v1",
        source_digest="abc",
        instance_digest="def",
    )

    assert _entry_path(tmp_path, ref) == tmp_path / "copilot" / "v1" / "abc" / "def"
