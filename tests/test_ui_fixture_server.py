from __future__ import annotations

import contextlib
import json
import threading
from urllib.parse import urlencode

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
    for case in server.LIVE_CHANGE_CASES:
        assert server._live_change_case({"case": case}) == case

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


_ADVISOR_CARD = r'data-live-key="agent:advisor" data-health="(\w+)" data-health-kind="(\w+)"'
_PENDING_SENTENCE = "Routine daily-review was due at 09:00 and has not run \u2014 3h late."


def _inbox_regions(client) -> dict[str, str]:
    body = client.get("/newsletter/?__live=1").json()
    return {region["key"]: region["html"] for region in body["regions"]}


def test_live_change_inbox_job_completion_turns_the_pending_advisor_healthy(monkeypatch):
    import re

    import flowgency.app as app_mod

    runtime, config_path = server._prepare_runtime()
    try:
        monkeypatch.setenv("FLOWGENCY_CONFIG", str(config_path))
        monkeypatch.setenv("FLOWGENCY_UI_RUNTIME", str(runtime))
        monkeypatch.setenv("FLOWGENCY_FIXED_NOW", server.FIXED_NOW)
        monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
        server._install_ui_test_runtime()
        app_mod.refresh_services()

        with TestClient(app_mod.app) as client:
            path = server.LIVE_CHANGE_PATH

            def card() -> tuple[str, str]:
                match = re.search(_ADVISOR_CARD, _inbox_regions(client)["fleet"])
                assert match is not None
                return match.groups()

            assert card() == ("red", "job_failed")

            assert client.post(path, json={"case": "inbox-routine-pending"}).status_code == 204
            assert card() == ("red", "overdue")
            assert _PENDING_SENTENCE in _inbox_regions(client)["attention"]

            assert client.post(path, json={"case": "inbox-job-completes"}).status_code == 204
            assert card() == ("green", "healthy")
            regions = _inbox_regions(client)
            assert _PENDING_SENTENCE not in regions["fleet"] + regions["attention"]

            assert client.post(server.UI_RESET_PATH).status_code == 204
            assert card() == ("red", "job_failed")
    finally:
        server._safe_remove_runtime(runtime)


def test_live_change_inbox_membership_queue_activity_and_clock_cases_reset_cleanly(monkeypatch):
    import re

    import flowgency.app as app_mod

    runtime, config_path = server._prepare_runtime()
    try:
        monkeypatch.setenv("FLOWGENCY_CONFIG", str(config_path))
        monkeypatch.setenv("FLOWGENCY_UI_RUNTIME", str(runtime))
        monkeypatch.setenv("FLOWGENCY_FIXED_NOW", server.FIXED_NOW)
        monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
        server._install_ui_test_runtime()
        app_mod.refresh_services()

        with TestClient(app_mod.app) as client:
            path = server.LIVE_CHANGE_PATH

            def agents() -> list[str]:
                return re.findall(r'data-live-key=\\"agent:([^\\"]+)\\"', json.dumps(_inbox_regions(client)["fleet"]))

            default = agents()
            assert default == ["advisor", "builder", "reviewer", "researcher"]

            assert client.post(path, json={"case": "inbox-agent-added"}).status_code == 204
            assert agents() == [*default, "scribe"]
            assert client.post(path, json={"case": "inbox-agent-moved"}).status_code == 204
            assert agents()[0] == "builder"
            assert client.post(path, json={"case": "inbox-agent-removed"}).status_code == 204
            assert "researcher" not in agents()

            before = _inbox_regions(client)
            assert client.post(path, json={"case": "inbox-ticket-activity"}).status_code == 204
            assert client.post(path, json={"case": "inbox-queue-grows"}).status_code == 204
            after = _inbox_regions(client)
            assert 'data-live-key=\\"activity:delivery:fixture-live-activity\\"' in json.dumps(after["activity"])
            assert json.dumps(after["work-queue"]).count('data-live-key=\\"job:inbox-queued-') == 2
            assert after["work-queue"] != before["work-queue"]

            assert client.post(path, json={"case": "inbox-clock-advances"}).status_code == 204
            assert _inbox_regions(client)["activity"] != after["activity"]

            assert client.post(server.UI_RESET_PATH).status_code == 204
            assert agents() == default
            assert _inbox_regions(client)["activity"] == before["activity"]
    finally:
        server._safe_remove_runtime(runtime)

