import asyncio

import pytest

import agency.app as app_mod
from agency.integrations import detect_integration


class FakeRequest:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def copilot_group(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {
            "grp": {
                "name": "Group",
                "path": str(agents_dir),
                "default_integration": "copilot",
                "agents": [],
            }
        },
    })
    app_mod.reload_groups()
    return agents_dir


def test_admin_create_prepares_copilot_agent_dir(copilot_group):
    _run(app_mod.admin_agent_create(FakeRequest({"name": "reviewer"}), "grp"))

    agent_dir = copilot_group / "reviewer"
    assert (agent_dir / "AGENTS.md").is_file()
    assert (agent_dir / ".copilot").is_dir()
    assert detect_integration(agent_dir).name == "copilot"


def test_admin_create_propagates_prepare_error_without_partial_files(
    copilot_group, monkeypatch
):
    integration = app_mod.get_integration("copilot")
    error = PermissionError("marker creation denied")

    def fail_preparation(agent_dir):
        raise error

    monkeypatch.setattr(integration, "prepare_agent_dir", fail_preparation)

    with pytest.raises(PermissionError) as exc_info:
        _run(app_mod.admin_agent_create(FakeRequest({"name": "reviewer"}), "grp"))

    agent_dir = copilot_group / "reviewer"
    assert exc_info.value is error
    assert not (agent_dir / "AGENTS.md").exists()
    assert not (agent_dir / ".agency-meta.yaml").exists()
    assert not (agent_dir / "memory.md").exists()