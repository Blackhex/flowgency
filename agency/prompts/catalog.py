from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Literal

from agency.configuration.issues import ValidationIssue
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
            raise ValueError(
                f"Prompt '{item.document.name}' exists in both blueprint and instance scopes for {group_id}/{agent_id}."
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
    group = snapshot.config.groups[group_id]
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


def validate_prompt_catalogs(
    snapshot: ConfigSnapshot,
    library: BlueprintLibrary,
    store: PromptStore,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for group_id, group in snapshot.config.groups.items():
        for agent_id in group.agents:
            try:
                effective_prompt_catalog(snapshot, library, store, group_id, agent_id)
            except PromptNotFoundError as exc:
                issues.append(
                    ValidationIssue(
                        code="missing-instance-prompt",
                        scope=f"groups.{group_id}.agents.{agent_id}",
                        field="prompts",
                        message=str(exc),
                        corrective_hint="Register only prompt names that exist in the configured prompt store.",
                    )
                )
            except ValueError as exc:
                issues.append(
                    ValidationIssue(
                        code="invalid-prompt-catalog",
                        scope=f"groups.{group_id}.agents.{agent_id}",
                        field="prompts",
                        message=str(exc),
                        corrective_hint="Use unique prompt names across blueprint and instance scopes.",
                    )
                )
    return tuple(issues)


__all__ = [
    "CatalogPrompt",
    "effective_prompt_catalog",
    "resolve_catalog_prompt",
    "validate_prompt_catalogs",
]