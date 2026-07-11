"""Agency CLI — terminal interface for agent management."""
import argparse
from argparse import Namespace
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

# Import shared helpers from the web app module
from agency.app import (
    load_config, reload_groups, get_agency_config, get_group,
    list_observations, list_proposals, list_decisions,
    collect_agents_with_identity, extract_display_title,
    parse_frontmatter, update_frontmatter_field,
    GROUPS, CONFIG, CONFIG_PATH, run_server,
)
from agency.config import load_config_path, save_config_path
from agency.dispatch.install import install_timer, uninstall_timer, get_timer_status


# ── ANSI Colors ──────────────────────────────────────────────────────────────

def _supports_color():
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

COLORS_ENABLED = _supports_color()

def _c(code: str, text: str) -> str:
    if not COLORS_ENABLED:
        return text
    return f"\033[{code}m{text}\033[0m"

def green(t): return _c("32", t)
def yellow(t): return _c("33", t)
def red(t): return _c("31", t)
def cyan(t): return _c("36", t)
def bold(t): return _c("1", t)
def dim(t): return _c("2", t)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _default_group() -> str:
    cfg = get_agency_config()
    return cfg.get("default_group", "")

def _resolve_group(args) -> dict:
    """Get group dict from --group flag or default."""
    group_key = getattr(args, "group", None) or _default_group()
    if not group_key:
        print("Error: No group specified and no default_group in config.", file=sys.stderr)
        sys.exit(1)
    try:
        return get_group(group_key)
    except Exception:
        print(f"Error: Group '{group_key}' not found.", file=sys.stderr)
        sys.exit(1)

def _health_dot(health: str) -> str:
    if health == "green": return green("●")
    if health == "amber": return yellow("●")
    return red("●")

def _relative_time(dt_val) -> str:
    if not dt_val:
        return "never"
    if isinstance(dt_val, str):
        try:
            dt_val = datetime.fromisoformat(dt_val)
        except (ValueError, TypeError):
            return str(dt_val)
    now = datetime.now(dt_val.tzinfo) if dt_val.tzinfo else datetime.now()
    diff = now - dt_val
    minutes = int(diff.total_seconds() / 60)
    if minutes < 60: return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24: return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


# ── Subcommands ──────────────────────────────────────────────────────────────

def cmd_serve(args):
    """Start the web server."""
    run_server(host=args.host, port=args.port, reload=args.reload)

def cmd_inbox(args):
    """Show what needs attention."""
    g = _resolve_group(args)
    observations = list_observations(g)
    proposals = list_proposals(g)
    decisions = list_decisions(g)

    actionable = [p for p in proposals if p.get("status") in ("proposed", "investigating")]
    floated = [o for o in observations if o.get("float") and o.get("status") == "open"]
    open_obs = [o for o in observations if o.get("status") == "open"]

    if getattr(args, "json", False):
        print(json.dumps({
            "group": g["key"],
            "actionable_proposals": [{"slug": p["_slug"], "title": p.get("_title", p["_slug"]), "status": p["status"], "agent": p.get("origin_agent", "")} for p in actionable],
            "floated_observations": [{"slug": o["_slug"], "title": o.get("_title", o["_slug"]), "agent": o.get("agent", "")} for o in floated],
            "open_observations": len(open_obs),
            "total_decisions": len(decisions),
        }, indent=2))
        return

    title = get_agency_config().get("title", "Agency")
    print(f"\n{bold(title)} — {g['name']}\n")

    if actionable:
        print(f"  {bold('NEEDS DECISION')} ({len(actionable)})")
        for p in actionable:
            agent = p.get("origin_agent", "?")
            title_text = p.get("_title", p["_slug"])
            print(f"  ├─ {cyan(p['_slug'][:30])}  {p.get('status', '')}  {dim(agent)}")
        print()

    if floated:
        print(f"  {bold('FLOATED SIGNALS')} ({len(floated)})")
        for o in floated:
            agent = o.get("agent", "?")
            print(f"  ├─ {yellow(o['_slug'][:30])}  {dim(agent)}")
        print()

    if open_obs:
        print(f"  {bold('OPEN OBSERVATIONS')} ({len(open_obs)})")
        for o in open_obs[:5]:
            agent = o.get("agent", "?")
            print(f"  ├─ {o['_slug'][:30]}  {dim(agent)}")
        if len(open_obs) > 5:
            print(f"  └─ ... and {len(open_obs) - 5} more")
        print()

    agents, _ = collect_agents_with_identity(g)
    healthy = sum(1 for a in agents if a["health"] == "green")
    print(f"  FLEET: {len(agents)} agents · {healthy} green · {len(agents) - healthy} other\n")

