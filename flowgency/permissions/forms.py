from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from flowgency.configuration.issues import ValidationFailed, ValidationIssue
from flowgency.integrations.tool_catalog import RuleTarget, ToolCatalog, available_names


class RuleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_index: int | None = None
    target: RuleTarget
    path: str | None = None
    selected: list[str]


class PermissionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["inherit", "restricted", "unrestricted"]
    rules: list[RuleDraft]


@dataclass(frozen=True)
class PermissionForm:
    draft: PermissionDraft
    choices: tuple[tuple[str, ...], ...]
    unbounded: tuple[bool, ...]


class PermissionFormError(ValidationFailed):
    pass


def selected_for_rule(rule: dict[str, Any], choices: tuple[str, ...]) -> list[str]:
    tools = rule.get("tools")
    return list(choices) if tools is None else list(dict.fromkeys(tools))


def encode_selection(selected: list[str], catalog: ToolCatalog) -> dict[str, Any]:
    if not selected:
        return {"tools": []}
    whole_catalog = {tool.name for tool in catalog.tools}
    if catalog.complete and whole_catalog and set(selected) == whole_catalog:
        return {}
    return {"tools": list(selected)}


def build_form(agent_raw: dict[str, Any], catalog: ToolCatalog) -> PermissionForm:
    permissions = _permissions_block(agent_raw)
    rules = _raw_rules(permissions)
    draft_rules: list[RuleDraft] = []
    choices: list[tuple[str, ...]] = []
    unbounded: list[bool] = []

    for index, rule in enumerate(rules):
        target = _rule_target(rule)
        rule_choices = _rule_choices(rule, catalog, target)
        draft_rules.append(
            RuleDraft(
                source_index=index,
                target=target,
                path=rule.get("path") if target == "path" else None,
                selected=selected_for_rule(rule, rule_choices),
            )
        )
        choices.append(rule_choices)
        unbounded.append(rule.get("tools") is None)

    mode = permissions.get("mode") if permissions.get("mode") in {"restricted", "unrestricted"} else "inherit"
    return PermissionForm(
        draft=PermissionDraft(mode=mode, rules=draft_rules),
        choices=tuple(choices),
        unbounded=tuple(unbounded),
    )


def serialize_permissions(
    agent_raw: dict[str, Any],
    draft: PermissionDraft,
    catalog: ToolCatalog,
) -> dict[str, Any] | None:
    permissions = _permissions_block(agent_raw)
    original_present = "permissions" in agent_raw
    original_rules = _raw_rules(permissions)
    baseline = build_form(agent_raw, catalog).draft

    issues = _validate_draft(draft, original_rules)
    if issues:
        raise PermissionFormError(issues)

    if draft == baseline:
        return deepcopy(permissions) if original_present else None

    serialized_rules: list[dict[str, Any]] = []
    new_rules: list[dict[str, Any]] = []

    for rule in draft.rules:
        if rule.source_index is None:
            new_rules.append(_serialize_new_rule(rule, catalog))
            continue
        serialized_rules.append(
            _serialize_existing_rule(
                original_rule=original_rules[rule.source_index],
                baseline_rule=baseline.rules[rule.source_index],
                draft_rule=rule,
                catalog=catalog,
            )
        )

    serialized_rules.extend(new_rules)

    serialized: dict[str, Any] = {}
    if draft.mode != "inherit":
        serialized["mode"] = draft.mode

    if draft.rules or "rules" in permissions:
        serialized["rules"] = serialized_rules

    if not serialized and not original_present:
        return None
    return serialized


