from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from flowgency.configuration.issues import ValidationFailed, ValidationIssue
from flowgency.dispatch.schedule import last_at_occurrence, parse_catch_up, parse_every

_AT_REFERENCE = datetime(2026, 1, 1, 12, 0)
_KEY_MAX_LENGTH = 128


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScheduleDraft(DraftModel):
    mode: Literal["every", "at"]
    amount: str = ""
    unit: Literal["m", "h", "d"] = "d"
    time: str = ""


class RecoveryDraft(DraftModel):
    mode: Literal["default", "none", "today", "always", "duration"] = "default"
    amount: str = ""
    unit: Literal["m", "h", "d"] = "h"


class MemoryDraft(DraftModel):
    scope: Literal["inherit", "run", "routine", "agent", "team", "channel"] = "inherit"
    channel: str = ""


class RoutineDraft(DraftModel):
    key: str
    source_index: int | None = None
    id: str
    prompt_scope: Literal["blueprint", "instance"]
    prompt_name: str
    enabled: bool = True
    arguments: list[str]
    schedule: ScheduleDraft
    recovery: RecoveryDraft
    memory: MemoryDraft


class RoutinesDraft(DraftModel):
    routines: list[RoutineDraft]


@dataclass(frozen=True)
class RoutineForm:
    draft: RoutinesDraft
    warnings: tuple[ValidationIssue, ...] = ()


class RoutineFormError(ValidationFailed):
    pass


def build_form(agent_raw: dict[str, Any]) -> RoutineForm:
    raw_routines = _raw_routines(agent_raw)
    routines: list[RoutineDraft] = []
    warnings: list[ValidationIssue] = []

    for index, row in enumerate(raw_routines):
        schedule = _mapping(row.get("schedule"))
        schedule_draft, schedule_warnings = _decode_schedule_with_warnings(schedule)
        recovery_draft, recovery_warnings = _decode_recovery_with_warnings(schedule)
        warnings.extend(
            _retarget_issues(schedule_warnings, f"routines.{index}.schedule")
        )
        warnings.extend(
            _retarget_issues(recovery_warnings, f"routines.{index}.recovery")
        )

        prompt = _mapping(row.get("prompt"))
        memory = _mapping_or_none(row.get("memory"))

        routines.append(
            RoutineDraft(
                key=f"saved-{index}",
                source_index=index,
                id=_string(row.get("id")),
                prompt_scope=_prompt_scope(prompt.get("scope")),
                prompt_name=_string(prompt.get("name")),
                enabled=_enabled_value(row.get("enabled")),
                arguments=_arguments_value(row.get("arguments")),
                schedule=schedule_draft,
                recovery=recovery_draft,
                memory=_decode_memory(memory),
            )
        )

    return RoutineForm(draft=RoutinesDraft(routines=routines), warnings=tuple(warnings))


def decode_schedule(raw: dict[str, Any]) -> ScheduleDraft:
    return _decode_schedule_with_warnings(raw)[0]


def decode_recovery(raw: dict[str, Any]) -> RecoveryDraft:
    return _decode_recovery_with_warnings(raw)[0]


def encode_schedule(draft: ScheduleDraft) -> dict[str, str]:
    if draft.mode == "every":
        if draft.amount == "":
            raise ValueError("Schedule interval amount is required.")
        if not draft.amount.isdigit():
            raise ValueError("Schedule interval amount must use digits only.")
        encoded = f"{draft.amount}{draft.unit}"
        period = parse_every(encoded)
        if period is None or period.total_seconds() <= 0:
            raise ValueError("Schedule interval must be a positive duration.")
        return {"every": encoded}

    if draft.time == "":
        raise ValueError("Schedule time is required.")
    if last_at_occurrence(draft.time, _AT_REFERENCE) is None:
        raise ValueError("Schedule time must use 24-hour HH:MM format.")
    return {"at": draft.time}


def encode_recovery(draft: RecoveryDraft) -> str | None:
    if draft.mode == "default":
        return None
    if draft.mode in {"none", "today", "always"}:
        return draft.mode
    if draft.amount == "":
        raise ValueError("Recovery duration amount is required.")
    if not draft.amount.isdigit():
        raise ValueError("Recovery duration amount must use digits only.")
    encoded = f"{draft.amount}{draft.unit}"
    if parse_catch_up(encoded) is None:
        raise ValueError("Recovery duration must use the accepted duration grammar.")
    return encoded


