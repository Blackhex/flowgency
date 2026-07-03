"""Contract tests: validate all registered integrations meet the BaseIntegration API."""
import inspect
import pytest
from pathlib import Path
from agency.integrations import REGISTRY, BaseIntegration, AgentIdentity


def all_integration_names():
    return list(REGISTRY.keys())


@pytest.fixture(params=all_integration_names())
def integration(request):
    return REGISTRY[request.param]


class TestIntegrationContract:
    def test_has_name(self, integration):
        assert isinstance(integration.name, str)
        assert len(integration.name) > 0

    def test_has_display_name(self, integration):
        assert isinstance(integration.display_name, str)

    def test_identity_filename_returns_string(self, integration):
        result = integration.identity_filename()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_detect_accepts_path_returns_bool(self, integration, tmp_path):
        result = integration.detect(tmp_path)
        assert isinstance(result, bool)

    def test_parse_identity_accepts_path(self, integration, tmp_path):
        result = integration.parse_identity(tmp_path)
        assert result is None or isinstance(result, AgentIdentity)

    def test_supports_execution_is_bool(self, integration):
        assert isinstance(integration.supports_execution, bool)

    def test_supports_ai_backend_is_bool(self, integration):
        assert isinstance(integration.supports_ai_backend, bool)

    def test_detect_priority_is_int(self, integration):
        assert isinstance(integration.detect_priority, int)

    def test_run_callable_if_execution_supported(self, integration):
        if integration.supports_execution:
            assert callable(integration.run)

    def test_is_base_integration_subclass(self, integration):
        assert isinstance(integration, BaseIntegration)


def test_all_execution_integrations_run_accepts_sandbox_root():
    """Every execution-capable integration.run must accept the sandbox_root kwarg.

    Both call sites (dispatch, decision execution) pass sandbox_root
    unconditionally, so an override missing it raises TypeError at runtime.
    """
    offenders = []
    for name, integration in REGISTRY.items():
        if not getattr(integration, "supports_execution", False):
            continue
        sig = inspect.signature(integration.run)
        param = sig.parameters.get("sandbox_root")
        accepts_var_kw = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if param is None and not accepts_var_kw:
            offenders.append(name)
    assert offenders == [], f"integrations missing sandbox_root kwarg: {offenders}"

