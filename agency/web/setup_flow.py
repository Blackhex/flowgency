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


def build_setup_prompt(project_dir: Path, config_path: Path) -> str:
    return (
        "Use the agency-setup skill to configure Agency for this project. "
        f"Project workspace: {project_dir.resolve()}. "
        f"Authoritative config: {config_path.resolve()}. "
        "Discuss and obtain approval for the group name, storage paths, agent team, "
        "integrations, routines, runtime policy, workspaces, and memory. Perform "
        "validation on the final config and make one atomic write for one complete configuration. Do not "
        "write a partial configuration."
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


def _concise_validation_error(exc: ValidationFailed) -> str:
    if exc.issues:
        return exc.issues[0].message
    return "Invalid setup configuration."


def _concise_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    message = message.splitlines()[0] if message else exc.__class__.__name__
    return message[:160]