def serialize_routines(
    agent_raw: dict[str, Any],
    draft: RoutinesDraft,
) -> list[dict[str, Any]] | None:
    original_present = "routines" in agent_raw
    original_routines = _raw_routines(agent_raw)
    baseline = build_form(agent_raw).draft

    validated, issues = _validated_draft(draft)
    if issues:
        raise RoutineFormError(issues)
    issues.extend(_validate_draft(validated, original_routines, baseline))
    if issues:
        raise RoutineFormError(issues)

    if validated == baseline:
        return deepcopy(original_routines) if original_present else None

    serialized: list[dict[str, Any]] = []
    for index, routine in enumerate(validated.routines):
        original_row = None
        baseline_row = None
        if routine.source_index is not None:
            original_row = original_routines[routine.source_index]
            baseline_row = baseline.routines[routine.source_index]
        serialized.append(_serialize_routine(index, routine, original_row, baseline_row))

    return serialized


def _decode_schedule_with_warnings(
    raw: Mapping[str, Any],
) -> tuple[ScheduleDraft, list[ValidationIssue]]:
    warnings: list[ValidationIssue] = []
    if raw.get("at") is not None:
        at_value = _string(raw.get("at"))
        occurrence = last_at_occurrence(at_value, _AT_REFERENCE)
        if occurrence is None:
            warnings.append(_warning("schedule", f"Unsupported saved daily time: {at_value}"))
            return ScheduleDraft(mode="at", time=""), warnings
        return ScheduleDraft(mode="at", time=occurrence.strftime("%H:%M")), warnings

    every_value = _string(raw.get("every"))
    unit = every_value[-1] if every_value[-1:] in {"m", "h", "d"} else "d"
    period = parse_every(every_value)
    if period is None:
        if every_value != "":
            warnings.append(_warning("schedule", f"Unsupported saved interval: {every_value}"))
        return ScheduleDraft(mode="every", amount="", unit=unit), warnings
    return ScheduleDraft(mode="every", amount=every_value[:-1], unit=unit), warnings


def _decode_recovery_with_warnings(
    raw: Mapping[str, Any],
) -> tuple[RecoveryDraft, list[ValidationIssue]]:
    warnings: list[ValidationIssue] = []
    if "catch_up" not in raw or raw.get("catch_up") is None:
        return RecoveryDraft(mode="default"), warnings

    catch_up = _string(raw.get("catch_up"))
    if catch_up in {"", "today"}:
        return RecoveryDraft(mode="today" if catch_up == "today" else "default"), warnings
    if catch_up in {"none", "always"}:
        return RecoveryDraft(mode=catch_up), warnings

    parsed = parse_catch_up(catch_up)
    unit = catch_up[-1] if catch_up[-1:] in {"m", "h", "d"} else "h"
    if parsed is None or parsed.kind != "duration":
        warnings.append(_warning("recovery", f"Unsupported saved recovery: {catch_up}"))
        return RecoveryDraft(mode="duration", amount="", unit=unit), warnings
    return RecoveryDraft(mode="duration", amount=catch_up[:-1], unit=unit), warnings


def _serialize_routine(
    index: int,
    routine: RoutineDraft,
    original_row: dict[str, Any] | None,
    baseline_row: RoutineDraft | None,
) -> dict[str, Any]:
    target = deepcopy(original_row) if original_row is not None else {}

    preserve_or_replace(target, original_row, "id", baseline_row.id if baseline_row else None, routine.id, routine.id)

    prompt_payload = {"scope": routine.prompt_scope, "name": routine.prompt_name}
    preserve_or_replace(
        target,
        original_row,
        "prompt",
        _prompt_baseline(baseline_row),
        prompt_payload,
        prompt_payload,
    )

    preserve_or_replace(
        target,
        original_row,
        "enabled",
        baseline_row.enabled if baseline_row else None,
        routine.enabled,
        _encode_enabled(routine.enabled, original_row is None),
    )

    preserve_or_replace(
        target,
        original_row,
        "arguments",
        baseline_row.arguments if baseline_row else None,
        routine.arguments,
        _encode_arguments(routine.arguments, original_row is None),
    )

    _serialize_schedule_field(target, original_row, baseline_row, routine, index)

    preserve_or_replace(
        target,
        original_row,
        "memory",
        baseline_row.memory if baseline_row else None,
        routine.memory,
        _encode_memory(routine.memory),
    )

    return target


