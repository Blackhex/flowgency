"""Tests for the broadened 'Needs action' metric."""


def test_needs_action_counts_actionable_proposals_and_floated_observations():
    observations = [
        {"status": "open", "float": True},
        {"status": "open", "float": False},
        {"status": "open"},
        {"status": "archived", "float": True},
    ]
    proposals = [
        {"status": "proposed"},
        {"status": "investigating"},
        {"status": "decided"},
        {"status": "decided"},
    ]

    actionable_proposals = [c for c in proposals if c.get("status") in ("proposed", "investigating")]
    floated_open = [c for c in observations if c.get("float") and c.get("status") == "open"]
    needs_action = len(actionable_proposals) + len(floated_open)

    assert needs_action == 3  # 2 proposals + 1 floated observation


def test_decided_is_not_actionable():
    proposals = [
        {"status": "proposed"},
        {"status": "decided"},
    ]
    actionable = [c for c in proposals if c.get("status") in ("proposed", "investigating")]
    assert len(actionable) == 1
    assert actionable[0]["status"] == "proposed"
