from __future__ import annotations

from agency.integrations import get_integration
from agency.integrations.models import RuntimeCapabilities


def test_capabilities_are_readable_as_a_property():
    caps = get_integration("aider").runtime_capabilities

    assert isinstance(caps, RuntimeCapabilities)
    assert "unrestricted" in caps.permission_modes


def test_detection_is_cached_per_key(monkeypatch):
    integration = get_integration("aider")
    calls = {"n": 0}

    def counting_detect():
        calls["n"] += 1
        return RuntimeCapabilities(permission_modes=frozenset({"unrestricted"}))

    monkeypatch.setattr(integration, "detect_runtime_capabilities", counting_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "alpha")
    integration.invalidate_capability_cache()

    integration.runtime_capabilities
    integration.runtime_capabilities

    assert calls["n"] == 1


def test_cache_invalidates_when_the_key_changes(monkeypatch):
    integration = get_integration("aider")
    calls = {"n": 0}
    key = {"value": "alpha"}

    def counting_detect():
        calls["n"] += 1
        return RuntimeCapabilities(permission_modes=frozenset({"unrestricted"}))

    monkeypatch.setattr(integration, "detect_runtime_capabilities", counting_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: key["value"])
    integration.invalidate_capability_cache()

    integration.runtime_capabilities
    key["value"] = "beta"
    integration.runtime_capabilities

    assert calls["n"] == 2


def test_detection_failure_narrows_rather_than_widens(monkeypatch):
    integration = get_integration("aider")

    def failing_detect():
        raise OSError("cli not found")

    monkeypatch.setattr(integration, "detect_runtime_capabilities", failing_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "boom")
    integration.invalidate_capability_cache()

    caps = integration.runtime_capabilities

    assert caps.path_scopable_tools == frozenset()
    assert caps.permission_modes <= integration.declared_runtime_capabilities.permission_modes


def test_detection_failure_drops_a_declared_scoping_claim(monkeypatch):
    """Aider declares nothing scopable, so it cannot show the fallback widening.

    Copilot declares write, and a failed detector must not hand that claim back
    unmeasured: the whole point of detecting is that the claim is conditional.
    """
    integration = get_integration("copilot")

    def failing_detect():
        raise OSError("cli not found")

    monkeypatch.setattr(integration, "detect_runtime_capabilities", failing_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "boom")
    integration.invalidate_capability_cache()

    assert "write" in integration.declared_runtime_capabilities.path_scopable_tools
    assert integration.runtime_capabilities.path_scopable_tools == frozenset()

    integration.invalidate_capability_cache()


def test_a_failing_cache_key_does_not_fail_the_read(monkeypatch):
    """Identifying the environment is bookkeeping; it must not break a read."""
    integration = get_integration("aider")

    def exploding_key():
        raise OSError("cannot stat the binary")

    monkeypatch.setattr(integration, "_capability_cache_key", exploding_key)
    integration.invalidate_capability_cache()

    caps = integration.runtime_capabilities

    assert isinstance(caps, RuntimeCapabilities)
    assert caps.permission_modes <= integration.declared_runtime_capabilities.permission_modes


def test_widening_permission_modes_is_capped(monkeypatch):
    integration = get_integration("aider")

    def widening_detect():
        return RuntimeCapabilities(
            permission_modes=frozenset({"unrestricted", "restricted", "extra"}),
        )

    monkeypatch.setattr(integration, "detect_runtime_capabilities", widening_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "capped")
    integration.invalidate_capability_cache()

    caps = integration.runtime_capabilities

    assert caps.permission_modes <= integration.declared_runtime_capabilities.permission_modes


def test_widening_path_scopable_tools_is_capped(monkeypatch):
    integration = get_integration("aider")

    def widening_detect():
        return RuntimeCapabilities(
            permission_modes=frozenset({"unrestricted"}),
            path_scopable_tools=frozenset({"write", "extra_tool"}),
        )

    monkeypatch.setattr(integration, "detect_runtime_capabilities", widening_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "capped")
    integration.invalidate_capability_cache()

    caps = integration.runtime_capabilities

    assert caps.path_scopable_tools <= integration.declared_runtime_capabilities.path_scopable_tools


def test_none_cache_key_bypasses_cache(monkeypatch):
    integration = get_integration("aider")
    calls = {"n": 0}

    def counting_detect():
        calls["n"] += 1
        return RuntimeCapabilities(permission_modes=frozenset({"unrestricted"}))

    monkeypatch.setattr(integration, "detect_runtime_capabilities", counting_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: None)
    integration.invalidate_capability_cache()

    integration.runtime_capabilities
    integration.runtime_capabilities

    assert calls["n"] == 2