def cmd_status(args):
    """Fleet overview across all groups."""
    if getattr(args, "json", False):
        result = {}
        for key in GROUPS:
            g = get_group(key)
            obs = list_observations(g)
            props = list_proposals(g)
            decs = list_decisions(g)
            agents, _ = collect_agents_with_identity(g)
            result[key] = {
                "name": g["name"],
                "observations": len(obs),
                "proposals": len(props),
                "decisions": len(decs),
                "agents": len(agents),
                "healthy": sum(1 for a in agents if a["health"] == "green"),
            }
        print(json.dumps(result, indent=2))
        return

    title = get_agency_config().get("title", "Agency")
    print(f"\n{bold(title)} — Fleet Status\n")

    for key in GROUPS:
        g = get_group(key)
        obs = list_observations(g)
        props = list_proposals(g)
        decs = list_decisions(g)
        agents, _ = collect_agents_with_identity(g)
        healthy = sum(1 for a in agents if a["health"] == "green")
        actionable = sum(1 for p in props if p.get("status") in ("proposed", "investigating"))

        status_dots = " ".join(f"{_health_dot(a['health'])} {a['name']}" for a in agents[:6])
        needs = f"{yellow(str(actionable))} needs decision" if actionable else green("clear")

        print(f"  {bold(g['name'])} ({key})")
        print(f"    {status_dots}")
        print(f"    {len(obs)} observations · {len(props)} proposals · {len(decs)} decisions · {needs}")
        print()

def cmd_observations(args):
    """List observations."""
    g = _resolve_group(args)
    items = list_observations(g)
    if args.agent:
        items = [i for i in items if i.get("agent") == args.agent]
    if args.status:
        items = [i for i in items if i.get("status") == args.status]

    if getattr(args, "json", False):
        print(json.dumps([{"slug": i["_slug"], "title": i.get("_title", i["_slug"]), "agent": i.get("agent", ""), "status": i.get("status", ""), "date": str(i.get("date", ""))} for i in items], indent=2))
        return

    print(f"\n{bold('Observations')} — {g['name']} ({len(items)} total)\n")
    for i in items:
        status = i.get("status", "")
        agent = i.get("agent", "")
        title_text = i.get("_title", i["_slug"])
        float_marker = f" {yellow('★')}" if i.get("float") else ""
        agent_str = agent[:12].rjust(12)
        print(f"  {dim(agent_str)}  {title_text[:60]}{float_marker}  {dim(status)}")
    print()

def cmd_proposals(args):
    """List proposals."""
    g = _resolve_group(args)
    items = list_proposals(g)
    if args.status:
        items = [i for i in items if i.get("status") == args.status]

    if getattr(args, "json", False):
        print(json.dumps([{"slug": i["_slug"], "title": i.get("_title", i["_slug"]), "status": i.get("status", ""), "agent": i.get("origin_agent", ""), "date": str(i.get("date", ""))} for i in items], indent=2))
        return

    print(f"\n{bold('Proposals')} — {g['name']} ({len(items)} total)\n")
    for i in items:
        status = i.get("status", "")
        agent = i.get("origin_agent", "")
        title_text = i.get("_title", i["_slug"])
        agent_str = agent[:12].rjust(12)
        print(f"  {dim(agent_str)}  {title_text[:60]}  {dim(status)}")
    print()

