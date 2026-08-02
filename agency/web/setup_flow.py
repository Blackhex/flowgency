from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import yaml

from agency.configuration import ConfigStore, ValidationFailed
from agency.integrations import BaseIntegration


SetupState = Literal["waiting", "invalid", "incomplete", "ready"]


@dataclass(frozen=True)
class SetupStatus:
    state: SetupState
    message: str = ""


def build_setup_prompt(
    project_dir: Path,
    config_path: Path,
    *,
    selected_integration: str,
) -> str:
    return (
        "Use the agency-setup skill to configure Agency for this project. "
        f"Project workspace: {project_dir.resolve()}. "
        f"Authoritative config: {config_path.resolve()}. "
        f"Selected integration: {selected_integration}. "
        "Use it for group.default_integration and the initial agent instances unless "
        "the user explicitly approves a different registered integration. "
        "After read-only project inspection, make the Agency data root the first user-facing question. "
        "Explain that it is a separate home for reusable agent blueprints, disposable compiled "
        "projections, semantic memory and durable jobs, and per-group records; the project "
        "workspace remains source and the authoritative config remains at the supplied path. "
        "Accept an existing directory or a new absolute path when its nearest existing parent "
        "is a writable real directory that can safely create it, and expand user-home syntax. "
        "Use C:\\Agency and ~/Agency as examples. "
        "By default derive agency.agent_library as <root>/agent-library, "
        "agency.compilation_cache as <root>/compiled-agents, agency.memory_store as "
        "<root>/memory, agency.prompt_store as <root>/prompts, and groups.<group-id>.path as <root>/groups/<group-id>. "
        "Configure schema_version: 5. For every group, set workspace_path to the project "
        "execution workspace and path to a disjoint Agency-owned group root. "
        "Never create or reference a project-local shared directory. "
        "After the group ID is approved, ask `Customize the derived storage paths?` once. "
        "Only if accepted, review all five derived paths together; otherwise do not ask about "
        "individual storage paths. Show one consolidated path summary and obtain approval "
        "before creating any directory or blueprint. Discuss and obtain approval for the group "
        "name, storage paths, agent team, integrations, routines, runtime policy, workspaces, "
        "and memory. Perform validation on the final config and make one atomic write for one "
        "complete configuration. Do not write a partial configuration."
    )


def launchable_integrations(
    integrations: Mapping[str, BaseIntegration],
    project_dir: Path,
) -> tuple[BaseIntegration, ...]:
    resolved_project_dir = Path(project_dir).expanduser().resolve()
    candidates: list[tuple[bool, int, str, str, BaseIntegration]] = []
    for integration in integrations.values():
        if not integration.interactive_setup_available():
            continue
        detected = integration.detect(resolved_project_dir)
        candidates.append(
            (
                not detected,
                integration.detect_priority,
                integration.display_name.lower(),
                integration.name,
                integration,
            )
        )
    candidates.sort()
    return tuple(integration for *_, integration in candidates)


def inspect_setup_status(store: ConfigStore) -> SetupStatus:
    snapshot = store.inspect()
    if not snapshot.exists:
        return SetupStatus(state="waiting")

    try:
        config = store.load().config
    except FileNotFoundError:
        return SetupStatus(state="waiting")
    except ValidationFailed as exc:
        return SetupStatus(state="invalid", message=_concise_validation_error(exc))
    except (OSError, TypeError, ValueError, yaml.YAMLError, UnicodeDecodeError) as exc:
        return SetupStatus(state="invalid", message=_concise_error_message(exc))

    if not config.groups:
        return SetupStatus(state="incomplete")
    return SetupStatus(state="ready")


def startup_error_status(error: Exception) -> SetupStatus:
    if isinstance(error, ValidationFailed):
        return SetupStatus(state="invalid", message=_concise_validation_error(error))
    return SetupStatus(
        state="invalid",
        message=f"Services could not start: {_concise_error_message(error)}",
    )


def _concise_validation_error(exc: ValidationFailed) -> str:
    if exc.issues:
        return exc.issues[0].message
    return "Invalid setup configuration."


def _concise_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    message = message.splitlines()[0] if message else exc.__class__.__name__
    return message[:160]
