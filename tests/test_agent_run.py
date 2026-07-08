from pathlib import Path

from fastapi.testclient import TestClient

from agency.app import app, CONFIG, GROUPS


def _setup_group(tmp_path: Path) -> Path:
    group_path = tmp_path / "grp"
    (group_path / "product").mkdir(parents=True)
    prompts = group_path / "shared" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "routine.md").write_text("# Routine\n")
    (group_path / "shared" / "logs").mkdir(parents=True)
    CONFIG.clear()
    CONFIG.update({"groups": {"test": {"name": "Test", "path": str(group_path)}}})
    GROUPS.clear()
    GROUPS["test"] = {
        "key": "test",
        "name": "Test",
        "path": group_path,
        "shared": group_path / "shared",
        "agents": ["product"],
        "agents_full": [{"name": "product", "integration": "script"}],
        "_agents_normalized": [{"name": "product", "integration": "script"}],
        "dispatch": {"timeout": 1800},
    }
    return group_path


def test_run_returns_202_and_schedules(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.is_agent_running", lambda *a, **k: False)
    monkeypatch.setattr("agency.app.run_agent_prompt", lambda *a, **k: calls.append((a, k)))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "routine.md"})

    assert resp.status_code == 202
    assert resp.json() == {"status": "started"}
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[1] == "product"
    assert args[2] == "routine.md"


def test_run_unknown_prompt_404(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.is_agent_running", lambda *a, **k: False)
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "nope.md"})

    assert resp.status_code == 404


def test_run_path_traversal_400(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.is_agent_running", lambda *a, **k: False)
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "../secret.md"})

    assert resp.status_code == 400


def test_run_already_running_409(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.is_agent_running", lambda *a, **k: True)
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "routine.md"})

    assert resp.status_code == 409
