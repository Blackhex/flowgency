# Routines Configuration UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the agent's raw routine list editor with the approved structured editor and integrated summaries, preserving configuration and execution semantics.

**Architecture:** A routine-specific form adapter maps structured drafts onto server-loaded raw rows without losing optional or extension data. A service validates candidate configuration and scoped prompt choices, presents draft details, and saves only the selected agent's list through ConfigStore.patch. The browser maintains local row identity/order and frozen saved-status display, while the server remains authoritative for validation and persistence.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, Jinja2, PyYAML, plain JavaScript/CSS, existing local Lucide bundle, pytest, Playwright and existing Axe/layout helpers. No new application dependencies.

## Global Constraints

- Specification: `docs/superpowers/specs/2026-09-07-routines-ui-design.md`, approved by the user's execution request.
- "This is a configuration UI change only. Keep the canonical configuration model, scheduled execution behavior, permission policy, and current scheduling/memory semantics."
- "No Run now action, configuration migration, new schedule grammar, or runtime data migration is included."
- "The existing `routines` list remains the canonical ordered configuration. There are no new persisted editor IDs, fields, defaults, schedules, or display-only names."
- "No-op save must not fill defaults or discard such data."
- "Enabled and disabled routines have the same editable fields, summary fields, order, typography, and spacing."
- "Summary **Enabled** and **Disabled** labels share 12px type, weight 500, and 18px line height." Enabled is green `#86dfb1`, Disabled red `#fca5a5` in the dark reference; light-theme equivalents must pass contrast checks.
- "Remove the standalone Schedule status section/table. Each routine summary includes Last fired and Next due directly alongside its other fields."
- "No generic **Routine** heading inside an editor." Keep ordinal, Enabled checkbox, move/remove icons and ID field.
- "Stack editor before summary on narrow screens." Repeated routine items have 6px radius; no nested section cards.
- "Do not add visible instructional text about controls, YAML syntax, or keyboard shortcuts." Actionable errors and ID-change warnings are exceptions.
- "Reordering is expressly supported: original indices may arrive in a new order, must be unique and in range, and must refer to the loaded revision."
- "Moving or deleting existing markers, memory, logs, or job records when IDs/routines change" is out of scope.
- Keep current prompt-catalog behavior: duplicate names across Blueprint and Instance scopes are currently rejected by `effective_prompt_catalog`; test that diagnostic rather than changing this policy to support collisions.
- Keep unsupported loaded timing strings unchanged on unrelated/no-op saves, with a warning; require a supported timing value when edited. Do not change the config/runtime parser to force old saved values into a new grammar.
- Work only in `C:/Projekty/Flowgency/.worktrees/routines-ui`, branch `feat/routines-ui`. Do not touch live `config.yaml`, backups, locks, or main-checkout runtime state.
- Baseline at `72a2bd0`: `2163 passed, 6 skipped` from `python -m pytest tests/ -q`. Recheck if code/environment changed before execution.
- Use TDD and task-scoped reviews before dependencies; full Python/UI suites before final review and integration. Commit the plan alone before implementation; use Conventional Commits.
- Repository boundary tests ban certain historical substrings in tracked names/content. Follow current wording such as "old raw form" and semantic asset filenames; do not weaken tests or alter the existing exact vendor exception.

---

## Normative Assets

- HTML: `docs/superpowers/specs/assets/2026-09-07-routines-ui/routines-disabled-red.html`
- Desktop: `docs/superpowers/specs/assets/2026-09-07-routines-ui/routines-disabled-red.png`
- Mobile: `docs/superpowers/specs/assets/2026-09-07-routines-ui/routines-disabled-red-mobile.png`
- Rename: `docs/superpowers/specs/assets/2026-09-07-routines-ui/routines-disabled-red-rename.png`

Only the application region is normative. Do not copy the brainstorming banner or sidebar; use existing app shell. Saved-status mock text is illustrative, not a new formatter contract. Empty argument display must not become an empty persisted string. Reference controls disabled in the mockup become functional in the application.

## File Structure and Ownership

| File | Responsibility |
| --- | --- |
| New `flowgency/routines/__init__.py` | Package marker only |
| New `flowgency/routines/forms.py` | Strict transport models, raw-to-form conversion and lossless ordered serialization |
| New `flowgency/routines/editor.py` | Current prompt options, validation, read-only candidate preparation, atomic save |
| New `flowgency/routines/presentation.py` | Draft summary formatting, effective memory, saved status helper relocation |
| New `flowgency/web/routes/agent_routines.py` | GET/form POST/preview POST with one snapshot and retained drafts |
| Modify `flowgency/web/routes/agent_detail.py` | Remove routine-specific raw parser/handlers, pass new routine context through existing shared detail rendering |
| Modify `flowgency/web/routes/__init__.py`, `flowgency/app.py` | Register dedicated routine router |
| Modify `flowgency/templates/agent_detail_routines.html` | Approved structured page and row templates |
| New `flowgency/templates/agent_routines_summary.html` | Escaped draft details with saved-status slots |
| New `flowgency/static/agent-routines.js`, `agent-routines.css` | Local draft editing, ordered controls, preview sequencing, matching responsive design |
| New `tests/test_routine_forms.py`, `test_routine_editor.py`, `test_routine_presentation.py`, `test_agent_routines.py` | Unit, transaction, provenance, and HTTP regression coverage |
| Modify `tests/test_agent_detail.py` | Preserve old routine behavior coverage in new structured transport tests |
| New `tests/ui/agent_routines.spec.ts` | Real browser editing and screenshot regressions |
| Modify `tests/ui/agent_configuration.spec.ts`, `accessibility.spec.ts`, `fixtures/config.yaml` | Existing tab/gate integration; isolated routine fixture data |
| Modify `kb/dispatch.md` | Structured editor workflow and rename/no-migration behavior |

Do not extract an app-wide generic editor framework. Reuse small established utilities where suitable; do not import Permissions' ascending-index validation into reorderable routines. Do not modify the dispatch runner, memory store, configuration schema, or tool catalog.

## Task Sequence

1. Lossless structured routine mapping.
2. Read-only preparation, draft/saved presentation and atomic persistence.
3. Dedicated routes and structured server-rendered page.
4. Browser interactions, accessibility and approved visual contract.
5. Full acceptance, independent review, evidence and repository integration.

### Task 1: Preserve Raw Routine Data Through Structured Drafts

**Files:** Create `flowgency/routines/__init__.py`, `flowgency/routines/forms.py`, `tests/test_routine_forms.py`.

