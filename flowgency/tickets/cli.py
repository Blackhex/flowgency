from __future__ import annotations

import json
import uuid
from argparse import Namespace

from flowgency.tickets.models import TicketOperation, TicketRef, UserTicketContext
from flowgency.tickets.views import build_board_view, build_ticket_detail_view
from flowgency.workflows.configuration import resolve_workflow_binding


def _cli():
    from flowgency import cli as cli_mod

    return cli_mod


def _ticket_context(args: Namespace):
    cli_mod = _cli()
    services = cli_mod._services(args)
    if services.tickets is None:
        raise cli_mod.CliFailure(
            cli_mod.ExitCode.OPERATIONAL_FAILURE,
            "ticket-service-unavailable",
            "Ticket service unavailable",
        )
    snapshot = services.config_store.load()
    team_id = cli_mod._team_id(args, snapshot)
    actor = UserTicketContext(team_id=team_id)
    return cli_mod, services, snapshot, team_id, actor


def _ticket_operation(label: str) -> TicketOperation:
    token = uuid.uuid4().hex
    return TicketOperation(operation_id=f"{label}-{token}", request_digest=token)


def _summary_payload(view) -> dict:
    state_name = view.record.state_id
    if view.definition is not None:
        try:
            state_name = view.definition.state(view.record.state_id).name
        except Exception:
            state_name = view.record.state_id
    return {
        "id": view.record.id,
        "title": view.record.title,
        "state_id": view.record.state_id,
        "state_name": state_name,
        "assignee": view.record.assignee,
        "revision": view.record.revision,
        "pending_run_job_id": (
            None if view.record.pending_run is None else view.record.pending_run.job_id
        ),
        "active_run_job_id": (
            None if view.record.active_run is None else view.record.active_run.job_id
        ),
        "issues": list(view.issues),
    }


def _ticket_version(services, actor: UserTicketContext, workflow_id: str, ticket_id: str):
    snapshot = services.config_store.load()
    binding = resolve_workflow_binding(snapshot, actor.team_id, workflow_id)
    detail = services.tickets.inspect(
        actor,
        TicketRef.from_binding(binding.storage, ticket_id),
    )
    if detail.version is None:
        cli_mod = _cli()
        raise cli_mod.CliFailure(
            cli_mod.ExitCode.OPERATIONAL_FAILURE,
            "workflow-unavailable",
            "Current workflow definition is unavailable",
        )
    return detail.version


def cmd_workflows(args: Namespace) -> int:
    cli_mod, services, snapshot, team_id, actor = _ticket_context(args)
    payload = []
    for binding in services.tickets.list_workflows(actor):
        board = build_board_view(
            services.tickets,
            actor,
            binding.workflow_id,
            ticket_jobs=services.ticket_jobs,
        )
        payload.append(
            {
                "id": binding.workflow_id,
                "name": snapshot.config.teams[team_id].workflows[binding.workflow_id].name,
                "blueprint": binding.blueprint_id,
                "integration": binding.storage.integration,
                "binding_id": binding.storage.binding_id,
                "ticket_count": board.ticket_count,
                "working_count": board.working_count,
                "unassigned_count": sum(
                    1
                    for column in board.columns
                    for ticket in column.tickets
                    if ticket.assignee is None
                ),
                "issue_count": len(board.issues),
            }
        )
    if args.json:
        cli_mod._print_json(payload)
    else:
        print(f"\n{cli_mod.bold('Workflows')} - {snapshot.config.teams[team_id].name}\n")
        for workflow in payload:
            print(
                f"  {workflow['name']} ({workflow['id']})  {workflow['ticket_count']} tickets  "
                f"{workflow['working_count']} active  {workflow['unassigned_count']} unassigned"
            )
        if not payload:
            print("  No workflows configured.")
        print()
    return 0


def cmd_tickets(args: Namespace) -> int:
    cli_mod, services, _snapshot, _team_id, actor = _ticket_context(args)
    views = services.tickets.list_tickets(
        actor,
        args.workflow,
        assignee=args.assignee,
        state_id=args.state,
        query=getattr(args, "query", "") or "",
    )
    payload = [_summary_payload(view) for view in views]
    if args.json:
        cli_mod._print_json(payload)
    else:
        print(f"\n{cli_mod.bold('Tickets')} - {args.workflow}\n")
        for ticket in payload:
            print(
                f"  {ticket['id']}  {ticket['title']}  {ticket['state_name']}"
                + (f"  @{ticket['assignee']}" if ticket['assignee'] else "")
            )
        if not payload:
            print("  No tickets found.")
        print()
    return 0


def cmd_ticket_show(args: Namespace) -> int:
    cli_mod, services, _snapshot, _team_id, actor = _ticket_context(args)
    detail = build_ticket_detail_view(
        services.tickets,
        actor,
        TicketRef.from_binding(
            resolve_workflow_binding(services.config_store.load(), actor.team_id, args.workflow).storage,
            args.ticket_id,
        ),
        ticket_jobs=services.ticket_jobs,
    )
    payload = detail.model_dump(mode="json")
    if args.json:
        cli_mod._print_json(payload)
    else:
        ticket = payload["ticket"]
        print(f"\n{cli_mod.bold(ticket['title'])} ({ticket['id']})\n")
        print(f"  State: {ticket['state_name']}")
        print(f"  Assignee: {ticket['assignee'] or 'unassigned'}")
        print(f"  Revision: {ticket['revision']}")
        print()
    return 0


