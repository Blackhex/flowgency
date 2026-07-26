from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from agency.fs.snapshot import AssetValidationError
from agency.prompts.assets import build_prompt_task_input, parse_prompt_document


def prompt_bytes(name: str = "pr-review", body: str = "Review the pull request.\n") -> bytes:
    return (
        f"---\nname: {name}\ndescription: Review pull requests.\n"
        "argument-hint: Optional review focus\n---\n\n"
        f"{body}"
    ).encode("utf-8")


def test_parse_prompt_document_returns_portable_metadata_and_digest():
    document = parse_prompt_document(
        PurePosixPath(".agents/prompts/pr-review.prompt.md"),
        prompt_bytes(),
    )

    assert document.name == "pr-review"
    assert document.description == "Review pull requests."
    assert document.argument_hint == "Optional review focus"
    assert document.body == "Review the pull request.\n"
    assert len(document.digest) == 64


@pytest.mark.parametrize(
    ("path", "payload", "code"),
    [
        (".agents/prompts/Bad.prompt.md", prompt_bytes("Bad"), "invalid-prompt-name"),
        (".agents/prompts/pr-review.md", prompt_bytes(), "invalid-prompt-location"),
        (".agents/prompts/pr-review.prompt.md", prompt_bytes("other"), "prompt-name-mismatch"),
        (".agents/prompts/pr-review.prompt.md", prompt_bytes(body="  \n"), "missing-prompt-body"),
        (".agents/prompts/pr-review.prompt.md", b"\xff\xfe", "invalid-prompt-encoding"),
    ],
)
def test_parse_prompt_document_rejects_noncanonical_source(path, payload, code):
    with pytest.raises(AssetValidationError) as excinfo:
        parse_prompt_document(path, payload)

    assert excinfo.value.issues[0].code == code


def test_build_prompt_task_input_appends_one_deterministic_section():
    assert build_prompt_task_input(
        "Review now.\n",
        arguments=("security", "correctness"),
        invocation_input="Focus on PR 42.",
    ) == (
        "Review now.\n\n## Invocation input\n\n"
        "- security\n- correctness\n\nFocus on PR 42."
    )