from __future__ import annotations

from copy import deepcopy
from multiprocessing import Event, Process, Queue
import os
from pathlib import Path

import pytest
import yaml

from flowgency import app as app_mod
from flowgency.configuration import ConfigConflictError, ConfigStore, ValidationFailed
from flowgency.dispatch.schedule import at_marker_path
from flowgency.memory import resolve_memory_selector
from flowgency.prompts import PromptNotFoundError
from flowgency.routines.editor import (
    RoutinesRequest,
    find_agent,
    load_choices,
    prepare_routines,
    save_routines,
)
from flowgency.routines.forms import MemoryDraft, RoutineDraft, build_form
from tests._lock_helpers import hold_exclusive_lock
from tests.test_agent_detail import _seed_app


def _agent(snapshot, team_id: str = "newsletter", agent_id: str = "advisor") -> dict:
    return find_agent(snapshot.raw, team_id, agent_id)


def _request(snapshot, draft_version: int = 1) -> RoutinesRequest:
    return RoutinesRequest(
        revision=snapshot.revision,
        draft_version=draft_version,
        draft=build_form(_agent(snapshot)).draft,
    )


def _memory_tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(p for p in root.rglob("*") if p.is_file())
    }


def _save_routines_in_process(
    config_path_str: str,
    request_payload: dict,
    ready: Event,
    queue: Queue,
) -> None:
    ready.set()

    from pathlib import Path

    from flowgency import app as app_mod
    from flowgency.configuration import ConfigConflictError, ConfigStore
    from flowgency.routines.editor import RoutinesRequest, save_routines

    config_path = Path(config_path_str)
    services = app_mod.build_services(config_path)
    store = ConfigStore(config_path)
    request = RoutinesRequest.model_validate(request_payload)
    try:
        saved = save_routines(
            store,
            services.blueprint_library,
            services.prompt_store,
            "newsletter",
            "advisor",
            request,
        )
    except ConfigConflictError as exc:
        queue.put(("conflict", str(exc)))
        return

    queue.put(("success", saved.raw))


def test_preview_preserves_config_bytes(monkeypatch, tmp_path, raw_config):
    import flowgency.configuration.store as store_mod
    import flowgency.jobs as jobs_mod

    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    agent = _agent(snapshot)
    request = RoutinesRequest(
        revision=snapshot.revision,
        draft_version=1,
        draft=build_form(agent).draft,
    )
    before = config_path.read_bytes()

    monkeypatch.setattr(
        store_mod,
        "initialize_storage_directories",
        lambda _config: (_ for _ in ()).throw(AssertionError("preview should not initialize storage")),
    )
    monkeypatch.setattr(
        jobs_mod,
        "submit_job_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("preview should not submit jobs")),
    )
    if services.memory_store is not None:
        monkeypatch.setattr(
            services.memory_store,
            "ensure",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("preview should not allocate memory")),
        )

    choices = load_choices(
        snapshot,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
    )
    prepared = prepare_routines(snapshot, "newsletter", "advisor", request, choices)

    assert prepared.candidate["teams"]["newsletter"]["agents"][0] == agent
    assert config_path.read_bytes() == before


