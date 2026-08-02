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
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: "v1")
    integration.invalidate_capability_cache()

    integration.runtime_capabilities
    integration.runtime_capabilities

    assert calls["n"] == 1


def test_cache_invalidates_when_the_key_changes(monkeypatch):
    integration = get_integration("aider")
    calls = {"n": 0}
    key = {"value": "v1"}

    def counting_detect():
        calls["n"] += 1
        return RuntimeCapabilities(permission_modes=frozenset({"unrestricted"}))

    monkeypatch.setattr(integration, "detect_runtime_capabilities", counting_detect)
    monkeypatch.setattr(integration, "_capability_cache_key", lambda: key["value"])
    integration.invalidate_capability_cache()

    integration.runtime_capabilities
    key["value"] = "v2"
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