**Interfaces:**
- Consumes existing `ValidationIssue`, `ValidationFailed`, `parse_every`, `parse_catch_up`, `last_at_occurrence`; raw agent dictionary from a revision-checked snapshot.
- Produces strict Pydantic models below; `build_form(agent_raw: dict[str, Any]) -> RoutineForm`; `serialize_routines(agent_raw: dict[str, Any], draft: RoutinesDraft) -> list[dict[str, Any]] | None`; `RoutineFormError(ValidationFailed)`.
- `None` serialization means routines was absent and remains absent, distinct from explicit `[]`.
- `RoutineForm.warnings` reports unsupported but unchanged loaded timing fields; blocking edited-value errors raise `RoutineFormError`. Warnings do not masquerade as a successfully parsed schedule.

- [x] **Step 1: Write the initial lossless/reorder regressions.** Put these in the new test file. They must fail because the new adapter is missing, before implementation. Tests using Pydantic model mutation should also exercise serialization validation, not assume construction alone is sufficient.

```python
from copy import deepcopy
from flowgency.routines.forms import build_form, serialize_routines

def test_reorder_and_rename_preserve_original_extra_fields():
    raw = {"routines": [
        {"id": "audit", "prompt": {"scope": "blueprint", "name": "review"},
         "schedule": {"every": "007d"}, "arguments": ["  --literal value  "],
         "extension": {"keep": [1, 2]}},
        {"id": "digest", "prompt": {"scope": "instance", "name": "digest"},
         "schedule": {"at": "09:00", "catch_up": None},
         "memory": None, "enabled": False},
    ]}
    original = deepcopy(raw)
    draft = build_form(raw).draft
    assert serialize_routines(raw, draft) == original["routines"]
    draft.routines.reverse()
    draft.routines[1].id = "audit-renamed"
    expected = deepcopy(original["routines"])
    expected[0]["id"] = "audit-renamed"
    expected.reverse()
    assert serialize_routines(raw, draft) == expected
    assert raw == original

def test_absent_list_stays_absent():
    assert serialize_routines({}, build_form({}).draft) is None
    assert serialize_routines({"routines": []}, build_form({"routines": []}).draft) == []
```

- [x] **Step 2: Run `python -m pytest tests/test_routine_forms.py -q` and record the expected missing-module failure.** Do not change the existing routine endpoint yet.

- [x] **Step 3: Define the wire and form models in `forms.py`.** Blank strings are representable drafts, not valid persisted settings. Keep semantic validation in serialization so invalid field values can render back to the user. Use a local row key for browser/error association and a separate optional original index for authoritative raw lookup.

```python
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict
from flowgency.configuration.issues import ValidationIssue, ValidationFailed

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
```

`build_form` creates `key="saved-<index>"` for loaded rows. New browser rows use `new-<counter>`; validate nonempty unique keys of bounded length, but never interpret a key as a filesystem path or persisted ID. Use `source_index` only after integer/not-bool/range/uniqueness checks; allow any permutation. No client original-row dictionary is accepted.

- [x] **Step 4: Decode supported timing without discarding the original.** For representable intervals use the existing `parse_every` grammar, retaining original numeric digits for display or a normalized UI amount with baseline comparison before serialization. Daily decoding uses `last_at_occurrence` with a fixed reference datetime for parsing and canonical `%H:%M` display; do not invoke a browser timezone conversion. Catch-up uses `parse_catch_up` and the same duration units.

For an unsupported loaded timing string (for example `every: "3600"`, already present in the current UI fixture), set its mode correctly but amount/time blank and return a plain-string warning message that includes the raw value, safely escaped, with no additional shared warning field or type. The original saved raw configuration remains authoritative server-side. Do not place an unrepresentable raw string into a time input that silently normalizes it to empty and then overwrite configuration. Compare draft schedule controls with the server-generated baseline: unchanged unsupported controls preserve the raw schedule; an actual edit must pass the supported parser. A custom editor diagnostic is nonblocking for an untouched unsupported saved value, while invalid edited values block Save.

Implement helpers in `forms.py`: `decode_schedule(raw: dict) -> ScheduleDraft`, `decode_recovery(raw: dict) -> RecoveryDraft`, `encode_schedule(draft: ScheduleDraft) -> dict[str, str]`, and `encode_recovery(draft: RecoveryDraft) -> str | None`. `encode_schedule` validates positive interval amounts and valid daily time; `encode_recovery` preserves the current accepted nonnegative duration grammar, including `0m` if the existing parser accepts it. Do not add negative, fractional, seconds, cron, or new unit support. Field errors wrap helper failures in `ValidationIssue` at `routines.<index>.schedule` or `.recovery`.

```python
def test_unsupported_loaded_interval_survives_unrelated_edit():
    raw = {"routines": [{"id": "audit", "prompt": {"scope": "blueprint", "name": "review"},
                        "schedule": {"every": "3600"}}]}
    form = build_form(raw)
    assert form.warnings
    form.draft.routines[0].enabled = False
    result = serialize_routines(raw, form.draft)
    assert result[0]["schedule"] == {"every": "3600"}
    assert result[0]["enabled"] is False
```

- [x] **Step 5: Serialize only changed fields onto copies of original rows.** Start each surviving row from `deepcopy(original[source_index])`, each new row from an empty dictionary. Compare each known field independently to the generated baseline, not a wholesale model dump. Preserve omitted/default/null fields, explicit argument whitespace/order, accepted extra routine data, and schedule subfield presence when that field is unchanged. Changing schedule mode removes the previous at/every key but preserves an unchanged catch_up; changing recovery leaves timing untouched. Changing a non-channel memory scope clears channel only within the edited selector. Removing a routine removes only its list entry.

```python
def preserve_or_replace(target, original, field, before, after, encoded):
    if original is not None and before == after:
        return
    if encoded is None:
        target.pop(field, None)
    else:
        target[field] = deepcopy(encoded)
```

This optional local helper illustrates field comparison; define/import `deepcopy` if using it. Do not pass `None` for a value where explicit null must be preserved; unchanged null preservation is achieved by retaining the original row. For new rows use explicit ID/prompt/schedule and omit default enabled/arguments/memory/recovery until configured; omitted enabled still displays checked. An explicit empty list resulting from removing all existing arguments remains supported.

Validate IDs using the existing domain rules through candidate `parse_config` in Task 2; locally require nonblank unique IDs for field messages. Arguments must be nonempty strings for new/edited argument lists; do not `strip()` their actual saved contents, shell-split, or silently drop blanks. Reject duplicate/out-of-range/noninteger source indices and unexpected transport fields. Revalidate mutated model values with `RoutinesDraft.model_validate(draft.model_dump())` before any set/hash-based operations, then convert type errors into field-addressable issues.

