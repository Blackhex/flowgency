"""Immutable prompt construction for decision-triggered jobs."""

import yaml


def build_decision_prompt(proposal_body: str, answers: dict, decision_note: str = "") -> str:
    """Render a self-contained prompt snapshot for a decision job.

    Embeds the proposal body and answers directly so the worker never needs
    to re-read the proposal or decision files (which may change or be
    concurrently updated).
    """
    rendered_answers = yaml.safe_dump(answers, sort_keys=False).strip()
    note_section = f"\n\nDecision note:\n{decision_note.strip()}" if decision_note.strip() else ""
    return (
        "A human has decided this proposal. Act on the decision below.\n\n"
        "Proposal:\n"
        f"{proposal_body.strip()}\n\n"
        "Answers:\n"
        f"{rendered_answers}"
        f"{note_section}\n\n"
        "Execute approved items. Do not implement declined items. Use choice and "
        "open-ended answers plus the decision note as binding implementation guidance. "
        "Do not modify the Agency decision file."
    )
