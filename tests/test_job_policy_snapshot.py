from __future__ import annotations

from pathlib import Path

from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule
from agency.jobs.models import RuntimePolicySnapshot


def policy(*rules, mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=60,
        mode=mode,
        rules=tuple(ResolvedPermissionRule(path=p, tools=t) for p, t in rules),
    )


def test_round_trip_preserves_rules():
    original = policy((Path("/ws"), ("read",)), (Path("/ws/tests"), ("read", "write")))

    restored = RuntimePolicySnapshot.from_effective_policy(original).to_effective_policy()

    assert restored.mode == "restricted"
    assert [(r.path, r.tools) for r in restored.rules] == [
        (Path("/ws").resolve(strict=False), ("read",)),
        (Path("/ws/tests").resolve(strict=False), ("read", "write")),
    ]


def test_round_trip_preserves_omitted_tools():
    restored = RuntimePolicySnapshot.from_effective_policy(
        policy((Path("/ws"), None))
    ).to_effective_policy()

    assert restored.rules[0].tools is None


def test_round_trip_preserves_empty_tools():
    restored = RuntimePolicySnapshot.from_effective_policy(
        policy((Path("/ws"), ()))
    ).to_effective_policy()

    assert restored.rules[0].tools == ()


def test_round_trip_preserves_a_pathless_rule():
    restored = RuntimePolicySnapshot.from_effective_policy(
        policy((None, ("fetch",)))
    ).to_effective_policy()

    assert restored.rules[0].path is None
    assert restored.rules[0].tools == ("fetch",)