- [x] **Step 6: Extend the regression matrix and run it.** Cover each of omitted/present arguments, enabled, memory, catch_up; repeated edit/discard-to-baseline; raw whitespace/digit formatting; reorder/rename extension preservation; same ID twice; same source index twice; malformed source index; new routines; clearing all rows; changed prompt scope; custom recovery; non-channel memory; unsupported timing unchanged versus corrected; blank new schedule; mutated argument values.

```python
import pytest
from flowgency.routines.forms import RoutineFormError

def test_duplicate_source_index_is_rejected():
    raw = {"routines": [{"id": "audit", "prompt": {"scope": "blueprint", "name": "review"},
                        "schedule": {"every": "7d"}}]}
    draft = build_form(raw).draft
    duplicate = draft.routines[0].model_copy(deep=True)
    duplicate.key = "another-row"
    duplicate.id = "another-id"
    draft.routines.append(duplicate)
    with pytest.raises(RoutineFormError) as caught:
        serialize_routines(raw, draft)
    assert any(issue.field == "routines.1.source_index" for issue in caught.value.issues)
```

Run `python -m pytest tests/test_routine_forms.py tests/test_dispatch_schedule.py -q`, require green, review, and commit `feat(routines): map structured configuration drafts`. No production route changes in this task.

### Task 2: Present Drafts and Save Against the Current Revision

**Files:** Create `flowgency/routines/editor.py`, `flowgency/routines/presentation.py`, `tests/test_routine_editor.py`, `tests/test_routine_presentation.py`. Modify `flowgency/web/routes/agent_detail.py` only to delegate its existing saved-status helper to the extracted equivalent; retain current behavior until Task 3.

**Interfaces:**
- Consumes Task 1 form types; `ConfigSnapshot`, `ConfigStore.patch`, `parse_config`; existing `effective_prompt_catalog`, `BlueprintLibrary`, `PromptStore`; `select_effective_memory` and existing status functions.
- Produces `RoutinesRequest(revision: str, draft_version: int, draft: RoutinesDraft)` as a strict Pydantic model with nonempty revision/nonnegative version.
- Produces `RoutineChoices(prompts: tuple[tuple[str, str], ...], channels: tuple[tuple[str, str], ...])`; `load_choices(snapshot, library, prompts, team_id, agent_id) -> RoutineChoices`.
- Produces `PreparedRoutines(candidate: dict[str, Any], config: FlowgencyConfig, form: RoutineForm, choices: RoutineChoices)`; `prepare_routines(snapshot, team_id, agent_id, request, choices) -> PreparedRoutines`; `save_routines(store, library, prompts, team_id, agent_id, request) -> ConfigSnapshot`.
- Presentation types: frozen `SavedRoutineStatus(source_index: int, original_id: str, last_fired: str, next_due: str)` and `RoutineSummary(key: str, source_index: int | None, id: str, enabled: bool, prompt_scope: str, prompt_name: str, schedule: str, memory: str, arguments: tuple[str, ...], recovery: str)`.
- Presentation functions: `saved_status(snapshot: ConfigSnapshot, team_id: str, agent_id: str) -> tuple[SavedRoutineStatus, ...]`; `summarize(prepared: PreparedRoutines, team_id: str, agent_id: str, draft: RoutinesDraft) -> tuple[RoutineSummary, ...]`. Use TYPE_CHECKING imports to avoid editor/presentation cycles.

Define the request type in `editor.py` with the following exact fields, so preview and save use the same parser:

```python
from pydantic import BaseModel, ConfigDict, Field
from flowgency.routines.forms import RoutinesDraft

class RoutinesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(min_length=1)
    draft_version: int = Field(ge=0)
    draft: RoutinesDraft
```

- [x] **Step 1: Add pure preparation tests using the existing app fixture.** Reuse `_seed_app` in `tests/test_agent_detail.py` and `app_mod.app.state.services` for real prompt files. `load_choices` must call `effective_prompt_catalog`, not return an empty success on unavailable library/store. No prompt body digest mechanism is needed: saved routines reference scope/name, not immutable prompt source; validate current availability on every prepare/save.

```python
from flowgency import app as app_mod
from flowgency.configuration import ConfigStore
from tests.test_agent_detail import _seed_app
from flowgency.routines.forms import build_form
from flowgency.routines.editor import RoutinesRequest, load_choices, prepare_routines

def test_preview_preserves_config_bytes(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    agent = snapshot.raw["teams"]["newsletter"]["agents"][0]
    request = RoutinesRequest(revision=snapshot.revision, draft_version=1, draft=build_form(agent).draft)
    before = config_path.read_bytes()
    choices = load_choices(snapshot, services.blueprint_library, services.prompt_store, "newsletter", "advisor")
    prepared = prepare_routines(snapshot, "newsletter", "advisor", request, choices)
    assert prepared.candidate["teams"]["newsletter"]["agents"][0] == agent
    assert config_path.read_bytes() == before
```

- [x] **Step 2: Run `python -m pytest tests/test_routine_editor.py -q` and implement shared validation.** Check requested revision before interpreting source indices. Apply `serialize_routines` to a deepcopy of the selected agent; remove routines only if the adapter returns None. Parse the complete candidate with `parse_config`; validate scoped prompts against choices and memory channels against candidate config. Empty catalog is valid if no selected routine requires a prompt; unavailable/invalid catalog is not an empty catalog. Use `ValidationIssue` for row fields, preserving actionable messages.

```python
if request.revision != snapshot.revision:
    raise ConfigConflictError("config.yaml changed; reload before saving")
candidate = deepcopy(snapshot.raw)
agent = find_agent(candidate, team_id, agent_id)
serialized = serialize_routines(agent, request.draft)
if serialized is None:
    agent.pop("routines", None)
else:
    agent["routines"] = serialized
config = parse_config(candidate, snapshot.path).resolved
```

Define `find_agent(raw, team_id, agent_id) -> dict[str, Any]` in `editor.py`: look up team and list entry by name, raise KeyError when absent. Never call ConfigStore.replace/create or a filesystem initializer during preparation. Add tests that monkeypatch `initialize_storage_directories`, job submission, and memory allocation to fail if preview reaches them. All routine indices/errors are tied to submitted order; transport local keys let browser map those indices to the correct row.

