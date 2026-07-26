from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re

import yaml

from agency.configuration.issues import ValidationIssue
from agency.fs.snapshot import AssetValidationError


PROMPT_PREFIX = PurePosixPath(".agents/prompts")
PROMPT_SUFFIX = ".prompt.md"
_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)
_ALLOWED_METADATA_KEYS = {"name", "description", "argument-hint"}


@dataclass(frozen=True)
class PromptDocument:
    name: str
    description: str
    argument_hint: str | None
    body: str
    source: bytes
    digest: str


def _raise_prompt(field: str, message: str, hint: str, *, code: str) -> None:
    raise AssetValidationError(
        (
            ValidationIssue(
                code=code,
                scope="prompt",
                field=field,
                message=message,
                corrective_hint=hint,
            ),
        )
    )


def prompt_source_path(name: str) -> PurePosixPath:
    return PROMPT_PREFIX / f"{name}{PROMPT_SUFFIX}"


def build_prompt_task_input(
    body: str,
    *,
    arguments: tuple[str, ...] = (),
    invocation_input: str = "",
) -> str:
    additions: list[str] = []
    if arguments:
        additions.append(yaml.safe_dump(list(arguments), sort_keys=False).strip())
    if invocation_input.strip():
        additions.append(invocation_input.strip())
    task = body.rstrip()
    return task if not additions else task + "\n\n## Invocation input\n\n" + "\n\n".join(additions)


def parse_prompt_document(path: str | PurePosixPath, payload: bytes) -> PromptDocument:
    source_path = PurePosixPath(path)
    field = source_path.as_posix()

    if source_path.parent != PROMPT_PREFIX or not source_path.name.endswith(PROMPT_SUFFIX):
        _raise_prompt(
            field,
            f"Prompt files are only allowed at {PROMPT_PREFIX.as_posix()}/<name>{PROMPT_SUFFIX}: {field}.",
            "Move the file to .agents/prompts and rename it to use the .prompt.md suffix.",
            code="invalid-prompt-location",
        )

    name = source_path.name[: -len(PROMPT_SUFFIX)]
    if not (1 <= len(name) <= 64) or not _IDENTIFIER.fullmatch(name):
        _raise_prompt(
            field,
            f"Prompt name must be a lowercase stable slug with 1-64 characters: {field}.",
            "Rename the prompt file using lowercase letters, digits, and single hyphen separators.",
            code="invalid-prompt-name",
        )

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        _raise_prompt(
            field,
            f"Prompt markdown must be valid UTF-8: {field}.",
            "Rewrite the prompt file using UTF-8 encoding.",
            code="invalid-prompt-encoding",
        )
        raise AssertionError("unreachable") from exc

    match = _FRONTMATTER.match(text)
    if match is None:
        _raise_prompt(
            field,
            f"Prompt markdown frontmatter is incomplete: {field}.",
            "Terminate the YAML frontmatter before the prompt body.",
            code="invalid-prompt-frontmatter",
        )

    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        _raise_prompt(
            field,
            f"Prompt markdown frontmatter is invalid YAML: {field}.",
            "Fix the YAML frontmatter in the prompt document.",
            code="invalid-prompt-frontmatter",
        )
        raise AssertionError("unreachable") from exc

    if not isinstance(metadata, dict):
        _raise_prompt(
            field,
            f"Prompt frontmatter must be a mapping: {field}.",
            "Set prompt frontmatter to a YAML mapping with name and description.",
            code="invalid-prompt-frontmatter",
        )

    unknown_keys = set(metadata) - _ALLOWED_METADATA_KEYS
    if unknown_keys:
        _raise_prompt(
            field,
            f"Prompt frontmatter contains unsupported keys: {field}.",
            "Only name, description, and argument-hint are supported in prompt frontmatter.",
            code="invalid-prompt-frontmatter",
        )

    declared_name = metadata.get("name")
    if declared_name != name:
        _raise_prompt(
            field,
            f"Prompt name must exactly match the file slug: {field}.",
            "Update the prompt frontmatter name to match the file name exactly.",
            code="prompt-name-mismatch",
        )

    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        _raise_prompt(
            field,
            f"Prompt description is required: {field}.",
            "Set a non-empty description in prompt frontmatter.",
            code="missing-prompt-description",
        )
    if len(description) > 1024:
        _raise_prompt(
            field,
            f"Prompt description must be at most 1024 characters: {field}.",
            "Shorten the prompt description to 1024 characters or fewer.",
            code="description-too-long",
        )

    argument_hint = metadata.get("argument-hint")
    if argument_hint is not None and not isinstance(argument_hint, str):
        _raise_prompt(
            field,
            f"Prompt argument-hint must be a string or null: {field}.",
            "Set argument-hint to a string or omit it.",
            code="invalid-prompt-argument-hint",
        )

    body = match.group(2)
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    if not body.strip():
        _raise_prompt(
            field,
            f"Prompt body is required: {field}.",
            "Add non-empty markdown content after the frontmatter.",
            code="missing-prompt-body",
        )

    return PromptDocument(
        name=name,
        description=description,
        argument_hint=argument_hint,
        body=body,
        source=payload,
        digest=hashlib.sha256(payload).hexdigest(),
    )


__all__ = [
    "PromptDocument",
    "build_prompt_task_input",
    "parse_prompt_document",
    "prompt_source_path",
]