from copy import deepcopy

from fastapi.testclient import TestClient
import yaml

from flowgency import app as app_mod
from tests._team_helpers import apply_team_paths, create_team_environment


def test_dashboard_and_jobs_do_not_touch_retired_records(workflow_web_env, tmp_path):
    env = workflow_web_env
    retired_record = env.team_root / "observations" / "old.md"
    retired_record.parent.mkdir(parents=True)
    retired_record.write_text(
        "---\nstatus: open\nttl_days: 1\ndate: 2000-01-01\n---\nOld record\n",
        encoding="utf-8",
    )
    before = retired_record.read_bytes()

    for url in ("/newsletter/", "/newsletter/agents", "/newsletter/jobs", env.base_path):
        assert env.client.get(url).status_code == 200

    assert retired_record.read_bytes() == before

    for url in ("/newsletter/observations", "/newsletter/proposals", "/newsletter/decisions"):
        assert env.client.get(url).status_code in (404, 410)
        assert env.client.post(url, data={}).status_code in (404, 405, 410)


def test_retired_detail_and_mutation_routes_are_unavailable(workflow_web_env):
    env = workflow_web_env
    observation_path = env.team_root / "observations" / "old.md"
    proposal_path = env.team_root / "proposals" / "old.md"
    decision_path = env.team_root / "decisions" / "old.md"

    observation_path.parent.mkdir(parents=True)
    proposal_path.parent.mkdir(parents=True)
    decision_path.parent.mkdir(parents=True)
    observation_path.write_text(
        "---\nstatus: open\ndate: 2000-01-01\n---\nOld observation\n",
        encoding="utf-8",
    )
    proposal_path.write_text(
        "---\nstatus: proposed\ndate: 2000-01-01\nquestions: []\n---\nOld proposal\n",
        encoding="utf-8",
    )
    decision_path.write_text(
        "---\nproposal: old.md\nexecution_status: failed\ndate: 2000-01-01\n---\nOld decision\n",
        encoding="utf-8",
    )
    before = {
        observation_path: observation_path.read_bytes(),
        proposal_path: proposal_path.read_bytes(),
        decision_path: decision_path.read_bytes(),
    }

    responses = [
        env.client.get("/newsletter/observations/old"),
        env.client.post("/newsletter/observations/old/status", data={"status": "dismissed"}),
        env.client.get("/newsletter/proposals/old"),
        env.client.post(
            "/newsletter/proposals/old/decide",
            data={"execution_agent": "builder", "decision_note": "retire"},
        ),
        env.client.get("/newsletter/decisions/old"),
        env.client.post(
            "/newsletter/decisions/old/retry",
            data={"execution_agent": "builder"},
        ),
        env.client.post(
            "/newsletter/decisions/old/verify",
            data={"verification_status": "verified"},
        ),
    ]

    for response in responses:
        assert response.status_code in (404, 405, 410)

    for path, payload in before.items():
        assert path.read_bytes() == payload


def test_home_with_no_workflows_does_not_create_retired_directories(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    paths = create_team_environment(tmp_path, "newsletter", create_state=False)
    library_root = tmp_path / "agent-library"
    (library_root / "builder-blueprint").mkdir(parents=True)
    (library_root / "builder-blueprint" / "AGENTS.md").write_text("# Builder\n", encoding="utf-8")

    raw["flowgency"]["agent_library"] = str(library_root)
    raw["flowgency"]["compilation_cache"] = str(tmp_path / "compiled-agents")
    raw["flowgency"]["memory_store"] = str(tmp_path / "memory-store")
    raw["flowgency"]["prompt_store"] = str(tmp_path / "prompts")
    raw["teams"]["newsletter"] = apply_team_paths(raw["teams"]["newsletter"], paths)
    raw["teams"]["newsletter"]["workflows"] = {}

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)

    response = TestClient(app_mod.app).get("/newsletter/")

    assert response.status_code == 200
    assert "No workflows configured." in response.text
    assert "Board A" not in response.text
    assert not (paths.state_root / "observations").exists()
    assert not (paths.state_root / "proposals").exists()
    assert not (paths.state_root / "decisions").exists()