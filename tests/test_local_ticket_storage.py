from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from flowgency.tickets.errors import (
    StorageUnavailable,
    TicketCorrupt,
    TicketForbidden,
    TicketNotFound,
)
from flowgency.tickets.models import TicketOperation, TicketRef
from flowgency.tickets.storages import local
from tests._ticket_helpers import storage_binding, ticket_record

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _seed(root: Path, ticket_id: str = "ticket-a"):
    provider = local.LocalTicketStorage(root, clock=lambda: NOW)
    binding = storage_binding(root)
    record = ticket_record(ticket_id=ticket_id)
    ref = TicketRef.from_binding(binding, ticket_id)
    provider.create(record.with_ref(ref), TicketOperation(f"seed-{ticket_id}", "seed-d"))
    return provider, ref


def _make_reparse(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        os.symlink(target, link)


def test_failed_replace_keeps_previous_ticket(tmp_path, monkeypatch):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    original = ticket_record()
    binding = storage_binding(tmp_path)
    ref = TicketRef.from_binding(binding, original.id)
    provider.create(original.with_ref(ref), TicketOperation("create-a", "digest-a"))
    before = provider.read(ref)

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(local, "atomic_write_text", fail_write)
    with pytest.raises(StorageUnavailable):
        provider.apply(
            ref,
            before.revision,
            TicketOperation("edit-a", "digest-b"),
            lambda ticket: ticket.model_copy(update={"title": "Changed"}),
        )
    assert provider.read(ref) == before


def test_document_is_frontmatter_with_markdown_body(tmp_path):
    provider, ref = _seed(tmp_path)
    text = provider._ticket_path(ref).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "schema_version: 1" in text
    body = text.split("---", 2)[2]
    assert "Body text for ticket A." in body


def test_truncated_yaml_is_corrupt(tmp_path):
    provider, ref = _seed(tmp_path)
    provider._ticket_path(ref).write_text("---\nid: ticket-a", encoding="utf-8")
    with pytest.raises(TicketCorrupt):
        provider.read(ref)


def test_oversized_document_is_corrupt(tmp_path):
    provider, ref = _seed(tmp_path)
    provider._ticket_path(ref).write_text("x" * (512 * 1024 + 16), encoding="utf-8")
    with pytest.raises(TicketCorrupt):
        provider.read(ref)


def test_non_slug_component_is_forbidden(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    ref = TicketRef(team_id="team-a", workflow_id="board-a", ticket_id="Bad_Id")
    with pytest.raises(TicketForbidden):
        provider.read(ref)


def test_traversal_component_is_forbidden(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    ref = TicketRef(team_id="team-a", workflow_id="board-a", ticket_id="../escape")
    with pytest.raises(TicketForbidden):
        provider.read(ref)


def test_reparse_point_below_root_is_forbidden(tmp_path):
    provider, ref = _seed(tmp_path)
    namespace = provider._namespace_dir(ref.team_id, ref.workflow_id)
    real = tmp_path / "real-tickets"
    namespace.rename(real)
    _make_reparse(namespace, real)
    with pytest.raises(TicketForbidden):
        provider.read(ref)


def test_missing_root_is_unavailable_and_not_created(tmp_path):
    missing = tmp_path / "not-configured"
    provider = local.LocalTicketStorage(missing, clock=lambda: NOW)
    ref = TicketRef(team_id="team-a", workflow_id="board-a", ticket_id="ticket-a")
    with pytest.raises(StorageUnavailable):
        provider.read(ref)
    assert not missing.exists()


def test_list_missing_root_is_unavailable(tmp_path):
    provider = local.LocalTicketStorage(tmp_path / "gone", clock=lambda: NOW)
    with pytest.raises(StorageUnavailable):
        provider.list("team-a", "board-a")


def test_list_on_empty_existing_root_is_empty(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    assert provider.list("team-a", "board-a") == ()


def test_read_missing_ticket_in_existing_root(tmp_path):
    provider, _ = _seed(tmp_path)
    other = TicketRef(team_id="team-a", workflow_id="board-a", ticket_id="ticket-z")
    with pytest.raises(TicketNotFound):
        provider.read(other)


def test_check_reports_missing_then_ok(tmp_path):
    missing = local.LocalTicketStorage(tmp_path / "absent", clock=lambda: NOW)
    assert missing.check().status == "missing"
    present = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    assert present.check().status == "ok"


def test_list_corruption_names_ticket_without_private_path(tmp_path):
    provider, ref = _seed(tmp_path)
    namespace = provider._namespace_dir(ref.team_id, ref.workflow_id)
    (namespace / "ticket-bad.md").write_text("---\nbroken", encoding="utf-8")
    with pytest.raises(TicketCorrupt) as excinfo:
        provider.list(ref.team_id, ref.workflow_id)
    error = excinfo.value
    assert error.details.get("ticket_id") == "ticket-bad"
    assert str(tmp_path) not in str(error)
    assert str(tmp_path) not in str(error.details)
