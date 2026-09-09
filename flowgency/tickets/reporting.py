from __future__ import annotations


_MARKER = "## Flowgency ticket reporting protocol"


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


def build_ticket_reporting_protocol(
    *,
    workflows_available: bool,
    tool_mode: str,
    tool_names: tuple[str, ...],
) -> str:
    lines = [
        _MARKER,
        "",
        "Use Flowgency ticket tools for work-item reporting. Inspect the current workflow",
        "and ticket before acting. Start work only on an unassigned ticket you claim or a",
        "ticket assigned to you. Evaluate the current project against transition rules;",
        "existing completed work may already satisfy them. Submit required outputs and an",
        "assessment for each criterion. Only an accepted transition changes ticket state.",
        "Assignment persists unless you choose to sign off. One run may work on several",
        "tickets. Write memory only in the provided memory directory.",
        "",
    ]
    if workflows_available:
        lines.extend(
            [
                "Configured workflows are available for this team. Use the ticket tools to inspect",
                "the current board state before deciding what to do next.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "This team currently has no configured workflows. Memory-only runs may still",
                "succeed when no ticket reporting is required.",
                "",
            ]
        )
    lines.append(_tool_sentence(tool_mode, tool_names))
    return "\n".join(lines)


def append_ticket_reporting_protocol(
    task_input: str,
    *,
    workflows_available: bool,
    tool_mode: str,
    tool_names: tuple[str, ...],
) -> str:
    if _MARKER in task_input:
        return task_input
    protocol = build_ticket_reporting_protocol(
        workflows_available=workflows_available,
        tool_mode=tool_mode,
        tool_names=tool_names,
    )
    if not task_input.strip():
        return protocol
    return f"{task_input.rstrip()}\n\n{protocol}"