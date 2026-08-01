from __future__ import annotations

from pathlib import Path

from agency.blueprints.cache import CacheRef, _entry_path, instance_digest
from agency.configuration.models import AgentIdentity
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def policy(*rules, timeout=60, mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=timeout,
        mode=mode,
        rules=tuple(ResolvedPermissionRule(path=Path(p), tools=t) for p, t in rules),
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


def test_entry_path_includes_the_instance_digest(tmp_path: Path):
    ref = CacheRef(
        integration="copilot",
        projector_version="v1",
        source_digest="abc",
        instance_digest="def",
    )

    assert _entry_path(tmp_path, ref) == tmp_path / "copilot" / "v1" / "abc" / "def"