def test_live_change_cases_all_have_a_dispatcher():
    dispatched = {
        "navigation-membership",
        "navigation-workflow-count",
        *server._INBOX_LIVE_CHANGES,
        *server._AGENT_LIVE_CHANGES,
        *server._JOB_LIVE_CHANGES,
        *server._LOG_LIVE_CHANGES,
    }
    assert dispatched == set(server.LIVE_CHANGE_CASES)


def _roster_rows(client) -> str:
    body = client.get("/newsletter/agents?__live=1").json()
    return next(region["html"] for region in body["regions"] if region["key"] == "roster-rows")


def test_live_change_agent_cases_update_the_roster_and_reset_restores_every_source(monkeypatch):
    import flowgency.app as app_mod

    runtime, config_path = server._prepare_runtime()
    try:
        monkeypatch.setenv("FLOWGENCY_CONFIG", str(config_path))
        monkeypatch.setenv("FLOWGENCY_UI_RUNTIME", str(runtime))
        monkeypatch.setenv("FLOWGENCY_FIXED_NOW", server.FIXED_NOW)
        monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
        server._install_ui_test_runtime()
        app_mod.refresh_services()

        with TestClient(app_mod.app) as client:
            path = server.LIVE_CHANGE_PATH
            before = _roster_rows(client)
            assert server.ADVISOR_IDENTITY_TITLE not in before
            assert "job:agent-live-running" not in before

            assert client.post(path, json={"case": "agent-source-and-status"}).status_code == 204
            changed = _roster_rows(client)
            assert server.ADVISOR_IDENTITY_TITLE in changed
            assert 'data-live-key="job:agent-live-running"' in changed
            prompt_file = runtime / "prompts" / "newsletter" / "advisor" / "local-triage.prompt.md"
            assert server.ADVISOR_EDITED_PROMPT_BODY.strip() in prompt_file.read_text(encoding="utf-8")
            assert "edited externally" in (runtime / "agent-library" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")

            for case in ("agent-routines", "agent-log-membership", "agent-memory-revision", "agent-report-history",
                         "agent-team-runtime", "agent-permissions"):
                assert client.post(path, json={"case": case}).status_code == 204, case

            assert client.post(server.UI_RESET_PATH).status_code == 204
            assert _roster_rows(client) == before
            assert prompt_file.read_bytes() == server._local_triage_payload()
            assert "edited externally" not in (runtime / "agent-library" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
            assert not (runtime / "teams" / "newsletter" / "logs" / "2026-07-16" / "advisor-live-refresh.out").exists()
    finally:
        server._safe_remove_runtime(runtime)


@contextlib.contextmanager
def _live_fixture(monkeypatch):
    import flowgency.app as app_mod

    runtime, config_path = server._prepare_runtime()
    try:
        monkeypatch.setenv("FLOWGENCY_CONFIG", str(config_path))
        monkeypatch.setenv("FLOWGENCY_UI_RUNTIME", str(runtime))
        monkeypatch.setenv("FLOWGENCY_FIXED_NOW", server.FIXED_NOW)
        monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
        server._install_ui_test_runtime()
        app_mod.refresh_services()
        with TestClient(app_mod.app) as client:
            yield client, runtime
    finally:
        server._safe_remove_runtime(runtime)


def _apply(client, case: str) -> None:
    assert client.post(server.LIVE_CHANGE_PATH, json={"case": case}).status_code == 204, case


def _reset(client) -> None:
    assert client.post(server.UI_RESET_PATH).status_code == 204


def _region(client, path: str, key: str) -> str:
    separator = "&" if "?" in path else "?"
    response = client.get(f"{path}{separator}__live=1")
    assert response.status_code == 200, response.text
    return next(region["html"] for region in response.json()["regions"] if region["key"] == key)


def _log_view_path(runtime, name: str = server.LIVE_TAIL_LOG_NAME) -> str:
    log = runtime / "teams" / "newsletter" / "logs" / "2026-07-16" / name
    return "/newsletter/logs/view?" + urlencode({"path": str(log)})


def test_live_change_job_finishes_completes_the_waiting_job(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        detail = "/newsletter/jobs/job-waiting"
        assert "Waiting for memory" in _region(client, detail, "job-status")

        _apply(client, "job-finishes")
        assert "Complete" in _region(client, detail, "job-status")
        assert "Waiting for memory" not in _region(client, detail, "job-status")
        assert "/cancel" not in _region(client, detail, "job-actions")

        _reset(client)
        assert "Waiting for memory" in _region(client, detail, "job-status")


def test_live_change_job_finishes_with_session_swaps_cancel_for_resume_and_reset_restores_it(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        detail = "/newsletter/jobs/job-waiting"
        before = _region(client, detail, "job-actions")
        assert 'data-live-key="job-action:cancel"' in before
        assert 'data-live-key="job-action:resume"' not in before

        _apply(client, "job-finishes-with-session")
        after = _region(client, detail, "job-actions")
        assert 'data-live-key="job-action:resume"' in after
        assert 'data-live-key="job-action:cancel"' not in after
        assert "Complete" in _region(client, detail, "job-status")

        _reset(client)
        assert _region(client, detail, "job-actions") == before


def test_live_change_job_added_and_removed_change_the_list_and_reset_restores_it(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        jobs = "/newsletter/jobs"
        before = _region(client, jobs, "jobs-list")
        assert 'data-live-key="job:job-live-added"' not in before

        _apply(client, "job-added")
        added = _region(client, jobs, "jobs-list")
        assert 'data-live-key="job:job-live-added"' in added
        assert 'data-live-key="job:job-failed"' in added

        _apply(client, "job-removed")
        removed = _region(client, jobs, "jobs-list")
        assert 'data-live-key="job:job-failed"' not in removed
        assert 'data-live-key="job:job-live-added"' in removed

        _reset(client)
        assert _region(client, jobs, "jobs-list") == before


def test_live_change_job_failure_artifacts_adds_a_retained_artifact_and_reset_restores_it(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        detail = "/newsletter/jobs/job-failed"
        before = _region(client, detail, "job-artifacts")
        assert "Failed memory snapshot" in before
        assert "Second Draft" not in before

        _apply(client, "job-failure-artifacts")
        assert "Second Draft" in _region(client, detail, "job-artifacts")
        assert "2 retained artifacts" in _region(client, detail, "job-publication")

        _reset(client)
        assert _region(client, detail, "job-artifacts") == before


def test_live_change_job_memory_published_clears_the_publication_state_and_reset_restores_it(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        detail = "/newsletter/jobs/job-failed"
        assert "1 retained artifact" in _region(client, detail, "job-publication")

        _apply(client, "job-memory-published")
        assert _region(client, detail, "job-publication").strip() == ""
        assert _region(client, detail, "job-artifacts").strip() == ""

        _reset(client)
        assert "1 retained artifact" in _region(client, detail, "job-publication")


def test_live_change_log_tall_creates_a_log_longer_than_the_viewport_and_reset_removes_it(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        view = _log_view_path(runtime)
        assert client.get(f"{view}&__live=1").status_code == 404

        _apply(client, "log-tall")
        content = _region(client, view, "log-content")
        assert f"tail line {server.LIVE_TAIL_LOG_LINES}" in content

        _reset(client)
        assert client.get(f"{view}&__live=1").status_code == 404


def test_live_change_log_appended_adds_lines_to_the_tail_log(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        view = _log_view_path(runtime)
        _apply(client, "log-tall")
        assert "appended line" not in _region(client, view, "log-content")

        _apply(client, "log-appended")
        content = _region(client, view, "log-content")
        assert f"appended line {server.LIVE_APPENDED_LINES}" in content
        assert f"tail line {server.LIVE_TAIL_LOG_LINES}" in content


def test_live_change_log_truncated_shrinks_the_tail_log(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        view = _log_view_path(runtime)
        _apply(client, "log-tall")
        _apply(client, "log-truncated")

        content = _region(client, view, "log-content")
        assert "rotated line" in content
        assert "tail line" not in content


def test_live_change_log_oversized_exceeds_the_preview_limit(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        view = _log_view_path(runtime)
        _apply(client, "log-tall")
        assert _region(client, view, "log-status").strip() == ""

        _apply(client, "log-oversized")
        assert "Preview truncated" in _region(client, view, "log-status")


def test_live_change_log_removed_makes_the_tail_log_unavailable(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        view = _log_view_path(runtime)
        _apply(client, "log-tall")
        assert client.get(f"{view}&__live=1").status_code == 200

        _apply(client, "log-removed")
        assert client.get(f"{view}&__live=1").status_code == 404


def test_live_change_log_listing_membership_adds_and_removes_entries(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        listing = "/newsletter/logs"
        before = _region(client, listing, "logs-list")
        assert 'data-live-key="log:2026-07-16:advisor-job-failed.err"' in before
        assert server.LIVE_MEMBERSHIP_LOG_NAME not in before

        _apply(client, "log-listing-membership")
        changed = _region(client, listing, "logs-list")
        assert f'data-live-key="log:2026-07-16:{server.LIVE_MEMBERSHIP_LOG_NAME}"' in changed
        assert 'data-live-key="log:2026-07-16:advisor-job-failed.err"' not in changed

        _reset(client)
        assert _region(client, listing, "logs-list") == before