- [x] **Step 3: Save through one ConfigStore patch callback.** The callback runs with config revision checked under the existing lock. Build the current ConfigSnapshot from the raw dictionary already supplied to the callback, load current choices, prepare/validate, and replace only selected agent routines. Do not call `store.load` while holding its lock. Do not recursively call `replace_agent_routines` inside `store.patch`.

```python
def save_routines(store, library, prompts, team_id, agent_id, request):
    def apply(raw):
        snapshot = ConfigSnapshot(store.path, request.revision, raw, parse_config(raw, store.path).resolved)
        choices = load_choices(snapshot, library, prompts, team_id, agent_id)
        prepared = prepare_routines(snapshot, team_id, agent_id, request, choices)
        target = find_agent(raw, team_id, agent_id)
        source = find_agent(prepared.candidate, team_id, agent_id)
        if "routines" in source:
            target["routines"] = deepcopy(source["routines"])
        else:
            target.pop("routines", None)
    return store.patch(request.revision, apply)
```

Import/annotate the named existing types and helpers. This narrowly specialized transaction shares the existing store, not a new persistence framework. Prompt file availability is checked during save; it is not a distributed transaction locking the entire external prompt library, and no new file-lock hierarchy is introduced.

- [x] **Step 4: Extract saved-status computation without changing semantics.** Move `_routine_status`, `_marker_stamp`, `_next_due_text` from agent detail to presentation, retaining the underlying calls (`routine_schedules`, `last_fired_at`, `schedule_lateness`, `next_occurrence`, `grace_window`, clock functions, formatting). Keep a narrow delegate at the old helper while current tests/imports require it. `saved_status` enumerates the original configured routines and assigns source indices from that original order, not draft order or draft IDs.

The server GET emits a frozen saved-status snapshot to the browser. Preview responds with draft summaries only; the browser reattaches the original saved snapshot by `source_index` for display. Saved status is read-only display data, never used for config validation or accepted back as authority. A post-conflict error page cannot map stale source indices onto current saved rows: hide their status as unavailable until explicit reload, rather than showing another routine's history.

`summarize` uses candidate routines for draft details and the original request's keys/source indices for identity. It does not call saved-status functions with draft IDs. Use `select_effective_memory(None, candidate_routine.memory, candidate_agent.default_memory)` for displayed memory; append Agent default provenance only for actual inheritance. Default recovery is today. Unsupported preserved timing shows the authored string and a warning, not a fabricated due date. Arguments remain distinct tuple entries in the view model.

- [x] **Step 5: Add the transaction and provenance matrix.** Test save/no-op/reorder and unrelated-data deep equality; two requests sharing a revision yield one success/one conflict; outside-lock changes preserve exactly the external writer's bytes after the conflict. Reuse `tests/_lock_helpers.py` and existing ConfigStore tests rather than sleeps. Delete/rename fixture prompt between load and save to prove current availability validation; add a duplicate prompt name across scopes and require existing catalog error. Test run fallback when agent default is omitted.

```python
def test_stale_revision_cannot_overwrite_other_settings(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    request = RoutinesRequest(revision=snapshot.revision, draft_version=1,
        draft=build_form(snapshot.raw["teams"]["newsletter"]["agents"][0]).draft)
    store.patch(snapshot.revision, lambda raw: raw["teams"]["newsletter"].update(name="Updated name"))
    current_bytes = config_path.read_bytes()
    with pytest.raises(ConfigConflictError):
        save_routines(store, services.blueprint_library, services.prompt_store, "newsletter", "advisor", request)
    assert config_path.read_bytes() == current_bytes
```

Import pytest, ConfigConflictError and save_routines in the test file. For provenance, seed two routines with distinct marker timestamps, reorder/rename draft, assert SavedRoutineStatus retains original index/ID/time; new row has `source_index=None` and no original history. Snapshot marker and memory files before/after saving renamed/removed routine and assert identical contents and paths. Do not create/delete memory just to render summaries.

- [x] **Step 6: Run `python -m pytest tests/test_routine_editor.py tests/test_routine_presentation.py tests/test_routine_forms.py tests/test_config_store.py tests/test_dispatch_schedule.py -q`, review, and commit.** Commit `feat(routines): validate drafts and preserve saved status` after green tests. Keep warnings for preserved unsupported schedule data separate from blocking form errors.

### Task 3: Replace the Raw Routine Form with Dedicated Routes and Markup

**Files:** Create `flowgency/web/routes/agent_routines.py`, `flowgency/templates/agent_routines_summary.html`, `tests/test_agent_routines.py`; modify `flowgency/templates/agent_detail_routines.html`, `flowgency/web/routes/agent_detail.py`, `flowgency/web/routes/__init__.py`, `flowgency/app.py`, `tests/test_agent_detail.py`.

**Interfaces:**
- Consumes Tasks 1-2 draft/service/presentation functions, existing `_detail_context(..., snapshot=..., overrides=...)`, `get_services`, and shared Jinja environment.
- GET and POST: `/{team}/agents/{agent}/routines`; POST preview: `/{team}/agents/{agent}/routines/preview`.
- Normal save form has one hidden `payload` containing serialized `RoutinesRequest`. Success is 303 back to Routines. Do not accept the old `routines_json` field alongside it; return 422 with reload guidance instead of parsing an empty list and clearing routines.
- Preview success: `{draft_version, revision, rows: RoutineSummary[], warnings: Issue[]}`. Preview failure: `{draft_version, code, issues: Issue[]}`. Issues have `field`, `message`, `hint`, `code`; no raw exception trace or reflected HTML. Status codes: invalid request/fields 422; config conflict 409; unavailable prompt/status dependency or filesystem access 503; unknown agent/team 404.
- Initial JSON script `#routines-initial`: `{baseline: RoutinesRequest, draft: RoutinesRequest, choices: RoutineChoices, saved_status: SavedRoutineStatus[], original_ids: string[], warnings: Issue[], issues: Issue[], conflict: boolean, preview_url: string, save_url: string, summary_rows: RoutineSummary[]}`. On initial GET, saved status and original_ids come from the same config snapshot. On error after revision conflict these two fields are empty to avoid mismapping stale indices; submitted draft keeps the old revision and is never silently rebased.
- Markup root `#routine-editor`, form `#routines-form`, list `[data-routine-list]`; rows `[data-routine-row]` with `data-key`, `data-source-index`; summary `#routine-summary`; row-specific field/error IDs use local keys, not mutable routine IDs.

- [x] **Step 1: Add failing route ownership and transport tests.** Use the existing `_seed_app` fixture helper. Parse initialization JSON in test code with a narrowly scoped regex and `json.loads`; app code must use structured form/JSON parsing.

