from __future__ import annotations

import flowgency.jobs.submission as submission_module

from tests._ticket_helpers import TicketRuntimeIntegration


def test_workflow_board_redirects_to_snapshot(workflow_web_env):
    response = workflow_web_env.client.get(workflow_web_env.base_path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"{workflow_web_env.base_path}/snapshot"


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


def test_board_snapshot_reports_invalid_definition_history(workflow_web_env):
    env = workflow_web_env
    env.library.source_path(env.blueprint_id).unlink()

    response = env.client.get(f"{env.base_path}/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["issues"][0]["code"] == "unavailable-workflow"
    assert any("missing-blueprint" in item for item in payload["issues"][0]["history"])


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
    assert [column["state_id"] for column in payload["columns"]] == ["unavailable-workflow"]
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
    assert [column["state_id"] for column in payload["columns"]] == [
        "review",
        "done",
        "invalid-data",
    ]
    assert payload["columns"][2]["count"] == 1
    assert payload["columns"][2]["tickets"][0]["state_id"] == "missing-state"
    assert payload["columns"][2]["tickets"][0]["issues"][0]["code"] == "invalid-data"


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