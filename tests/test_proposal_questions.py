"""Tests for proposal questions and decision answers."""
import yaml
from fastapi.testclient import TestClient

import agency.app as app_mod
from agency.app import app


def _make_proposal_frontmatter(questions):
    """Build proposal frontmatter YAML string with questions."""
    meta = {
        "origin_agent": "product",
        "date": "2026-03-24",
        "status": "proposed",
        "observations": [],
        "feedback_requested": [],
        "feedback_received": [],
        "ttl_days": 14,
        "questions": questions,
    }
    body = "## Proposal: Test\n\nSome context."
    fm = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body}\n"


def _parse_frontmatter(text):
    """Minimal frontmatter parser for tests."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return meta, body


class TestQuestionTypes:
    def test_boolean_question_structure(self):
        q = {"id": "approve", "type": "boolean", "prompt": "Proceed?"}
        assert q["type"] == "boolean"
        assert q["id"] == "approve"

    def test_choice_question_structure(self):
        q = {
            "id": "color",
            "type": "choice",
            "prompt": "Pick a color",
            "options": [{"label": "Red"}, {"label": "Blue"}],
            "multi": False,
        }
        assert q["type"] == "choice"
        assert len(q["options"]) == 2
        assert q["multi"] is False

    def test_choice_multi_select(self):
        q = {
            "id": "features",
            "type": "choice",
            "prompt": "Pick features",
            "options": [{"label": "Auth"}, {"label": "Search"}, {"label": "Chat"}],
            "multi": True,
        }
        assert q["multi"] is True

    def test_free_response_question_structure(self):
        q = {"id": "description", "type": "free-response", "prompt": "Describe it"}
        assert q["type"] == "free-response"


class TestBuildAnswers:
    def test_builds_answers_from_form_data(self):
        """Simulate building answers dict from form data."""
        questions = [
            {"id": "approve", "type": "boolean", "prompt": "Proceed?"},
            {"id": "color", "type": "choice", "prompt": "Pick", "options": [{"label": "Red"}, {"label": "Blue"}], "multi": False},
            {"id": "desc", "type": "free-response", "prompt": "Describe"},
        ]
        form_data = {
            "answer_approve": "approved",
            "answer_color": "Red",
            "answer_desc": "A sunset scene",
        }
        answers = {}
        for q in questions:
            key = f"answer_{q['id']}"
            answers[q["id"]] = form_data.get(key, "")

        assert answers == {
            "approve": "approved",
            "color": "Red",
            "desc": "A sunset scene",
        }

    def test_multi_select_answers(self):
        """Multi-select choice answers should be a list."""
        questions = [
            {"id": "features", "type": "choice", "prompt": "Pick", "options": [{"label": "Auth"}, {"label": "Search"}], "multi": True},
        ]
        form_values = ["Auth", "Search"]
        answers = {"features": form_values}
        assert answers["features"] == ["Auth", "Search"]


class TestProposalFrontmatter:
    def test_proposal_with_questions_parses(self):
        questions = [
            {"id": "approve", "type": "boolean", "prompt": "Proceed?"},
        ]
        text = _make_proposal_frontmatter(questions)
        meta, body = _parse_frontmatter(text)
        assert "questions" in meta
        assert len(meta["questions"]) == 1
        assert meta["questions"][0]["id"] == "approve"

    def test_decision_with_answers_parses(self):
        answers = {"approve": "approved", "color": "Red"}
        meta = {
            "proposal": "test.md",
            "decided_by": "admin",
            "date": "2026-03-24",
            "answers": answers,
        }
        fm = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
        text = f"---\n{fm}\n---\n\n"
        parsed_meta, _ = _parse_frontmatter(text)
        assert parsed_meta["answers"]["approve"] == "approved"
        assert parsed_meta["answers"]["color"] == "Red"


def _setup_decision_group(tmp_path, monkeypatch, *, explicit_executor=True):
    group = tmp_path / "group"
    for agent in ("product", "engineer", "sdk-agent"):
        (group / agent).mkdir(parents=True)
    shared = group / "shared"
    for name in ("proposals", "decisions", "observations", "logs", "prompts"):
        (shared / name).mkdir(parents=True)
    metadata = {
        "origin_agent": "product", "status": "proposed",
        "questions": [{"id": "approve", "type": "boolean", "prompt": "Proceed?"}],
    }
    if explicit_executor:
        metadata["execution_agent"] = "engineer"
    proposal_path = shared / "proposals" / "change.md"
    proposal_path.write_text(
        "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n\nProposal body\n"
    )
    agents = [
        {"name": "product", "integration": "script", "integration_config": {"command": "echo ok"}},
        {
            "name": "engineer",
            "integration": "script",
            "integration_config": {"command": "echo ok"},
            "capabilities": {"write": True},
        },
        {"name": "sdk-agent", "integration": "sdk"},
    ]
    monkeypatch.setattr(app_mod, "CONFIG", {"groups": {"test": {"path": str(group), "agents": agents}}})
    monkeypatch.setattr(app_mod, "GROUPS", {"test": {
        "key": "test", "name": "Test", "path": group,
        "agents": [item["name"] for item in agents], "_agents_normalized": agents,
    }})
    return TestClient(app), proposal_path, shared / "decisions" / "change.md"


def test_executor_options_exclude_agents_without_explicit_write_capability(tmp_path, monkeypatch):
    _setup_decision_group(tmp_path, monkeypatch)
    assert app_mod.execution_agent_options(app_mod.GROUPS["test"]) == ["engineer"]


def test_proposal_form_defaults_executor_to_explicit_execution_agent(tmp_path, monkeypatch):
    client, _, _ = _setup_decision_group(tmp_path, monkeypatch)
    response = client.get("/test/proposals/change")
    assert response.status_code == 200
    assert '<option value="engineer" selected>' in response.text


def test_unanswered_boolean_form_only_offers_approve_and_decline(tmp_path, monkeypatch):
    client, _, _ = _setup_decision_group(tmp_path, monkeypatch)
    response = client.get("/test/proposals/change")
    assert response.status_code == 200
    assert 'value="approved"' in response.text
    assert 'value="declined"' in response.text
    assert 'value="deferred"' not in response.text
    assert 'value="rejected"' not in response.text
    assert ">Defer<" not in response.text


def test_superseded_proposal_excludes_origin_agent_without_write_capability(tmp_path, monkeypatch):
    client, _, _ = _setup_decision_group(tmp_path, monkeypatch, explicit_executor=False)
    response = client.get("/test/proposals/change")
    assert '<option value="product"' not in response.text
    assert '<option value="engineer"' in response.text


def test_invalid_executor_rerenders_without_creating_decision(tmp_path, monkeypatch):
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "approved", "execution_agent": "sdk-agent"},
    )
    assert response.status_code == 400
    assert "does not support execution" in response.text
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()


def test_missing_execution_agent_blocks_get_and_post(tmp_path, monkeypatch):
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch, explicit_executor=False)
    get_response = client.get("/test/proposals/change")
    post_response = client.post("/test/proposals/change/decide", data={"answer_approve": "approved", "execution_agent": "engineer"})
    assert get_response.status_code == 200
    assert "execution_agent is required" in get_response.text
    assert post_response.status_code == 400
    assert "execution_agent is required" in post_response.text
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()


def test_invalid_answers_preserve_submitted_values_without_side_effects(tmp_path, monkeypatch):
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "deferred", "decision_note": "Keep this note", "execution_agent": "engineer"},
    )
    assert response.status_code == 400
    assert "requires Approve or Decline" in response.text
    assert "Keep this note" in response.text
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()


def test_ineligible_declared_executor_blocks_post_with_eligible_submitted_executor(tmp_path, monkeypatch):
    """POST must return 400 when the proposal's declared execution_agent is not eligible,
    even when the submitted form selects a different eligible executor."""
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    # Overwrite proposal to declare an ineligible executor
    proposal_path.write_text(
        "---\n" + yaml.safe_dump({
            "origin_agent": "product", "status": "proposed",
            "execution_agent": "sdk-agent",  # ineligible: sdk integration has no write capability
            "questions": [{"id": "approve", "type": "boolean", "prompt": "Proceed?"}],
        }, sort_keys=False) + "---\n\nProposal body\n"
    )
    submitted = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: submitted.append(spec))
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "approved", "execution_agent": "engineer"},
    )
    assert response.status_code == 400
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()
    assert submitted == []