def cmd_decisions(args):
    """List decisions."""
    g = _resolve_group(args)
    items = list_decisions(g)

    if getattr(args, "json", False):
        print(json.dumps([{"slug": i["_slug"], "title": i.get("_title", i["_slug"]), "answers": i.get("answers", {}), "date": str(i.get("date", ""))} for i in items], indent=2))
        return

    print(f"\n{bold('Decisions')} — {g['name']} ({len(items)} total)\n")
    for i in items:
        title_text = i.get("_title", i["_slug"])
        answers = i.get("answers", {})
        answer_count = len(answers)
        print(f"  {green('decided')}  {title_text[:60]}  {dim(str(i.get('date', '')))}  {dim(f'{answer_count} answer(s)')}")
    print()

def cmd_agents(args):
    """List agents with health status."""
    g = _resolve_group(args)
    agents, subagents = collect_agents_with_identity(g)

    if getattr(args, "json", False):
        all_agents = agents + subagents
        print(json.dumps([{"name": a["name"], "health": a["health"], "integration": a["integration"], "open_observations": a["open_observations"]} for a in all_agents], indent=2))
        return

    print(f"\n{bold('Agents')} — {g['name']}\n")
    for a in agents:
        emoji = a.get("emoji", "")
        display = a.get("display_name", a["name"])
        dot = _health_dot(a["health"])
        obs = f"{a['open_observations']} open" if a["open_observations"] else ""
        print(f"  {dot} {emoji} {display} ({a['name']})  {dim(a['integration'])}  {dim(obs)}")
    if subagents:
        print(f"\n  {dim('Subagents:')}")
        for a in subagents:
            dot = _health_dot(a["health"])
            print(f"  {dot} {a.get('emoji', '')} {a['name']}  {dim(a['integration'])}")
    print()

def cmd_decide(args):
    """Interactively answer a proposal's questions."""
    import yaml

    g = _resolve_group(args)
    slug = args.slug
    proposals_dir = g["shared"] / "proposals"
    decisions_dir = g["shared"] / "decisions"

    path = proposals_dir / f"{slug}.md"
    if not path.exists():
        print(f"Error: Proposal '{slug}' not found.", file=sys.stderr)
        sys.exit(1)

    meta, body = parse_frontmatter(path.read_text())
    questions = meta.get("questions", [])
    if not questions:
        print("Error: Proposal has no questions.", file=sys.stderr)
        sys.exit(1)

    origin_agent = meta.get("origin_agent", "")
    print(f"\n{bold(slug)}\n")

    answers = {}
    for i, q in enumerate(questions, 1):
        print(f"  {cyan(str(i))}. {q['prompt']}")

        if q["type"] == "boolean":
            print(f"     {green('[a]')}pprove  {yellow('[d]')}efer  {red('[r]')}eject")
            while True:
                choice = input("     > ").strip().lower()
                if choice in ("a", "approve"):
                    answers[q["id"]] = "approved"
                    break
                elif choice in ("d", "defer"):
                    answers[q["id"]] = "deferred"
                    break
                elif choice in ("r", "reject"):
                    answers[q["id"]] = "rejected"
                    break
                print("     Invalid choice. Enter a/d/r.")

        elif q["type"] == "choice":
            options = q.get("options", [])
            for j, opt in enumerate(options, 1):
                print(f"     [{j}] {opt['label']}")
            if q.get("multi"):
                print("     (comma-separated numbers for multiple)")
                raw = input("     > ").strip()
                indices = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
                answers[q["id"]] = [options[idx - 1]["label"] for idx in indices if 1 <= idx <= len(options)]
            else:
                while True:
                    raw = input("     > ").strip()
                    if raw.isdigit() and 1 <= int(raw) <= len(options):
                        answers[q["id"]] = options[int(raw) - 1]["label"]
                        break
                    print(f"     Enter a number 1-{len(options)}.")

        elif q["type"] == "free-response":
            answers[q["id"]] = input("     > ").strip()

        print()

    # Create decision file
    today = datetime.now().strftime("%Y-%m-%d")
    dec_meta = {
        "proposal": f"{slug}.md",
        "decided_by": "cli",
        "date": today,
        "answers": answers,
        "execution_status": "pending",
    }
    frontmatter = yaml.dump(dec_meta, default_flow_style=False, sort_keys=False).strip()
    content = f"---\n{frontmatter}\n---\n"

    decisions_dir.mkdir(exist_ok=True)
    decision_path = decisions_dir / f"{slug}.md"
    decision_path.write_text(content)

    # Update proposal status
    update_frontmatter_field(path, "status", "decided")

    print(f"{green('✓')} Decision saved: shared/decisions/{slug}.md")
    for qid, ans in answers.items():
        if isinstance(ans, list):
            print(f"  {qid}: {', '.join(ans)}")
        else:
            print(f"  {qid}: {ans}")


