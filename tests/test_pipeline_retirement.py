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


# Single-segment slugs that a restored loader could try to resolve into the
# retired record roots. The path param never spans '/', so traversal is probed
# through percent-encoding and dot segments alongside plain unknown/known ids.
HOSTILE_SLUGS = (
    "old",
    "old.md",
    "%2e%2e",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..%5c..%5cwindows",
    "does-not-exist",
    "a" * 512,
    "%00",
    "<script>alert(1)</script>",
)


def _seed_retired_roots(team_root):
    sentinels = {}
    for kind, front in (
        ("observations", "status: open\ndate: 2000-01-01"),
        ("proposals", "status: proposed\ndate: 2000-01-01\nquestions: []"),
        ("decisions", "proposal: old.md\nexecution_status: failed\ndate: 2000-01-01"),
    ):
        path = team_root / kind / "old.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{front}\n---\nOld {kind} record\n", encoding="utf-8")
        sentinels[path] = path.read_bytes()
    return sentinels


def _retired_requests(client, team, slug):
    return (
        client.get(f"/{team}/observations/{slug}"),
        client.post(f"/{team}/observations/{slug}/status", data={"status": "dismissed"}),
        client.get(f"/{team}/proposals/{slug}"),
        client.post(
            f"/{team}/proposals/{slug}/decide",
            data={"execution_agent": "builder", "decision_note": slug},
        ),
        client.get(f"/{team}/decisions/{slug}"),
        client.post(f"/{team}/decisions/{slug}/retry", data={"execution_agent": "builder"}),
        client.post(f"/{team}/decisions/{slug}/verify", data={"verification_status": "verified"}),
    )


def test_retired_routes_reject_hostile_and_unknown_ids_without_touching_records(workflow_web_env):
    env = workflow_web_env
    sentinels = _seed_retired_roots(env.team_root)

    for slug in HOSTILE_SLUGS:
        for response in _retired_requests(env.client, env.team_id, slug):
            assert response.status_code in (404, 405, 410)

    # No hostile or unknown id created, deleted, or mutated a retired record, and
    # each retired root still holds exactly the sentinel it started with.
    for kind in ("observations", "proposals", "decisions"):
        assert [p.name for p in (env.team_root / kind).iterdir()] == ["old.md"]
    for path, payload in sentinels.items():
        assert path.read_bytes() == payload


def test_retired_routes_never_invoke_team_or_record_loaders(workflow_web_env, monkeypatch):
    """The retired handlers must short-circuit before any team/record read."""
    env = workflow_web_env
    _seed_retired_roots(env.team_root)

    loaded: list[str] = []
    real_get_team = app_mod.get_team
    monkeypatch.setattr(
        app_mod, "get_team", lambda team: loaded.append(team) or real_get_team(team)
    )

    for slug in ("old", "does-not-exist", "%2e%2e"):
        for response in _retired_requests(env.client, env.team_id, slug):
            assert response.status_code in (404, 405, 410)
    for response in (
        env.client.get(f"/{env.team_id}/observations"),
        env.client.get(f"/{env.team_id}/proposals"),
        env.client.get(f"/{env.team_id}/decisions"),
    ):
        assert response.status_code in (404, 410)

    assert loaded == []