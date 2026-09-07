from __future__ import annotations

from copy import deepcopy

import pytest

from flowgency.routines.forms import (
    MemoryDraft,
    RecoveryDraft,
    RoutineDraft,
    RoutineFormError,
    RoutinesDraft,
    ScheduleDraft,
    build_form,
    serialize_routines,
)


def _new_routine(
    *,
    key: str = "new-1",
    routine_id: str = "audit",
    prompt_scope: str = "blueprint",
    prompt_name: str = "review",
    enabled: bool = True,
    arguments: list[str] | None = None,
    schedule: ScheduleDraft | None = None,
    recovery: RecoveryDraft | None = None,
    memory: MemoryDraft | None = None,
) -> RoutineDraft:
    return RoutineDraft(
        key=key,
        id=routine_id,
        prompt_scope=prompt_scope,
        prompt_name=prompt_name,
        enabled=enabled,
        arguments=[] if arguments is None else arguments,
        schedule=schedule or ScheduleDraft(mode="every", amount="7", unit="d"),
        recovery=recovery or RecoveryDraft(mode="default"),
        memory=memory or MemoryDraft(scope="inherit"),
    )


def test_reorder_and_rename_preserve_original_extra_fields():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"every": "007d"},
                "arguments": ["  --literal value  "],
                "extension": {"keep": [1, 2]},
            },
            {
                "id": "digest",
                "prompt": {"scope": "instance", "name": "digest"},
                "schedule": {"at": "09:00", "catch_up": None},
                "memory": None,
                "enabled": False,
            },
        ]
    }
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


def test_unsupported_loaded_interval_survives_unrelated_edit():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"every": "3600"},
            }
        ]
    }

    form = build_form(raw)

    assert form.warnings

    form.draft.routines[0].enabled = False
    result = serialize_routines(raw, form.draft)

    assert result[0]["schedule"] == {"every": "3600"}
    assert result[0]["enabled"] is False


def test_duplicate_source_index_is_rejected():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"every": "7d"},
            }
        ]
    }

    draft = build_form(raw).draft
    duplicate = draft.routines[0].model_copy(deep=True)
    duplicate.key = "another-row"
    duplicate.id = "another-id"
    draft.routines.append(duplicate)

    with pytest.raises(RoutineFormError) as caught:
        serialize_routines(raw, draft)

    assert any(issue.field == "routines.1.source_index" for issue in caught.value.issues)


def test_unchanged_existing_rows_preserve_argument_whitespace_and_digit_formatting():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "arguments": ["  --literal value  ", "  spaced  "],
                "schedule": {"every": "007d"},
            }
        ]
    }

    form = build_form(raw)

    assert form.draft.routines[0].arguments == ["  --literal value  ", "  spaced  "]
    assert form.draft.routines[0].schedule.amount == "007"
    assert serialize_routines(raw, form.draft) == raw["routines"]


def test_existing_row_can_change_arguments_to_explicit_empty_list():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "arguments": ["--brief"],
                "schedule": {"every": "7d"},
            }
        ]
    }

    draft = build_form(raw).draft
    draft.routines[0].arguments = []

    assert serialize_routines(raw, draft) == [
        {
            "id": "audit",
            "prompt": {"scope": "blueprint", "name": "review"},
            "arguments": [],
            "schedule": {"every": "7d"},
        }
    ]


def test_enabled_default_is_omitted_for_new_rows_but_false_is_persisted():
    enabled_default = RoutinesDraft(routines=[_new_routine(key="new-1", routine_id="audit")])
    enabled_false = RoutinesDraft(
        routines=[_new_routine(key="new-2", routine_id="digest", enabled=False)]
    )

    assert serialize_routines({}, enabled_default) == [
        {
            "id": "audit",
            "prompt": {"scope": "blueprint", "name": "review"},
            "schedule": {"every": "7d"},
        }
    ]
    assert serialize_routines({}, enabled_false) == [
        {
            "id": "digest",
            "prompt": {"scope": "blueprint", "name": "review"},
            "enabled": False,
            "schedule": {"every": "7d"},
        }
    ]


def test_memory_field_preserves_null_when_unchanged_and_clears_channel_when_scope_changes():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"every": "7d"},
                "memory": None,
            },
            {
                "id": "digest",
                "prompt": {"scope": "instance", "name": "digest"},
                "schedule": {"at": "09:00"},
                "memory": {"scope": "channel", "channel": "brand-strategy", "extra": "keep"},
            },
        ]
    }

    draft = build_form(raw).draft
    assert serialize_routines(raw, draft) == raw["routines"]

    draft.routines[1].memory.scope = "agent"
    assert serialize_routines(raw, draft)[1]["memory"] == {"scope": "agent"}


def test_recovery_round_trips_and_duration_edits_only_touch_catch_up():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"at": "09:00", "catch_up": "048h", "note": "keep"},
            }
        ]
    }

    draft = build_form(raw).draft
    assert draft.routines[0].recovery == RecoveryDraft(mode="duration", amount="048", unit="h")
    assert serialize_routines(raw, draft) == raw["routines"]

    draft.routines[0].recovery.amount = "24"
    assert serialize_routines(raw, draft) == [
        {
            "id": "audit",
            "prompt": {"scope": "blueprint", "name": "review"},
            "schedule": {"at": "09:00", "catch_up": "24h", "note": "keep"},
        }
    ]