```python
import json
import re
from tests.test_agent_detail import _seed_app

def initial_payload(html):
        match = re.search(r'<script id="routines-initial" type="application/json">(.*?)</script>', html, re.S)
        assert match is not None
        return json.loads(match.group(1))

def test_routines_uses_structured_form(monkeypatch, tmp_path, raw_config):
        client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
        response = client.get("/newsletter/agents/advisor/routines")
        assert response.status_code == 200
        assert 'name="routines_json"' not in response.text
        assert '<textarea' not in response.text
        initial = initial_payload(response.text)
        assert initial["draft"]["draft"]["routines"][0]["id"] == "daily-review"
        assert initial["saved_status"][0]["original_id"] == "daily-review"
        assert 'data-routine-list' in response.text

def test_old_raw_form_cannot_clear_the_list(monkeypatch, tmp_path, raw_config):
        client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
        before = config_path.read_bytes()
        response = client.post("/newsletter/agents/advisor/routines", data={"routines_json": "[]"})
        assert response.status_code == 422
        assert config_path.read_bytes() == before
```

- [x] **Step 2: Run `python -m pytest tests/test_agent_routines.py -q` to establish red.** The old page lacks structured initialization and still accepts raw list payloads.

- [x] **Step 3: Add dedicated routes and retain invalid drafts.** Register/export `agent_routines_router` next to `agent_permissions_router`. Move the existing routine GET/POST ownership out of agent_detail so route registration order cannot select the old handler. Remove `_parse_routines_payload` and obsolete `_routines_context` after their usages are replaced; keep `_available_prompts` for any remaining other callers. `_detail_context` receives a supplied snapshot and routine overrides, with no routine-specific automatic reload that overwrites them.

Implement `render_routines_page(request, services, snapshot, team, agent, *, submitted=None, issues=(), conflict=False, status_code=200)` in the new route module. On GET use `build_form` from raw agent data and `load_choices`; catch unavailable dependencies so an editable/retained form and diagnostic can still render. On typed semantic errors use submitted draft rows and their order. On transport shape errors return a clean 422 without attempting to infer or save a list. Error HTML must not present current saved summaries as validation success for the submitted draft.

```python
@router.post("/{team}/agents/{agent}/routines", response_class=HTMLResponse)
async def routines_save(request: Request, team: str, agent: str,
                                                services: FlowgencyServices = Depends(get_services)):
        form = await request.form()
        if "routines_json" in form or "payload" not in form:
                raise HTTPException(status_code=422, detail="Reload the Routines editor before saving.")
        submitted = RoutinesRequest.model_validate_json(str(form["payload"]))
        save_routines(services.config_store, services.blueprint_library, services.prompt_store,
                                    team, agent, submitted)
        request.app.state.refresh_services()
        return RedirectResponse(f"/{team}/agents/{agent}/routines", status_code=303)
```

Wrap that happy path with the explicit error contracts above. Convert malformed JSON/Pydantic errors to 422, not an uncaught exception; semantic `ValidationFailed` to retained page 422; `ConfigConflictError` to retained page 409; missing/deleted prompt or library availability errors to an actionable 503/422 according to unavailable catalog versus missing selected prompt. Keep error payloads safe and full input draft available. If current config itself cannot load, return existing setup/config diagnostics without guessing a baseline.

Preview validates the request before extracting `draft_version`; echo it only when it is a nonnegative integer (exclude bool), otherwise use 0. Never call `int()` on unvalidated arbitrary JSON. Load one snapshot, load choices, prepare, summarize, return dataclasses via structured JSON encoding. Include preserved schedule warnings without falsely treating them as blocking or creating a due-time prediction. It performs no file writes and returns no saved-status values computed from the draft.

- [x] **Step 4: Render the exact approved controls with stable identifiers.** Use row templates for new routines and arguments, but show every saved row fully regardless of Enabled. Label and ID inputs remain normal weight. Reuse the current local Lucide asset with plus, trash-2, arrow-up/down, and x. Scope radio names to each local row key; do not allow toggling one routine's schedule to change another's radio group.

```html
<section id="routine-editor">
    <div class="routine-heading">
        <h2>Routines</h2>
        <button type="button" data-add-routine><i data-lucide="plus"></i>Add routine</button>
    </div>
    <form id="routines-form" method="post" action="/{{ team }}/agents/{{ agent }}/routines">
        <input type="hidden" name="payload">
        <div class="routine-columns">
            <div data-routine-list></div>
            <aside id="routine-summary" aria-label="Routine summary" aria-live="polite"></aside>
        </div>
        <div class="routine-actions">
            <button type="button" data-discard-routines>Discard changes</button>
            <button type="submit" data-save-routines>Save routines</button>
        </div>
    </form>
    <script id="routines-initial" type="application/json">{{ routines_initial | tojson }}</script>
</section>
```

This is the structural skeleton: server-render saved rows and initial summary into its named containers, and add `<template id="routine-row-template">` and `<template id="routine-argument-template">`. Every field has a label. Use `data-field` paths `id`, `prompt`, `enabled`, `schedule.mode`, `schedule.amount`, `schedule.unit`, `schedule.time`, `memory.scope`, `memory.channel`, `recovery.mode`, `recovery.amount`, `recovery.unit`. Arguments have `[data-argument-row]` and `[data-argument-value]`. Add/remove/reorder actions have row-scoped data attributes and unique accessible names including current ID or ordinal.

The summary partial renders draft fields with escaping, then `[data-saved-last]` and `[data-saved-next]` values with Saved labels, attached through source index. Both states have Prompt, Schedule, Memory, Arguments, Recovery, Last fired, Next due. On renamed saved rows show the original-ID note; on new rows show **Not saved** status without claiming a saved lookup. Do not add a generic Routine header or separate Schedule status panel. Do not display unsupported timing as an empty schedule or invented valid default.

- [x] **Step 5: Port existing endpoint regression coverage to structured payloads.** Update routine-specific tests in `tests/test_agent_detail.py` or move them into `test_agent_routines.py`; retain the real behavioral assertions for ordered list replacement, disabled state, catch_up retention/rejection, prompt scopes, unknown scope/name, and duplicate IDs. Assert optional enabled is not manufactured when its baseline was omitted; do not preserve old default-expansion bugs as test requirements.