def _serialize_existing_rule(
    original_rule: dict[str, Any],
    baseline_rule: RuleDraft,
    draft_rule: RuleDraft,
    catalog: ToolCatalog,
) -> dict[str, Any]:
    path_changed = (draft_rule.target, draft_rule.path) != (baseline_rule.target, baseline_rule.path)
    selected_changed = set(draft_rule.selected) != set(baseline_rule.selected)

    if not path_changed and not selected_changed:
        return deepcopy(original_rule)

    if selected_changed:
        serialized = _path_payload(draft_rule, original_rule, path_changed)
        serialized.update(encode_selection(draft_rule.selected, catalog))
        return serialized

    serialized = deepcopy(original_rule)
    _apply_path_change(serialized, draft_rule)
    return serialized


def _serialize_new_rule(rule: RuleDraft, catalog: ToolCatalog) -> dict[str, Any]:
    serialized = _path_payload(rule, {}, True)
    serialized.update(encode_selection(rule.selected, catalog))
    return serialized


def _path_payload(
    draft_rule: RuleDraft,
    original_rule: dict[str, Any],
    path_changed: bool,
) -> dict[str, Any]:
    if draft_rule.target == "no_path":
        return {}
    if not path_changed and "path" in original_rule:
        return {"path": deepcopy(original_rule["path"])}
    return {"path": draft_rule.path}


def _apply_path_change(serialized: dict[str, Any], draft_rule: RuleDraft) -> None:
    if draft_rule.target == "no_path":
        serialized.pop("path", None)
        return
    serialized["path"] = draft_rule.path


def _validate_draft(
    draft: PermissionDraft,
    original_rules: list[dict[str, Any]],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_indices: set[int] = set()
    last_source_index = -1

    for index, rule in enumerate(draft.rules):
        field_prefix = f"rules.{index}"
        source_index = rule.source_index
        if source_index is not None:
            source_field = f"{field_prefix}.source_index"
            if source_index < 0:
                issues.append(_issue(source_field, "Source index must be nonnegative."))
                continue
            if source_index >= len(original_rules):
                issues.append(_issue(source_field, "Source index is out of range."))
                continue
            if source_index in seen_indices:
                issues.append(_issue(source_field, "Source index must be unique."))
                continue
            if source_index < last_source_index:
                issues.append(_issue(source_field, "Original rules must stay in source order."))
                continue
            seen_indices.add(source_index)
            last_source_index = source_index

        path_field = f"{field_prefix}.path"
        if rule.target == "no_path":
            if rule.path is not None:
                issues.append(_issue(path_field, "Pathless rules must not include a path."))
        elif rule.path is None or rule.path.strip() == "":
            issues.append(_issue(path_field, "Path rules must include a nonblank path."))

        selected_field = f"{field_prefix}.selected"
        seen_names: set[str] = set()
        for name in rule.selected:
            if not isinstance(name, str) or name == "":
                issues.append(_issue(selected_field, "Selected tool names must be non-empty strings."))
                break
            if name in seen_names:
                issues.append(_issue(selected_field, "Selected tool names must be unique."))
                break
            seen_names.add(name)

    return issues


def _issue(field: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        code="invalid-permission-form",
        scope=field.rsplit(".", 1)[0],
        field=field,
        message=message,
        corrective_hint="Correct the permission form field and try again.",
    )


def _permissions_block(agent_raw: dict[str, Any]) -> dict[str, Any]:
    permissions = agent_raw.get("permissions")
    if isinstance(permissions, Mapping):
        return dict(permissions)
    return {}


def _raw_rules(permissions: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_rules = permissions.get("rules")
    if not isinstance(raw_rules, list):
        return []
    return [dict(rule) for rule in raw_rules if isinstance(rule, Mapping)]


def _rule_target(rule: Mapping[str, Any]) -> RuleTarget:
    return "path" if rule.get("path") is not None else "no_path"


def _rule_choices(
    rule: Mapping[str, Any],
    catalog: ToolCatalog,
    target: RuleTarget,
) -> tuple[str, ...]:
    names = list(available_names(catalog, target))
    seen = set(names)
    tools = rule.get("tools")
    if isinstance(tools, list):
        for name in tools:
            if isinstance(name, str) and name not in seen:
                names.append(name)
                seen.add(name)
    return tuple(names)