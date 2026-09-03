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
    data_root: Path,
    config_path: Path,
    *,
    selected_integration: str,
) -> str:
    return (
        "Use the agency-setup skill to configure Agency.\n"
        "Setup mode: guided-first-run.\n"
        f"Agency data root: {data_root.resolve()}.\n"
        f"Authoritative config: {config_path.resolve()}.\n"
        f"Selected integration: {selected_integration}.\n"
        "The Agency data root was selected in the browser; do not ask for it again. "
        "Ask for the first group project workspace as the first user-facing question. "
        "After the user selects it, inspect that project read-only before discussing "
        "the team. "
        "While planning the initial team, carry inspected project facts and every "
        "approved setup answer forward. Approve the group display name and stable "
        "ID, then an initial positive agent count, before generating the first "
        "complete team draft with exactly that many profiles. Do not use a fixed "
        "role slate. Keep team drafts and revisions inside this interactive "
        "conversation. The canonical config remains the only setup completion "
        "output; do not emit or request a second application-side team payload. "
        "The project workspace remains source and execution context, the "
        "Agency data root remains Agency-owned storage, and the authoritative config "
        "remains at the supplied path. "
        "Use the selected integration for group.default_integration and the initial "
        "agent instances unless the user explicitly approves a different registered "
        "integration. By default derive agency.agent_library as <root>/agent-library, "
        "agency.compilation_cache as <root>/compiled-agents, agency.memory_store as "
        "<root>/memory, agency.prompt_store as <root>/prompts, and "
        "groups.<group-id>.path as <root>/groups/<group-id>. Configure "
        "schema_version: 5. Set each group workspace_path to its approved project "
        "execution workspace and path to a disjoint Agency-owned group root. Never "
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
