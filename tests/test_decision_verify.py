"""Tests for the Verify stage — the governance-only outcome state on decisions.

Agency observes and governs the result of a dispatched decision without executing
it. Verification records whether an executed outcome satisfied its originating
proposal and, when it did not, floats a linked follow-up observation so the
execute -> verify loop stays connected.
"""
from copy import deepcopy

import yaml
from fastapi.testclient import TestClient

import agency.app as app_mod
from agency.app import app


def _setup_group(tmp_path, monkeypatch, *, decision_meta):
    group = tmp_path / "group"
    (group / "engineer").mkdir(parents=True)
    shared = group / "shared"
    for name in ("proposals", "decisions", "observations", "logs", "prompts"):
        (shared / name).mkdir(parents=True)

    proposal_meta = {"origin_agent": "product", "status": "decided"}
    (shared / "proposals" / "change.md").write_text(
        "---\n" + yaml.safe_dump(proposal_meta, sort_keys=False) + "---\n\nProposal body\n",
        encoding="utf-8",
    )

    decision_path = shared / "decisions" / "change.md"
    decision_path.write_text(
        "---\n" + yaml.safe_dump(decision_meta, sort_keys=False) + "---\n\nDecision body\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "agency": {
                    "title": "Agency",
                    "default_group": "test",
                    "ai_backend": "claude-code",
                    "decided_by": "captain",
                    "agent_library": str((tmp_path / "agent-library").resolve()),
                    "compilation_cache": str((tmp_path / "compiled-agents").resolve()),
                    "memory_store": str((tmp_path / "memory").resolve()),
                },
                "groups": {
                    "test": {
                        "name": "Test",
                        "path": str(group.resolve()),
                        "default_integration": "script",
                        "agents": [
                            {
                                "name": "engineer",
                                "blueprint": "engineer-blueprint",
                                "integration": "script",
                                "integration_config": {"command": "echo ok"},
                            }
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    return TestClient(app), decision_path, shared


def _meta(path):
    _, fm, _ = path.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(fm) or {}


def test_verify_marks_decision_verified(tmp_path, monkeypatch):
    client, decision_path, _ = _setup_group(
        tmp_path, monkeypatch,
        decision_meta={"proposal": "change.md", "execution_status": "complete", "executed_by": "engineer"},
    )
    resp = client.post(
        "/test/decisions/change/verify",
        data={"verification_status": "verified"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/test/decisions/change"

    meta = _meta(decision_path)
    assert meta["verification_status"] == "verified"
    assert meta["verified_by"] == "captain"
    assert meta["verified_at"]
    assert "follow_up_observation" not in meta


def test_verify_needs_follow_up_creates_linked_observation(tmp_path, monkeypatch):
    client, decision_path, shared = _setup_group(
        tmp_path, monkeypatch,
        decision_meta={"proposal": "change.md", "execution_status": "complete", "executed_by": "engineer"},
    )
    resp = client.post(
        "/test/decisions/change/verify",
        data={"verification_status": "needs_follow_up"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    meta = _meta(decision_path)
    assert meta["verification_status"] == "needs_follow_up"
    follow_up = meta["follow_up_observation"]
    assert follow_up.endswith(".md")

    obs_path = shared / "observations" / follow_up
    assert obs_path.exists()
    obs_meta = _meta(obs_path)
    assert obs_meta["follow_up_of_decision"] == "change.md"
    assert obs_meta["linked_proposal"] == "change.md"
    assert obs_meta["float"] is True
    assert resp.headers["location"] == f"/test/observations/{follow_up[:-3]}"


def test_verify_rejects_invalid_status(tmp_path, monkeypatch):
    client, decision_path, _ = _setup_group(
        tmp_path, monkeypatch,
        decision_meta={"proposal": "change.md", "execution_status": "complete"},
    )
    resp = client.post(
        "/test/decisions/change/verify",
        data={"verification_status": "bogus"},
    )
    assert resp.status_code == 400
    assert "verification_status" not in _meta(decision_path)


def test_verify_missing_decision_returns_404(tmp_path, monkeypatch):
    client, _, _ = _setup_group(
        tmp_path, monkeypatch,
        decision_meta={"proposal": "change.md", "execution_status": "complete"},
    )
    resp = client.post("/test/decisions/nope/verify", data={"verification_status": "verified"})
    assert resp.status_code == 404
