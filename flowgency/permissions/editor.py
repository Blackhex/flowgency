from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flowgency.configuration import ConfigConflictError, ConfigSnapshot, ConfigStore, ValidationFailed, parse_config
from flowgency.configuration.issues import ValidationIssue
from flowgency.integrations import get_integration
from flowgency.integrations.tool_catalog import ToolCatalog, catalog_id, get_tool_catalog
from flowgency.permissions.forms import PermissionDraft, PermissionForm, build_form, serialize_permissions
from flowgency.permissions.presentation import PermissionSummary, present_permissions


_CATALOG_CONFLICT_MESSAGE = "Permission tool catalog changed; reload before saving"


class EditorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    revision: str = Field(min_length=1)
    catalog_id: str = Field(min_length=1)
    draft_version: int = Field(ge=0)
    draft: PermissionDraft


class CatalogConflictError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedPermissions:
    candidate: dict[str, Any]
    form: PermissionForm
    summary: PermissionSummary | None
    catalog: ToolCatalog
    issues: tuple[ValidationIssue, ...] = ()


def load_editor(
    snapshot: ConfigSnapshot, team_id: str, agent_id: str
) -> PreparedPermissions:
    instance = snapshot.config.teams[team_id].agents[agent_id]
    catalog = get_tool_catalog(get_integration(instance.integration))
    agent_raw = _find_agent_raw(snapshot.raw, team_id, agent_id)
    form = build_form(agent_raw, catalog)
    try:
        summary = present_permissions(snapshot.config, team_id, agent_id)
    except ValidationFailed as exc:
        return PreparedPermissions(
            candidate=deepcopy(snapshot.raw),
            form=form,
            summary=None,
            catalog=catalog,
            issues=exc.issues,
        )
    return PreparedPermissions(
        candidate=deepcopy(snapshot.raw),
        form=form,
        summary=summary,
        catalog=catalog,
    )


def prepare_permissions(
    snapshot: ConfigSnapshot,
    team_id: str,
    agent_id: str,
    request: EditorRequest,
    catalog: ToolCatalog,
) -> PreparedPermissions:
    if request.revision != snapshot.revision:
        raise ConfigConflictError("config.yaml changed; reload before saving")
    if request.catalog_id != catalog_id(catalog):
        raise CatalogConflictError(_CATALOG_CONFLICT_MESSAGE)

    candidate = deepcopy(snapshot.raw)
    agent_raw = _find_agent_raw(candidate, team_id, agent_id)
    serialized = serialize_permissions(agent_raw, request.draft, catalog)
    if serialized is None:
        agent_raw.pop("permissions", None)
    else:
        agent_raw["permissions"] = serialized

    config = parse_config(candidate, snapshot.path).resolved
    summary = present_permissions(config, team_id, agent_id)
    return PreparedPermissions(
        candidate=candidate,
        form=build_form(agent_raw, catalog),
        summary=summary,
        catalog=catalog,
    )


def save_permissions(
    store: ConfigStore,
    team_id: str,
    agent_id: str,
    request: EditorRequest,
) -> ConfigSnapshot:
    def patcher(raw: dict[str, Any]) -> None:
        current = parse_config(raw, store.path).resolved
        snapshot = ConfigSnapshot(store.path, request.revision, raw, current)
        instance = current.teams[team_id].agents[agent_id]
        catalog = get_tool_catalog(get_integration(instance.integration))
        prepared = prepare_permissions(snapshot, team_id, agent_id, request, catalog)
        original_agent = _find_agent_raw(raw, team_id, agent_id)
        candidate_agent = _find_agent_raw(prepared.candidate, team_id, agent_id)
        if "permissions" in candidate_agent:
            original_agent["permissions"] = deepcopy(candidate_agent["permissions"])
        else:
            original_agent.pop("permissions", None)

    return store.patch(request.revision, patcher)


def _find_agent_raw(raw: dict[str, Any], team_id: str, agent_id: str) -> dict[str, Any]:
    team = raw["teams"][team_id]
    for entry in team["agents"]:
        if entry.get("name") == agent_id:
            return entry
    raise KeyError(f"Unknown agent: {agent_id}")