# ── Dispatch Command ─────────────────────────────────────────────────────────

def _dispatch_config_path(args: Namespace) -> Path:
    selected = Path(args.config).expanduser() if args.config else CONFIG_PATH
    config_path = selected.resolve()
    if not config_path.is_file():
        raise ValueError(f"Agency config not found: {config_path}")
    return config_path


def _dispatch_interval(config: dict) -> int:
    return int(config.get("agency", {}).get("dispatch", {}).get("interval", 15))


def _dispatch_status_exit_code(status: dict) -> int:
    if status["error"]:
        return 4
    if not status["installed"]:
        return 1
    if status["state"] == "inactive":
        return 2
    if status["state"] == "misconfigured":
        return 3
    return 0


def _print_dispatch_status(status: dict) -> None:
    if status["error"]:
        print(f"Dispatcher inspection failed: {status['error']}", file=sys.stderr)
    elif not status["installed"]:
        print("Dispatcher absent")
    elif status["state"] == "misconfigured":
        print("Dispatcher misconfigured: " + ", ".join(status["mismatches"]))
    elif status["state"] == "inactive":
        print("Dispatcher inactive")
    else:
        print(f"Dispatcher active: heartbeat every {status['expected_interval']} minutes")


def cmd_dispatch(args: Namespace) -> int:
    try:
        config_path = _dispatch_config_path(args)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 4
    config = load_config_path(config_path)
    interval = args.interval if args.interval is not None else _dispatch_interval(config)
    if args.dispatch_command == "install":
        if args.interval is not None:
            dispatch_config = config.setdefault("agency", {}).setdefault("dispatch", {})
            dispatch_config.pop("installed", None)
            dispatch_config["interval"] = interval
            save_config_path(config_path, config)
        error = install_timer(str(config_path), interval, replace=args.replace)
        if error:
            print(f"Error: {error}", file=sys.stderr)
            return 4
        status = get_timer_status(str(config_path), interval)
        _print_dispatch_status(status)
        return _dispatch_status_exit_code(status)
    if args.dispatch_command == "status":
        status = get_timer_status(str(config_path), interval)
        _print_dispatch_status(status)
        return _dispatch_status_exit_code(status)
    error = uninstall_timer(str(config_path), force=args.force)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 4
    print("Dispatcher removed")
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────

def _job_records(g: dict):
    """Read all durable job records for a group, newest first. Returns list of
    (path, record-or-None) tuples; record is None when a file can't be parsed."""
    from agency.jobs.store import read_job

    jobs_dir = g["shared"] / "jobs"
    if not jobs_dir.is_dir():
        return []
    records = []
    for path in jobs_dir.glob("*.yaml"):
        try:
            records.append((path, read_job(path)))
        except Exception:
            records.append((path, None))
    def _key(item):
        _, rec = item
        if rec is None:
            return ""
        return rec.started_at or rec.spec.created_at or ""
    records.sort(key=_key, reverse=True)
    return records


