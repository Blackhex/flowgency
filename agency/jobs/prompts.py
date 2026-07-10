"""Immutable prompt construction for decision-triggered jobs."""

import yaml


def build_decision_prompt(proposal_body: str, answers: dict) -> str:
    """Render a self-contained prompt snapshot for a decision job.

    Embeds the proposal body and answers directly so the worker never needs
    to re-read the proposal or decision files (which may change or be
    concurrently updated).
    """
    rendered_answers = yaml.safe_dump(answers, sort_keys=False).strip()
    return (
        "A human has decided this proposal. Act on the decision below.\n\n"
        "Proposal:\n"
        f"{proposal_body.strip()}\n\n"
        "Answers:\n"
        f"{rendered_answers}\n\n"
        "If approved or accepted, execute the proposed action. If deferred, "
        "acknowledge it without doing the deferred work. If rejected, close the "
        "loop without proceeding. Use choice and free-response answers as binding "
        "implementation guidance. Do not modify the Agency decision file."
    )
