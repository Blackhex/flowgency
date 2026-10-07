from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient
import yaml

from flowgency.configuration.models import MemorySelector
from flowgency.configuration.store import ConfigStore
from flowgency.jobs.authority import JobStore
from flowgency.jobs.store import active_jobs, read_job
from tests.ui import server


def test_seed_jobs_assigns_running_ticket_job_to_its_owner(tmp_path):
    # The running ticket job belongs to reviewer. If its spec owner leaks to
    # advisor it shadows advisor's waiting-for-memory job on the dashboard
    # fleet card and the "waiting for memory" link disappears.
    server._seed_jobs(tmp_path, tmp_path / "config.yaml")

    store = JobStore(tmp_path / "memory-store")
    paths = store.paths("newsletter")
    by_id = {record.spec.job_id: record for record in (read_job(path) for path in paths)}

    assert by_id["fixture-active-job"].spec.agent_name == "reviewer"
    assert [record.spec.job_id for record in active_jobs(paths, "advisor")] == ["job-waiting"]


def test_ui_memory_binding_selector_matches_canonical_schema(tmp_path):
    binding = server._ui_memory_binding(
        tmp_path / "memory-store",
        team_key="newsletter",
        agent_name="advisor",
        job_id="ui-ticket-job",
    )

    # The stored selector is exactly what a real job persists: a MemorySelector
    # dump (scope/channel), never the criteria keys (team/agent/version) that
    # feed the memory hash. It must validate under the strict service schema.
    selector = MemorySelector.model_validate(binding.selector)
    assert selector.scope == "agent"
    assert set(binding.selector) == {"scope", "channel"}
    assert binding.memory_hash == server.resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="ui-ticket-job",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels={},
        store_root=tmp_path / "memory-store",
    ).memory_hash


def test_write_runtime_config_is_atomic_under_concurrent_reads(tmp_path):
    config_path = tmp_path / "config.yaml"
    old = {"schema_version": 1, "marker": "OLD" * 4000}
    new = {"schema_version": 1, "marker": "NEW" * 4000}
    server._write_runtime_config(config_path, old)
    old_bytes = config_path.read_bytes()
    new_bytes = yaml.safe_dump(new, sort_keys=False).encode("utf-8")

    failures: list[object] = []

    def writer() -> None:
        try:
            for _ in range(80):
                server._write_runtime_config(config_path, new)
                server._write_runtime_config(config_path, old)
        except Exception as error:  # pragma: no cover - only on regression
            failures.append(("writer", repr(error)))

    def reader() -> None:
        try:
            store = ConfigStore(config_path)
            for _ in range(600):
                snapshot = store.inspect()
                assert snapshot.exists is True
                data = snapshot.payload
                assert data is not None
                if data not in (old_bytes, new_bytes):
                    failures.append(("reader", len(data)))
                    return
        except Exception as error:  # pragma: no cover - only on regression
            failures.append(("reader", repr(error)))

    writer_thread = threading.Thread(target=writer)
    reader_threads = [threading.Thread(target=reader) for _ in range(3)]
    writer_thread.start()
    for thread in reader_threads:
        thread.start()
    writer_thread.join()
    for thread in reader_threads:
        thread.join()

    assert not failures


def test_ui_reset_clears_durable_ticket_run_reservations_before_board_reread(monkeypatch):
    import flowgency.app as app_mod

    runtime, config_path = server._prepare_runtime()
    try:
        monkeypatch.setenv("FLOWGENCY_CONFIG", str(config_path))
        monkeypatch.setenv("FLOWGENCY_UI_RUNTIME", str(runtime))
        monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
        server._install_ui_test_runtime()
        app_mod.refresh_services()

        with TestClient(app_mod.app) as client:
            detail = client.get("/newsletter/workflows/delivery/tickets/fixture-review/snapshot")
            assert detail.status_code == 200
            version = detail.json()["ticket"]["version"]

            response = client.post(
                "/newsletter/workflows/delivery/tickets/fixture-review/run",
                data={
                    "payload": json.dumps(
                        {
                            "version": version,
                            "operation_id": "run-request",
                        }
                    )
                },
                follow_redirects=False,
            )

            assert response.status_code == 303

            reservations = app_mod.app.state.services.ticket_jobs.iter_reservations("newsletter")
            assert [reservation.target.ref.ticket_id for reservation in reservations] == ["fixture-review"]

            reservation_root = JobStore(runtime / "memory-store").team_root("newsletter") / "ticket-runs"
            assert len(list(reservation_root.glob("*.json"))) == 1

            reset = client.post(server.UI_RESET_PATH)

            assert reset.status_code == 204
            assert list(reservation_root.glob("*.json")) == []

            board = client.get("/newsletter/workflows/delivery", params={"ticket": "fixture-review"})
            assert board.status_code == 200
    finally:
        server._safe_remove_runtime(runtime)


