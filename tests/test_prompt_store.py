from __future__ import annotations

from pathlib import Path
import stat
from threading import Barrier, Event, Thread, current_thread

import pytest

from agency.prompts import PromptConflictError, PromptNotFoundError, PromptStore


def private_prompt_bytes(name: str = "local-triage") -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Triage local work.\n"
        "---\n\n"
        "Review the private work queue.\n"
    ).encode("utf-8")


def _make_hostile_infra_entry(path: Path, target: Path, monkeypatch) -> str:
    try:
        path.symlink_to(target, target_is_directory=True)
        return "real-link"
    except OSError:
        original = Path.lstat
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)

        class FakeStatResult:
            def __init__(self, result):
                self.st_mode = result.st_mode
                self.st_file_attributes = reparse_flag

        def fake_lstat(self):
            result = original(self)
            if self == path and reparse_flag:
                return FakeStatResult(result)
            return result

        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "lstat", fake_lstat)
        return "simulated-reparse"


def _replace_instance_with_hostile_entry(
    store: PromptStore,
    tmp_path: Path,
    monkeypatch,
    *,
    prompt_name: str = "local-triage",
) -> tuple[Path, str]:
    path = store.path("newsletter", "reviewer", prompt_name)
    external = tmp_path / "external-instance"
    external.mkdir()
    (external / f"{prompt_name}.prompt.md").write_bytes(private_prompt_bytes(prompt_name))
    reviewer_dir = path.parent
    for child in reviewer_dir.iterdir():
        child.unlink()
    reviewer_dir.rmdir()
    mode = _make_hostile_infra_entry(reviewer_dir, external, monkeypatch)
    return external, mode


def test_prompt_store_round_trips_registered_source(tmp_path):
    store = PromptStore(tmp_path / "prompts")

    created = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )

    assert created.path == (
        tmp_path
        / "prompts"
        / "newsletter"
        / "reviewer"
        / "local-triage.prompt.md"
    ).resolve(strict=False)
    assert store.read("newsletter", "reviewer", "local-triage") == created


