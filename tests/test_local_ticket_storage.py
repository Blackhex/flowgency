from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from flowgency.tickets.errors import (
    StorageUnavailable,
    TicketConflict,
    TicketCorrupt,
    TicketForbidden,
    TicketNotFound,
    TicketTooLarge,
)
from flowgency.tickets.models import ActiveTicketRun, TicketOperation, TicketRef
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
    ref = TicketRef.from_binding(storage_binding(tmp_path), "Bad_Id")
    with pytest.raises(TicketForbidden):
        provider.read(ref)


def test_traversal_component_is_forbidden(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    ref = TicketRef.from_binding(storage_binding(tmp_path), "../escape")
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
    ref = TicketRef.from_binding(storage_binding(missing), "ticket-a")
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
    other = TicketRef.from_binding(storage_binding(tmp_path), "ticket-z")
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


# -- Finding 1: oversized-write pre-write rejection -----------------------


def test_near_limit_ticket_persists(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    binding = storage_binding(tmp_path)
    # The receipt snapshot embeds the body once more, so the on-disk document is
    # roughly twice the description; stay safely under the 512 KiB read cap.
    body = "y" * (200 * 1024)
    record = ticket_record(ticket_id="ticket-big").model_copy(
        update={"description": body}
    )
    ref = TicketRef.from_binding(binding, record.id)
    provider.create(record.with_ref(ref), TicketOperation("c-big", "d-big"))
    assert len(provider.read(ref).description) == 200 * 1024


def test_oversized_write_is_rejected_and_preserves_prior(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    binding = storage_binding(tmp_path)
    record = ticket_record(ticket_id="ticket-grow")
    ref = TicketRef.from_binding(binding, record.id)
    provider.create(record.with_ref(ref), TicketOperation("c-grow", "d-grow"))
    first = provider.apply(
        ref,
        provider.read(ref).revision,
        TicketOperation("ok-op", "ok-d"),
        lambda ticket: ticket.model_copy(update={"title": "Kept"}),
    )
    before = provider.read(ref)
    with pytest.raises(TicketTooLarge):
        provider.apply(
            ref,
            before.revision,
            TicketOperation("too-big", "d-big"),
            lambda ticket: ticket.model_copy(update={"description": "z" * (512 * 1024)}),
        )
    after = provider.read(ref)
    assert after == before
    assert after.revision == before.revision
    assert len(after.receipts) == len(before.receipts)
    replay = provider.apply(
        ref,
        before.revision,
        TicketOperation("ok-op", "ok-d"),
        lambda ticket: pytest.fail("refused write must leave replay intact"),
    )
    assert replay.replayed is True
    assert replay.ticket == first.ticket


def test_oversized_create_is_rejected(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    binding = storage_binding(tmp_path)
    record = ticket_record(ticket_id="ticket-huge").model_copy(
        update={"description": "z" * (512 * 1024)}
    )
    ref = TicketRef.from_binding(binding, record.id)
    with pytest.raises(TicketTooLarge):
        provider.create(record.with_ref(ref), TicketOperation("c-huge", "d-huge"))
    assert provider.list("team-a", "board-a") == ()


# -- Finding 2: physical binding identity ---------------------------------


def test_refs_are_bound_to_their_storage_root(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    prov_a = local.LocalTicketStorage(root_a, clock=lambda: NOW)
    prov_b = local.LocalTicketStorage(root_b, clock=lambda: NOW)
    record = ticket_record()
    ref_a = TicketRef.from_binding(storage_binding(root_a), record.id)
    ref_b = TicketRef.from_binding(storage_binding(root_b), record.id)
    prov_a.create(record.with_ref(ref_a), TicketOperation("ca", "da"))
    prov_b.create(record.with_ref(ref_b), TicketOperation("cb", "db"))
    assert ref_a.binding_id != ref_b.binding_id
    with pytest.raises(TicketForbidden):
        prov_b.read(ref_a)
    with pytest.raises(TicketForbidden):
        prov_a.read(ref_b)


def test_envelope_carries_binding_identity(tmp_path):
    provider, ref = _seed(tmp_path)
    text = provider._ticket_path(ref).read_text(encoding="utf-8")
    assert "binding_id" in text
    assert ref.binding_id in text


def test_field_values_roundtrip_strict_scalars_and_artifacts(tmp_path):
    from flowgency.workflows.models import ArtifactRef

    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    binding = storage_binding(tmp_path)
    values = {
        "flag": True,
        "count": 3,
        "ratio": 1.5,
        "label": "text",
        "ref_id": ArtifactRef(kind="id", value="artifact-1"),
        "ref_url": ArtifactRef(kind="url", value="https://example.com/a"),
        "missing": None,
    }
    record = ticket_record(ticket_id="ticket-fv").model_copy(
        update={"field_values": values}
    )
    ref = TicketRef.from_binding(binding, record.id)
    provider.create(record.with_ref(ref), TicketOperation("c-fv", "d-fv"))
    got = provider.read(ref).field_values
    assert got["flag"] is True
    assert isinstance(got["count"], int) and not isinstance(got["count"], bool)
    assert got["count"] == 3
    assert got["ratio"] == 1.5
    assert got["label"] == "text"
    assert got["ref_id"] == ArtifactRef(kind="id", value="artifact-1")
    assert got["ref_url"] == ArtifactRef(kind="url", value="https://example.com/a")
    assert got["missing"] is None


def test_active_run_and_field_provenance_roundtrip(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    binding = storage_binding(tmp_path)
    record = ticket_record(ticket_id="ticket-owned", agent="builder").model_copy(
        update={
            "active_run": ActiveTicketRun(
                job_id="run-a",
                session_id="session-a",
                started_at=NOW,
            )
        }
    )
    ref = TicketRef.from_binding(binding, record.id)
    provider.create(record.with_ref(ref), TicketOperation("c-owned", "d-owned"))
    loaded = provider.read(ref)
    assert loaded.active_run is not None
    assert loaded.active_run.job_id == "run-a"
    assert loaded.active_run.generation == "session-a"
    assert loaded.field_provenance["summary"].actor_kind == "user"


def test_with_ref_returns_revalidated_independent_copy(tmp_path):
    binding = storage_binding(tmp_path)
    record = ticket_record()
    ref = TicketRef.from_binding(binding, record.id)
    scoped = record.with_ref(ref)
    assert scoped is not record
    assert scoped.ref == ref
    assert record.ref is None
    with pytest.raises(ValueError):
        record.with_ref(TicketRef.from_binding(binding, "different-id"))


# -- Finding 4: list safe-read and metadata path guards -------------------


def test_list_rejects_reparse_entry(tmp_path):
    provider, ref = _seed(tmp_path)
    namespace = provider._namespace_dir(ref.team_id, ref.workflow_id)
    target = tmp_path / "outside"
    target.mkdir()
    _make_reparse(namespace / "evil.md", target)
    with pytest.raises(TicketForbidden):
        provider.list(ref.team_id, ref.workflow_id)


def test_list_rejects_oversized_entry(tmp_path):
    provider, ref = _seed(tmp_path)
    namespace = provider._namespace_dir(ref.team_id, ref.workflow_id)
    (namespace / "ticket-big.md").write_text("x" * (512 * 1024 + 16), encoding="utf-8")
    with pytest.raises(TicketCorrupt):
        provider.list(ref.team_id, ref.workflow_id)


def test_list_rejects_invalid_slug_entry(tmp_path):
    provider, ref = _seed(tmp_path)
    namespace = provider._namespace_dir(ref.team_id, ref.workflow_id)
    (namespace / "BadName.md").write_text(
        "---\nschema_version: 1\n---\n", encoding="utf-8"
    )
    with pytest.raises(TicketForbidden):
        provider.list(ref.team_id, ref.workflow_id)


def test_apply_guards_reparse_lock_leaf(tmp_path):
    provider, ref = _seed(tmp_path)
    namespace = provider._namespace_dir(ref.team_id, ref.workflow_id)
    target = tmp_path / "lock-target"
    target.mkdir()
    _make_reparse(namespace / f"{ref.ticket_id}.lock", target)
    with pytest.raises(TicketForbidden):
        provider.apply(
            ref,
            1,
            TicketOperation("op-x", "d-x"),
            lambda ticket: ticket.model_copy(update={"title": "X"}),
        )


def test_create_guards_reparse_sequence_leaf(tmp_path):
    provider, ref = _seed(tmp_path)
    namespace = provider._namespace_dir(ref.team_id, ref.workflow_id)
    (namespace / ".sequence").unlink()
    target = tmp_path / "seq-target"
    target.mkdir()
    _make_reparse(namespace / ".sequence", target)
    new_ref = TicketRef.from_binding(storage_binding(tmp_path), "ticket-b")
    with pytest.raises(TicketForbidden):
        provider.create(
            ticket_record(ticket_id="ticket-b").with_ref(new_ref),
            TicketOperation("c-b", "d-b"),
        )


# -- Finding 5: namespace creation and sequence safety --------------------


def test_apply_missing_namespace_does_not_create_it(tmp_path):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)
    ref = TicketRef.from_binding(storage_binding(tmp_path), "ticket-a")
    with pytest.raises(TicketNotFound):
        provider.apply(ref, 1, TicketOperation("op", "d"), lambda ticket: ticket)
    assert not provider._namespace_dir("team-a", "board-a").exists()


def test_missing_sequence_on_populated_namespace_fails_closed(tmp_path):
    provider, ref = _seed(tmp_path)
    provider._sequence_path(ref).unlink()
    new_ref = TicketRef.from_binding(storage_binding(tmp_path), "ticket-b")
    with pytest.raises(TicketCorrupt):
        provider.create(
            ticket_record(ticket_id="ticket-b").with_ref(new_ref),
            TicketOperation("c-b", "d-b"),
        )


def test_corrupt_sequence_on_populated_namespace_fails_closed(tmp_path):
    provider, ref = _seed(tmp_path)
    provider._sequence_path(ref).write_text("not-a-number", encoding="utf-8")
    new_ref = TicketRef.from_binding(storage_binding(tmp_path), "ticket-b")
    with pytest.raises(TicketCorrupt):
        provider.create(
            ticket_record(ticket_id="ticket-b").with_ref(new_ref),
            TicketOperation("c-b", "d-b"),
        )


# -- Finding 6: distinct typed availability outcomes ----------------------


def test_permission_error_on_read_is_unavailable(tmp_path, monkeypatch):
    provider, ref = _seed(tmp_path)
    real_read_text = Path.read_text

    def denied(self, *args, **kwargs):
        if self.name.endswith(".md"):
            raise PermissionError("denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(StorageUnavailable):
        provider.read(ref)


def test_invalid_utf8_document_is_corrupt(tmp_path):
    provider, ref = _seed(tmp_path)
    provider._ticket_path(ref).write_bytes(b"\xff\xfe not valid utf8")
    with pytest.raises(TicketCorrupt):
        provider.read(ref)


def test_check_detects_unreadable_root(tmp_path, monkeypatch):
    provider = local.LocalTicketStorage(tmp_path, clock=lambda: NOW)

    def denied(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(local.os, "scandir", denied)
    assert provider.check().status == "unreadable"