Add GET -> initial -> reorder/rename -> preview -> POST -> 303 -> GET tests. On a revision conflict assert original draft IDs/order/argument contents remain in the response, current saved data is not relabeled under stale indices, and disk equals the newer external payload. Add invalid time/amount, nonstring version, missing payload, duplicate key/index, XSS in ID/argument/extra field values, prompt/channel deletion after load, and disabled-invalid-row tests. A blank ID remains visible and fixable rather than removing the row.

- [x] **Step 6: Run `python -m pytest tests/test_agent_routines.py tests/test_agent_detail.py tests/test_routine_editor.py tests/test_routine_presentation.py -q`, review, and commit.** Commit `feat(routines): replace raw list configuration form`. Browser behavior and final style are the next task; no silent auto-save or launch action is added here.

### Task 4: Build the Interactive Editor and Approved Layout

**Files:** Create `flowgency/static/agent-routines.js`, `flowgency/static/agent-routines.css`, `tests/ui/agent_routines.spec.ts`; modify `flowgency/templates/agent_detail_routines.html`, `flowgency/templates/agent_routines_summary.html`, `tests/ui/agent_configuration.spec.ts`, `tests/ui/accessibility.spec.ts`, `tests/ui/fixtures/config.yaml` only as required for deterministic routine data. No new library/package changes.

**Interfaces:**
- Consumes Task 3 initial JSON, typed request shape, named DOM hooks, preview response, and local icon library.
- Produces file-local controller functions `collectDraft()`, `renderRows(draft)`, `renderSummary(rows)`, `markChanged()`, `requestPreview(version, draft)`, `showIssues(issues)`, `discardDraft()`. No global routine engine.
- Local state contains baseline and mutable draft, original saved status/IDs, inactive schedule inputs, disclosure/focus state, dirty flag, pending argument strings, draft version, validated draft serialization, abort controller, and submitting/conflict flags. Nothing editor-only is persisted.

- [x] **Step 1: Write red browser tests for the selected layout and reorder/rename behavior.** Reuse `assertNoLayoutIssues`, console gates, and existing four-project light/dark viewport settings. Add Routines to existing tab/accessibility checks without removing other pages.

```typescript
import { expect, test } from '@playwright/test';
import { assertNoLayoutIssues, assertNoConsoleErrors, installConsoleErrorGate } from './layout';

const pagePath = '/newsletter/agents/advisor/routines';
test.beforeEach(async ({ page }, testInfo) => {
    installConsoleErrorGate(page);
    await page.addInitScript(theme => localStorage.setItem('theme', theme),
        testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test('renaming and reordering preserve saved provenance', async ({ page }) => {
    await page.goto(pagePath);
    const rows = page.locator('[data-routine-row]');
    const first = rows.first();
    const originalId = await first.locator('[data-field="id"]').inputValue();
    const savedBefore = await page.locator('#routine-summary [data-summary-key="saved-0"] [data-saved-last]').innerText();
    await first.locator('[data-field="id"]').fill('renamed-review');
    await expect(first.locator('[data-rename-warning]')).toContainText(originalId);
    await first.locator('[data-move-down]').click();
    await expect(rows.last().locator('[data-field="id"]')).toHaveValue('renamed-review');
    const summary = page.locator('#routine-summary [data-summary-key="saved-0"]');
    await expect(summary.locator('[data-saved-last]')).toHaveText(savedBefore);
    await expect(summary.locator('[data-original-id-note]')).toContainText(originalId);
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
    await expect(rows.first().locator('[data-field="id"]')).toHaveValue(originalId);
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
});
```

- [x] **Step 2: Prepare and run the red UI gate.** If absent, create local `.venv` with `python -m venv .venv` and install `.[test]`; run `npm ci` with the existing lock. Set `$env:PLAYWRIGHT_SKIP_BROWSER_GC = '1'` before `npm exec playwright install chromium` if a matching browser is missing. Do not reinstall or update dependencies speculatively. Run `npm run test:ui -- tests/ui/agent_routines.spec.ts --project=desktop-dark` and record the missing-interaction failure. Use the configured disposable test server, never port 8500 live configuration.

- [x] **Step 3: Implement local editing with stable keys.** Parse initial JSON once, clone baseline state, then render all rows. Add starts a new blank-ID row with no prompt selected, enabled true, no arguments, blank interval amount/days, default recovery, and inherited memory; these are draft defaults, not persisted until valid. Focus its ID. Reorder the local array and DOM by key, preserving inactive fields, pending text, and disclosure state; update ordinal and move boundaries. Remove only local row data and move focus to the nearest row or Add routine. Never use ID as DOM identity.

`collectDraft()` must read current DOM input values and synchronize them into the local row model before returning active transport fields; reading only cached input-event state misses programmatic/autofill changes. Preserve source identity from the row model, not editable inputs. Flush current input values before a reorder/rerender so a focused field cannot lose its latest text. This also makes the later submit-time serialization comparison meaningful when an input event did not fire.

Implement argument add/remove/move using one string per input. Empty argument list shows an empty state plus Add argument, not a persisted blank argument. If using a pending-entry input, keep it editor-local until Add and block Save while nonempty pending text remains; Enter adds instead of submitting the form. Alternatively, Add argument creates a blank argument row immediately and normal required-field validation blocks Save until filled or removed. Choose the latter to minimize state: every argument input belongs to the draft, so all text participates in dirty/unload protection. Do not trim/split or evaluate strings.

```javascript
function moveRoutine(key, direction) {
    const index = draft.routines.findIndex(row => row.key === key);
    const destination = index + direction;
    if (index < 0 || destination < 0 || destination >= draft.routines.length) return;
    const [row] = draft.routines.splice(index, 1);
    draft.routines.splice(destination, 0, row);
    renderRows(draft);
    markChanged();
    focusRoutineAction(key, direction < 0 ? 'up' : 'down');
}
```

Define `focusRoutineAction(key, direction)` locally to focus the requested enabled move button or the other move/remove button if it became disabled at the boundary. Before renderRows capture active field and selection range and restore focus by stable key/field; avoid resetting the active input's cursor on each keystroke. Simpler keyed DOM moves are acceptable instead of full rerenders.

- [x] **Step 4: Implement conditional fields and identical state styling.** Radio groups unique per row reveal Interval amount/unit or Daily time. Memory Channel and duration recovery reveal their respective fields. Inactive values remain in local state but `collectDraft` emits inactive fields at a fixed neutral value, so editing an inactive field cannot accidentally change serialization/no-op comparison. On switching back restore the locally cached values. No Target selector, YAML toggle, Run now, or per-row Save is introduced.