def test_prompt_store_rejects_stale_digest(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    store.create("newsletter", "reviewer", "local-triage", private_prompt_bytes())

    with pytest.raises(PromptConflictError, match="changed; reload"):
        store.update(
            "newsletter",
            "reviewer",
            "local-triage",
            expected_digest="0" * 64,
            payload=private_prompt_bytes(),
        )


@pytest.mark.parametrize(
    "team,instance,name",
    [
        ("", "reviewer", "local-triage"),
        ("newsletter", "", "local-triage"),
        ("newsletter", "reviewer", ""),
        ("News", "reviewer", "local-triage"),
        ("newsletter", "Reviewer", "local-triage"),
        ("newsletter", "reviewer", "local/Triage"),
        ("newsletter", "reviewer", "local-triage "),
        ("newsletter", "..", "local-triage"),
    ],
)
def test_prompt_store_rejects_invalid_slugs(tmp_path, team, instance, name):
    store = PromptStore(tmp_path / "prompts")

    with pytest.raises(ValueError, match="stable slug"):
        store.path(team, instance, name)


def test_prompt_store_rejects_noncanonical_case_before_filesystem_access(
    tmp_path,
    monkeypatch,
):
    store = PromptStore(tmp_path / "prompts")

    def fail_mkdir(self, *args, **kwargs):
        raise AssertionError("filesystem should not be touched")

    def fail_atomic_write(path, payload):
        raise AssertionError("filesystem should not be touched")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    monkeypatch.setattr("agency.prompts.store.atomic_write_bytes", fail_atomic_write)

    with pytest.raises(ValueError, match="stable slug"):
        store.create(
            "newsletter",
            "reviewer",
            "Local-Triage",
            private_prompt_bytes(),
        )


def test_prompt_store_rejects_missing_and_non_regular_files(tmp_path):
    store = PromptStore(tmp_path / "prompts")

    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer", "local-triage")

    file_path = store.path("newsletter", "reviewer", "local-triage")
    file_path.mkdir(parents=True)

    with pytest.raises(ValueError, match="regular file"):
        store.read("newsletter", "reviewer", "local-triage")


def test_prompt_store_rejects_hostile_root_symlink_or_reparse(
    tmp_path,
    monkeypatch,
):
    external = tmp_path / "external-root"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    root = tmp_path / "prompts"
    mode = _make_hostile_infra_entry(root, external, monkeypatch)
    store = PromptStore(root)

    with pytest.raises(ValueError, match="prompts"):
        store.create(
            "newsletter",
            "reviewer",
            "local-triage",
            private_prompt_bytes(),
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert mode in {"real-link", "simulated-reparse"}


def test_prompt_store_rejects_hostile_team_directory_symlink_or_reparse(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "prompts"
    external = tmp_path / "external-team"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    hostile_team = root / "newsletter"
    root.mkdir(parents=True)
    mode = _make_hostile_infra_entry(hostile_team, external, monkeypatch)
    store = PromptStore(root)

    with pytest.raises(ValueError, match="prompts"):
        store.create(
            "newsletter",
            "reviewer",
            "local-triage",
            private_prompt_bytes(),
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert mode in {"real-link", "simulated-reparse"}


@pytest.mark.parametrize("operation", ["read", "update", "delete", "delete_namespace"])
def test_prompt_store_rejects_hostile_parent_for_all_operations(
    tmp_path,
    monkeypatch,
    operation,
):
    store = PromptStore(tmp_path / "prompts")
    created = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )
    external, mode = _replace_instance_with_hostile_entry(store, tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="unsafe prompts|escapes store root"):
        if operation == "read":
            store.read("newsletter", "reviewer", "local-triage")
        elif operation == "update":
            store.update(
                "newsletter",
                "reviewer",
                "local-triage",
                expected_digest=created.document.digest,
                payload=private_prompt_bytes(),
            )
        elif operation == "delete":
            store.delete(
                "newsletter",
                "reviewer",
                "local-triage",
                expected_digest=created.document.digest,
            )
        else:
            store.delete_namespace(
                "newsletter",
                "reviewer",
                registered=(("local-triage", created.document.digest),),
            )

    assert (external / "local-triage.prompt.md").exists()
    assert mode in {"real-link", "simulated-reparse"}


def test_prompt_store_delete_requires_expected_digest(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    created = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )

    with pytest.raises(PromptConflictError, match="changed; reload"):
        store.delete(
            "newsletter",
            "reviewer",
            "local-triage",
            expected_digest="f" * 64,
        )

    assert store.read("newsletter", "reviewer", "local-triage").document.digest == created.document.digest

    deleted = store.delete(
        "newsletter",
        "reviewer",
        "local-triage",
        expected_digest=created.document.digest,
    )

    assert deleted.document.digest == created.document.digest
    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer", "local-triage")


def test_prompt_store_delete_namespace_deletes_registered_prompts(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    first = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )
    second = store.create(
        "newsletter", "reviewer", "hotfix", private_prompt_bytes("hotfix")
    )

    deleted = store.delete_namespace(
        "newsletter",
        "reviewer",
        registered=(
            ("local-triage", first.document.digest),
            ("hotfix", second.document.digest),
        ),
    )

    assert deleted == (
        store.path("newsletter", "reviewer", "local-triage"),
        store.path("newsletter", "reviewer", "hotfix"),
    )
    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer", "local-triage")
    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer", "hotfix")


def test_prompt_store_delete_namespace_rejects_stale_digest(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    created = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )

    with pytest.raises(PromptConflictError, match="changed; reload"):
        store.delete_namespace(
            "newsletter",
            "reviewer",
            registered=(("local-triage", "0" * 64),),
        )

    assert (
        store.read("newsletter", "reviewer", "local-triage").document.digest
        == created.document.digest
    )


def test_prompt_store_delete_namespace_deletes_registered_only(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    registered = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )
    leftover = store.create(
        "newsletter", "reviewer", "hotfix", private_prompt_bytes("hotfix")
    )

    deleted = store.delete_namespace(
        "newsletter",
        "reviewer",
        registered=(("local-triage", registered.document.digest),),
    )

    assert deleted == (store.path("newsletter", "reviewer", "local-triage"),)
    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer", "local-triage")
    assert store.read("newsletter", "reviewer", "hotfix").document.digest == leftover.document.digest


def test_prompt_store_delete_namespace_restores_deleted_files_on_failure(
    tmp_path,
    monkeypatch,
):
    store = PromptStore(tmp_path / "prompts")
    first = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )
    second = store.create(
        "newsletter", "reviewer", "hotfix", private_prompt_bytes("hotfix")
    )
    target = store.path("newsletter", "reviewer", "hotfix")
    original_unlink = Path.unlink

    def fail_second_unlink(self, *args, **kwargs):
        if self == target:
            raise OSError("simulated delete failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_unlink)

    with pytest.raises(RuntimeError, match="restored"):
        store.delete_namespace(
            "newsletter",
            "reviewer",
            registered=(
                ("local-triage", first.document.digest),
                ("hotfix", second.document.digest),
            ),
        )

    assert store.read("newsletter", "reviewer", "local-triage").document.digest == first.document.digest
    assert store.read("newsletter", "reviewer", "hotfix").document.digest == second.document.digest


def test_prompt_store_copy_namespace_detects_collision(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    src_a = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes("local-triage")
    )
    src_b = store.create(
        "newsletter", "reviewer", "hotfix", private_prompt_bytes("hotfix")
    )
    store.create(
        "newsletter",
        "reviewer-target",
        "hotfix",
        private_prompt_bytes("hotfix"),
    )

    with pytest.raises(PromptConflictError, match="already exists"):
        store.copy_namespace(
            "newsletter",
            "reviewer",
            "newsletter",
            "reviewer-target",
            registered=(
                ("local-triage", src_a.document.digest),
                ("hotfix", src_b.document.digest),
            ),
        )

    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer-target", "local-triage")


def test_prompt_store_copy_namespace_rolls_back_created_files_on_error(
    tmp_path,
    monkeypatch,
):
    store = PromptStore(tmp_path / "prompts")
    src_a = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes("local-triage")
    )
    src_b = store.create(
        "newsletter", "reviewer", "hotfix", private_prompt_bytes("hotfix")
    )

    from agency.prompts import store as prompt_store_module

    real_atomic_write = prompt_store_module.atomic_write_bytes
    state = {"writes": 0}

    def fail_second_write(path, payload):
        state["writes"] += 1
        if state["writes"] == 2:
            raise OSError("simulated write failure")
        real_atomic_write(path, payload)

    monkeypatch.setattr(
        "agency.prompts.store.atomic_write_bytes",
        fail_second_write,
    )

    with pytest.raises(RuntimeError, match="rolled back"):
        store.copy_namespace(
            "newsletter",
            "reviewer",
            "newsletter",
            "reviewer-target",
            registered=(
                ("local-triage", src_a.document.digest),
                ("hotfix", src_b.document.digest),
            ),
        )

    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer-target", "local-triage")
    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer-target", "hotfix")


def test_prompt_store_update_detects_concurrent_change(tmp_path):
    store = PromptStore(tmp_path / "prompts")
    created = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )

    barrier = Barrier(3)
    errors: list[Exception] = []

    def worker(content_suffix: str):
        try:
            payload = (
                "---\n"
                "name: local-triage\n"
                "description: Triage local work.\n"
                "---\n\n"
                f"Review queue {content_suffix}.\n"
            ).encode("utf-8")
            barrier.wait(timeout=5)
            store.update(
                "newsletter",
                "reviewer",
                "local-triage",
                expected_digest=created.document.digest,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    first = Thread(target=worker, args=("A",))
    second = Thread(target=worker, args=("B",))
    first.start()
    second.start()
    barrier.wait(timeout=5)
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(errors) == 1
    assert isinstance(errors[0], PromptConflictError)
    stored = store.read("newsletter", "reviewer", "local-triage")
    assert "Review queue" in stored.document.body


def test_prompt_store_delete_namespace_blocks_silent_update_loss(
    tmp_path,
    monkeypatch,
):
    store = PromptStore(tmp_path / "prompts")
    created = store.create(
        "newsletter", "reviewer", "local-triage", private_prompt_bytes()
    )
    target_path = store.path("newsletter", "reviewer", "local-triage")
    original_read_regular_file = __import__("agency.prompts.store", fromlist=["_read_regular_file"])._read_regular_file
    delete_ready = Event()
    allow_delete_to_continue = Event()
    update_done = Event()
    update_started = Event()
    update_errors: list[Exception] = []
    update_results: list[object] = []

    def gated_read_regular_file(path):
        if (
            current_thread().name == "deleter"
            and path == target_path
            and not delete_ready.is_set()
        ):
            payload = original_read_regular_file(path)
            delete_ready.set()
            assert update_started.wait(timeout=5)
            update_done.wait(timeout=0.2)
            assert allow_delete_to_continue.wait(timeout=5)
            return payload
        return original_read_regular_file(path)

    monkeypatch.setattr("agency.prompts.store._read_regular_file", gated_read_regular_file)

    def run_delete():
        store.delete_namespace(
            "newsletter",
            "reviewer",
            registered=(("local-triage", created.document.digest),),
        )

    def run_update():
        assert delete_ready.wait(timeout=5)
        update_started.set()
        try:
            update_results.append(
                store.update(
                    "newsletter",
                    "reviewer",
                    "local-triage",
                    expected_digest=created.document.digest,
                    payload=(
                        "---\n"
                        "name: local-triage\n"
                        "description: Triage local work.\n"
                        "---\n\n"
                        "Review queue updated.\n"
                    ).encode("utf-8"),
                )
            )
        except Exception as exc:  # noqa: BLE001
            update_errors.append(exc)
        finally:
            update_done.set()

    delete_thread = Thread(target=run_delete, name="deleter")
    update_thread = Thread(target=run_update, name="updater")
    delete_thread.start()
    update_thread.start()
    assert delete_ready.wait(timeout=5)
    assert update_started.wait(timeout=5)
    allow_delete_to_continue.set()
    delete_thread.join(timeout=5)
    update_thread.join(timeout=5)

    assert update_done.is_set()
    assert not update_results
    assert len(update_errors) == 1
    assert isinstance(update_errors[0], (PromptNotFoundError, PromptConflictError))
    with pytest.raises(PromptNotFoundError):
        store.read("newsletter", "reviewer", "local-triage")