def _job_status_color(status: str) -> str:
    if status == "complete":
        return green(status)
    if status == "failed":
        return red(status)
    if status == "running":
        return cyan(status)
    return yellow(status)


def cmd_jobs(args):
    """List durable agent jobs (read-only): status, agent, changed files."""
    g = _resolve_group(args)
    records = _job_records(g)
    if getattr(args, "status", None):
        records = [(p, r) for p, r in records if r and r.status == args.status]
    if getattr(args, "agent", None):
        records = [(p, r) for p, r in records if r and r.spec.agent_name == args.agent]

    if getattr(args, "json", False):
        out = []
        for _, r in records:
            if r is None:
                continue
            out.append({
                "job_id": r.spec.job_id,
                "agent": r.spec.agent_name,
                "trigger": r.spec.trigger,
                "status": r.status,
                "changed_files": len(r.changed_files or []),
                "exit_code": r.exit_code,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "log": r.stdout_path,
            })
        print(json.dumps(out, indent=2))
        return

    print(f"\n{bold('Jobs')} — {g['name']} ({len(records)} total)\n")
    if not records:
        print(f"  {dim('No jobs recorded yet.')}\n")
        return
    for _, r in records:
        if r is None:
            print(f"  {red('unreadable')}  {dim('(could not parse job file)')}")
            continue
        changed = r.changed_files or []
        changed_str = f"{len(changed)} file(s)" if changed else "no changes"
        when = _relative_time(r.started_at or r.spec.created_at)
        print(f"  {_job_status_color(r.status.ljust(8))}  {r.spec.agent_name[:16].ljust(16)}  "
              f"{dim(r.spec.trigger[:16].ljust(16))}  {dim(changed_str.ljust(12))}  {dim(when)}")
        print(f"    {dim(r.spec.job_id)}")
    print()