Rename warnings compare current ID with `initial.original_ids[source_index]`, not another row's current ID. Show no warning for new rows, and clear it on rename-back or discard. Enabled checkbox never disables/collapses the rest of the row. In summaries use the same `.routine-state` styles for both words; only text and color differ.

```css
#routine-editor .routine-columns {
    display: grid;
    grid-template-columns: minmax(0, 1.6fr) minmax(270px, 1fr);
    gap: 28px;
}
#routine-editor .routine-columns > * { min-width: 0; }
#routine-editor .routine-item { border-radius: 6px; padding: 16px; }
#routine-editor .routine-label { font-size: 14px; font-weight: 400; }
#routine-editor .routine-state { font-size: 12px; font-weight: 500; line-height: 18px; }
.dark #routine-editor .routine-state[data-enabled="true"] { color: #86dfb1; }
.dark #routine-editor .routine-state[data-enabled="false"] { color: #fca5a5; }
#routine-editor input, #routine-editor select { min-width: 0; max-width: 100%; }
#routine-editor .routine-summary-value { overflow-wrap: anywhere; }
@media (max-width: 1100px) {
    #routine-editor .routine-columns { grid-template-columns: minmax(0, 1fr); }
}
```

Complete CSS using existing app light/dark border/background conventions, green/red light-theme contrast, 34px icon actions, label spacing and summary separators matching the final asset. Use normal font size rather than viewport scaling; no global tag styling that affects other tabs. Keep both full editors and summaries visible for disabled examples. On small screens inputs and actions wrap without overflow and editor precedes summary. Load existing local Lucide and initialize icons for new rows; do not add another icon dependency.

- [x] **Step 5: Implement preview sequencing and saved-status slots.** On every input/change/add/remove/move immediately increment version, mark preview pending, clear valid state, and disable Save. Debounce 250ms and abort previous fetch, but also compare version to ignore responses already in flight. Capture the exact submitted draft before fetch; only that snapshot can become `validatedDraft`.

```javascript
let version = initial.draft.draft_version;
let validVersion = -1;
let validatedDraft = null;
let controller = null;
let timer = null;
function markChanged() {
    version += 1;
    validVersion = -1;
    validatedDraft = null;
    controller?.abort();
    clearTimeout(timer);
    showPending();
    updateActions();
    const requestVersion = version;
    timer = setTimeout(() => requestPreview(requestVersion, collectDraft()), 250);
}
async function requestPreview(requestVersion, requestDraft) {
    controller = new AbortController();
    try {
        const response = await fetch(initial.preview_url, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
            body: JSON.stringify({ revision: initial.draft.revision, draft_version: requestVersion, draft: requestDraft }),
        });
        const result = await response.json();
        if (requestVersion !== version || result.draft_version !== requestVersion) return;
        if (!response.ok) { showFailure(result); return; }
        renderSummary(result.rows);
        showWarnings(result.warnings);
        validVersion = requestVersion;
        validatedDraft = JSON.stringify(requestDraft);
        updateActions();
    } catch (error) {
        if (error.name !== 'AbortError' && requestVersion === version) showFailure({ code: 'preview-unavailable', issues: [] });
    }
}
```

Define referenced file-local helpers: `showPending` sets summary busy/pending and removes success implication; `showFailure` displays errors with safe text and a retry action for transient failures, sets conflict on 409, and leaves draft rows intact; `showWarnings` marks preserved unsupported schedule values without falsely claiming parsed timing; `updateActions` computes dirty and permits Save only for latest valid draft and no submission/conflict. `collectDraft` returns typed active field data after synchronizing the current DOM values with stable local state. `renderSummary` builds text safely (`textContent`, no interpolation into HTML) and joins saved fields from the frozen initial saved_status by source index. It never calculates last-fired or next-due from a draft.

For saved source keys present in a valid initial snapshot, retain original status even after local toggle/rename/reorder. For new rows show Not saved. After a conflict where original saved status was not available, display unavailable rather than attaching the current server row at that index. Draft summary rows must follow current order and provide identical fields for both states.

- [x] **Step 6: Finish submit/discard/error recovery and keyboard behavior.** Save's native POST handler collects the current draft, compares its JSON with validatedDraft and validates version before populating hidden payload; if different, prevent submission and preview again. Set submitting to avoid duplicate saves and suppress unload warning only for the intentional valid POST. No-preview bypass is relied on for security; server validation always runs. Discard cancels timer/request, increments version, rebuilds baseline order/values/summary, clears rename/errors/new rows, and restores focus; if conflicted, explicitly reload current data with a user-confirmed discard.

```javascript
form.addEventListener('submit', event => {
    const current = collectDraft();
    if (conflict || submitting || validVersion !== version || JSON.stringify(current) !== validatedDraft) {
        event.preventDefault();
        if (!conflict && !submitting) markChanged();
        return;
    }
    form.elements.payload.value = JSON.stringify({ revision: initial.draft.revision, draft_version: version, draft: current });
    submitting = true;
    updateActions();
});
window.addEventListener('beforeunload', event => {
    if (!dirty || submitting) return;
    event.preventDefault();
    event.returnValue = '';
});
```

Define form, dirty, submitting and conflict in the controller closure. Add button tooltips/accessible names, per-input error associations and live alerts; automatically open Arguments & recovery for errors inside. On HTTP error reload, use submitted draft, retain stale revision, and do not label saved summary as valid draft. Never send stored original fields or saved marker data to authorize changes.

- [x] **Step 7: Expand browser tests to exact acceptance cases.** Add finite deterministic routines to the existing research fixture agent only for these tests if needed; do not add a new fleet agent unless unavoidable. Do not alter its permission policy or launch routines. Keep existing advisor tests read-only or discard-only. Use a dedicated test fixture with explicit fields for real save tests, snapshot its original routines and restore using a fresh revision in finally. Do not copy restoration logic that reuses invalid old source indices after deleting rows; recreate explicit fixture rows as new indices for restoration, and cover exact optional-field no-op fidelity separately in Python.

Tests: all state colors/typography/detail parity; add/delete/move first/last; argument ordering and spaces; radio-group independence; memory channel visibility; every recovery mode; rename warnings/provenance; disabled edits; missing prompt option retention; pending blank argument disables save; discard while preview in flight; malformed/422/409/503 responses; real save/reload; beforeunload accept/cancel; valid preview followed by programmatic value change cannot submit unnoticed.