def test_load_choices_reads_effective_prompt_catalog_and_channels(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    snapshot = ConfigStore(config_path).load()

    choices = load_choices(
        snapshot,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
    )

    assert choices.prompts == (
        ("blueprint", "pr-review"),
        ("instance", "local-triage"),
    )
    assert choices.channels == (("support", "Support"),)


def test_load_choices_surfaces_prompt_catalog_collisions(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    private_dir = tmp_path / "prompts" / "newsletter" / "advisor"
    (private_dir / "pr-review.prompt.md").write_text(
        "---\nname: pr-review\ndescription: Collision.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    raw["teams"]["newsletter"]["agents"][0]["prompts"].append("pr-review")
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    services = app_mod.app.state.services
    snapshot = ConfigStore(config_path).load()

    with pytest.raises(ValidationFailed) as caught:
        load_choices(
            snapshot,
            services.blueprint_library,
            services.prompt_store,
            "newsletter",
            "advisor",
        )

    assert [issue.code for issue in caught.value.issues] == ["invalid-prompt-catalog"]


def test_prepare_checks_revision_before_source_indices(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    request = _request(snapshot)
    request.draft.routines[0].source_index = 99
    store.patch(snapshot.revision, lambda raw: raw["flowgency"].update(title="Changed elsewhere"))
    current = store.load()
    choices = load_choices(
        current,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
    )

    with pytest.raises(ConfigConflictError, match="config.yaml changed; reload before saving"):
        prepare_routines(current, "newsletter", "advisor", request, choices)


def test_prepare_validates_memory_channel_against_candidate_config(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    snapshot = ConfigStore(config_path).load()
    request = _request(snapshot)
    request.draft.routines[0].memory = MemoryDraft(scope="channel", channel="missing")
    choices = load_choices(
        snapshot,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
    )

    with pytest.raises(ValidationFailed) as caught:
        prepare_routines(snapshot, "newsletter", "advisor", request, choices)

    assert any(issue.field.endswith("memory.channel") for issue in caught.value.issues)


def test_save_replaces_only_selected_agent_and_preserves_unrelated_data(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    request = _request(snapshot)
    request.draft.routines = [
        RoutineDraft(
            key="saved-0",
            source_index=0,
            id="daily-review-renamed",
            prompt_scope="blueprint",
            prompt_name="pr-review",
            enabled=True,
            arguments=["--brief"],
            schedule=request.draft.routines[0].schedule.model_copy(deep=True),
            recovery=request.draft.routines[0].recovery.model_copy(deep=True),
            memory=request.draft.routines[0].memory.model_copy(deep=True),
        ),
        RoutineDraft(
            key="new-1",
            id="local-review",
            prompt_scope="instance",
            prompt_name="local-triage",
            enabled=False,
            arguments=["--focused"],
            schedule=request.draft.routines[0].schedule.model_copy(update={"mode": "every", "amount": "6", "unit": "h"}),
            recovery=request.draft.routines[0].recovery.model_copy(deep=True),
            memory=MemoryDraft(scope="inherit"),
        ),
    ]
    expected = deepcopy(snapshot.raw)
    expected["teams"]["newsletter"]["agents"][0]["routines"] = [
        {
            "id": "daily-review-renamed",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "arguments": ["--brief"],
            "schedule": {"at": "09:00"},
            "memory": {"scope": "routine"},
        },
        {
            "id": "local-review",
            "prompt": {"scope": "instance", "name": "local-triage"},
            "enabled": False,
            "arguments": ["--focused"],
            "schedule": {"every": "6h"},
        },
    ]

    saved = save_routines(
        store,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
        request,
    )

    assert saved.raw == expected


def test_stale_revision_cannot_overwrite_other_settings(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    request = _request(snapshot)

    store.patch(snapshot.revision, lambda raw: raw["teams"]["newsletter"].update(name="Updated name"))
    current_bytes = config_path.read_bytes()

    with pytest.raises(ConfigConflictError):
        save_routines(
            store,
            services.blueprint_library,
            services.prompt_store,
            "newsletter",
            "advisor",
            request,
        )

    assert config_path.read_bytes() == current_bytes


def test_same_revision_save_requests_allow_one_winner_and_one_conflict(
    monkeypatch, tmp_path, raw_config
):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    first = _request(snapshot)
    second = _request(snapshot)
    first.draft.routines[0].id = "daily-review-first"
    second.draft.routines[0].id = "daily-review-second"

    expected_first = deepcopy(snapshot.raw)
    expected_first["teams"]["newsletter"]["agents"][0]["routines"][0]["id"] = "daily-review-first"
    expected_second = deepcopy(snapshot.raw)
    expected_second["teams"]["newsletter"]["agents"][0]["routines"][0]["id"] = "daily-review-second"

    acquired = Event()
    release = Event()
    ready_first = Event()
    ready_second = Event()
    queue: Queue = Queue()
    lock_holder = Process(
        target=hold_exclusive_lock,
        args=(str(store.lock_path), acquired, release, 30),
    )
    first_process = Process(
        target=_save_routines_in_process,
        args=(str(config_path), first.model_dump(mode="python"), ready_first, queue),
    )
    second_process = Process(
        target=_save_routines_in_process,
        args=(str(config_path), second.model_dump(mode="python"), ready_second, queue),
    )
    lock_holder.start()
    assert acquired.wait(15)
    first_process.start()
    second_process.start()
    assert ready_first.wait(15)
    assert ready_second.wait(15)

    try:
        release.set()
        results = [queue.get(timeout=15) for _ in range(2)]
    finally:
        release.set()
        lock_holder.join(15)
        first_process.join(15)
        second_process.join(15)
        for process in (lock_holder, first_process, second_process):
            if process.is_alive():
                process.terminate()
                process.join(15)
            assert not process.is_alive()
            assert process.exitcode == 0

    statuses = [status for status, _payload in results]
    assert sorted(statuses) == ["conflict", "success"]
    assert [payload for status, payload in results if status == "conflict"] == [
        "config.yaml changed; reload before saving"
    ]
    winner = [payload for status, payload in results if status == "success"]
    assert winner in ([expected_first], [expected_second])
    assert store.load().raw == winner[0]


def test_outside_lock_conflict_preserves_exact_external_bytes(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    request = _request(snapshot)
    request.draft.routines[0].id = "renamed-daily-review"
    original_encode = ConfigStore._encode
    external = {"bytes": b""}

    def encode_and_induce_conflict(self, raw):
        payload = original_encode(self, raw)
        external["bytes"] = self.path.read_bytes() + b"\n"
        self.path.write_bytes(external["bytes"])
        return payload

    monkeypatch.setattr(ConfigStore, "_encode", encode_and_induce_conflict)

    with pytest.raises(
        ConfigConflictError,
        match="config.yaml changed outside the Flowgency lock",
    ):
        save_routines(
            store,
            services.blueprint_library,
            services.prompt_store,
            "newsletter",
            "advisor",
            request,
        )

    assert config_path.read_bytes() == external["bytes"]


def test_save_revalidates_current_prompt_availability(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    request = _request(snapshot)
    request.draft.routines.append(
        RoutineDraft(
            key="new-1",
            id="local-review",
            prompt_scope="instance",
            prompt_name="local-triage",
            enabled=True,
            arguments=[],
            schedule=request.draft.routines[0].schedule.model_copy(update={"mode": "every", "amount": "6", "unit": "h"}),
            recovery=request.draft.routines[0].recovery.model_copy(deep=True),
            memory=MemoryDraft(scope="inherit"),
        )
    )
    prompt_path = tmp_path / "prompts" / "newsletter" / "advisor" / "local-triage.prompt.md"
    before = config_path.read_bytes()
    prompt_path.rename(prompt_path.with_name("local-triage-renamed.prompt.md"))

    with pytest.raises(PromptNotFoundError, match=r"prompt not found: .*local-triage\.prompt\.md"):
        save_routines(
            store,
            services.blueprint_library,
            services.prompt_store,
            "newsletter",
            "advisor",
            request,
        )

    assert config_path.read_bytes() == before


def test_save_does_not_mutate_saved_history_or_memory_files(monkeypatch, tmp_path, raw_config):
    _, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    store = ConfigStore(config_path)
    snapshot = store.load()
    request = _request(snapshot)
    request.draft.routines = []
    logs_root = tmp_path / "groups" / "newsletter" / "logs"
    marker = at_marker_path(logs_root, "advisor", "daily-review", "2026-09-07")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("marker", encoding="utf-8")
    resolved = resolve_memory_selector(
        snapshot.config.teams["newsletter"].agents["advisor"].routines[0].memory,
        job_id="detail-newsletter-advisor",
        team_key="newsletter",
        agent_name="advisor",
        routine_id="daily-review",
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )
    services.memory_store.ensure(resolved)
    memory_file = resolved.directory / "memory.md"
    memory_file.write_text("seeded", encoding="utf-8")
    before_logs = _memory_tree(logs_root)
    before_memory = _memory_tree(services.memory_store.root)

    save_routines(
        store,
        services.blueprint_library,
        services.prompt_store,
        "newsletter",
        "advisor",
        request,
    )

    assert _memory_tree(logs_root) == before_logs
    assert _memory_tree(services.memory_store.root) == before_memory