def cmd_logs(args):
    """Tail an execution log by job id, or list recent jobs with logs."""
    g = _resolve_group(args)
    records = _job_records(g)

    if not getattr(args, "job_id", None):
        with_logs = [(p, r) for p, r in records if r and r.stdout_path]
        print(f"\n{bold('Execution logs')} — {g['name']}\n")
        if not with_logs:
            print(f"  {dim('No execution logs yet.')}\n")
            return
        for _, r in with_logs[:20]:
            when = _relative_time(r.completed_at or r.started_at)
            print(f"  {_job_status_color(r.status.ljust(8))}  {dim(r.spec.job_id)}  "
                  f"{r.spec.agent_name[:16]}  {dim(when)}")
        print(f"\n  {dim('Run: agency logs <job_id> [--lines N] [--stderr]')}\n")
        return

    match = next((r for _, r in records if r and r.spec.job_id.startswith(args.job_id)), None)
    if match is None:
        print(f"Error: No job matching '{args.job_id}'.", file=sys.stderr)
        sys.exit(1)

    log_path = match.stderr_path if getattr(args, "stderr", False) else match.stdout_path
    if not log_path or not Path(log_path).is_file():
        print(f"Error: No {'stderr' if getattr(args, 'stderr', False) else 'stdout'} log for job {match.spec.job_id}.", file=sys.stderr)
        sys.exit(1)

    lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    n = getattr(args, "lines", 0) or 0
    shown = lines[-n:] if n > 0 else lines
    print(f"\n{bold(match.spec.job_id)} — {log_path}  ({_job_status_color(match.status)})\n")
    for line in shown:
        print(f"  {line}")
    if n > 0 and len(lines) > n:
        print(f"\n  {dim(f'... {len(lines) - n} earlier line(s) hidden (use --lines 0 for all)')}")
    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="christag-agency",
        description="Agency — AI Agent Management",
    )
    sub = parser.add_subparsers(dest="command")

    # serve
    p = sub.add_parser("serve", help="Start the web dashboard")
    p.add_argument("--port", type=int, default=8500)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--reload", action="store_true", help="Restart when project files change")

    # inbox
    p = sub.add_parser("inbox", help="What needs attention")
    p.add_argument("--group", "-g")
    p.add_argument("--json", action="store_true")

    # status
    p = sub.add_parser("status", help="Fleet overview across all groups")
    p.add_argument("--json", action="store_true")

    # observations
    p = sub.add_parser("observations", help="List observations")
    p.add_argument("--group", "-g")
    p.add_argument("--status", "-s")
    p.add_argument("--agent", "-a")
    p.add_argument("--json", action="store_true")

    # proposals
    p = sub.add_parser("proposals", help="List proposals")
    p.add_argument("--group", "-g")
    p.add_argument("--status", "-s")
    p.add_argument("--json", action="store_true")

    # decisions
    p = sub.add_parser("decisions", help="List decisions")
    p.add_argument("--group", "-g")
    p.add_argument("--json", action="store_true")

    # decide
    p = sub.add_parser("decide", help="Answer a proposal's questions")
    p.add_argument("slug", help="Proposal slug")
    p.add_argument("--group", "-g")

    # agents
    p = sub.add_parser("agents", help="List agents with health status")
    p.add_argument("--group", "-g")
    p.add_argument("--json", action="store_true")

    # dispatch
    dispatch_parser = sub.add_parser("dispatch", help="Manage the global dispatcher")
    dispatch_sub = dispatch_parser.add_subparsers(dest="dispatch_command", required=True)
    install_parser = dispatch_sub.add_parser("install", help="Install or repair the dispatcher")
    install_parser.add_argument("--config")
    install_parser.add_argument("--interval", type=int, choices=range(5, 121))
    install_parser.add_argument("--replace", action="store_true")
    install_parser.set_defaults(force=False)
    status_parser = dispatch_sub.add_parser("status", help="Inspect the dispatcher")
    status_parser.add_argument("--config")
    status_parser.set_defaults(interval=None, replace=False, force=False)
    uninstall_parser = dispatch_sub.add_parser("uninstall", help="Remove the dispatcher")
    uninstall_parser.add_argument("--config")
    uninstall_parser.add_argument("--force", action="store_true")
    uninstall_parser.set_defaults(interval=None, replace=False)

    # jobs
    p = sub.add_parser("jobs", help="List durable agent jobs (status, agent, changed files)")
    p.add_argument("--group", "-g")
    p.add_argument("--status", "-s", help="Filter by status (queued/running/complete/failed)")
    p.add_argument("--agent", "-a", help="Filter by agent name")
    p.add_argument("--json", action="store_true")

    # logs
    p = sub.add_parser("logs", help="Tail a job's execution log, or list recent logs")
    p.add_argument("job_id", nargs="?", help="Job id (or unique prefix) to tail")
    p.add_argument("--group", "-g")
    p.add_argument("--lines", "-n", type=int, default=40, help="Show last N lines (0 = all)")
    p.add_argument("--stderr", action="store_true", help="Show the stderr log instead of stdout")

    args = parser.parse_args()

    # No command → show help
    if not args.command:
        parser.print_help()
        return

    # Initialize config for all commands except serve and dispatch (which do their own init)
    if args.command not in ("serve", "dispatch"):
        if not CONFIG_PATH.exists():
            print("Error: No config.yaml found. Run 'agency serve' first.", file=sys.stderr)
            sys.exit(1)
        reload_groups()

    dispatch = {
        "serve": cmd_serve,
        "inbox": cmd_inbox,
        "status": cmd_status,
        "observations": cmd_observations,
        "proposals": cmd_proposals,
        "decisions": cmd_decisions,
        "decide": cmd_decide,
        "agents": cmd_agents,
        "dispatch": cmd_dispatch,
        "jobs": cmd_jobs,
        "logs": cmd_logs,
    }
    result = dispatch[args.command](args)
    if isinstance(result, int) and result:
        raise SystemExit(result)


if __name__ == "__main__":
    main()
