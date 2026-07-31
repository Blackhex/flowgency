"""The reporting contract appended to every job's immutable task input."""

from __future__ import annotations

from agency.proposals import SUPPORTED_QUESTION_TYPES
from agency.records.validation import MAX_RECORD_BYTES, MAX_RECORDS_PER_KIND

from .outbox import (
    OUTBOX_RELATIVE_MEMORY,
    OUTBOX_RELATIVE_OBSERVATIONS,
    OUTBOX_RELATIVE_PROPOSALS,
)

_MARKER = "## Agency reporting protocol"


def _tool_sentence(tool_mode: str, tool_names: tuple[str, ...]) -> str:
    if tool_mode == "allowlist":
        granted = ", ".join(tool_names) if tool_names else "no tools"
        return (
            f"Your granted tool policy is an allowlist: {granted}. "
            "If a task needs a capability outside that list, say so explicitly "
            "and name the missing tool; do not attribute the refusal to any "
            "other cause."
        )
    if tool_mode == "none":
        return "You have been granted no tools."
    return "You have been granted all tools."


def build_reporting_protocol(
    *,
    tool_mode: str,
    tool_names: tuple[str, ...],
) -> str:
    sorted_types = ", ".join(sorted(SUPPORTED_QUESTION_TYPES))
    return "\n".join(
        [
            _MARKER,
            "",
            "Report findings by writing Markdown files into these directories,",
            "relative to your working directory. Agency validates and files them",
            "after the run; do not write anywhere else to record them.",
            "",
            f"- Observations: `{OUTBOX_RELATIVE_OBSERVATIONS}`",
            f"- Proposals: `{OUTBOX_RELATIVE_PROPOSALS}`",
            f"- Your memory: `{OUTBOX_RELATIVE_MEMORY}`",
            "",
            "Each record is one Markdown file with YAML front matter and a body.",
            "Open the body with a bold summary sentence; it becomes the title.",
            "Agency assigns the `agent`, `date`, and `status` fields and the file",
            "name, so anything you set for those is discarded.",
            "",
            "Record file requirements: each file must have a `.md` extension,",
            f"be under {MAX_RECORD_BYTES} bytes, contain valid UTF-8 text, and have",
            "a non-empty body. Write files directly into the listed directories;",
            f"subdirectories are rejected. At most {MAX_RECORDS_PER_KIND} records per",
            "directory; if you exceed this, Agency rejects the entire directory.",
            "",
            "A proposal additionally requires `execution_agent` naming a",
            "configured agent that is allowed to write, and a non-empty",
            "`questions` list whose entries each have a unique non-empty `id`,",
            f"a non-empty `prompt`, and a `type` in: {sorted_types}.",
            "A question of type `choice` additionally requires a non-empty `options`",
            "list.",
            "",
            "The memory directory is seeded with your current memory. Edit those",
            "files to change what you remember; leave them alone to keep it.",
            "",
            _tool_sentence(tool_mode, tool_names),
        ]
    )


def append_reporting_protocol(
    task_input: str,
    *,
    tool_mode: str,
    tool_names: tuple[str, ...],
) -> str:
    if _MARKER in task_input:
        return task_input
    protocol = build_reporting_protocol(tool_mode=tool_mode, tool_names=tool_names)
    if not task_input.strip():
        return protocol
    return f"{task_input.rstrip()}\n\n{protocol}"
