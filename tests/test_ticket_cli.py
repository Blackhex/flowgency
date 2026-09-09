from __future__ import annotations

import json
from pathlib import Path

from flowgency import cli
from flowgency.jobs import JobHandle
from flowgency.tickets.models import TicketOperation, UserTicketContext
from flowgency.web.dependencies import build_services
from tests.test_cli_contract import cli_config, cli_runner


def _services(config_path: Path):
    services = build_services(config_path)
    assert services.tickets is not None
    return services


def _seed(config_path: Path, *, title: str, assignee: str | None = None):
    services = _services(config_path)
    actor = UserTicketContext(team_id="newsletter")
    key = title.lower().replace(" ", "-")
    created = services.tickets.create(
        actor,
        "delivery",
        title,
        "",
        None,
        TicketOperation(operation_id=f"seed-{key}", request_digest=f"seed-{key}"),
    )
    if assignee is not None:
        version = services.tickets.inspect(actor, created.ticket.ref).version
        assert version is not None
        services.tickets.assign(
            actor,
            version,
            assignee,
            TicketOperation(operation_id=f"assign-{key}", request_digest=f"assign-{key}"),
        )
    return created.ticket.ref.ticket_id


def test_workflows_lists_ticket_counts(cli_config, cli_runner):
    _seed(cli_config, title="Alpha review", assignee="builder")
    _seed(cli_config, title="Beta review")

    result = cli_runner("workflows", "--team", "newsletter", "--json", config=cli_config)

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == [
        {
            "id": "delivery",
            "name": "Delivery",
            "blueprint": "delivery",
            "integration": "local",
            "binding_id": payload[0]["binding_id"],
            "ticket_count": 2,
            "working_count": 0,
            "unassigned_count": 1,
            "issue_count": 0,
        }
    ]


def test_tickets_filters_by_assignee(cli_config, cli_runner):
    _seed(cli_config, title="Alpha review", assignee="builder")
    _seed(cli_config, title="Beta review")

    result = cli_runner(
        "tickets",
        "--team",
        "newsletter",
        "--workflow",
        "delivery",
        "--assignee",
        "builder",
        "--json",
        config=cli_config,
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["title"] == "Alpha review"
    assert payload[0]["assignee"] == "builder"


def test_ticket_show_returns_ticket_detail(cli_config, cli_runner):
    ticket_id = _seed(cli_config, title="Alpha review", assignee="builder")

    result = cli_runner(
        "ticket",
        "show",
        ticket_id,
        "--team",
        "newsletter",
        "--workflow",
        "delivery",
        "--json",
        config=cli_config,
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ticket"]["title"] == "Alpha review"
    assert payload["ticket"]["assignee"] == "builder"
    assert payload["binding"]["workflow_id"] == "delivery"


def test_ticket_create_and_assignment_commands_mutate_ticket(cli_config, cli_runner):
    created = cli_runner(
        "ticket",
        "create",
        "--team",
        "newsletter",
        "--workflow",
        "delivery",
        "--title",
        "Gamma review",
        "--json",
        config=cli_config,
    )
    assert created.exit_code == 0
    created_payload = json.loads(created.stdout)
    ticket_id = created_payload["ticket_id"]

    assigned = cli_runner(
        "ticket",
        "assign",
        ticket_id,
        "--team",
        "newsletter",
        "--workflow",
        "delivery",
        "--agent",
        "builder",
        "--json",
        config=cli_config,
    )
    assert assigned.exit_code == 0
    assert json.loads(assigned.stdout)["ticket"]["assignee"] == "builder"

    unassigned = cli_runner(
        "ticket",
        "unassign",
        ticket_id,
        "--team",
        "newsletter",
        "--workflow",
        "delivery",
        "--json",
        config=cli_config,
    )
    assert unassigned.exit_code == 0
    assert json.loads(unassigned.stdout)["ticket"]["assignee"] is None


def test_ticket_run_submits_ticket_job(cli_config, cli_runner, monkeypatch):
    ticket_id = _seed(cli_config, title="Alpha review", assignee="builder")
    submitted = []

    def fake_submit(self, actor, version, operation_id):
        submitted.append((actor, version, operation_id))
        return JobHandle("queued-ticket-job", "queued", Path("job.yaml"), None)

    monkeypatch.setattr("flowgency.jobs.tickets.TicketJobCoordinator.submit", fake_submit)

    result = cli_runner(
        "ticket",
        "run",
        ticket_id,
        "--team",
        "newsletter",
        "--workflow",
        "delivery",
        "--json",
        config=cli_config,
    )

    assert result.exit_code == 0
    assert len(submitted) == 1
    assert submitted[0][0].team_id == "newsletter"
    assert submitted[0][1].ref.ticket_id == ticket_id
    payload = json.loads(result.stdout)
    assert payload["status"] == "queued"