def _serialize_schedule_field(
    target: dict[str, Any],
    original_row: dict[str, Any] | None,
    baseline_row: RoutineDraft | None,
    routine: RoutineDraft,
    index: int,
) -> None:
    schedule_changed = baseline_row is None or routine.schedule != baseline_row.schedule
    recovery_changed = baseline_row is None or routine.recovery != baseline_row.recovery
    if not schedule_changed and not recovery_changed:
        return

    schedule_payload = deepcopy(_mapping((original_row or {}).get("schedule")))

    if schedule_changed:
        try:
            encoded_schedule = encode_schedule(routine.schedule)
        except ValueError as exc:
            raise RoutineFormError([
                _issue(
                    f"routines.{index}.schedule",
                    str(exc),
                    "Correct the schedule controls and try again.",
                )
            ]) from exc
        schedule_payload.pop("at", None)
        schedule_payload.pop("every", None)
        schedule_payload.update(encoded_schedule)

    if recovery_changed:
        try:
            encoded_recovery = encode_recovery(routine.recovery)
        except ValueError as exc:
            raise RoutineFormError([
                _issue(
                    f"routines.{index}.recovery",
                    str(exc),
                    "Correct the recovery controls and try again.",
                )
            ]) from exc
        if encoded_recovery is None:
            schedule_payload.pop("catch_up", None)
        else:
            schedule_payload["catch_up"] = encoded_recovery

    target["schedule"] = schedule_payload


def preserve_or_replace(
    target: dict[str, Any],
    original: dict[str, Any] | None,
    field: str,
    before: Any,
    after: Any,
    encoded: Any,
) -> None:
    if original is not None and before == after:
        return
    if encoded is None:
        target.pop(field, None)
    else:
        target[field] = deepcopy(encoded)


def _validated_draft(draft: RoutinesDraft) -> tuple[RoutinesDraft, list[ValidationIssue]]:
    try:
        payload = draft.model_dump(warnings=False)
        return RoutinesDraft.model_validate(payload), []
    except ValidationError as exc:
        return draft, [_pydantic_issue(error) for error in exc.errors()]


