from __future__ import annotations

import contextlib
import json
import re
import threading
from pathlib import Path
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
        *server._WORKSPACE_LIVE_CHANGES,
        *server._ADMIN_LIVE_CHANGES,
        *server._LIBRARY_LIVE_CHANGES,
        *server._WORKFLOW_LIVE_CHANGES,
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


def _workspaces_fixture(client) -> None:
    response = client.post(server.UI_RESET_PATH, json={"fixture": server.WORKSPACES_FIXTURE})
    assert response.status_code == 204


def _workspace_file_url(client, index: int = 0) -> str:
    page = client.get(f"/newsletter/workspaces/{index}/file")
    assert page.status_code == 200
    initial = re.search(r'<script type="application/json" id="live-initial">(.*?)</script>', page.text, re.S)
    return json.loads(initial.group(1))["url"]


def test_the_default_fixture_has_no_workspaces_and_the_opt_in_fixture_seeds_two(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        assert "No workspaces configured" in _region(client, "/newsletter/workspaces", "workspaces-status")

        _workspaces_fixture(client)
        listing = _region(client, "/newsletter/workspaces", "workspaces-list")
        assert "Editorial Notes" in listing and "Session Script" in listing
        notes = runtime / server.WORKSPACE_SOURCES_DIR / server.WORKSPACE_NOTES_NAME
        assert notes.read_bytes() == server.WORKSPACE_NOTES_TEXT.encode("utf-8")

        _reset(client)
        assert "No workspaces configured" in _region(client, "/newsletter/workspaces", "workspaces-status")
        assert list((runtime / server.WORKSPACE_SOURCES_DIR).iterdir()) == []


def test_live_change_workspace_file_changes_moves_the_status_and_reset_restores_it(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        _workspaces_fixture(client)
        url = _workspace_file_url(client)
        before = _region(client, url, "workspace-metadata")
        assert f"{len(server.WORKSPACE_NOTES_TEXT.encode('utf-8'))} bytes" in before

        _apply(client, "workspace-file-changes")
        after = _region(client, url, "workspace-metadata")
        assert f"{len(server.WORKSPACE_CHANGED_NOTES_TEXT.encode('utf-8'))} bytes" in after
        assert before != after
        notes = runtime / server.WORKSPACE_SOURCES_DIR / server.WORKSPACE_NOTES_NAME
        assert notes.read_bytes() == server.WORKSPACE_CHANGED_NOTES_TEXT.encode("utf-8")

        _workspaces_fixture(client)
        assert _region(client, _workspace_file_url(client), "workspace-metadata").count("bytes") == 1
        assert notes.read_bytes() == server.WORKSPACE_NOTES_TEXT.encode("utf-8")


def test_live_change_workspace_file_removed_reports_the_source_missing(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        _workspaces_fixture(client)
        url = _workspace_file_url(client)

        _apply(client, "workspace-file-removed")

        assert "File not found" in _region(client, url, "workspace-metadata")
        assert "Config file not found: Notes" in _region(client, "/newsletter/workspaces", "workspaces-list")

        _workspaces_fixture(client)
        assert "File not found" not in _region(client, _workspace_file_url(client), "workspace-metadata")


def test_live_change_workspace_reordered_makes_the_loaded_position_incompatible(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        _workspaces_fixture(client)
        url = _workspace_file_url(client)
        assert client.get(url).json()["regions"] != []

        _apply(client, "workspace-reordered")

        assert client.get(url).json()["regions"] == []
        listing = _region(client, "/newsletter/workspaces", "workspaces-list")
        assert listing.index("Session Script") < listing.index("Editorial Notes")

        _workspaces_fixture(client)
        assert client.get(url).json()["regions"] != []


def test_live_change_workspace_removed_and_added_change_the_list_and_the_second_page(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        _workspaces_fixture(client)
        second = _workspace_file_url(client, index=1)
        assert client.get(second).status_code == 200

        _apply(client, "workspace-removed")
        assert client.get(second).status_code == 404
        assert "Session Script" not in _region(client, "/newsletter/workspaces", "workspaces-list")

        _apply(client, "workspace-added")
        added = _region(client, "/newsletter/workspaces", "workspaces-list")
        assert "Review Script" in added and "Editorial Notes" in added

        _workspaces_fixture(client)
        assert "Review Script" not in _region(client, "/newsletter/workspaces", "workspaces-list")
        assert client.get(second).status_code == 200


def test_the_non_live_fixture_page_registers_nothing(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        response = client.get(server.NON_LIVE_PAGE_PATH)

        assert response.status_code == 200
        assert 'id="live-initial"' not in response.text
        assert "live-refresh.js" not in response.text
        assert "data-live-status" not in response.text


def _live_keys(html: str) -> list[str]:
    return re.findall(r'data-live-key="([^"]+)"', html)


@contextlib.contextmanager
def _admin_fixture(monkeypatch):
    """The live fixture with the integration listing redirected to its private copy, restored afterwards."""
    import flowgency.integrations as integrations_module

    monkeypatch.setattr(integrations_module, "INTEGRATIONS_DIR", integrations_module.INTEGRATIONS_DIR)
    with _live_fixture(monkeypatch) as (client, runtime):
        monkeypatch.setattr(integrations_module, "INTEGRATIONS_DIR", server._integration_source(runtime))
        yield client, runtime


def test_live_change_admin_settings_changed_moves_title_revision_and_interval_and_reset_restores_them(monkeypatch):
    with _admin_fixture(monkeypatch) as (client, _runtime):
        status = _region(client, "/admin/", "settings-status")
        interval = _region(client, "/admin/dispatch", "dispatch-status")
        assert 'data-live-revision="15"' in interval

        _apply(client, "admin-settings-changed")

        changed = _region(client, "/admin/", "settings-status")
        assert changed != status
        assert server.ADMIN_SETTINGS_TITLE in client.get("/admin/").text
        assert f'data-live-revision="{server.ADMIN_DISPATCH_INTERVAL}"' in _region(client, "/admin/dispatch", "dispatch-status")

        _reset(client)

        assert _region(client, "/admin/", "settings-status") == status
        assert _region(client, "/admin/dispatch", "dispatch-status") == interval
        assert server.ADMIN_SETTINGS_TITLE not in client.get("/admin/").text


def test_live_change_admin_dispatch_changed_enables_the_research_team_and_reset_restores_it(monkeypatch):
    with _admin_fixture(monkeypatch) as (client, _runtime):
        before = _region(client, "/admin/dispatch", "dispatch-teams")
        assert before.count('data-dispatch-enabled="true"') == 0

        _apply(client, "admin-dispatch-changed")

        after = _region(client, "/admin/dispatch", "dispatch-teams")
        assert after.count('data-dispatch-enabled="true"') == 1
        assert after.split("dispatch-team:research")[1].lstrip('"').startswith(' data-dispatch-enabled="true"')

        _reset(client)
        assert _region(client, "/admin/dispatch", "dispatch-teams") == before


def test_live_change_admin_team_created_adds_a_card_and_moves_every_team_revision_and_reset_restores_them(monkeypatch):
    with _admin_fixture(monkeypatch) as (client, runtime):
        listing = _region(client, "/admin/teams", "teams-list")
        edit = _region(client, "/admin/teams/newsletter/edit", "team-edit-status")
        new = _region(client, "/admin/teams/new", "team-new-status")
        assert f"admin-team:{server.ADMIN_CREATED_TEAM}" not in _live_keys(listing)

        _apply(client, "admin-team-created")

        created = _region(client, "/admin/teams", "teams-list")
        assert f"admin-team:{server.ADMIN_CREATED_TEAM}" in _live_keys(created)
        assert "3 teams configured" in _region(client, "/admin/teams", "teams-status")
        assert _region(client, "/admin/teams/newsletter/edit", "team-edit-status") != edit
        assert _region(client, "/admin/teams/new", "team-new-status") != new
        assert client.get(f"/admin/teams/{server.ADMIN_CREATED_TEAM}/edit?__live=1").status_code == 200

        _reset(client)

        assert _region(client, "/admin/teams", "teams-list") == listing
        assert _region(client, "/admin/teams/newsletter/edit", "team-edit-status") == edit
        assert _region(client, "/admin/teams/new", "team-new-status") == new
        assert client.get(f"/admin/teams/{server.ADMIN_CREATED_TEAM}/edit?__live=1").status_code == 404
        assert not any(path.exists() for path in server._admin_created_team_paths(runtime))


def test_live_change_admin_team_changed_renames_the_team_and_adds_an_agent_and_reset_restores_them(monkeypatch):
    with _admin_fixture(monkeypatch) as (client, _runtime):
        listing = _region(client, "/admin/teams", "teams-list")
        agents = _region(client, "/admin/teams/newsletter/edit", "team-edit-agents")
        assert "Manage agents (4)" in agents

        _apply(client, "admin-team-changed")

        changed = _region(client, "/admin/teams", "teams-list")
        assert server.ADMIN_CHANGED_TEAM_NAME in changed and "5 agents" in changed
        assert "Schedule enabled" in changed
        assert "Manage agents (5)" in _region(client, "/admin/teams/newsletter/edit", "team-edit-agents")

        _reset(client)

        assert _region(client, "/admin/teams", "teams-list") == listing
        assert _region(client, "/admin/teams/newsletter/edit", "team-edit-agents") == agents


def test_live_change_admin_team_removed_drops_the_card_and_the_second_page_and_reset_restores_them(monkeypatch):
    with _admin_fixture(monkeypatch) as (client, _runtime):
        listing = _region(client, "/admin/teams", "teams-list")
        assert client.get("/admin/teams/research/edit?__live=1").status_code == 200

        _apply(client, "admin-team-removed")

        removed = _region(client, "/admin/teams", "teams-list")
        assert "admin-team:research" not in _live_keys(removed)
        assert client.get("/admin/teams/research/edit?__live=1").status_code == 404
        assert "admin-team:research" not in _live_keys(_region(client, "/admin/dispatch", "dispatch-teams"))

        _reset(client)

        assert _region(client, "/admin/teams", "teams-list") == listing
        assert client.get("/admin/teams/research/edit?__live=1").status_code == 200


def test_live_change_integration_cases_change_the_available_listing_and_reset_restores_the_private_copy(monkeypatch):
    import hashlib
    from pathlib import Path

    import flowgency.integrations as integrations_module

    product_config = Path(integrations_module.__file__).parent / "integrations.yaml"
    product_before = hashlib.sha256(product_config.read_bytes()).hexdigest()
    with _admin_fixture(monkeypatch) as (client, runtime):
        source = server._integration_source(runtime)
        before = _region(client, "/admin/integrations", "integrations-available")
        installed = _region(client, "/admin/integrations", "integrations-installed")
        assert _live_keys(before) == ["available:list", f"available:{server.INTEGRATION_REGISTERED_MODULE}",
                                      f"available-action:{server.INTEGRATION_REGISTERED_MODULE}"]

        _apply(client, "integration-available-added")

        added = _live_keys(_region(client, "/admin/integrations", "integrations-available"))
        assert f"available:{server.INTEGRATION_ADDED_MODULE}" in added
        assert f"available:{server.INTEGRATION_REGISTERED_MODULE}" in added

        _apply(client, "integration-registered")

        registered = _region(client, "/admin/integrations", "integrations-available")
        assert _live_keys(registered) == ["available:list", f"available:{server.INTEGRATION_ADDED_MODULE}",
                                          f"available-action:{server.INTEGRATION_ADDED_MODULE}"]
        assert "2 available" not in _region(client, "/admin/integrations", "integrations-status")
        assert _region(client, "/admin/integrations", "integrations-installed") == installed
        assert server.INTEGRATION_REGISTERED_MODULE in (source / "integrations.yaml").read_text(encoding="utf-8")

        _reset(client)

        assert _region(client, "/admin/integrations", "integrations-available") == before
        assert not (source / "acme" / "gadget.py").exists()
        assert server.INTEGRATION_REGISTERED_MODULE not in (source / "integrations.yaml").read_text(encoding="utf-8")
    assert hashlib.sha256(product_config.read_bytes()).hexdigest() == product_before


_ADVISOR_URL = "/admin/agent-library/blueprints/advisor"
_SKILL_FILE = ".agents/skills/daily-review/SKILL.md"
_CHECKLIST_FILE = ".agents/skills/daily-review/checklist.md"
_RELEASE_WINDOW_FILE = ".agents/prompts/release-window.prompt.md"
_BRAND_STRATEGY = "/admin/memory-channels/brand-strategy"
_LIBRARY_CASE_SEQUENCES = (
    ("library-source-changes",),
    ("library-blueprint-added",),
    ("library-blueprint-added", "library-blueprint-removed"),
    ("library-selected-files-removed",),
    ("channel-memory-changes",),
    ("channel-metadata-changed",),
    ("channel-added",),
    ("channel-added", "channel-removed"),
)


def _library_tree(runtime) -> dict[str, bytes]:
    root = runtime / "agent-library"
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _channel_markdown(runtime, channel_key: str) -> dict[str, bytes]:
    channels = {**ConfigStore(runtime / "config.yaml").load().config.memory.channels, server.CHANNEL_ADDED: {}}
    directory = server._channel_memory(runtime, channel_key, channels)[1].directory
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.md"))} if directory.exists() else {}


def _library_state(runtime) -> dict:
    return {
        "library": _library_tree(runtime),
        "brand-strategy": _channel_markdown(runtime, server.CHANNEL_KEY),
        "launch-notes": _channel_markdown(runtime, server.CHANNEL_ADDED),
        "config": ConfigStore(runtime / "config.yaml").load().revision,
    }


def test_every_library_case_belongs_to_a_reset_completeness_sequence():
    assert {case for sequence in _LIBRARY_CASE_SEQUENCES for case in sequence} == set(server._LIBRARY_LIVE_CHANGES)


@pytest.mark.parametrize("sequence", _LIBRARY_CASE_SEQUENCES, ids="+".join)
def test_library_and_channel_cases_are_fully_undone_by_a_reset(monkeypatch, sequence):
    with _live_fixture(monkeypatch) as (client, runtime):
        before = _library_state(runtime)
        assert before["launch-notes"] == {}

        changed = False
        for case in sequence:
            _apply(client, case)
            client.get(f"/admin/memory-channels/{server.CHANNEL_ADDED}")
            changed = changed or _library_state(runtime) != before
        assert changed

        _reset(client)

        assert _library_state(runtime) == before


def test_live_change_library_source_changes_move_the_title_files_prompts_and_digest(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        header = _region(client, _ADVISOR_URL, "blueprint-header")
        files = _region(client, _ADVISOR_URL, "blueprint-files")
        prompts = _region(client, f"{_ADVISOR_URL}/prompts?path={_RELEASE_WINDOW_FILE}", "prompts-list")
        assert server.LIBRARY_EDITED_TITLE not in header
        assert server.LIBRARY_ADDED_SKILL_FILE not in files
        assert f"prompt:{server.LIBRARY_ADDED_PROMPT}" not in prompts

        _apply(client, "library-source-changes")

        assert server.LIBRARY_EDITED_TITLE in _region(client, _ADVISOR_URL, "blueprint-header")
        assert _region(client, _ADVISOR_URL, "blueprint-header") != header
        assert server.LIBRARY_ADDED_SKILL_FILE in _region(client, _ADVISOR_URL, "blueprint-files")
        assert f"prompt:{server.LIBRARY_ADDED_PROMPT}" in _region(
            client, f"{_ADVISOR_URL}/prompts?path={_RELEASE_WINDOW_FILE}", "prompts-list"
        )

        _reset(client)

        assert _region(client, _ADVISOR_URL, "blueprint-header") == header
        assert _region(client, _ADVISOR_URL, "blueprint-files") == files


def test_live_change_library_blueprint_added_and_removed_change_the_list_and_the_second_page(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        listing = "/admin/agent-library"
        before = _region(client, listing, "library-blueprints")
        added_url = f"/admin/agent-library/blueprints/{server.LIBRARY_ADDED_BLUEPRINT}"
        assert client.get(f"{added_url}?__live=1").status_code == 404

        _apply(client, "library-blueprint-added")

        assert f"blueprint:{server.LIBRARY_ADDED_BLUEPRINT}" in _live_keys(_region(client, listing, "library-blueprints"))
        assert client.get(f"{added_url}?__live=1").status_code == 200

        _apply(client, "library-blueprint-removed")

        assert _region(client, listing, "library-blueprints") == before
        assert client.get(f"{added_url}?__live=1").status_code == 404


def test_live_change_library_selected_files_removed_makes_the_loaded_selections_unavailable(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        skill = f"{_ADVISOR_URL}/skills/daily-review?path={_CHECKLIST_FILE}&__live=1"
        prompt = f"{_ADVISOR_URL}/prompts?path={_RELEASE_WINDOW_FILE}&__live=1"
        assert client.get(skill).status_code == 200
        assert client.get(prompt).status_code == 200

        _apply(client, "library-selected-files-removed")

        assert client.get(skill).status_code == 404
        assert client.get(prompt).status_code == 404
        assert client.get(f"{_ADVISOR_URL}/skills/daily-review?path={_SKILL_FILE}&__live=1").status_code == 200

        _reset(client)

        assert client.get(skill).status_code == 200
        assert client.get(prompt).status_code == 200


def test_live_change_channel_memory_changes_move_only_the_content_marker(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        status = _region(client, _BRAND_STRATEGY, "channel-status")
        files = _region(client, _BRAND_STRATEGY, "channel-files")
        config = ConfigStore(runtime / "config.yaml").load().revision
        assert "1 file" in files

        _apply(client, "channel-memory-changes")

        changed = _region(client, _BRAND_STRATEGY, "channel-status")
        assert changed != status
        assert f'data-live-revision="{config}"' in changed
        assert "2 files" in _region(client, _BRAND_STRATEGY, "channel-files")


def test_live_change_channel_metadata_changed_renames_the_channel_and_moves_the_config_marker(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        header = _region(client, _BRAND_STRATEGY, "channel-header")
        before = ConfigStore(runtime / "config.yaml").load().revision

        _apply(client, "channel-metadata-changed")

        assert server.CHANNEL_RENAMED in _region(client, _BRAND_STRATEGY, "channel-header")
        assert _region(client, _BRAND_STRATEGY, "channel-header") != header
        assert f'data-live-revision="{before}"' not in _region(client, _BRAND_STRATEGY, "channel-status")


def test_live_change_channel_added_and_removed_change_the_list_and_the_second_page(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        listing = "/admin/memory-channels"
        detail = f"{listing}/{server.CHANNEL_ADDED}?__live=1"
        assert client.get(detail).status_code == 404

        _apply(client, "channel-added")

        assert f"channel:{server.CHANNEL_ADDED}" in _live_keys(_region(client, listing, "channels-table"))
        assert client.get(detail).status_code == 200

        _apply(client, "channel-removed")

        assert f"channel:{server.CHANNEL_ADDED}" not in _live_keys(_region(client, listing, "channels-table"))
        assert client.get(detail).status_code == 404


_WORKFLOW_LIBRARY = "/admin/workflow-library"
_DELIVERY_BLUEPRINT = f"{_WORKFLOW_LIBRARY}/blueprints/delivery"
_ADDED_BLUEPRINT = f"{_WORKFLOW_LIBRARY}/blueprints/{server.WORKFLOW_ADDED_BLUEPRINT}"
_DELIVERY_SETTINGS = "/newsletter/workflows/delivery/settings"
_ADDED_SETTINGS = f"/newsletter/workflows/{server.WORKFLOW_ADDED_ID}/settings"
_WORKFLOW_CASE_SEQUENCES = (
    ("workflow-source-changes",),
    ("workflow-blueprint-added",),
    ("workflow-blueprint-added", "workflow-blueprint-removed"),
    ("workflow-settings-changed",),
    ("workflow-added",),
    ("workflow-added", "workflow-removed"),
)


def _tree(root) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _directories(root) -> list[str]:
    return [path.relative_to(root).as_posix() for path in sorted(root.rglob("*")) if path.is_dir()]


def _workflow_state(runtime) -> dict:
    # Seeded ticket bodies carry generated identifiers, so the ticket roots are compared by layout.
    return {
        "library": _tree(runtime / "workflow-library"),
        "ticket-files": sorted(_tree(runtime / "tickets")),
        "ticket-directories": _directories(runtime / "tickets"),
        "config": ConfigStore(runtime / "config.yaml").load().revision,
    }


def test_every_workflow_case_belongs_to_a_reset_completeness_sequence():
    assert {case for sequence in _WORKFLOW_CASE_SEQUENCES for case in sequence} == set(server._WORKFLOW_LIVE_CHANGES)


@pytest.mark.parametrize("sequence", _WORKFLOW_CASE_SEQUENCES, ids="+".join)
def test_workflow_cases_are_fully_undone_by_a_reset(monkeypatch, sequence):
    with _live_fixture(monkeypatch) as (client, runtime):
        _reset(client)
        before = _workflow_state(runtime)

        changed = False
        for case in sequence:
            _apply(client, case)
            for path in (_WORKFLOW_LIBRARY, _DELIVERY_BLUEPRINT, _DELIVERY_SETTINGS, "/newsletter/workflows/new"):
                client.get(f"{path}?__live=1")
            changed = changed or _workflow_state(runtime) != before
        assert changed

        _reset(client)

        assert _workflow_state(runtime) == before


def test_live_change_workflow_source_changes_move_the_card_title_and_the_source_marker(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        cards = _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints")
        marker = _region(client, _DELIVERY_BLUEPRINT, "workflow-blueprint-source")
        assert server.WORKFLOW_EDITED_NAME not in cards

        _apply(client, "workflow-source-changes")

        assert server.WORKFLOW_EDITED_NAME in _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints")
        assert _region(client, _DELIVERY_BLUEPRINT, "workflow-blueprint-source") != marker
        assert server.WORKFLOW_EDITED_DESCRIPTION in (runtime / "workflow-library" / "delivery" / "workflow.yaml").read_text(encoding="utf-8")

        _reset(client)

        assert _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints") == cards
        assert _region(client, _DELIVERY_BLUEPRINT, "workflow-blueprint-source") == marker


def test_live_change_workflow_blueprint_added_and_removed_change_the_list_and_the_second_page(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        before = _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints")
        assert client.get(f"{_ADDED_BLUEPRINT}?__live=1").status_code == 404

        _apply(client, "workflow-blueprint-added")

        assert f"workflow-blueprint:{server.WORKFLOW_ADDED_BLUEPRINT}" in _live_keys(
            _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints")
        )
        assert client.get(f"{_ADDED_BLUEPRINT}?__live=1").status_code == 200

        _apply(client, "workflow-blueprint-removed")

        assert _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints") == before
        assert client.get(f"{_ADDED_BLUEPRINT}?__live=1").status_code == 404


def test_live_change_workflow_settings_changed_renames_the_title_and_moves_the_config_marker(monkeypatch):
    with _live_fixture(monkeypatch) as (client, _runtime):
        header = _region(client, _DELIVERY_SETTINGS, "workflow-settings-header")
        marker = _region(client, _DELIVERY_SETTINGS, "workflow-settings-source")

        _apply(client, "workflow-settings-changed")

        changed = _region(client, _DELIVERY_SETTINGS, "workflow-settings-header")
        assert f"{server.WORKFLOW_RENAMED} settings" in changed
        assert changed != header
        assert _region(client, _DELIVERY_SETTINGS, "workflow-settings-source") != marker

        _reset(client)

        assert _region(client, _DELIVERY_SETTINGS, "workflow-settings-header") == header
        assert _region(client, _DELIVERY_SETTINGS, "workflow-settings-source") == marker


def test_live_change_workflow_added_and_removed_change_the_references_and_the_second_page(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        before = _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints")
        assert client.get(f"{_ADDED_SETTINGS}?__live=1").status_code == 404

        _apply(client, "workflow-added")

        references = _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints")
        assert f"workflow-reference:delivery:newsletter:{server.WORKFLOW_ADDED_ID}" in _live_keys(references)
        assert server.WORKFLOW_ADDED_NAME in references
        assert client.get(f"{_ADDED_SETTINGS}?__live=1").status_code == 200

        _apply(client, "workflow-removed")

        assert _region(client, _WORKFLOW_LIBRARY, "workflow-library-blueprints") == before
        assert client.get(f"{_ADDED_SETTINGS}?__live=1").status_code == 404

        _apply(client, "workflow-added")
        _reset(client)

        assert not (runtime / "tickets" / server.WORKFLOW_ADDED_ID).exists()


# ── Coverage matrix: every inventory row answers its policy and changes its declared region ──

_COVERAGE = json.loads((Path(__file__).parent / "ui" / "live_coverage.json").read_text(encoding="utf-8"))
_LIVE_INITIAL = re.compile(r'<script type="application/json" id="live-initial">(.*?)</script>', re.S)
_GIT_EVIDENCE_INDEX = Path("fixture-index") / "git-evidence.json"


def _coverage_url(runtime, url: str) -> str:
    if url == "{tail-log-view}":
        return _log_view_path(runtime)
    if url == "{git-evidence-diff}":
        index = json.loads((runtime / _GIT_EVIDENCE_INDEX).read_text(encoding="utf-8"))
        return f"{index['ticket_href']}/artifacts/{index['current']['artifact_id']}/diff?source=ticket"
    return url


def _snapshot_get(client, url: str, **kwargs):
    separator = "&" if "?" in url else "?"
    return client.get(url if "__live=1" in url else f"{url}{separator}__live=1", **kwargs)


def _live_regions(client, url: str) -> dict[str, str]:
    response = _snapshot_get(client, url)
    assert response.status_code == 200, f"{url} answered {response.status_code}"
    return {region["key"]: region["html"] for region in response.json()["regions"]}


def _check_live_entry(client, runtime, entry: dict) -> list[str]:
    """Return every way this inventory row disagrees with the running app."""
    problems: list[str] = []
    navigation = set(_COVERAGE["navigation"][entry["shell"]])
    declared = set(entry["regions"])
    change = entry.get("change")
    kind = entry["kind"]

    def snapshot() -> tuple[object, str | None]:
        url = _coverage_url(runtime, entry["url"]) if entry.get("url") else None
        if kind in {"workflow-snapshot", "lifecycle"}:
            body = client.get(entry["snapshot"])
            return (body.json() if body.status_code == 200 else body.status_code), url
        if url is not None:
            page = client.get(url)
            if page.status_code != 200:
                return page.status_code, url
            initial = _LIVE_INITIAL.search(page.text)
            if initial is None:
                problems.append("page has no live registration")
                return {}, url
            registration = json.loads(initial.group(1))
            if registration["format"] != 1 or not registration["url"].endswith("__live=1"):
                problems.append(f"unexpected registration {registration}")
            for key in sorted(declared | navigation):
                if f'data-live-region="{key}"' not in page.text:
                    problems.append(f"no root for region {key}")
            return _live_regions(client, registration["url"]), registration["url"]
        return _live_regions(client, entry["snapshot"]), entry["snapshot"]

    for setup_case in (change or {}).get("setup", []):
        _apply(client, setup_case)

    before, snapshot_url = snapshot()
    if not isinstance(before, dict):
        return [f"snapshot unavailable: {before}"]

    if kind == "lifecycle":
        return problems if "state" in before else [*problems, "status payload has no state"]
    if kind == "workflow-snapshot":
        missing = declared - set(before)
        if missing:
            problems.append(f"snapshot misses {sorted(missing)}")
        if entry["shell"] == "team":
            shell = {region["key"] for region in before["shell"]["regions"]}
            if shell != navigation:
                problems.append(f"shell regions {sorted(shell)}")
    else:
        keys = set(before)
        if keys - navigation != declared:
            problems.append(f"regions {sorted(keys - navigation)} != declared {sorted(declared)}")
        if keys & navigation != navigation:
            problems.append(f"navigation regions {sorted(keys & navigation)} != {sorted(navigation)}")
        response = _snapshot_get(client, snapshot_url)
        revalidated = _snapshot_get(client, snapshot_url, headers={"If-None-Match": response.headers["etag"]})
        if revalidated.status_code != 304:
            problems.append(f"conditional read answered {revalidated.status_code}")

    assert change["case"] in server.LIVE_CHANGE_CASES
    _apply(client, change["case"])
    after, _url = snapshot()
    if not isinstance(after, dict):
        return [*problems, f"snapshot lost after {change['case']}: {after}"]
    if before.get(change["region"]) == after.get(change["region"]):
        problems.append(f"region {change['region']} did not change after {change['case']}")
    return problems


def test_every_live_inventory_row_answers_its_policy_and_changes_its_declared_region(monkeypatch):
    live = [entry for entry in _COVERAGE["entries"] if entry["kind"] in {"snapshot", "shell-only", "workflow-snapshot", "lifecycle"}]
    failures: dict[str, list[str]] = {}
    with _admin_fixture(monkeypatch) as (client, runtime):
        for entry in live:
            _reset_to(client, entry.get("fixture", "default"))
            problems = _check_live_entry(client, runtime, entry)
            if problems:
                failures[entry["id"]] = problems
        _reset(client)
    assert failures == {}


def _reset_to(client, fixture: str) -> None:
    response = client.post(server.UI_RESET_PATH, json={"fixture": fixture})
    assert response.status_code == 204, fixture


def test_every_non_poll_inventory_row_keeps_its_classification(monkeypatch):
    rows = [entry for entry in _COVERAGE["entries"] if entry["kind"] in {"redirect", "retired", "standalone"} and entry.get("sample")]
    rows.append(next(entry for entry in _COVERAGE["entries"] if entry["id"] == "setup-complete"))
    failures: dict[str, str] = {}
    with _admin_fixture(monkeypatch) as (client, _runtime):
        for entry in rows:
            sample = entry.get("sample") or entry["url"]
            response = client.get(sample, follow_redirects=False)
            expected = entry.get("status", 200)
            if response.status_code != expected:
                failures[entry["id"]] = f"answered {response.status_code}, classified {expected}"
            elif response.status_code == 200 and "live-initial" in response.text:
                failures[entry["id"]] = "a non-poll page registered for live refresh"
            elif "json" in response.headers.get("content-type", "") and "regions" in response.text and response.status_code == 200:
                failures[entry["id"]] = "a non-poll route answered with a snapshot"
    assert failures == {}


# ── Fixture and reset safety ─────────────────────────────────────────────────


def test_live_change_endpoint_rejects_an_oversized_body_without_changing_state(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        before = ConfigStore(runtime / "config.yaml").load().revision
        padded = json.dumps({"case": "navigation-membership", "padding": "x" * (server.LIVE_CHANGE_MAX_BODY_BYTES + 1)})

        response = client.post(server.LIVE_CHANGE_PATH, content=padded, headers={"content-type": "application/json"})

        assert response.status_code == 413
        assert ConfigStore(runtime / "config.yaml").load().revision == before
        assert client.post(server.LIVE_CHANGE_PATH, json={"case": "navigation-membership"}).status_code == 204


_LIVE_CASE_PREREQUISITES = {
    "log-appended": ("log-tall",),
    "log-truncated": ("log-tall",),
    "log-oversized": ("log-tall",),
    "log-removed": ("log-tall",),
}


def _runtime_fingerprint(runtime) -> dict[str, str]:
    import hashlib

    fingerprint = {}
    for path in sorted(runtime.rglob("*")):
        relative = path.relative_to(runtime).as_posix()
        if relative == "server.pid" or relative.startswith("__pycache__"):
            continue
        # The memory store creates empty bookkeeping directories on its first write and keeps them.
        if path.is_dir() and relative.startswith("memory-store/."):
            continue
        # The seeder stamps tickets with fresh times, so only their membership is stable.
        stable = path.is_file() and not relative.startswith("tickets/")
        fingerprint[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if stable else "node"
    return fingerprint


@pytest.mark.parametrize("fixture", ["default", server.WORKSPACES_FIXTURE])
def test_every_live_change_case_is_undone_by_a_reset_to_the_pristine_runtime(monkeypatch, fixture):
    workspace_cases = {case for case in server.LIVE_CHANGE_CASES if case.startswith("workspace")}
    cases = sorted(workspace_cases if fixture == server.WORKSPACES_FIXTURE else set(server.LIVE_CHANGE_CASES) - workspace_cases)
    drifted: list[str] = []
    with _admin_fixture(monkeypatch) as (client, runtime):
        _reset_to(client, fixture)
        pristine = _runtime_fingerprint(runtime)
        for case in cases:
            for prerequisite in _LIVE_CASE_PREREQUISITES.get(case, ()):
                _apply(client, prerequisite)
            _apply(client, case)
            _reset_to(client, fixture)
            if _runtime_fingerprint(runtime) != pristine:
                drifted.append(case)
    assert drifted == []


def test_reset_keeps_the_workflow_library_and_durable_job_roots_in_place(monkeypatch):
    with _live_fixture(monkeypatch) as (client, runtime):
        roots = [
            runtime / "workflow-library",
            runtime / "memory-store" / ".jobs" / "newsletter",
            runtime / "memory-store" / ".jobs" / "research",
            runtime / "tickets" / "delivery",
        ]
        _reset(client)
        identities = {root: root.stat().st_ino for root in roots}
        assert all(root.is_dir() for root in roots)

        _apply(client, "workflow-blueprint-added")
        _apply(client, "job-added")
        _reset(client)

        assert {root: root.stat().st_ino for root in roots} == identities

