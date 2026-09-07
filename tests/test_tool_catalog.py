from __future__ import annotations

from dataclasses import replace

import pytest

from flowgency.integrations import BaseIntegration, get_integration
from flowgency.integrations.tool_catalog import (
    ToolCatalog,
    ToolDescriptor,
    available_names,
    catalog_id,
    get_tool_catalog,
)


def test_default_catalog_does_not_claim_completeness():
    catalog = get_tool_catalog(BaseIntegration())

    assert catalog.complete is False
    assert catalog.tools == ()


def test_catalog_identity_includes_completeness_and_vocabulary():
    catalog = ToolCatalog("fixture", "v1", (ToolDescriptor("read"),), True)

    assert catalog_id(catalog) != catalog_id(replace(catalog, complete=False))
    assert catalog_id(catalog) != catalog_id(replace(catalog, tools=()))


def test_detection_failure_is_not_empty_success(monkeypatch):
    integration = BaseIntegration()

    def fail():
        raise OSError("probe unavailable")

    monkeypatch.setattr(integration, "permission_tool_catalog", fail)

    catalog = get_tool_catalog(integration)

    assert not catalog.complete
    assert catalog.warning


def test_invalid_duplicate_metadata_follows_the_incomplete_failure_path(monkeypatch):
    integration = BaseIntegration()

    monkeypatch.setattr(
        integration,
        "permission_tool_catalog",
        lambda: ToolCatalog(
            "fixture",
            "v1",
            (ToolDescriptor("read"), ToolDescriptor("read")),
            True,
        ),
    )

    catalog = get_tool_catalog(integration)

    assert catalog.integration == integration.name
    assert catalog.version == "unavailable"
    assert catalog.complete is False
    assert catalog.tools == ()
    assert catalog.warning == "Tool availability could not be determined."


def test_unknown_target_applicability_offers_the_name_on_both_rule_types():
    catalog = ToolCatalog("fixture", "v1", (ToolDescriptor("read"),), True)

    assert available_names(catalog, "path") == ("read",)
    assert available_names(catalog, "no_path") == ("read",)


def test_filtered_target_lists_only_include_matching_names():
    catalog = ToolCatalog(
        "fixture",
        "v1",
        (
            ToolDescriptor("read", ("path",)),
            ToolDescriptor("search", ("no_path",)),
            ToolDescriptor("write"),
        ),
        True,
    )

    assert available_names(catalog, "path") == ("read", "write")
    assert available_names(catalog, "no_path") == ("search", "write")


def test_catalog_identity_changes_when_targets_change():
    catalog = ToolCatalog("fixture", "v1", (ToolDescriptor("read"),), True)

    assert catalog_id(catalog) != catalog_id(
        replace(catalog, tools=(ToolDescriptor("read", ("path",)),))
    )


def test_complete_empty_catalog_stays_empty_for_every_target():
    catalog = ToolCatalog("fixture", "v1", (), True)

    assert available_names(catalog, "path") == ()
    assert available_names(catalog, "no_path") == ()


def test_copilot_catalog_is_truthful_but_incomplete():
    catalog = get_tool_catalog(get_integration("copilot"))

    assert catalog.integration == "copilot"
    assert catalog.version == "flowgency-permission-names-v1"
    assert catalog.complete is False
    assert tuple(tool.name for tool in catalog.tools) == ("read", "search", "write")
    assert all(tool.targets == () for tool in catalog.tools)
    assert catalog.warning == "Additional integration tools may exist."


def test_unknown_target_value_follows_the_incomplete_failure_path(monkeypatch):
    integration = BaseIntegration()

    monkeypatch.setattr(
        integration,
        "permission_tool_catalog",
        lambda: ToolCatalog(
            "fixture",
            "v1",
            (ToolDescriptor("read", ("shell",)),),
            False,
        ),
    )

    catalog = get_tool_catalog(integration)

    assert catalog.version == "unavailable"
    assert catalog.complete is False
    assert catalog.tools == ()
    assert catalog.warning == "Tool availability could not be determined."


def test_descriptor_name_must_be_nonempty():
    with pytest.raises(ValueError, match="nonempty"):
        ToolDescriptor("")