def test_connected_setup_ready_preserves_existing_workflow_library_and_tickets():
    runtime, _config_path = server._prepare_runtime()
    try:
        server._reset_runtime_state(runtime)

        research_source = runtime / "workflow-library" / "research-workflow" / "workflow.yaml"
        research_before = research_source.read_bytes()

        custom_selected = runtime / "workflow-library" / "software-delivery" / "workflow.yaml"
        custom_selected.parent.mkdir(parents=True, exist_ok=True)
        custom_selected.write_text(
            yaml.safe_dump(
                {
                    **server._delivery_definition(),
                    "id": "software-delivery",
                    "name": "Customized software delivery",
                },
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        custom_before = custom_selected.read_bytes()

        seeded_ticket = (
            runtime
            / "tickets"
            / "delivery"
            / "newsletter"
            / "delivery"
            / "tickets"
            / "preserve-me.md"
        )
        seeded_ticket.write_text("keep existing ticket contents\n", encoding="utf-8")
        ticket_before = seeded_ticket.read_bytes()

        missing_config = server._connected_setup_ready_config(runtime, "missing")
        server._prepare_connected_setup_ready(runtime, missing_config, "missing")

        assert research_source.read_bytes() == research_before
        assert not custom_selected.exists()
        assert seeded_ticket.read_bytes() == ticket_before

        custom_selected.parent.mkdir(parents=True, exist_ok=True)
        custom_selected.write_bytes(custom_before)

        valid_config = server._connected_setup_ready_config(runtime, "valid")
        server._prepare_connected_setup_ready(runtime, valid_config, "valid")

        assert research_source.read_bytes() == research_before
        assert custom_selected.read_bytes() == custom_before
        assert seeded_ticket.read_bytes() == ticket_before
    finally:
        server._safe_remove_runtime(runtime)


def test_completion_fixture_body_accepts_only_the_closed_schema():
    assert server._completion_fixture_body({"scheduler_result": "declined"}) == (
        "declined", False, "current"
    )
    assert server._completion_fixture_body(
        {"scheduler_result": "failed", "limitations_acknowledged": True, "revision": "stale"}
    ) == ("failed", True, "stale")

    rejected = [
        None,
        [],
        {},
        {"scheduler_result": "bogus"},
        {"scheduler_result": "confirmed", "token": "x"},
        {"scheduler_result": "confirmed", "launch_id": "0" * 32},
        {"scheduler_result": "confirmed", "all_questions_answered": False},
        {"scheduler_result": "confirmed", "limitations_acknowledged": "yes"},
        {"scheduler_result": "confirmed", "limitations_acknowledged": 1},
        {"scheduler_result": "confirmed", "revision": "abc"},
    ]
    for payload in rejected:
        with pytest.raises(ValueError):
            server._completion_fixture_body(payload)


def test_completion_fixture_endpoint_rejects_invalid_bodies_without_an_attempt(monkeypatch):
    import flowgency.app as app_mod

    runtime, config_path = server._prepare_runtime()
    try:
        monkeypatch.setenv("FLOWGENCY_CONFIG", str(config_path))
        monkeypatch.setenv("FLOWGENCY_UI_RUNTIME", str(runtime))
        monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
        server._install_ui_test_runtime()
        app_mod.refresh_services()

        with TestClient(app_mod.app) as client:
            path = "/__ui/setup/session/complete"
            assert client.post(path, json={"scheduler_result": "bogus"}).status_code == 400
            assert client.post(path, json={"scheduler_result": "confirmed", "x": 1}).status_code == 400
            assert client.post(path, content=b"not json").status_code == 400
            assert client.post(path, json={"scheduler_result": "confirmed"}).status_code == 404
    finally:
        server._safe_remove_runtime(runtime)


def test_live_change_case_accepts_only_the_closed_allowlist():
    assert server._live_change_case({"case": "navigation-membership"}) == "navigation-membership"
    assert server._live_change_case({"case": "navigation-workflow-count"}) == "navigation-workflow-count"

    rejected = [
        None,
        [],
        {},
        {"case": "unknown"},
        {"case": ""},
        {"case": 1},
        {"case": "navigation-membership", "path": "config.yaml"},
        {"case": "../navigation-membership"},
        {"case": "navigation-membership; import os"},
    ]
    for payload in rejected:
        with pytest.raises(ValueError):
            server._live_change_case(payload)


def test_live_change_endpoint_changes_navigation_and_reset_restores_it(monkeypatch):
    import flowgency.app as app_mod

    runtime, config_path = server._prepare_runtime()
    try:
        monkeypatch.setenv("FLOWGENCY_CONFIG", str(config_path))
        monkeypatch.setenv("FLOWGENCY_UI_RUNTIME", str(runtime))
        monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
        server._install_ui_test_runtime()
        app_mod.refresh_services()

        with TestClient(app_mod.app) as client:
            path = server.LIVE_CHANGE_PATH
            assert client.post(path, json={"case": "unknown"}).status_code == 400
            assert client.post(path, json={"case": "navigation-membership", "x": 1}).status_code == 400
            assert client.post(path, content=b"not json").status_code == 400

            before = client.get("/newsletter/agents?__live=1").text
            assert "Research updated" not in before

            assert client.post(path, json={"case": "navigation-membership"}).status_code == 204
            changed = client.get("/newsletter/agents?__live=1").text
            assert changed.count("Research updated") == 2
            assert 'data-live-key=\\"nav:workspaces\\"' in changed

            assert client.post(path, json={"case": "navigation-workflow-count"}).status_code == 204
            assert 'data-workflow-state=\\"count\\">' in client.get("/newsletter/agents?__live=1").text

            assert client.post(server.UI_RESET_PATH).status_code == 204
            restored = client.get("/newsletter/agents?__live=1").text
            assert "Research updated" not in restored
            assert 'data-live-key=\\"nav:workspaces\\"' not in restored
    finally:
        server._safe_remove_runtime(runtime)
