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
    config_path = tmp_path / "config.yaml"
    config = {
        "agency": {"title": "Agency", "default_group": "test"},
        "groups": {
            "test": {
                "name": "Test",
                "path": str(group_path),
                "agents": ["product"],
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
    app_mod.reload_groups()

    client = TestClient(app_mod.app)
    response = client.get("/admin/dispatch")

    assert response.status_code == 200

    # Debug: print patterns found
    import re
    html = response.text
    onsubmit_patterns = re.findall(r'onsubmit="[^"]*"', html, re.IGNORECASE)
    print(f"\nFound onsubmit patterns: {onsubmit_patterns}")
    print(f"\nO'Brien in text: {'O' in html or 'Brien' in html}")

    # Must not interpolate the path into onsubmit attribute
    for pattern in onsubmit_patterns:
        print(f"Checking pattern: {pattern}")
        assert "O'Brien" not in pattern, f"Path must not be in onsubmit handler: {pattern}"
        assert "Brien" not in pattern, f"Path must not be in onsubmit handler: {pattern}"

    # Must still have the replace hidden input
    assert 'name="replace" value="true"' in response.text

    # Confirmation message should be static (not contain the dynamic path)
    if onsubmit_patterns:
        # Should use a generic message like "Replace the existing dispatcher"
        for pattern in onsubmit_patterns:
            assert "config_path" not in pattern.lower(), "onsubmit must not reference config_path variable"