def _validate_draft(
    draft: RoutinesDraft,
    original_routines: list[dict[str, Any]],
    baseline: RoutinesDraft,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    seen_source_indices: set[int] = set()

    for index, routine in enumerate(draft.routines):
        field_prefix = f"routines.{index}"

        if routine.key.strip() == "":
            issues.append(_issue(f"{field_prefix}.key", "Row key must be nonblank.", "Regenerate or replace the row key."))
        elif len(routine.key) > _KEY_MAX_LENGTH:
            issues.append(_issue(f"{field_prefix}.key", "Row key is too long.", "Keep the row key within the supported length."))
        elif routine.key in seen_keys:
            issues.append(_issue(f"{field_prefix}.key", "Row key must be unique.", "Give each routine row its own key."))
        else:
            seen_keys.add(routine.key)

        if routine.source_index is not None:
            if isinstance(routine.source_index, bool) or not isinstance(routine.source_index, int):
                issues.append(_issue(f"{field_prefix}.source_index", "Source index must be an integer.", "Use the original integer row index or omit it for new rows."))
            elif routine.source_index < 0:
                issues.append(_issue(f"{field_prefix}.source_index", "Source index must be nonnegative.", "Use the original integer row index or omit it for new rows."))
            elif routine.source_index >= len(original_routines):
                issues.append(_issue(f"{field_prefix}.source_index", "Source index is out of range.", "Use an existing source index or omit it for new rows."))
            elif routine.source_index in seen_source_indices:
                issues.append(_issue(f"{field_prefix}.source_index", "Source index must be unique.", "Keep each saved row bound to at most one draft row."))
            else:
                seen_source_indices.add(routine.source_index)

        if routine.id.strip() == "":
            issues.append(_issue(f"{field_prefix}.id", "Routine id is required.", "Provide a stable routine slug."))
        elif routine.id in seen_ids:
            issues.append(_issue(f"{field_prefix}.id", f"Duplicate routine id: {routine.id}", "Keep each routine id unique within the agent."))
        else:
            seen_ids.add(routine.id)

        if routine.prompt_name.strip() == "":
            issues.append(_issue(f"{field_prefix}.prompt_name", "Prompt name is required.", "Select a saved prompt name."))

        baseline_row = None
        if routine.source_index is not None and routine.source_index < len(baseline.routines):
            baseline_row = baseline.routines[routine.source_index]

        if routine.source_index is None or (baseline_row is not None and routine.arguments != baseline_row.arguments):
            for value in routine.arguments:
                if value == "":
                    issues.append(_issue(f"{field_prefix}.arguments", "Routine arguments must be non-empty strings.", "Remove empty argument entries or provide a value for each one."))
                    break

        if routine.source_index is None or (baseline_row is not None and routine.memory != baseline_row.memory):
            if routine.memory.scope == "channel" and routine.memory.channel.strip() == "":
                issues.append(_issue(f"{field_prefix}.memory.channel", "Channel memory requires a channel.", "Choose a declared memory channel."))
            clearable_channel = (
                baseline_row is not None
                and baseline_row.memory.scope == "channel"
                and routine.memory.scope != "channel"
            )
            if routine.memory.scope != "channel" and routine.memory.channel != "" and not clearable_channel:
                issues.append(_issue(f"{field_prefix}.memory.channel", "Only channel memory selectors may define a channel.", "Clear channel unless memory scope is channel."))

    return issues


def _encode_enabled(enabled: bool, is_new: bool) -> bool | None:
    if is_new and enabled is True:
        return None
    return enabled


def _encode_arguments(arguments: list[str], is_new: bool) -> list[str] | None:
    if is_new and arguments == []:
        return None
    return arguments


def _encode_memory(memory: MemoryDraft) -> dict[str, str] | None:
    if memory.scope == "inherit":
        return None
    encoded = {"scope": memory.scope}
    if memory.scope == "channel":
        encoded["channel"] = memory.channel
    return encoded


def _decode_memory(memory: Mapping[str, Any] | None) -> MemoryDraft:
    if memory is None:
        return MemoryDraft(scope="inherit")
    scope = _string(memory.get("scope"))
    if scope not in {"run", "routine", "agent", "team", "channel"}:
        return MemoryDraft(scope="inherit")
    channel = _string(memory.get("channel")) if scope == "channel" else ""
    return MemoryDraft(scope=scope, channel=channel)


def _prompt_scope(value: Any) -> Literal["blueprint", "instance"]:
    return "instance" if value == "instance" else "blueprint"


def _prompt_baseline(routine: RoutineDraft | None) -> dict[str, str] | None:
    if routine is None:
        return None
    return {"scope": routine.prompt_scope, "name": routine.prompt_name}


def _retarget_issues(
    issues: list[ValidationIssue],
    field: str,
) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            code=issue.code,
            scope=field,
            field=field,
            message=issue.message,
            corrective_hint=issue.corrective_hint,
        )
        for issue in issues
    ]


def _warning(field: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        code=f"unsupported-routine-{field}",
        scope=field,
        field=field,
        message=message,
        corrective_hint="Leave the unsupported saved value untouched, or replace it with a supported form control value.",
    )


def _issue(field: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(
        code="invalid-routine-form",
        scope=field.rsplit(".", 1)[0],
        field=field,
        message=message,
        corrective_hint=hint,
    )


def _pydantic_issue(error: dict[str, Any]) -> ValidationIssue:
    field = ".".join(str(part) for part in error.get("loc", ())) or "routines"
    return _issue(field, error.get("msg", "Invalid routine form value."), "Correct the routine form field and try again.")


def _raw_routines(agent_raw: dict[str, Any]) -> list[dict[str, Any]]:
    raw_routines = agent_raw.get("routines")
    if not isinstance(raw_routines, list):
        return []
    return [dict(row) for row in raw_routines if isinstance(row, Mapping)]


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _mapping_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _enabled_value(value: Any) -> bool:
    return value if isinstance(value, bool) else True


def _arguments_value(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]