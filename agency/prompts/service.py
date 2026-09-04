from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agency.configuration import (
    ConfigSnapshot,
    ConfigStore,
    ValidationFailed,
    ValidationIssue,
    register_agent_prompt,
    unregister_agent_prompt,
)

from .assets import PromptDocument
from .catalog import CatalogPrompt, effective_prompt_catalog
from .store import PromptConflictError, PromptNotFoundError, PromptStore


if TYPE_CHECKING:
    from agency.blueprints.library import BlueprintLibrary


@dataclass(frozen=True)
class PromptMutationResult:
    snapshot: ConfigSnapshot
    document: PromptDocument
    orphaned_path: Path | None = None


class PromptService:
    def __init__(
        self,
        *,
        config_store: ConfigStore,
        library: BlueprintLibrary,
        store: PromptStore,
    ):
        self.config_store = config_store
        self.library = library
        self.store = store

    def catalog(
        self,
        snapshot: ConfigSnapshot | None,
        group_id: str,
        agent_id: str,
    ) -> tuple[CatalogPrompt, ...]:
        current = snapshot or self.config_store.load()
        return effective_prompt_catalog(
            current,
            self.library,
            self.store,
            group_id,
            agent_id,
        )

    def create_private(
        self,
        group_id: str,
        agent_id: str,
        prompt_name: str,
        payload: bytes,
        *,
        expected_revision: str,
    ) -> PromptMutationResult:
        created = self.store.create(group_id, agent_id, prompt_name, payload)
        try:
            snapshot = register_agent_prompt(
                self.config_store,
                expected_revision,
                group_id,
                agent_id,
                prompt_name,
            )
        except Exception:
            try:
                self.store.delete(
                    group_id,
                    agent_id,
                    prompt_name,
                    expected_digest=created.document.digest,
                )
            except (PromptConflictError, PromptNotFoundError):
                pass
            raise
        return PromptMutationResult(snapshot=snapshot, document=created.document)

    def update_private(
        self,
        group_id: str,
        agent_id: str,
        prompt_name: str,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> PromptMutationResult:
        snapshot = self.config_store.load()
        updated = self.store.update(
            group_id,
            agent_id,
            prompt_name,
            expected_digest=expected_digest,
            payload=payload,
        )
        return PromptMutationResult(snapshot=snapshot, document=updated.document)

    def delete_private(
        self,
        group_id: str,
        agent_id: str,
        prompt_name: str,
        *,
        expected_revision: str,
        expected_digest: str,
    ) -> PromptMutationResult:
        snapshot = self.config_store.load()
        agent = snapshot.config.teams[group_id].agents[agent_id]
        if prompt_name not in agent.prompts:
            raise PromptNotFoundError(
                f"prompt not registered: {group_id}/{agent_id}/{prompt_name}"
            )
        for routine in agent.routines:
            if (
                routine.prompt.scope == "instance"
                and routine.prompt.name == prompt_name
            ):
                raise ValidationFailed(
                    (
                        ValidationIssue(
                            code="prompt-in-use",
                            scope=f"groups.{group_id}.agents.{agent_id}.routines.{routine.id}",
                            field="prompt",
                            message=(
                                f"Prompt '{prompt_name}' is still referenced by routine '{routine.id}'."
                            ),
                            corrective_hint=(
                                "Update or remove the routine prompt reference before deleting the prompt."
                            ),
                        ),
                    )
                )
        stored = self.store.read(group_id, agent_id, prompt_name)
        updated = unregister_agent_prompt(
            self.config_store,
            expected_revision,
            group_id,
            agent_id,
            prompt_name,
        )
        try:
            self.store.delete(
                group_id,
                agent_id,
                prompt_name,
                expected_digest=expected_digest,
            )
            orphaned_path = None
        except Exception:
            orphaned_path = self.store.path(group_id, agent_id, prompt_name)
        return PromptMutationResult(
            snapshot=updated,
            document=stored.document,
            orphaned_path=orphaned_path,
        )


__all__ = ["PromptMutationResult", "PromptService"]