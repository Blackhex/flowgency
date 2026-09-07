from __future__ import annotations

from copy import deepcopy

import pytest

from flowgency.integrations.tool_catalog import ToolCatalog, ToolDescriptor
from flowgency.permissions.forms import (
    PermissionDraft,
    PermissionFormError,
    RuleDraft,
    build_form,
    serialize_permissions,
)


def _catalog(*tools: ToolDescriptor, complete: bool = True) -> ToolCatalog:
    return ToolCatalog("fixture", "v1", tools, complete)


@pytest.mark.parametrize(
    "rule",
    [
        {"path": "."},
        {"path": ".", "tools": None},
        {"path": ".", "tools": ["read", "write"]},
        {"path": ".", "tools": []},
        {"tools": ["custom_tool"]},
        {"path": None, "tools": []},
    ],
)
def test_unchanged_rule_is_lossless(rule):
    catalog = _catalog(ToolDescriptor("read"), ToolDescriptor("write"))
    agent = {"permissions": {"rules": [rule]}, "runtime": {"timeout": 2400}}
    original = deepcopy(agent)

    draft = build_form(agent, catalog).draft

    assert serialize_permissions(agent, draft, catalog) == agent["permissions"]
    assert agent == original


def test_build_form_absent_permissions_defaults_to_inherit_and_empty_rules():
    catalog = _catalog(ToolDescriptor("read"))

    form = build_form({}, catalog)

    assert form.draft == PermissionDraft(mode="inherit", rules=[])
    assert form.choices == ()
    assert form.unbounded == ()
    assert serialize_permissions({}, form.draft, catalog) is None


def test_build_form_includes_applicable_catalog_names_then_original_configured_names():
    catalog = _catalog(
        ToolDescriptor("read", ("path",)),
        ToolDescriptor("search", ("no_path",)),
        ToolDescriptor("write"),
        complete=False,
    )
    agent = {
        "permissions": {
            "rules": [
                {"path": "docs", "tools": ["search", "custom", "search"]},
                {"path": None, "tools": None},
            ]
        }
    }

    form = build_form(agent, catalog)

    assert form.choices == (
        ("read", "write", "search", "custom"),
        ("search", "write"),
    )
    assert form.draft.rules[0] == RuleDraft(
        source_index=0,
        target="path",
        path="docs",
        selected=["search", "custom"],
    )
    assert form.draft.rules[1] == RuleDraft(
        source_index=1,
        target="no_path",
        path=None,
        selected=["search", "write"],
    )
    assert form.unbounded == (False, True)


def test_path_only_edit_never_unbounds_explicit_tools():
    catalog = _catalog(ToolDescriptor("read"))
    agent = {"permissions": {"rules": [{"path": ".", "tools": ["read"]}]}}

    draft = build_form(agent, catalog).draft
    draft.rules[0].path = "docs"

    assert serialize_permissions(agent, draft, catalog)["rules"] == [
        {"path": "docs", "tools": ["read"]}
    ]


@pytest.mark.parametrize(
    ("complete", "expected"),
    [
        (True, {}),
        (False, {"tools": ["read", "write"]}),
    ],
)
def test_new_full_selection_respects_catalog_completeness(complete, expected):
    catalog = _catalog(ToolDescriptor("read"), ToolDescriptor("write"), complete=complete)
    draft = PermissionDraft(
        mode="inherit",
        rules=[RuleDraft(target="no_path", selected=["read", "write"])],
    )

    assert serialize_permissions({}, draft, catalog) == {"rules": [expected]}


def test_incomplete_empty_catalog_preserves_existing_unbounded_rule_when_no_choices_exist():
    catalog = _catalog(complete=False)
    agent = {"permissions": {"rules": [{"tools": None}]}}
    draft = PermissionDraft(
        mode="inherit",
        rules=[RuleDraft(source_index=0, target="no_path", selected=[])],
    )

    assert serialize_permissions(agent, draft, catalog) == agent["permissions"]


def test_clearing_unbounded_rule_with_visible_choices_becomes_explicit_empty_list():
    catalog = _catalog(ToolDescriptor("read"), complete=False)
    agent = {"permissions": {"rules": [{"tools": None}]}}
    draft = build_form(agent, catalog).draft
    draft.rules[0].selected = []

    assert serialize_permissions(agent, draft, catalog) == {"rules": [{"tools": []}]}


