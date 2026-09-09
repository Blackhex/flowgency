from __future__ import annotations

import hashlib
import json
import re

import flowgency.jobs.submission as submission_module

from tests._ticket_helpers import TicketRuntimeIntegration


def test_workflow_board_renders_html_page(workflow_web_env):
    response = workflow_web_env.client.get(workflow_web_env.base_path)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Search tickets" in response.text
    assert "New ticket" in response.text


def test_workflow_board_sidebar_uses_workflow_library_links(workflow_web_env):
    response = workflow_web_env.client.get(workflow_web_env.base_path)

    assert response.status_code == 200
    assert 'href="/admin/workflow-library"' in response.text
    assert "Workflow Library" in response.text
    assert "Pipeline" not in response.text
    assert 'href="/newsletter/observations"' not in response.text
    assert 'href="/newsletter/proposals"' not in response.text
    assert 'href="/newsletter/decisions"' not in response.text


def test_workflow_board_assignee_options_follow_configured_team_agents(workflow_web_env):
    response = workflow_web_env.client.get(f"{workflow_web_env.base_path}?ticket=ticket-a")

    assert response.status_code == 200
    assert 'option value="builder"' in response.text
    assert 'option value="advisor"' not in response.text
    assert 'option value="reviewer"' not in response.text
    assert 'option value="researcher"' not in response.text


def test_workflow_board_close_link_returns_to_filtered_board(workflow_web_env):
    env = workflow_web_env
    ticket = env.create(title="Close target")
    env.service.assign(env.user, ticket.version, "builder", env.operation("assign-close"))

    response = env.client.get(
        f"{env.base_path}?ticket={ticket.ref.ticket_id}&query=Close&assignee=builder"
    )

    assert response.status_code == 200
    match = re.search(r'aria-label="Close" href="([^"]+)"', response.text)
    assert match is not None
    href = match.group(1)
    assert href.startswith(env.base_path)
    assert "ticket=" not in href
    assert "query=Close" in href
    assert "assignee=builder" in href


def test_board_snapshot_uses_definition_order_and_filtered_counts(workflow_web_env):
    env = workflow_web_env
    first = env.create(title="Alpha review")
    second = env.create(title="Beta review")
    env.seed_ticket(state_id="done", values={"summary": "shipped"})
    env.service.assign(env.user, first.version, "builder", env.operation("assign-alpha"))
    builder = env.agent("builder", "run-alpha")
    env.service.start_work(
        builder,
        env.read(first.ref).version,
        env.operation("start-alpha", actor_name=builder.agent_name),
    )
    env.service.assign(env.user, second.version, "observer", env.operation("assign-beta"))

    response = env.client.get(f"{env.base_path}/snapshot?query=Alpha&assignee=builder")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket_count"] == 3
    assert payload["working_count"] == 1
    assert [column["state_id"] for column in payload["columns"]] == ["review", "done"]
    assert [column["count"] for column in payload["columns"]] == [1, 0]
    assert payload["columns"][0]["tickets"][0]["title"] == "Alpha review"


