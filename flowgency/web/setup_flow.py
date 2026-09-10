from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import yaml

from flowgency.configuration import ConfigStore, ValidationFailed
from flowgency.integrations import BaseIntegration


SetupState = Literal["waiting", "invalid", "incomplete", "ready"]


@dataclass(frozen=True)
class SetupStatus:
    state: SetupState
    message: str = ""


def build_setup_prompt(
    data_root: Path,
    config_path: Path,
    *,
    selected_integration: str,
) -> str:
    return (
        "Use the flowgency-setup skill to configure Flowgency.\n"
        "Setup mode: guided-first-run.\n"
        f"Flowgency data root: {data_root.resolve()}.\n"
        f"Authoritative config: {config_path.resolve()}.\n"
        f"Selected integration: {selected_integration}.\n"
        "The Flowgency data root was selected in the browser; do not ask for it again. "
        "Ask for the first team project workspace as the first user-facing question. "
        "After the user selects it, inspect that project read-only before discussing "
        "the team. "
        "While planning the initial team, carry inspected project facts and every "
        "approved setup answer forward. Approve the team display name and stable "
        "team ID, then an initial positive agent count, before generating the first "
        "complete team draft with exactly that many profiles. Do not use a fixed "
        "role slate. Keep team drafts and revisions inside this interactive "
        "conversation. The canonical config remains the only setup completion "
        "output; do not emit or request a second application-side team payload. "
        "Communicate all required semantic categories in any clear layout; "
        "closely related optional categories may be combined; verify that the "
        "count of complete profiles equals the approved count and every required "
        "semantic category is identifiable before the team decision. When the "
        "team concept provides a clear theme, apply it to every display name "
        "and title. "
        "The project workspace remains source and execution context, the "
        "Flowgency data root remains Flowgency-owned storage, and the authoritative config "
        "remains at the supplied path. "
        "Use the selected integration for the team default_integration and the initial "
        "agent instances unless the user explicitly approves a different registered integration. "
        "By default derive flowgency.agent_library as <root>/agent-library, "
        "flowgency.compilation_cache as <root>/compiled-agents, flowgency.memory_store as "
        "<root>/memory, flowgency.prompt_store as <root>/prompts, and "
        "teams.<team-id>.path as <root>/teams/<team-id>. "
        "If the selected integration is copilot and the approved team will run ticket workflows "
        "in restricted mode, explicitly propose per-agent integration_config.allow_local_network "
        "before writing true. Use the Runtime label 'Allow local-network access' and explain "
        "'Allows connections to local services and LAN hosts, not only Flowgency.' If the user "
        "declines, leave the field absent or false and report ticket-local-network-required "
        "instead of silently widening policy. "
        "Configure "
        "schema_version: 1. Set flowgency.default_team to the approved team ID. "
        "Set each team workspace_path to its approved project "
        "execution workspace and path to a disjoint Flowgency-owned team root. Never "
        "create or reference a project-local shared directory. After one consolidated "
        "team approval, ask `Customize the derived storage paths?` once. Only if accepted, "
        "review all five derived paths together; otherwise do not ask about individual "
        "storage paths. Show one consolidated path summary and obtain approval before "
        "creating any derived directory or blueprint. "
        "The consolidated team approval covers complete operating profiles including "
        "integration, routines, runtime policy, workspaces, and memory for each agent. "
        "Storage paths are approved afterward in the separate grouped path review. "
        "Perform validation on the final config and make one atomic write for one "
        "complete configuration. Do not write a partial configuration."
    )


def launchable_integrations(
    integrations: Mapping[str, BaseIntegration],
    data_root: Path,
) -> tuple[BaseIntegration, ...]:
    resolved_data_root = Path(data_root).expanduser().resolve()
    candidates: list[tuple[bool, int, str, str, BaseIntegration]] = []
    for integration in integrations.values():
        if not integration.interactive_setup_available():
            continue
        detected = integration.detect(resolved_data_root)
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

    if not config.teams:
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