def cmd_ticket_create(args: Namespace) -> int:
    cli_mod, services, _snapshot, _team_id, actor = _ticket_context(args)
    result = services.tickets.create(
        actor,
        args.workflow,
        args.title,
        args.description or "",
        None,
        _ticket_operation("ticket-create"),
    )
    payload = {
        "ticket_id": result.ticket.id,
        "revision": result.ticket.revision,
        "replayed": result.replayed,
    }
    if args.json:
        cli_mod._print_json(payload)
    else:
        print(f"Created {payload['ticket_id']} in {args.workflow}")
    return 0


def cmd_ticket_assign(args: Namespace) -> int:
    cli_mod, services, _snapshot, _team_id, actor = _ticket_context(args)
    version = _ticket_version(services, actor, args.workflow, args.ticket_id)
    result = services.tickets.assign(
        actor,
        version,
        args.agent,
        _ticket_operation("ticket-assign"),
    )
    payload = {
        "ticket": result.ticket.model_dump(mode="json"),
        "replayed": result.replayed,
    }
    if args.json:
        cli_mod._print_json(payload)
    else:
        print(f"Assigned {args.ticket_id} to {args.agent}")
    return 0


def cmd_ticket_unassign(args: Namespace) -> int:
    cli_mod, services, _snapshot, _team_id, actor = _ticket_context(args)
    version = _ticket_version(services, actor, args.workflow, args.ticket_id)
    result = services.tickets.assign(
        actor,
        version,
        None,
        _ticket_operation("ticket-unassign"),
    )
    payload = {
        "ticket": result.ticket.model_dump(mode="json"),
        "replayed": result.replayed,
    }
    if args.json:
        cli_mod._print_json(payload)
    else:
        print(f"Unassigned {args.ticket_id}")
    return 0


def cmd_ticket_run(args: Namespace) -> int:
    cli_mod, services, _snapshot, _team_id, actor = _ticket_context(args)
    if services.ticket_jobs is None:
        raise cli_mod.CliFailure(
            cli_mod.ExitCode.OPERATIONAL_FAILURE,
            "ticket-jobs-unavailable",
            "Ticket job service unavailable",
        )
    version = _ticket_version(services, actor, args.workflow, args.ticket_id)
    handle = services.ticket_jobs.submit(actor, version, f"cli-run-{uuid.uuid4().hex}")
    payload = {
        "job_id": handle.job_id,
        "status": handle.status,
        "path": str(handle.path),
    }
    if args.json:
        cli_mod._print_json(payload)
    else:
        print(f"Queued {handle.job_id} for {args.ticket_id}")
    return 0


def register_ticket_commands(subparsers) -> None:
    cli_mod = _cli()

    workflows = subparsers.add_parser("workflows", help="List configured workflows")
    cli_mod._add_team_json(workflows)
    workflows.set_defaults(handler=cmd_workflows)

    tickets = subparsers.add_parser("tickets", help="List tickets in one workflow")
    cli_mod._add_team_json(tickets)
    tickets.add_argument("--workflow", required=True)
    tickets.add_argument("--state")
    tickets.add_argument("--assignee")
    tickets.add_argument("--query")
    tickets.set_defaults(handler=cmd_tickets)

    ticket = subparsers.add_parser("ticket", help="Inspect or mutate one ticket")
    cli_mod._add_config(ticket)
    ticket_subparsers = ticket.add_subparsers(dest="ticket_command", required=True)

    show = ticket_subparsers.add_parser("show", help="Show one ticket")
    cli_mod._add_team_json(show)
    show.add_argument("ticket_id")
    show.add_argument("--workflow", required=True)
    show.set_defaults(handler=cmd_ticket_show)

    create = ticket_subparsers.add_parser("create", help="Create a ticket")
    cli_mod._add_team_json(create)
    create.add_argument("--workflow", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--description", default="")
    create.set_defaults(handler=cmd_ticket_create)

    assign = ticket_subparsers.add_parser("assign", help="Assign a ticket")
    cli_mod._add_team_json(assign)
    assign.add_argument("ticket_id")
    assign.add_argument("--workflow", required=True)
    assign.add_argument("--agent", required=True)
    assign.set_defaults(handler=cmd_ticket_assign)

    unassign = ticket_subparsers.add_parser("unassign", help="Clear a ticket assignee")
    cli_mod._add_team_json(unassign)
    unassign.add_argument("ticket_id")
    unassign.add_argument("--workflow", required=True)
    unassign.set_defaults(handler=cmd_ticket_unassign)

    run = ticket_subparsers.add_parser("run", help="Queue work for an assigned ticket")
    cli_mod._add_team_json(run)
    run.add_argument("ticket_id")
    run.add_argument("--workflow", required=True)
    run.set_defaults(handler=cmd_ticket_run)