from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from flowgency.configuration import ConfigConflictError, ConfigSnapshot, ValidationFailed, ValidationIssue
from flowgency.configuration.models import MemorySelector
from flowgency.memory import select_effective_memory
from flowgency.prompts import PromptNotFoundError
from flowgency.routines.editor import RoutineChoices, RoutinesRequest, find_agent, load_choices, prepare_routines, save_routines
from flowgency.routines.forms import MemoryDraft, RecoveryDraft, RoutineDraft, RoutinesDraft, ScheduleDraft, build_form
from flowgency.routines.presentation import RoutineSummary, saved_status, summarize
from flowgency.web.dependencies import FlowgencyServices, get_services
from flowgency.web.routes.agent_detail import _detail_context, _get_snapshot_instance


logger = logging.getLogger(__name__)

router = APIRouter()


def issue_dicts(issues: tuple[ValidationIssue, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for issue in issues:
        rows.append(
            {
                "code": issue.code,
                "field": issue.field,
                "message": issue.message,
                "hint": issue.corrective_hint,
            }
        )
    return rows


def _issue(code: str, field: str, message: str, hint: str) -> ValidationIssue:
    scope = field.rsplit(".", 1)[0] if "." in field else field
    return ValidationIssue(
        code=code,
        scope=scope,
        field=field,
        message=message,
        corrective_hint=hint,
    )


def _safe_pydantic_message(error_type: str) -> str:
    return {
        "missing": "Required field is missing.",
        "extra_forbidden": "Unexpected field.",
        "literal_error": "Choose a supported value.",
        "dict_type": "Value must be an object.",
        "model_type": "Value must be an object.",
        "list_type": "Value must be a list.",
        "string_type": "Value must be a string.",
        "string_too_short": "Value must not be blank.",
        "greater_than_equal": "Value is below the allowed minimum.",
        "int_type": "Value must be an integer.",
        "bool_type": "Value must be true or false.",
    }.get(error_type, "Invalid value.")


def _pydantic_issues(exc: ValidationError) -> tuple[ValidationIssue, ...]:
    rows: list[ValidationIssue] = []
    for error in exc.errors(include_url=False):
        field = ".".join(str(part) for part in error.get("loc", ())) or "payload"
        rows.append(
            _issue(
                "invalid-request",
                field,
                _safe_pydantic_message(str(error.get("type", ""))),
                "Correct the routines payload and try again.",
            )
        )
    return tuple(rows)


def _fallback_draft_version(decoded: Any) -> int:
    if not isinstance(decoded, dict):
        return 0
    draft_version = decoded.get("draft_version")
    if isinstance(draft_version, bool) or not isinstance(draft_version, int) or draft_version < 0:
        return 0
    return draft_version


def _empty_choices(snapshot: ConfigSnapshot) -> RoutineChoices:
    return RoutineChoices(
        prompts=(),
        channels=tuple(
            (key, channel.display_name)
            for key, channel in snapshot.config.memory.channels.items()
        ),
    )


def _load_choices_or_issues(
    snapshot: ConfigSnapshot,
    services: FlowgencyServices,
    team: str,
    agent: str,
) -> tuple[RoutineChoices, tuple[ValidationIssue, ...], int | None]:
    if services.blueprint_library is None or services.prompt_store is None:
        return (
            _empty_choices(snapshot),
            (
                _issue(
                    "routines-unavailable",
                    "prompt",
                    "Routine prompt catalog is temporarily unavailable.",
                    "Reload and try again once prompt metadata is available.",
                ),
            ),
            503,
        )
    try:
        return load_choices(
            snapshot,
            services.blueprint_library,
            services.prompt_store,
            team,
            agent,
        ), (), None
    except PromptNotFoundError as exc:
        return (
            _empty_choices(snapshot),
            (
                _issue(
                    "missing-instance-prompt",
                    "prompt",
                    str(exc),
                    "Restore the missing prompt or choose another saved prompt.",
                ),
            ),
            422,
        )
    except ValidationFailed as exc:
        return _empty_choices(snapshot), tuple(exc.issues), 422
    except OSError:
        logger.exception(
            "Unable to load routines choices",
            extra={"team": team, "agent": agent},
        )
        return (
            _empty_choices(snapshot),
            (
                _issue(
                    "routines-unavailable",
                    "prompt",
                    "Routine prompt catalog is temporarily unavailable.",
                    "Reload and try again once prompt metadata is available.",
                ),
            ),
            503,
        )


def _baseline_request(snapshot: ConfigSnapshot, team: str, agent: str) -> tuple[RoutinesRequest, tuple[ValidationIssue, ...], list[dict[str, Any]]]:
    agent_raw = find_agent(snapshot.raw, team, agent)
    form = build_form(agent_raw)
    return (
        RoutinesRequest(
            revision=snapshot.revision,
            draft_version=0,
            draft=form.draft,
        ),
        form.warnings,
        agent_raw.get("routines") if isinstance(agent_raw.get("routines"), list) else [],
    )


def _dom_token(key: str, index: int) -> str:
    token = re.sub(r"[^a-zA-Z0-9_-]+", "-", key).strip("-")
    return token or f"row-{index}"


def _prompt_options(choices: RoutineChoices, routine: RoutineDraft) -> list[dict[str, Any]]:
    options = [
        {"scope": scope, "name": name, "label": f"{scope}:{name}"}
        for scope, name in choices.prompts
    ]
    selected = (routine.prompt_scope, routine.prompt_name)
    if routine.prompt_name and selected not in choices.prompts:
        options.insert(
            0,
            {
                "scope": routine.prompt_scope,
                "name": routine.prompt_name,
                "label": f"{routine.prompt_scope}:{routine.prompt_name}",
                "missing": True,
            },
        )
    for option in options:
        option["selected"] = option["scope"] == routine.prompt_scope and option["name"] == routine.prompt_name
    return options


def _channel_options(choices: RoutineChoices, routine: RoutineDraft) -> list[dict[str, Any]]:
    options = [{"key": key, "label": label} for key, label in choices.channels]
    if routine.memory.scope == "channel" and routine.memory.channel and all(option["key"] != routine.memory.channel for option in options):
        options.insert(0, {"key": routine.memory.channel, "label": routine.memory.channel, "missing": True})
    for option in options:
        option["selected"] = option["key"] == routine.memory.channel
    return options


def _routine_rows(draft: RoutinesDraft, choices: RoutineChoices) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, routine in enumerate(draft.routines):
        token = _dom_token(routine.key, index)
        title = routine.id.strip() or f"routine {index + 1}"
        rows.append(
            {
                "index": index,
                "key": routine.key,
                "source_index": "" if routine.source_index is None else str(routine.source_index),
                "source_index_value": routine.source_index,
                "title": title,
                "dom_id": f"routine-{token}",
                "schedule_mode_name": f"routine-{token}-schedule-mode",
                "id_input_id": f"routine-{token}-id",
                "prompt_input_id": f"routine-{token}-prompt",
                "enabled_input_id": f"routine-{token}-enabled",
                "schedule_every_id": f"routine-{token}-schedule-every",
                "schedule_at_id": f"routine-{token}-schedule-at",
                "schedule_amount_id": f"routine-{token}-schedule-amount",
                "schedule_unit_id": f"routine-{token}-schedule-unit",
                "schedule_time_id": f"routine-{token}-schedule-time",
                "memory_scope_id": f"routine-{token}-memory-scope",
                "memory_channel_id": f"routine-{token}-memory-channel",
                "recovery_mode_id": f"routine-{token}-recovery-mode",
                "recovery_amount_id": f"routine-{token}-recovery-amount",
                "recovery_unit_id": f"routine-{token}-recovery-unit",
                "row_error_id": f"routine-{token}-row-error",
                "field_error_prefix": f"routine-{token}",
                "prompt_options": _prompt_options(choices, routine),
                "channel_options": _channel_options(choices, routine),
                "arguments": [
                    {
                        "index": argument_index,
                        "value": value,
                        "input_id": f"routine-{token}-argument-{argument_index}",
                    }
                    for argument_index, value in enumerate(routine.arguments)
                ],
                "routine": routine,
            }
        )
    return rows


def _warning_map(warnings: tuple[ValidationIssue, ...]) -> dict[tuple[int, str], str]:
    mapped: dict[tuple[int, str], str] = {}
    for issue in warnings:
        parts = issue.field.split(".")
        if len(parts) < 3 or parts[0] != "routines":
            continue
        if not parts[1].isdigit():
            continue
        mapped[(int(parts[1]), parts[2])] = issue.message
    return mapped


def _draft_routine_for_source(draft: RoutinesDraft, source_index: int) -> RoutineDraft | None:
    for routine in draft.routines:
        if routine.source_index == source_index:
            return routine
    return None


def _warning_is_unchanged(
    routine: RoutineDraft,
    baseline: RoutineDraft | None,
    field: str,
) -> bool:
    if baseline is None:
        return False
    if field == "schedule":
        return routine.schedule == baseline.schedule
    if field == "recovery":
        return routine.recovery == baseline.recovery
    return True


def _display_warnings(
    current: RoutinesDraft,
    baseline: RoutinesDraft,
    warnings: tuple[ValidationIssue, ...],
) -> tuple[ValidationIssue, ...]:
    visible: list[ValidationIssue] = []
    for issue in warnings:
        parts = issue.field.split(".")
        if len(parts) < 3 or parts[0] != "routines" or not parts[1].isdigit():
            visible.append(issue)
            continue
        source_index = int(parts[1])
        if source_index >= len(baseline.routines):
            continue
        routine = _draft_routine_for_source(current, source_index)
        if routine is None:
            continue
        if _warning_is_unchanged(routine, baseline.routines[source_index], parts[2]):
            visible.append(issue)
    return tuple(visible)


def _schedule_summary_text(
    routine: RoutineDraft,
    baseline: RoutineDraft | None,
    source_index: int | None,
    raw_routines: list[dict[str, Any]],
    warnings: dict[tuple[int, str], str],
) -> str:
    if source_index is not None:
        warning = warnings.get((source_index, "schedule"))
        if warning and _warning_is_unchanged(routine, baseline, "schedule"):
            raw_schedule = raw_routines[source_index].get("schedule") if source_index < len(raw_routines) and isinstance(raw_routines[source_index], dict) else {}
            if isinstance(raw_schedule, dict) and raw_schedule.get("at") is not None:
                return f"{raw_schedule['at']} ({warning})"
            if isinstance(raw_schedule, dict) and raw_schedule.get("every") is not None:
                return f"{raw_schedule['every']} ({warning})"
            return warning
    if routine.schedule.mode == "at":
        return f"at {routine.schedule.time}" if routine.schedule.time else "not set"
    if routine.schedule.amount:
        return f"every {routine.schedule.amount}{routine.schedule.unit}"
    return "not set"


def _memory_from_draft(draft: MemoryDraft) -> MemorySelector | None:
    if draft.scope == "inherit":
        return None
    return MemorySelector(scope=draft.scope, channel=draft.channel or None)


def _memory_label(selector: MemorySelector, choices: RoutineChoices) -> str:
    if selector.scope == "run":
        return "Run memory"
    if selector.scope == "routine":
        return "Routine memory"
    if selector.scope == "agent":
        return "Agent memory"
    if selector.scope == "team":
        return "Team memory"
    channel_map = dict(choices.channels)
    return f"Channel: {channel_map.get(selector.channel or '', selector.channel or 'Channel')}"


def _memory_summary_text(agent_default: MemorySelector | None, draft: MemoryDraft, choices: RoutineChoices) -> str:
    selector = _memory_from_draft(draft)
    effective = select_effective_memory(None, selector, agent_default)
    label = _memory_label(effective, choices)
    if selector is None and agent_default is not None:
        return f"{label} (Agent default)"
    return label


def _recovery_summary_text(
    routine: RoutineDraft,
    baseline: RoutineDraft | None,
    source_index: int | None,
    raw_routines: list[dict[str, Any]],
    warnings: dict[tuple[int, str], str],
) -> str:
    if source_index is not None:
        warning = warnings.get((source_index, "recovery"))
        if warning and _warning_is_unchanged(routine, baseline, "recovery"):
            raw_schedule = raw_routines[source_index].get("schedule") if source_index < len(raw_routines) and isinstance(raw_routines[source_index], dict) else {}
            if isinstance(raw_schedule, dict) and raw_schedule.get("catch_up") is not None:
                return f"{raw_schedule['catch_up']} ({warning})"
            return warning
    if routine.recovery.mode == "default":
        return "today"
    if routine.recovery.mode in {"none", "today", "always"}:
        return routine.recovery.mode
    if routine.recovery.amount:
        return f"{routine.recovery.amount}{routine.recovery.unit}"
    return "not set"


def _fallback_summaries(
    snapshot: ConfigSnapshot,
    team: str,
    agent: str,
    draft: RoutinesDraft,
    baseline: RoutinesDraft,
    choices: RoutineChoices,
    raw_routines: list[dict[str, Any]],
    warnings: tuple[ValidationIssue, ...],
) -> tuple[RoutineSummary, ...]:
    agent_cfg = snapshot.config.teams[team].agents[agent]
    warning_lookup = _warning_map(warnings)
    rows: list[RoutineSummary] = []
    for routine in draft.routines:
        baseline_routine = baseline.routines[routine.source_index] if routine.source_index is not None and routine.source_index < len(baseline.routines) else None
        rows.append(
            RoutineSummary(
                key=routine.key,
                source_index=routine.source_index,
                id=routine.id,
                enabled=routine.enabled,
                prompt_scope=routine.prompt_scope,
                prompt_name=routine.prompt_name,
                schedule=_schedule_summary_text(routine, baseline_routine, routine.source_index, raw_routines, warning_lookup),
                memory=_memory_summary_text(agent_cfg.default_memory, routine.memory, choices),
                arguments=tuple(routine.arguments),
                recovery=_recovery_summary_text(routine, baseline_routine, routine.source_index, raw_routines, warning_lookup),
            )
        )
    return tuple(rows)


def _summary_view(summary_rows: tuple[RoutineSummary, ...], saved_rows: tuple, original_ids: list[str]) -> list[dict[str, Any]]:
    saved_by_source = {row.source_index: row for row in saved_rows}
    rendered: list[dict[str, Any]] = []
    for index, row in enumerate(summary_rows):
        saved = saved_by_source.get(row.source_index)
        original_id = original_ids[row.source_index] if row.source_index is not None and row.source_index < len(original_ids) else None
        rendered.append(
            {
                "index": index,
                **asdict(row),
                "arguments_text": ", ".join(row.arguments) if row.arguments else "None",
                "saved_last": saved.last_fired if saved is not None else "",
                "saved_next": saved.next_due if saved is not None else "",
                "original_id": original_id or "",
                "renamed_from": original_id if original_id and original_id != row.id else "",
                "is_new": row.source_index is None,
            }
        )
    return rendered


def render_routines_page(
    request: Request,
    services: FlowgencyServices,
    snapshot: ConfigSnapshot,
    team: str,
    agent: str,
    *,
    submitted: RoutinesRequest | None = None,
    issues: tuple[ValidationIssue, ...] = (),
    conflict: bool = False,
    status_code: int = 200,
):
    _get_snapshot_instance(snapshot, team, agent)
    baseline, baseline_warnings, raw_routines = _baseline_request(snapshot, team, agent)
    current = submitted or baseline
    choices, choice_issues, choice_status = _load_choices_or_issues(snapshot, services, team, agent)
    page_issues = tuple([*issues, *choice_issues])

    summary_rows: tuple[RoutineSummary, ...]
    display_warnings = _display_warnings(current.draft, baseline.draft, baseline_warnings)
    if submitted is None and not page_issues:
        prepared = prepare_routines(snapshot, team, agent, current, choices)
        summary_rows = summarize(prepared, team, agent, current.draft)
        display_warnings = prepared.form.warnings
    else:
        summary_rows = _fallback_summaries(
            snapshot,
            team,
            agent,
            current.draft,
            baseline.draft,
            choices,
            raw_routines,
            display_warnings,
        )

    saved_rows = () if conflict else saved_status(snapshot, team, agent)
    original_ids = [] if conflict else [row.original_id for row in saved_rows]
    routines_initial = {
        "baseline": baseline.model_dump(mode="json"),
        "draft": current.model_dump(mode="json"),
        "choices": {
            "prompts": [list(item) for item in choices.prompts],
            "channels": [list(item) for item in choices.channels],
        },
        "inherited_memory_label": _memory_summary_text(
            snapshot.config.teams[team].agents[agent].default_memory,
            MemoryDraft(scope="inherit"),
            choices,
        ),
        "saved_status": [asdict(row) for row in saved_rows],
        "original_ids": original_ids,
        "warnings": issue_dicts(display_warnings),
        "issues": issue_dicts(page_issues),
        "conflict": conflict,
        "preview_url": f"/{team}/agents/{agent}/routines/preview",
        "save_url": f"/{team}/agents/{agent}/routines",
        "summary_rows": [asdict(row) for row in summary_rows],
    }
    effective_status = status_code
    if effective_status == 200 and choice_status == 503:
        effective_status = 503
    return _detail_context(
        request,
        services,
        team,
        agent,
        "routines",
        snapshot=snapshot,
        status_code=effective_status,
        issues=issue_dicts(page_issues),
        overrides={
            "routines_initial": routines_initial,
            "routine_rows": _routine_rows(current.draft, choices),
            "routine_summary_rows": _summary_view(summary_rows, saved_rows, original_ids),
            "routine_conflict": conflict,
        },
    )


def _failure_json(
    draft_version: int,
    code: str,
    issues: tuple[ValidationIssue, ...],
    *,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        {
            "draft_version": draft_version,
            "code": code,
            "issues": issue_dicts(issues),
        },
        status_code=status_code,
    )


@router.get("/{team}/agents/{agent}/routines", response_class=HTMLResponse)
async def routines_page(
    request: Request,
    team: str,
    agent: str,
    services: FlowgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    return render_routines_page(request, services, snapshot, team, agent)


@router.post("/{team}/agents/{agent}/routines/preview")
async def routines_preview(
    request: Request,
    team: str,
    agent: str,
    services: FlowgencyServices = Depends(get_services),
) -> JSONResponse:
    try:
        decoded = await request.json()
    except json.JSONDecodeError:
        return _failure_json(
            0,
            "malformed-transport",
            (
                _issue(
                    "malformed-transport",
                    "payload",
                    "Request body must be valid JSON.",
                    "Resubmit the current routines draft.",
                ),
            ),
            status_code=422,
        )

    draft_version = _fallback_draft_version(decoded)
    try:
        submitted = RoutinesRequest.model_validate(decoded)
        snapshot = services.config_store.load()
        _get_snapshot_instance(snapshot, team, agent)
        choices, choice_issues, choice_status = _load_choices_or_issues(snapshot, services, team, agent)
        if choice_issues:
            code = "preview-unavailable" if choice_status == 503 else choice_issues[0].code
            return _failure_json(submitted.draft_version, code, choice_issues, status_code=choice_status or 422)
        prepared = prepare_routines(snapshot, team, agent, submitted, choices)
    except ValidationError as exc:
        return _failure_json(draft_version, "invalid-request", _pydantic_issues(exc), status_code=422)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown agent") from exc
    except ConfigConflictError:
        return _failure_json(
            draft_version,
            "config-conflict",
            (
                _issue(
                    "config-conflict",
                    "revision",
                    "Configuration changed while this draft was open.",
                    "Reload before previewing routines again.",
                ),
            ),
            status_code=409,
        )
    except ValidationFailed as exc:
        return _failure_json(draft_version, "validation-failed", tuple(exc.issues), status_code=422)
    except PromptNotFoundError as exc:
        return _failure_json(
            draft_version,
            "missing-prompt",
            (
                _issue(
                    "missing-prompt",
                    "prompt",
                    str(exc),
                    "Choose a saved prompt that still exists.",
                ),
            ),
            status_code=422,
        )
    except OSError:
        logger.exception("Unexpected routines preview failure", extra={"team": team, "agent": agent})
        return _failure_json(
            draft_version,
            "preview-unavailable",
            (
                _issue(
                    "preview-unavailable",
                    "prompt",
                    "Preview is temporarily unavailable.",
                    "Reload and try again once routine dependencies are available.",
                ),
            ),
            status_code=503,
        )

    return JSONResponse(
        {
            "draft_version": submitted.draft_version,
            "revision": snapshot.revision,
            "rows": [asdict(row) for row in summarize(prepared, team, agent, submitted.draft)],
            "warnings": issue_dicts(prepared.form.warnings),
        }
    )


@router.post("/{team}/agents/{agent}/routines", response_class=HTMLResponse)
async def routines_save(
    request: Request,
    team: str,
    agent: str,
    services: FlowgencyServices = Depends(get_services),
):
    form = await request.form()
    snapshot = services.config_store.load()
    _get_snapshot_instance(snapshot, team, agent)

    if "routines_json" in form or "payload" not in form:
        return render_routines_page(
            request,
            services,
            snapshot,
            team,
            agent,
            issues=(
                _issue(
                    "malformed-transport",
                    "payload",
                    "Reload the Routines editor before saving.",
                    "Submit the structured routines payload from the current page.",
                ),
            ),
            status_code=422,
        )

    payload_text = str(form.get("payload", ""))
    try:
        decoded = json.loads(payload_text)
    except json.JSONDecodeError:
        return render_routines_page(
            request,
            services,
            snapshot,
            team,
            agent,
            issues=(
                _issue(
                    "malformed-transport",
                    "payload",
                    "Payload must be valid JSON.",
                    "Reload the page and retry the save.",
                ),
            ),
            status_code=422,
        )

    submitted: RoutinesRequest | None = None
    try:
        submitted = RoutinesRequest.model_validate(decoded)
        if services.blueprint_library is None or services.prompt_store is None:
            return render_routines_page(
                request,
                services,
                snapshot,
                team,
                agent,
                submitted=submitted,
                issues=(
                    _issue(
                        "routines-unavailable",
                        "prompt",
                        "Routine prompt catalog is temporarily unavailable.",
                        "Reload and try again once prompt metadata is available.",
                    ),
                ),
                status_code=503,
            )
        save_routines(
            services.config_store,
            services.blueprint_library,
            services.prompt_store,
            team,
            agent,
            submitted,
        )
    except ValidationError as exc:
        return render_routines_page(
            request,
            services,
            snapshot,
            team,
            agent,
            issues=_pydantic_issues(exc),
            status_code=422,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown agent") from exc
    except ConfigConflictError:
        if submitted is None:
            raise HTTPException(status_code=409, detail="config.yaml changed; reload before saving")
        return render_routines_page(
            request,
            services,
            services.config_store.load(),
            team,
            agent,
            submitted=submitted,
            issues=(
                _issue(
                    "config-conflict",
                    "revision",
                    "Configuration changed while this draft was open.",
                    "Reload before saving routines again.",
                ),
            ),
            conflict=True,
            status_code=409,
        )
    except PromptNotFoundError as exc:
        if submitted is None:
            raise HTTPException(status_code=422, detail=str(exc))
        return render_routines_page(
            request,
            services,
            snapshot,
            team,
            agent,
            submitted=submitted,
            issues=(
                _issue(
                    "missing-prompt",
                    "prompt",
                    str(exc),
                    "Choose a saved prompt that still exists.",
                ),
            ),
            status_code=422,
        )
    except ValidationFailed as exc:
        if submitted is None:
            raise
        return render_routines_page(
            request,
            services,
            snapshot,
            team,
            agent,
            submitted=submitted,
            issues=tuple(exc.issues),
            status_code=422,
        )
    except OSError:
        if submitted is None:
            raise HTTPException(status_code=503, detail="Routine dependencies are unavailable")
        return render_routines_page(
            request,
            services,
            snapshot,
            team,
            agent,
            submitted=submitted,
            issues=(
                _issue(
                    "routines-unavailable",
                    "prompt",
                    "Routine dependencies are temporarily unavailable.",
                    "Reload and try again once prompt metadata is available.",
                ),
            ),
            status_code=503,
        )

    request.app.state.refresh_services()
    return RedirectResponse(f"/{team}/agents/{agent}/routines", status_code=303)