def test_removing_one_of_repeated_original_rules_preserves_the_other_row():
    catalog = _catalog(ToolDescriptor("read"))
    agent = {
        "permissions": {
            "rules": [
                {"path": "docs", "tools": ["read"]},
                {"path": "docs", "tools": []},
            ]
        }
    }
    draft = PermissionDraft(
        mode="inherit",
        rules=[RuleDraft(source_index=1, target="path", path="docs", selected=[])],
    )

    assert serialize_permissions(agent, draft, catalog) == {"rules": [{"path": "docs", "tools": []}]}


@pytest.mark.parametrize(
    ("agent", "draft", "field"),
    [
        (
            {"permissions": {"rules": [{"tools": []}]}} ,
            PermissionDraft(
                mode="inherit",
                rules=[RuleDraft(source_index=-1, target="no_path", selected=[])],
            ),
            "rules.0.source_index",
        ),
        (
            {"permissions": {"rules": [{"tools": []}]}} ,
            PermissionDraft(
                mode="inherit",
                rules=[RuleDraft(source_index=1, target="no_path", selected=[])],
            ),
            "rules.0.source_index",
        ),
        (
            {"permissions": {"rules": [{"tools": []}, {"tools": []}]}} ,
            PermissionDraft(
                mode="inherit",
                rules=[
                    RuleDraft(source_index=1, target="no_path", selected=[]),
                    RuleDraft(source_index=0, target="no_path", selected=[]),
                ],
            ),
            "rules.1.source_index",
        ),
        (
            {"permissions": {"rules": [{"tools": []}, {"tools": []}]}} ,
            PermissionDraft(
                mode="inherit",
                rules=[
                    RuleDraft(source_index=0, target="no_path", selected=[]),
                    RuleDraft(source_index=0, target="no_path", selected=[]),
                ],
            ),
            "rules.1.source_index",
        ),
        (
            {"permissions": {"rules": [{"tools": []}]}} ,
            PermissionDraft(
                mode="inherit",
                rules=[RuleDraft(target="path", path="   ", selected=[])],
            ),
            "rules.0.path",
        ),
        (
            {"permissions": {"rules": [{"tools": []}]}} ,
            PermissionDraft(
                mode="inherit",
                rules=[RuleDraft(target="no_path", path="docs", selected=[])],
            ),
            "rules.0.path",
        ),
        (
            {"permissions": {"rules": [{"tools": []}]}} ,
            PermissionDraft(
                mode="inherit",
                rules=[RuleDraft(target="no_path", selected=["read", "read"])],
            ),
            "rules.0.selected",
        ),
        (
            {"permissions": {"rules": [{"tools": []}]}} ,
            PermissionDraft(
                mode="inherit",
                rules=[RuleDraft(target="no_path", selected=[""])],
            ),
            "rules.0.selected",
        ),
    ],
)
def test_serialize_permissions_rejects_invalid_drafts(agent, draft, field):
    catalog = _catalog(ToolDescriptor("read"))

    with pytest.raises(PermissionFormError) as excinfo:
        serialize_permissions(agent, draft, catalog)

    assert [issue.field for issue in excinfo.value.issues] == [field]


@pytest.mark.parametrize(
    "selected",
    [
        [1],
        [None],
        [["read"]],
        [{"name": "read"}],
        ["read", "read"],
    ],
)
def test_serialize_permissions_rejects_mutated_invalid_selected_entries(selected):
    catalog = _catalog(ToolDescriptor("read"))
    agent = {"permissions": {"rules": [{"tools": ["read"]}]}}
    draft = build_form(agent, catalog).draft

    draft.rules[0].selected = selected

    with pytest.raises(PermissionFormError) as excinfo:
        serialize_permissions(agent, draft, catalog)

    assert [issue.field for issue in excinfo.value.issues] == ["rules.0.selected"]


def test_round_trip_back_to_baseline_restores_the_original_block_shape():
    catalog = _catalog(ToolDescriptor("read"), complete=False)
    agent = {"permissions": {"rules": [{"path": None, "tools": None}]}}
    draft = build_form(agent, catalog).draft

    draft.rules[0].selected = []
    assert serialize_permissions(agent, draft, catalog) == {"rules": [{"tools": []}]}

    draft.rules[0].selected = ["read"]
    assert serialize_permissions(agent, draft, catalog) == agent["permissions"]