```typescript
test('failed preview preserves the edited ID', async ({ page }) => {
    await page.goto(pagePath);
    await page.route('**/routines/preview', async route => {
        const request = route.request().postDataJSON();
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({
            draft_version: request.draft_version, code: 'preview-unavailable', issues: [],
        }) });
    });
    await page.locator('[data-routine-row]').first().locator('[data-field="id"]').fill('keep-my-draft');
    await expect(page.getByText('Preview unavailable', { exact: true })).toBeVisible();
    await expect(page.locator('[data-routine-row]').first().locator('[data-field="id"]')).toHaveValue('keep-my-draft');
    await expect(page.getByRole('button', { name: 'Save routines', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
});
```

For out-of-order tests use a held Promise in `page.route`, let a second response complete, then release the first and assert its values never replace the second. Do not use sleeps or disable application validation to make tests pass. Assert saved status text stays exactly fixed during draft changes, not merely that a Saved label exists.

- [x] **Step 8: Run all four UI projects and adjacent gates, inspect screenshots, then commit.** Commands: `npm run test:ui -- tests/ui/agent_routines.spec.ts`, `npm run test:ui -- tests/ui/accessibility.spec.ts tests/ui/agent_configuration.spec.ts`, and `python -m pytest tests/test_agent_routines.py -q`. Store new screenshot baselines for populated enabled/disabled, empty, rename/error, and long-text states in `tests/ui/agent_routines.spec.ts-snapshots/`. Inspect actual PNGs against normative design assets; do not hide overflow by modifying test element styles. Refresh only screenshots whose legitimate content changed and document why. Commit `feat(routines): build structured list editor` after review.

### Task 5: Acceptance, Documentation, and Integration

**Files:** Modify `kb/dispatch.md` for the structured workflow and ID warning; create `docs/superpowers/verification/2026-09-07-routines-ui.md`. Update only task-owned tests/code if acceptance identifies a concrete defect, through a failing regression and review.

**Interfaces:** Consumes all prior contracts and the four normative asset paths; produces verified implementation evidence and a branch ready for the repository's preauthorized integration workflow.

- [x] **Step 1: Document the operator workflow without changing runtime guidance.** Add to `kb/dispatch.md` that Agent -> Routines owns ordered structured configuration with explicit Save/Discard, grouped scoped prompts, enabled state and original-ID warning. Explain that Saved Last fired/Next due does not predict unsaved changes and changing IDs does not move memory/markers. Retain existing scheduler/recovery semantics and command examples.

```markdown
## Configure Routines

Open an agent's Routines tab to edit its ordered routine list. Save routines
applies the list together; Discard changes restores the loaded values.
Changing an existing ID changes routine identity without moving its schedule
markers or routine-scoped memory. Saved Last fired and Next due values describe
the saved routine, not a prediction for an unsaved schedule.
```

- [x] **Step 2: Run full acceptance from the active worktree.** Run `python -m pytest tests/ -q` and `npm run test:ui` to completion. The UI gate uses the existing four viewport/theme projects and its disposable configuration; do not use live config. Run `git diff --check` and the repository boundary tests after staging new documentation/assets, since tracked-file checks read the index. Do not suppress unrelated third-party warnings; identify/document them without unrequested dependency churn.

- [x] **Step 3: Compare actual UI and test failure boundaries.** Verify final asset layout, green/red consistent text, full disabled details, integrated Saved fields, no redundant headings/panels, readable long values, and editor-before-summary on mobile. Inspect screenshots from disk, ensuring full document coverage rather than cropped browser screenshots. Capture and inspect empty, invalid, rename, reordered, disabled, missing-prompt and unsupported-saved-timing states. Assert no save/preview/rename operation touches marker or memory files and no tests accidentally change main-checkout config.

- [x] **Step 4: Record exact evidence and perform final review.** Write the verification document with tested code commits, exact commands/results/skips/warnings, scope of visual comparison, saved-data preservation tests, and real limitations. Separate historical baseline from implementation test results. Complete per-task review and a whole-branch review; fix concrete defects with regression tests and scoped re-review. Do not label the final review approved until it actually is. Commit documentation separately with `docs(routines): record configuration ui verification`.

- [ ] **Step 5: Integrate only after review and green gates.** Follow AGENTS.md: check main checkout/branch tips, preserve unrelated main edits with a named stash only if needed, and rebase this feature onto master only if master advanced, rerunning the full suite/UI gates after any rebase. Fast-forward master, rerun the complete Python suite from master, push master and the feature branch, then remove this feature worktree and prune it. Keep the feature branch. Do not ask for merge-vs-PR options; integration is preauthorized. Do not remove a worktree or claim publication if a required test or push fails.

```text
git -C C:/Projekty/Flowgency status --short --branch
git -C C:/Projekty/Flowgency merge --ff-only feat/routines-ui
```

Then switch shell cwd to `C:/Projekty/Flowgency` for `python -m pytest tests/ -q`. Only when green: `git push origin master feat/routines-ui`, `git worktree remove .worktrees/routines-ui`, `git worktree prune`. Preserve live config and runtime state throughout. If a preview is useful, serve the isolated UI fixture server from master on a free port and clearly label it as fixture data; never replace the user's running live server.

## Coverage and Self-Review

| Requirement | Implementation and verification |
| --- | --- |
| Defined fields without YAML; unchanged schema/runtime | Tasks 1, 3, 4 and final authority check |
| Reorder/rename with raw extension/optional fidelity | Task 1 permutation/no-op tests; Task 2 save tests |
| ID warnings, no marker/memory migration | Tasks 2 and 4 provenance/filesystem checks |
| Scoped prompt choices and catalog changes | Tasks 2 and 3 real catalog fixtures and save revalidation |
| Argument order, whitespace, empty/pending text | Tasks 1 and 4 serializer/browser tests |
| Daily/interval/recovery and unsupported loaded strings | Tasks 1 and 4 form controls and warnings |
| Memory scopes, channels, correct inheritance fallback | Task 2 domain selector tests; Task 4 conditional UI |
| Identical disabled details and red status | Tasks 3-5 markup/computed styles/screenshot gates |
| Saved fields inside each summary, no draft history | Tasks 2-4 frozen saved snapshots and per-row slots |
| Atomic saves/conflicts/unrelated data preserved | Task 2 transaction tests; Task 3 error retention |
| Stale previews, navigation, discard and duplicate submit | Task 4 controlled-response browser tests |
| Accessibility, long text, themes and mobile | Task 4 four-project UI/Axe gates; Task 5 asset comparison |
| Review, verified publication, cleanup | Task 5 integration gate |

Implementation checklist remains uncompleted until execution evidence exists. Writing this plan does not implement the feature. After the plan-only commit, choose subagent-driven or inline execution; no implementation subagent is dispatched during plan writing.