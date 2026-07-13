"""Tests for immutable prompt construction with decision notes."""
from agency.jobs.prompts import build_decision_prompt


def test_decision_prompt_includes_note_and_decline_semantics():
    prompt = build_decision_prompt("Proposal body", {"approve": "declined"}, "Use the alternate path")
    assert "Decision note:\nUse the alternate path" in prompt
    assert "declined items" in prompt
    assert "deferred" not in prompt.lower()
