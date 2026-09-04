from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from typing import Literal

from agency.configuration.issues import ValidationFailed, ValidationIssue
from agency.configuration.store import ConfigSnapshot

from .assets import PromptDocument, prompt_source_path
from .store import PromptNotFoundError, PromptStore


if TYPE_CHECKING:
    from agency.blueprints.library import BlueprintLibrary


@dataclass(frozen=True)
class CatalogPrompt:
    scope: Literal["blueprint", "instance"]
    document: PromptDocument
    source_path: str


def _validate_effective_catalog(
    prompts: tuple[CatalogPrompt, ...],
    group_id: str,
    agent_id: str,
) -> tuple[CatalogPrompt, ...]:
    seen: dict[str, str] = {}
    for item in prompts:
        prior_scope = seen.get(item.document.name)
        if prior_scope is not None and prior_scope != item.scope:
            raise ValidationFailed(
                (
                    ValidationIssue(
                        code="invalid-prompt-catalog",
                        scope=f"groups.{group_id}.agents.{agent_id}",
                        field="prompts",
                        message=(
                            f"Prompt '{item.document.name}' exists in both blueprint and "
                            f"instance scopes for {group_id}/{agent_id}."
                        ),
                        corrective_hint="Use unique prompt names across blueprint and instance scopes.",
                    ),
                )
            )
        seen[item.document.name] = item.scope
    return prompts


def effective_prompt_catalog(
    snapshot: ConfigSnapshot,
    library: BlueprintLibrary,
    store: PromptStore,
    group_id: str,
    agent_id: str,
) -> tuple[CatalogPrompt, ...]:
    group = snapshot.config.teams[group_id]
    instance = group.agents[agent_id]
    inspection = library.inspect(instance.blueprint)
    shared = tuple(
        CatalogPrompt(
            "blueprint",
            document,
            prompt_source_path(document.name).as_posix(),
        )
        for document in inspection.prompts
    )
    private = tuple(
        CatalogPrompt(
            "instance",
            store.read(group_id, agent_id, name).document,
            str(store.path(group_id, agent_id, name)),
        )
        for name in instance.prompts
    )
    return _validate_effective_catalog(shared + private, group_id, agent_id)


def resolve_catalog_prompt(
    snapshot: ConfigSnapshot,
    library: BlueprintLibrary,
    store: PromptStore,
    group_id: str,
    agent_id: str,
    *,
    scope: Literal["blueprint", "instance"],
    name: str,
) -> CatalogPrompt:
    for item in effective_prompt_catalog(snapshot, library, store, group_id, agent_id):
        if item.scope == scope and item.document.name == name:
            return item
    raise KeyError(name)


def _agent_scoped(issue: ValidationIssue, group_id: str, agent_id: str) -> ValidationIssue:
    scope = f"groups.{group_id}.agents.{agent_id}"
    return issue if issue.scope == scope else replace(issue, scope=scope)


def validate_prompt_catalogs(
    snapshot: ConfigSnapshot,
    library: BlueprintLibrary,
    store: PromptStore,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for group_id, group in snapshot.config.teams.items():
        for agent_id in group.agents:
            try:
                effective_prompt_catalog(snapshot, library, store, group_id, agent_id)
            except PromptNotFoundError as exc:
                collected: tuple[ValidationIssue, ...] = (
                    ValidationIssue(
                        code="missing-instance-prompt",
                        scope=f"groups.{group_id}.agents.{agent_id}",
                        field="prompts",
                        message=str(exc),
                        corrective_hint="Register only prompt names that exist in the configured prompt store.",
                    ),
                )
            except ValidationFailed as exc:
                collected = tuple(_agent_scoped(issue, group_id, agent_id) for issue in exc.issues)
            else:
                continue
            for issue in collected:
                key = (issue.code, issue.field, issue.message)
                if key in seen:
                    continue
                seen.add(key)
                issues.append(issue)
    return tuple(issues)


__all__ = [
    "CatalogPrompt",
    "effective_prompt_catalog",
    "resolve_catalog_prompt",
    "validate_prompt_catalogs",
]