def test_board_snapshot_reports_missing_storage_without_disabling_other_services(workflow_web_env):
    env = workflow_web_env
    env.root_a.rmdir()

    response = env.client.get(f"{env.base_path}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["issues"][0]["code"] == "missing-root"
    assert env.client.app.state.services.instances is not None

    html = env.client.get(env.base_path)
    assert html.status_code == 200
    assert "missing-root" in html.text


def test_board_snapshot_uses_deterministic_etag(workflow_web_env):
    response = workflow_web_env.client.get(f"{workflow_web_env.base_path}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert response.headers["etag"] == f'W/"{expected}"'

    not_modified = workflow_web_env.client.get(
        f"{workflow_web_env.base_path}/snapshot",
        headers={"If-None-Match": response.headers["etag"]},
    )

    assert not_modified.status_code == 304


def test_board_snapshot_reports_invalid_definition_history(workflow_web_env):
    env = workflow_web_env
    env.library.source_path(env.blueprint_id).unlink()

    response = env.client.get(f"{env.base_path}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["issues"][0]["code"] == "unavailable-workflow"
    assert any("missing-blueprint" in item for item in payload["issues"][0]["history"])

    html = env.client.get(env.base_path)
    assert html.status_code == 200
    assert "unavailable-workflow" in html.text


def test_board_snapshot_keeps_readable_tickets_and_counts_when_definition_is_unavailable(
    workflow_web_env,
):
    env = workflow_web_env
    first = env.create(title="Alpha review")
    second = env.create(title="Beta review")
    env.service.assign(env.user, first.version, "builder", env.operation("assign-alpha"))
    builder = env.agent("builder", "run-alpha")
    env.service.start_work(
        builder,
        env.read(first.ref).version,
        env.operation("start-alpha", actor_name=builder.agent_name),
    )
    env.library.source_path(env.blueprint_id).unlink()

    response = env.client.get(
        f"{env.base_path}/snapshot?selected_ticket={first.ref.ticket_id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["issues"][0]["code"] == "unavailable-workflow"
    assert payload["ticket_count"] == 2
    assert payload["working_count"] == 1
    assert [column["kind"] for column in payload["columns"]] == ["unavailable"]
    assert [column["key"] for column in payload["columns"]] == ["unavailable"]
    assert [column["state_id"] for column in payload["columns"]] == [None]
    assert payload["columns"][0]["count"] == 2
    assert [ticket["title"] for ticket in payload["columns"][0]["tickets"]] == [
        "Alpha review",
        "Beta review",
    ]
    assert payload["selected_ticket"]["ticket"]["title"] == "Alpha review"
    assert payload["selected_ticket"]["ticket"]["version"] is None
    assert payload["selected_ticket"]["ticket"]["history"][0]["kind"] == "opened"


def test_board_snapshot_groups_unknown_state_records_without_fabricating_a_state_column(
    workflow_web_env,
):
    env = workflow_web_env
    env.seed_ticket(state_id="missing-state", values={"summary": "orphaned"})

    response = env.client.get(f"{env.base_path}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket_count"] == 1
    assert [column["kind"] for column in payload["columns"]] == [
        "state",
        "state",
        "invalid-data",
    ]
    assert [column["key"] for column in payload["columns"]] == [
        "state:review",
        "state:done",
        "invalid-data",
    ]
    assert [column["state_id"] for column in payload["columns"]] == [
        "review",
        "done",
        None,
    ]
    assert payload["columns"][2]["count"] == 1
    assert payload["columns"][2]["tickets"][0]["state_id"] == "missing-state"
    assert payload["columns"][2]["tickets"][0]["issues"][0]["code"] == "invalid-data"


def test_board_snapshot_keeps_real_state_ids_distinct_from_synthetic_group_keys(
    workflow_web_env,
):
    env = workflow_web_env
    source = env.library.inspect(env.blueprint_id)
    states = source.definition.states + (
        source.definition.state("review").model_copy(
            update={"id": "invalid-data", "name": "Real invalid-data", "color": "#4f46e5"}
        ),
        source.definition.state("done").model_copy(
            update={
                "id": "unavailable-workflow",
                "name": "Real unavailable-workflow",
                "color": "#0891b2",
            }
        ),
    )
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        source.definition.model_copy(
            update={
                "states": states,
                "initial_state": "invalid-data",
            }
        ),
    )
    real_invalid = env.seed_ticket(state_id="invalid-data", values={"summary": "real invalid"})
    real_unavailable = env.seed_ticket(
        state_id="unavailable-workflow",
        values={"summary": "real unavailable"},
    )
    unknown = env.seed_ticket(state_id="missing-state", values={"summary": "orphaned"})

    response = env.client.get(
        f"{env.base_path}/snapshot?selected_ticket={unknown.ticket_id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert [column["state_id"] for column in payload["columns"]] == [
        "review",
        "done",
        "invalid-data",
        "unavailable-workflow",
        None,
    ]
    assert [column["kind"] for column in payload["columns"]] == [
        "state",
        "state",
        "state",
        "state",
        "invalid-data",
    ]
    assert [column["key"] for column in payload["columns"]] == [
        "state:review",
        "state:done",
        "state:invalid-data",
        "state:unavailable-workflow",
        "invalid-data",
    ]
    assert [column["count"] for column in payload["columns"]] == [0, 0, 1, 1, 1]
    assert payload["columns"][2]["tickets"][0]["ref"]["ticket_id"] == real_invalid.ticket_id
    assert payload["columns"][3]["tickets"][0]["ref"]["ticket_id"] == real_unavailable.ticket_id
    assert payload["columns"][4]["tickets"][0]["state_id"] == "missing-state"
    assert payload["selected_ticket"]["ticket"]["state_id"] == "missing-state"


def test_board_snapshot_reads_only_current_binding_after_rebind(workflow_web_env):
    env = workflow_web_env
    env.create(title="Old root ticket")
    env.set_storage_root(env.root_b)

    response = env.client.get(f"{env.base_path}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket_count"] == 0
    assert all(not column["tickets"] for column in payload["columns"])


def test_board_snapshot_counts_active_tickets_not_jobs(workflow_web_env):
    env = workflow_web_env
    first = env.create(title="First active")
    second = env.create(title="Second active")
    env.service.assign(env.user, first.version, "builder", env.operation("assign-first"))
    env.service.assign(env.user, second.version, "builder", env.operation("assign-second"))
    authority = env.running_job("builder", "run-a")

    with env.broker_session(authority) as (context, client):
        for index, ticket in enumerate((first, second), start=1):
            started = client.call(
                "start_work",
                {
                    "version": env.read(ticket.ref).version.model_dump(mode="json"),
                    "operation_id": f"start-run-a-{index}",
                },
            )
            assert started["ok"] is True

    response = env.client.get(f"{env.base_path}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket_count"] == 2
    assert payload["working_count"] == 2


def test_run_route_accepts_read_only_assignee_when_live_channel_supported(workflow_web_env, monkeypatch):
    env = workflow_web_env
    monkeypatch.setattr(submission_module, "REGISTRY", {"claude-code": TicketRuntimeIntegration()})
    ticket = env.create(title="Read only run")
    env.service.assign(env.user, ticket.version, "observer", env.operation("assign-observer"))
    current = env.read(ticket.ref)

    response = env.client.post(
        f"{env.base_path}/tickets/{ticket.ref.ticket_id}/run",
        data={
            "payload": __import__("json").dumps(
                {
                    "version": current.version.model_dump(mode="json"),
                    "operation_id": "run-observer",
                }
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert len(env.job_store.paths(env.team_id)) == 1