def test_round_trip_back_to_baseline_restores_original_schedule_shape():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"every": "007d"},
            }
        ]
    }

    draft = build_form(raw).draft
    draft.routines[0].schedule.amount = "7"
    assert serialize_routines(raw, draft)[0]["schedule"] == {"every": "7d"}

    draft.routines[0].schedule.amount = "007"
    assert serialize_routines(raw, draft) == raw["routines"]


def test_duplicate_id_is_rejected():
    draft = RoutinesDraft(
        routines=[
            _new_routine(key="new-1", routine_id="audit"),
            _new_routine(key="new-2", routine_id="audit", prompt_name="digest"),
        ]
    )

    with pytest.raises(RoutineFormError) as caught:
        serialize_routines({}, draft)

    assert [issue.field for issue in caught.value.issues] == ["routines.1.id"]


@pytest.mark.parametrize(
    ("mutator", "field"),
    [
        (lambda draft: setattr(draft.routines[0], "source_index", -1), "routines.0.source_index"),
        (lambda draft: setattr(draft.routines[0], "source_index", 2), "routines.0.source_index"),
        (lambda draft: setattr(draft.routines[0], "source_index", True), "routines.0.source_index"),
    ],
)
def test_malformed_source_index_is_rejected(mutator, field):
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"every": "7d"},
            }
        ]
    }
    draft = build_form(raw).draft
    mutator(draft)

    with pytest.raises(RoutineFormError) as caught:
        serialize_routines(raw, draft)

    assert [issue.field for issue in caught.value.issues] == [field]


def test_new_routines_and_clearing_all_rows_are_supported():
    new_draft = RoutinesDraft(
        routines=[
            _new_routine(
                key="new-1",
                routine_id="audit",
                prompt_scope="instance",
                prompt_name="digest",
                schedule=ScheduleDraft(mode="at", time="09:00"),
            )
        ]
    )
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"every": "7d"},
            }
        ]
    }

    assert serialize_routines({}, new_draft) == [
        {
            "id": "audit",
            "prompt": {"scope": "instance", "name": "digest"},
            "schedule": {"at": "09:00"},
        }
    ]
    assert serialize_routines(raw, RoutinesDraft(routines=[])) == []


def test_changed_prompt_scope_updates_only_the_prompt_field():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review", "label": "keep-if-unchanged"},
                "schedule": {"every": "7d"},
                "extension": {"keep": True},
            }
        ]
    }

    draft = build_form(raw).draft
    draft.routines[0].prompt_scope = "instance"

    assert serialize_routines(raw, draft) == [
        {
            "id": "audit",
            "prompt": {"scope": "instance", "name": "review"},
            "schedule": {"every": "7d"},
            "extension": {"keep": True},
        }
    ]


def test_unsupported_loaded_interval_can_be_corrected_to_supported_value():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "schedule": {"every": "3600"},
            }
        ]
    }

    form = build_form(raw)
    assert form.draft.routines[0].schedule.amount == ""

    form.draft.routines[0].schedule.amount = "60"
    form.draft.routines[0].schedule.unit = "m"

    assert serialize_routines(raw, form.draft) == [
        {
            "id": "audit",
            "prompt": {"scope": "blueprint", "name": "review"},
            "schedule": {"every": "60m"},
        }
    ]


def test_blank_new_schedule_is_rejected():
    draft = RoutinesDraft(
        routines=[_new_routine(schedule=ScheduleDraft(mode="every", amount="", unit="d"))]
    )

    with pytest.raises(RoutineFormError) as caught:
        serialize_routines({}, draft)

    assert [issue.field for issue in caught.value.issues] == ["routines.0.schedule"]


def test_mutated_argument_values_are_rejected():
    raw = {
        "routines": [
            {
                "id": "audit",
                "prompt": {"scope": "blueprint", "name": "review"},
                "arguments": ["--brief"],
                "schedule": {"every": "7d"},
            }
        ]
    }
    draft = build_form(raw).draft
    draft.routines[0].arguments = ["--brief", 1]

    with pytest.raises(RoutineFormError) as caught:
        serialize_routines(raw, draft)

    assert [issue.field for issue in caught.value.issues] == ["routines.0.arguments.1"]


def test_non_channel_memory_requires_channel_and_clears_it_elsewhere():
    missing_channel = RoutinesDraft(
        routines=[
            _new_routine(memory=MemoryDraft(scope="channel", channel=""))
        ]
    )
    extra_channel = RoutinesDraft(
        routines=[
            _new_routine(memory=MemoryDraft(scope="team", channel="brand-strategy"))
        ]
    )

    with pytest.raises(RoutineFormError) as missing:
        serialize_routines({}, missing_channel)
    with pytest.raises(RoutineFormError) as extra:
        serialize_routines({}, extra_channel)

    assert [issue.field for issue in missing.value.issues] == ["routines.0.memory.channel"]
    assert [issue.field for issue in extra.value.issues] == ["routines.0.memory.channel"]