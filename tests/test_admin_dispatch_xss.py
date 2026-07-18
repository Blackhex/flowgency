"""Test for XSS prevention in admin_dispatch.html conflict repair form."""
from pathlib import Path
import yaml
from fastapi.testclient import TestClient
import agency.app as app_mod


def test_conflict_repair_form_does_not_embed_path_in_onsubmit(tmp_path, monkeypatch):
    """Prove conflict config path is not interpolated into onsubmit JS string."""
    # Create test environment
    group_path = tmp_path / "agents"
    (group_path / "shared" / "prompts").mkdir(parents=True)
    (group_path / "product").mkdir()
    (tmp_path / "agent-library").mkdir()
    config_path = tmp_path / "config.yaml"
    config = {
        "schema_version": 2,
        "agency": {
            "title": "Agency",
            "default_group": "test",
            "ai_backend": "claude-code",
            "agent_library": str((tmp_path / "agent-library").resolve()),
            "compilation_cache": str((tmp_path / "compiled-agents").resolve()),
            "memory_store": str((tmp_path / "memory").resolve()),
        },
        "groups": {
            "test": {
                "name": "Test",
                "path": str(group_path),
                "default_integration": "claude-code",
                "agents": [
                    {
                        "name": "product",
                        "blueprint": "product-blueprint",
                        "integration": "claude-code",
                    }
                ],
            },
        },
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)

    # Mock status with a path containing single quote (O'Brien)
    conflict_status = {
        "state": "misconfigured",
        "installed": True,
        "enabled": False,
        "timer_active": False,
        "definition_matches": False,
        "config_conflict": True,
        "config_path": "C:/Users/O'Brien/projects/agency/config.yaml",
        "interval": 15,
        "expected_config_path": str(config_path.resolve()),
        "expected_interval": 15,
        "mismatches": ["config_path"],
        "error": None,
    }
    monkeypatch.setattr(
        app_mod,
        "_get_timer_status",
        lambda path, interval: conflict_status
    )
    app_mod.refresh_services()

    client = TestClient(app_mod.app)
    response = client.get("/admin/dispatch")

    assert response.status_code == 200

    import re
    html = response.text
    onsubmit_patterns = re.findall(r'onsubmit="[^"]*"', html, re.IGNORECASE)

    for pattern in onsubmit_patterns:
        assert "O'Brien" not in pattern, f"Path must not be in onsubmit handler: {pattern}"
        assert "Brien" not in pattern, f"Path must not be in onsubmit handler: {pattern}"

    assert 'name="replace" value="true"' in response.text

    if onsubmit_patterns:
        for pattern in onsubmit_patterns:
            assert "config_path" not in pattern.lower(), "onsubmit must not reference config_path variable"
