"""Flowgency Dashboard — multi-team agent management interface."""

import logging
import os
import re
import stat
import subprocess
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import markdown
import nh3
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.concurrency import run_in_threadpool

import uvicorn
from uvicorn.supervisors.watchfilesreload import WatchFilesReload

from flowgency.clock import now as clock_now, today as clock_today
from flowgency.configuration import (
    FlowgencySettingsPatch,
    ConfigConflictError,
    ConfigStore,
    PromptSelector,
    dismiss_tip,
    hide_all_tips,
    patch_flowgency_settings,
    resolve_team_paths,
)
from flowgency.configuration.models import MemorySelector
from flowgency.jobs.store import revision_bound_team_operation
from flowgency.integrations import get_integration, REGISTRY
from flowgency.dispatch.install import install_timer, get_timer_status as _get_timer_status
from flowgency.jobs import (
    JobRequest,
    JobSubmissionError,
    JobValidationError,
    active_jobs,
    latest_executed_job,
    submit_job_request,
)
from flowgency.jobs.queue import drain, queue_snapshot, QueueView
from flowgency.health import (
    describe_agent_health,
    elapsed_coarse,
    elapsed_precise,
    grace_window,
    next_occurrence,
    relative_future,
    routine_schedules,
    schedule_lateness,
)
from flowgency.prompts import resolve_catalog_prompt
from flowgency.tickets.models import UserTicketContext
from flowgency.tickets.views import build_board_view
import json as json_module
from flowgency.workspaces import REGISTRY as WORKSPACE_REGISTRY
from flowgency.web import FlowgencyServices, build_services, get_services
from flowgency.web.log_preview import read_log_preview
from flowgency.web.state import flowgency_settings, runtime_team
from flowgency.web.team_navigation import build_team_context
from flowgency.web.routes import (
    admin_teams_router,
    admin_library_router,
    admin_memory_router,
    agent_detail_router,
    agent_permissions_router,
    agent_routines_router,
    agents_router,
    jobs_router,
    tickets_router,
    workflow_library_router,
    workflow_settings_router,
    workflows_router,
)

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(os.environ.get("FLOWGENCY_CONFIG") or Path.cwd() / "config.yaml").expanduser().resolve()

def refresh_services() -> FlowgencyServices:
    services = build_services(CONFIG_PATH)
    app.state.services = services
    return services


def _services() -> FlowgencyServices:
    services = getattr(app.state, "services", None)
    if (
        services is None
        or not hasattr(services, "config_path")
        or services.config_path != CONFIG_PATH
    ):
        services = refresh_services()
    return services


def _load_snapshot():
    return ConfigStore(CONFIG_PATH).load()


def _has_config_file() -> bool:
    return ConfigStore(CONFIG_PATH).inspect().exists


def _config_error_message(error: Exception) -> str:
    return (
        f"Configuration is not available: {error}. "
        "Create a canonical config and reload."
    )


def _update_tip_settings(patcher) -> None:
    store = ConfigStore(CONFIG_PATH)
    for _ in range(2):
        snapshot = store.load()
        try:
            patcher(store, snapshot.revision)
            return
        except ConfigConflictError:
            continue
    raise ConfigConflictError("config.yaml changed; reload before saving")


def get_flowgency_config() -> dict:
    """Return flowgency-level config derived from the canonical config snapshot."""
    try:
        return flowgency_settings(_load_snapshot())
    except Exception as error:
        if not _has_config_file():
            return {
                "title": "Flowgency",
                "default_team": "",
                "decided_by": "admin",
                "ai_backend": "claude-code",
                "theme": "",
                "dispatch_interval": 15,
                "show_tips": True,
                "tips_dismissed": [],
            }
        raise HTTPException(
            status_code=409,
            detail=_config_error_message(error),
        )


# ── Themes ─────────────────────────────────────────────────────────────────

THEMES_DIR = Path(__file__).parent / "themes"


def load_themes() -> dict[str, dict]:
    """Read all theme YAML files from the themes directory."""
    themes = {}
    if not THEMES_DIR.is_dir():
        return themes
    for f in sorted(THEMES_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text()) or {}
            themes[f.stem] = data
        except (yaml.YAMLError, OSError):
            continue
    return themes


def generate_theme_css(theme: dict) -> str:
    """Generate CSS custom properties and structural overrides from a theme dict."""
    lines = ["<style>/* Theme: {} */".format(theme.get("name", "Custom"))]

    light = theme.get("light", {})
    dark = theme.get("dark", {})
    logo = theme.get("logo", {})
    scale = theme.get("scale", {})
    ui = theme.get("ui", {})

    props_light = []
    props_dark = []
    for key, val in light.items():
        props_light.append(f"  --t-{key.replace('_', '-')}: {val};")
    for key, val in dark.items():
        props_dark.append(f"  --t-{key.replace('_', '-')}: {val};")

    logo_light = logo.get("light", {})
    logo_dark = logo.get("dark", {})
    for key, val in logo_light.items():
        props_light.append(f"  --t-logo-{key.replace('_', '-')}: {val};")
    for key, val in logo_dark.items():
        props_dark.append(f"  --t-logo-{key.replace('_', '-')}: {val};")

    for key, val in scale.items():
        props_light.append(f"  --t-scale-{key}: {val};")
        props_dark.append(f"  --t-scale-{key}: {val};")
    for key, val in ui.items():
        prop = f"  --t-ui-{key.replace('_', '-')}: {val};"
        props_light.append(prop)
        props_dark.append(prop)

    lines.append(":root {")
    lines.extend(props_light)
    lines.append("}")
    lines.append(".dark {")
    lines.extend(props_dark)
    lines.append("}")
    lines.append("""
/* Body */
body {
  background-color: var(--t-bg) !important;
  color: var(--t-text) !important;
  font-family: var(--t-ui-font-family, \"DM Sans\", system-ui, sans-serif) !important;
}
@media (min-width: 768px) {
  main { font-size: var(--t-ui-main-font-size, 1.0625rem); }
}

/* Sidebar */
nav#sidebar { background-color: var(--t-sidebar-bg) !important; }
.nav-item {
  color: var(--t-sidebar-text) !important;
  border-radius: var(--t-ui-nav-radius, 0.5rem) !important;
  font-size: var(--t-ui-nav-font-size, 0.9375rem) !important;
}
.nav-item:hover { color: var(--t-sidebar-active-text) !important; background: var(--t-sidebar-hover-bg, rgba(255,255,255,0.06)) !important; }
.nav-item.active { color: var(--t-sidebar-active-text, #fff) !important; background: var(--t-sidebar-active-bg) !important; }
.nav-section { color: var(--t-sidebar-section) !important; }
.theme-toggle {
  color: var(--t-sidebar-text) !important;
  border-radius: var(--t-ui-nav-radius, 0.5rem) !important;
  font-size: var(--t-ui-nav-font-size, 0.9375rem) !important;
}
.theme-toggle:hover { color: var(--t-sidebar-active-text) !important; background: var(--t-sidebar-hover-bg, rgba(255,255,255,0.06)) !important; }

/* Logo */
nav#sidebar .logo-square { background-color: var(--t-logo-bg, var(--t-sidebar-bg)) !important; }
nav#sidebar .logo-square svg line { stroke: var(--t-logo-line, white) !important; }
nav#sidebar .logo-square svg circle { fill: var(--t-logo-node, white) !important; }

/* Mobile top bar */
.mobile-topbar { background-color: var(--t-sidebar-bg) !important; border-color: var(--t-border-subtle) !important; }

/* Cards and surfaces */
.bg-white, .dark .bg-white { background-color: var(--t-bg-card) !important; }
.bg-gray-50, .dark .bg-gray-50 { background-color: var(--t-bg) !important; }
.dark .bg-gray-100 { background-color: var(--t-bg-surface, var(--t-bg-card)) !important; }
.rounded-lg { border-radius: var(--t-ui-radius-lg, 0.5rem) !important; }
.rounded-xl { border-radius: var(--t-ui-radius-xl, 0.75rem) !important; }

/* Borders */
.border-gray-200, .dark .border-gray-200 { border-color: var(--t-border) !important; }
.border-gray-100, .dark .border-gray-100 { border-color: var(--t-border-subtle) !important; }

/* Text */
.text-gray-900, .dark .text-gray-900 { color: var(--t-text-heading) !important; }
.text-gray-800, .dark .text-gray-800 { color: var(--t-text-heading) !important; }
.text-gray-700, .dark .text-gray-700 { color: var(--t-text) !important; }
.text-gray-600, .dark .text-gray-600 { color: var(--t-text-muted) !important; }
.text-gray-500, .dark .text-gray-500 { color: var(--t-text-faint) !important; }
html.dark body.text-gray-900 { color: var(--t-text) !important; }

/* Primary action buttons */
.bg-indigo-600, .bg-purple-600 { background-color: var(--t-primary) !important; color: var(--t-primary-text) !important; }
.hover\\:bg-indigo-700:hover, .hover\\:bg-purple-700:hover { background-color: var(--t-primary-hover) !important; }
.text-indigo-600 { color: var(--t-primary) !important; }
.hover\\:text-indigo-800:hover { color: var(--t-primary-hover) !important; }
.focus\\:ring-indigo-500:focus { --tw-ring-color: var(--t-primary) !important; }
.focus\\:border-indigo-500:focus { border-color: var(--t-primary) !important; }

/* Form inputs */
.dark input, .dark textarea, .dark select {
  background-color: var(--t-input-bg, var(--t-code-bg)) !important;
  border-color: var(--t-input-border, var(--t-border)) !important;
  color: var(--t-text) !important;
}
.dark input.border-gray-300, .dark textarea.border-gray-300, .dark select.border-gray-300 {
  border-color: var(--t-input-border, var(--t-border)) !important;
}
.dark input::placeholder, .dark textarea::placeholder {
  color: var(--t-input-placeholder, var(--t-text-faint)) !important;
}
.dark input:focus, .dark textarea:focus, .dark select:focus {
  border-color: var(--t-primary) !important;
}

/* Code */
.prose code { background: var(--t-code-bg) !important; }
.prose pre { background: var(--t-code-bg) !important; }

/* Prose links */
.prose a { color: var(--t-link) !important; }
""")

    lines.append("</style>")
    return "\n".join(lines)


_THEME_CSS_CACHE: dict[str, str] = {}


def get_theme_css() -> str:
    """Return theme CSS for the currently selected theme, or empty string."""
    try:
        theme_key = get_flowgency_config().get("theme", "")
    except HTTPException:
        return ""
    if not theme_key:
        return ""
    if theme_key in _THEME_CSS_CACHE:
        return _THEME_CSS_CACHE[theme_key]
    themes = load_themes()
    if theme_key not in themes:
        return ""
    css = generate_theme_css(themes[theme_key])
    _THEME_CSS_CACHE[theme_key] = css
    return css


# ── Dispatch Helpers ──────────────────────────────────────────────────────────

def _workspace_types_json() -> str:
    """Serialize workspace registry for admin templates."""
    return json_module.dumps([
        {"name": ws.name, "display_name": ws.display_name, "description": ws.description}
        for ws in WORKSPACE_REGISTRY.values()
    ])


def get_dispatch_status() -> dict:
    """Return runtime scheduler status for the active singleton config."""
    interval = int(get_flowgency_config().get("dispatch_interval", 15))
    return _get_timer_status(CONFIG_PATH.resolve(), interval)


def install_dispatch(interval: int | None = None, replace: bool = False) -> str | None:
    """Install or repair the scheduler for the active singleton config."""
    desired_interval = interval if interval is not None else int(get_flowgency_config().get("dispatch_interval", 15))
    return install_timer(str(CONFIG_PATH.resolve()), desired_interval, replace=replace)


log = logging.getLogger("flowgency.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    services = refresh_services()
    if services.startup_error is None:
        snapshot = services.config_store.load()
        try:
            drain(
                snapshot.config,
                memory_store=snapshot.config.flowgency.memory_store,
                full_reconcile=True,
                config_path=services.config_store.path,
            )
        except Exception:
            log.exception("startup drain failed")
    yield


app = FastAPI(title="Flowgency Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.state.templates = templates
app.state.theme_css_getter = get_theme_css
app.state.workspace_types_json_getter = _workspace_types_json
app.state.build_services = build_services
app.state.refresh_services = refresh_services
app.state.get_config_path = lambda: CONFIG_PATH

md = markdown.Markdown(extensions=["tables", "fenced_code", "meta", "nl2br"])

STATIC_DIR = Path(__file__).parent / "static"


# ── PWA ──────────────────────────────────────────────────────────────────────


@app.get("/sw.js")
async def service_worker():
    """Serve service worker from root for full scope."""
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/manifest.json")
async def manifest():
    """Serve PWA manifest with dynamic app title."""
    cfg = get_flowgency_config()
    data = json_module.loads((STATIC_DIR / "manifest.json").read_text())
    data["name"] = cfg.get("title", "Flowgency")
    data["short_name"] = cfg.get("title", "Flowgency")
    return data


# ── Team Resolution ───────────────────────────────────────────────────────────


def get_team(team: str) -> dict:
    """Resolve a team key to its full config dict."""
    try:
        snapshot = _load_snapshot()
    except Exception as error:
        raise HTTPException(
            status_code=409,
            detail=_config_error_message(error),
        )
    if team not in snapshot.config.teams:
        raise HTTPException(404, f"Unknown team: {team}")
    return runtime_team(snapshot, team)


def get_agent_integration(g: dict, agent_name: str):
    """Resolve the integration explicitly pinned by a configured instance."""
    for agent_info in g.get("agents_full", []):
        if agent_info["name"] == agent_name:
            return get_integration(agent_info["integration"])
    raise KeyError(agent_name)


def safe_redirect(url: str, fallback: str = "/") -> str:
    """Validate a redirect URL is a safe relative path."""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return fallback


def team_context(g: dict) -> dict:
    """Return standard template context for a team."""
    snapshot = _load_snapshot()
    flowgency = flowgency_settings(snapshot)
    return build_team_context(
        snapshot,
        g["key"],
        theme_css=get_theme_css(),
        show_tips=flowgency.get("show_tips", True),
        tips_dismissed=flowgency.get("tips_dismissed", []),
        ticket_service=_services().tickets,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────



def render_md(text: str) -> Markup:
    """Render markdown to HTML."""
    md.reset()
    return Markup(md.convert(text))


_ticket_md = markdown.Markdown(extensions=["tables", "fenced_code", "nl2br"])
_TICKET_MD_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em",
    "del", "a", "table", "thead", "tbody", "tr", "th", "td",
}


def render_ticket_markdown(text: str | None) -> Markup:
    """Render sanitized Markdown for user-supplied ticket content."""
    _ticket_md.reset()
    converted = _ticket_md.convert(text or "")
    cleaned = nh3.clean(
        converted,
        tags=_TICKET_MD_TAGS,
        attributes={"a": {"href", "title"}, "code": {"class"}},
        clean_content_tags={"script", "style", "iframe", "object", "embed", "form"},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )
    return Markup(cleaned)


def validate_file_access(fpath: Path, base_path: Path, allowed_roots: list[Path] | None = None) -> None:
    """Validate file is within base_path or any allowed root. Raises HTTPException(403) if not."""
    resolved = fpath.resolve()
    # Check base path first
    try:
        resolved.relative_to(base_path.resolve())
        return
    except ValueError:
        pass
    # Check additional allowed roots
    if allowed_roots:
        for root in allowed_roots:
            try:
                resolved.relative_to(root.resolve())
                return
            except ValueError:
                continue
    raise HTTPException(403, "Access denied")


def build_ticket_dashboard(services: FlowgencyServices, team_id: str) -> dict[str, Any]:
    def _dashboard_time(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=None) if value.tzinfo is not None else value

    snapshot = services.config_store.load()
    team = snapshot.config.teams[team_id]
    configured_count = len(team.workflows)
    if services.tickets is None:
        return {
            "workflows": [],
            "activity": [],
            "unassigned": [],
            "issues": [],
            "issue_count": 0,
            "ticket_count": 0,
            "working_count": 0,
            "configured_count": configured_count,
        }
    actor = UserTicketContext(team_id=team_id)
    workflows: list[dict] = []
    activity: list[dict] = []
    unassigned: list[dict] = []
    issues: list[dict] = []
    issue_count = 0
    ticket_count = 0
    working_count = 0
    try:
        bindings = services.tickets.list_workflows(actor)
    except Exception:
        return {
            "workflows": [],
            "activity": [],
            "unassigned": [],
            "issues": [{"workflow_id": None, "workflow_name": None, "message": "Ticket workflows are unavailable"}] if configured_count else [],
            "issue_count": 1 if configured_count else 0,
            "ticket_count": 0,
            "working_count": 0,
            "configured_count": configured_count,
        }
    for binding in bindings:
        board = build_board_view(
            services.tickets,
            actor,
            binding.workflow_id,
            ticket_jobs=services.ticket_jobs,
        )
        rows = [ticket for column in board.columns for ticket in column.tickets]
        issue_count += len(board.issues)
        ticket_count += board.ticket_count
        working_count += board.working_count
        workflow_name = team.workflows[binding.workflow_id].name
        workflows.append(
            {
                "id": binding.workflow_id,
                "name": workflow_name,
                "href": f"/{team_id}/workflows/{binding.workflow_id}",
                "ticket_count": board.ticket_count,
                "working_count": board.working_count,
                "unassigned_count": sum(1 for ticket in rows if ticket.assignee is None),
                "issue_count": len(board.issues),
            }
        )
        for issue in board.issues:
            issues.append(
                {
                    "workflow_id": binding.workflow_id,
                    "workflow_name": workflow_name,
                    "message": issue.message,
                }
            )
        for ticket in rows:
            if ticket.assignee is None:
                unassigned.append(
                    {
                        "workflow_id": binding.workflow_id,
                        "workflow_name": workflow_name,
                        "ticket_id": ticket.ref.ticket_id,
                        "title": ticket.title,
                        "href": f"/{team_id}/workflows/{binding.workflow_id}?ticket={ticket.ref.ticket_id}",
                    }
                )
            latest = ticket.history[-1] if ticket.history else None
            if latest is not None:
                activity.append(
                    {
                        "workflow_id": binding.workflow_id,
                        "workflow_name": workflow_name,
                        "ticket_id": ticket.ref.ticket_id,
                        "title": ticket.title,
                        "kind": latest.kind,
                        "summary": latest.summary,
                        "at": _dashboard_time(latest.at),
                        "href": f"/{team_id}/workflows/{binding.workflow_id}?ticket={ticket.ref.ticket_id}",
                    }
                )
    activity.sort(
        key=lambda item: item["at"] or datetime.min,
        reverse=True,
    )
    workflows.sort(key=lambda item: item["name"])
    return {
        "workflows": workflows,
        "activity": activity[:8],
        "unassigned": unassigned[:8],
        "issues": issues,
        "issue_count": issue_count,
        "ticket_count": ticket_count,
        "working_count": working_count,
        "configured_count": configured_count,
    }


def _is_empty_error_log(path: Path, size: int | None = None) -> bool:
    return path.suffix.lower() == ".err" and (
        path.stat().st_size if size is None else size
    ) == 0


def collect_logs(g: dict) -> dict[str, list[dict]]:
    """Collect log files grouped by date."""
    logs_dir = Path(g["logs"])
    if not logs_dir.exists():
        return {}
    result = {}
    for date_dir in sorted(logs_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        entries = []
        for f in date_dir.iterdir():
            if f.name.startswith("."):
                continue
            file_stat = f.stat()
            size = file_stat.st_size
            if _is_empty_error_log(f, size):
                continue
            entries.append({
                "name": f.name,
                "path": str(f),
                "suffix": f.suffix,
                "size": size,
                "timestamp": datetime.fromtimestamp(file_stat.st_mtime),
            })
        if entries:
            entries.sort(
                key=lambda entry: (entry["timestamp"], entry["suffix"].lower() == ".out"),
                reverse=True,
            )
            result[date_dir.name] = entries
    return result


def status_badge(status: str) -> Markup:
    """Return a colored badge for observation/proposal status."""
    colors = {
        "open": "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
        "connected": "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200",
        "investigating": "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-200",
        "proposed": "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200",
        "decided": "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
        "dismissed": "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-200",
        "archived": "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200",
    }
    cls = colors.get(status or "", "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200")
    return Markup(f'<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium {cls}">{status or "unknown"}</span>')


def agent_badge(agent: str) -> Markup:
    """Return a colored badge for agent name."""
    palette = [
        "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
        "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-200",
        "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200",
        "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-200",
        "bg-lime-100 text-lime-800 dark:bg-lime-900/40 dark:text-lime-200",
        "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-200",
        "bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-900/40 dark:text-fuchsia-200",
        "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-200",
        "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-200",
        "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/40 dark:text-cyan-200",
        "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
        "bg-pink-100 text-pink-800 dark:bg-pink-900/40 dark:text-pink-200",
        "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
        "bg-slate-100 text-slate-800 dark:bg-slate-700 dark:text-slate-200",
    ]
    idx = hash(agent or "") % len(palette)
    cls = palette[idx]
    return Markup(f'<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium {cls}">{agent}</span>')


# Register template filters
templates.env.filters["status_badge"] = status_badge
templates.env.filters["agent_badge"] = agent_badge
templates.env.filters["render_md"] = render_md
templates.env.filters["ticket_markdown"] = render_ticket_markdown


# ── Agent Helpers ─────────────────────────────────────────────────────────────


def execution_agent_options(g: dict) -> list[str]:
    """List configured writable instances whose integration supports execution."""
    from flowgency.permissions.eligibility import may_write_workspace
    config = _load_snapshot().config
    options = []
    for name in g["agents"]:
        try:
            integration = get_agent_integration(g, name)
            if integration.supports_execution and may_write_workspace(config, g["key"], name):
                options.append(name)
        except KeyError:
            continue
    return options


def get_agent_last_run(g: dict, agent_name: str) -> dict | None:
    """Return the newest stdout log path and timestamp for an agent."""
    logs_dir = Path(g["logs"])
    if not logs_dir.exists():
        return None

    candidates = []
    for path in logs_dir.glob("*/*.out"):
        if not path.name.startswith(f"{agent_name}-"):
            continue
        try:
            path_stat = path.stat()
        except OSError:
            continue
        if stat.S_ISREG(path_stat.st_mode):
            candidates.append((path_stat.st_mtime, path))

    latest = max(candidates, key=lambda candidate: candidate[0], default=None)
    if latest is None:
        return None

    modified_at, latest_path = latest
    return {
        "at": datetime.fromtimestamp(modified_at),
        "path": str(latest_path.resolve()),
    }


def get_agent_last_seen(g: dict, agent_name: str) -> datetime | None:
    """Scan log date directories newest-first, return mtime of first matching file."""
    logs_dir = Path(g["logs"])
    if not logs_dir.exists():
        return None
    for date_dir in sorted(logs_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.iterdir(), reverse=True):
            if f.name.startswith(f"{agent_name}-") and f.suffix in (".out", ".err"):
                return datetime.fromtimestamp(f.stat().st_mtime)
    return None


def is_agent_running(g: dict, agent_name: str, timeout: int = 1800) -> bool:
    """Return whether persisted jobs show queued or running work for an agent.

    ``timeout`` is retained temporarily for call-site compatibility; durable
    job records are authoritative.
    """
    return bool(active_jobs(tuple(g.get("job_paths", ())), agent_name))


def _agent_routines(g: dict, agent_name: str):
    for instance in g.get("agents_full", []):
        if instance.get("name") == agent_name:
            return instance.get("routines") or ()
    return ()


def compute_next_run_detail(
    g: dict,
    agent_name: str,
    dispatch_cfg: dict,
) -> dict | None:
    """Return the soonest scheduled run with its originating rule identity."""
    if not dispatch_cfg.get("enabled", False):
        return None

    now = clock_now()
    logs_root = Path(g["logs"])
    candidates: list[dict] = []

    for rule_index, schedule in enumerate(
        routine_schedules(_agent_routines(g, agent_name))
    ):
        target = next_occurrence(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
        )
        if target is None:
            continue
        candidates.append({
            "when": target,
            "routine_id": schedule.routine_id,
            "rule_index": rule_index,
        })

    return min(candidates, key=lambda candidate: candidate["when"], default=None)


def compute_next_run(g: dict, agent_name: str, dispatch_cfg: dict) -> datetime | None:
    """Return the soonest upcoming dispatch datetime for an agent."""
    detail = compute_next_run_detail(g, agent_name, dispatch_cfg)
    return detail["when"] if detail else None


def relative_time(dt: datetime | None) -> str:
    """Format datetime as relative string."""
    if dt is None:
        return "No activity recorded"
    now = clock_now()
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "Just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days <= 30:
        return f"{days}d ago"
    return dt.strftime("%Y-%m-%d")


templates.env.filters["relative_time"] = relative_time


templates.env.filters["relative_future"] = relative_future


def queue_due_time(value: "datetime | str | None") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    local = value.astimezone()
    if local.date() == clock_today():
        return local.strftime("%H:%M")
    return local.strftime("%a %H:%M")


templates.env.filters["queue_due_time"] = queue_due_time


def initials(name: str) -> str:
    """Two-letter avatar for an agent with no configured emoji."""
    words = (name or "").split()
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


templates.env.filters["initials"] = initials
templates.env.filters["elapsed"] = elapsed_coarse


def integration_badge_filter(name: str) -> Markup:
    """Render a colored badge for an integration name."""
    colors = {
        "claude-code": "bg-orange-100 text-orange-800",
        "codex": "bg-green-100 text-green-800",
        "gemini": "bg-blue-100 text-blue-800",
        "aider": "bg-purple-100 text-purple-800",
        "goose": "bg-yellow-100 text-yellow-800",
        "copilot": "bg-slate-100 text-slate-800",
        "script": "bg-gray-100 text-gray-800",
        "sdk": "bg-indigo-100 text-indigo-800",
    }
    color = colors.get(name, "bg-gray-100 text-gray-800")
    try:
        display = get_integration(name).display_name
    except KeyError:
        display = name
    return Markup(f'<span class="inline-block whitespace-nowrap px-2 py-0.5 rounded-full text-xs font-medium {color}">{display}</span>')


templates.env.filters["integration_badge"] = integration_badge_filter

app.include_router(admin_teams_router)
app.include_router(admin_library_router)
app.include_router(admin_memory_router)
app.include_router(workflow_library_router)
app.include_router(workflow_settings_router)
app.include_router(agents_router)
app.include_router(agent_permissions_router)
app.include_router(agent_routines_router)
app.include_router(agent_detail_router)
app.include_router(jobs_router)
app.include_router(workflows_router)
app.include_router(tickets_router)


def _fault_line(status, now: datetime) -> str:
    """One terse line for the card, empty when there is nothing wrong."""
    if status.kind == "job_failed":
        return "last job failed"
    if status.kind not in ("overdue", "due"):
        return ""
    same_day = status.due_at.date() == now.date()
    stamp = status.due_at.strftime("%H:%M" if same_day else "%Y-%m-%d %H:%M")
    return f"{status.routine_id} due {stamp}"


def _health_sentence(status, job, now: datetime) -> str:
    """The full explanation, shared by the card tooltip and the queue item."""
    if status.kind == "job_failed":
        sentence = f"Job {job.spec.job_id[:8]}"
        sentence += f" exited {job.exit_code}" if job.exit_code is not None else " failed"
        if job.duration_seconds is not None:
            sentence += f" after {elapsed_precise(timedelta(seconds=job.duration_seconds))}"
        finished = _job_finished_at(job)
        if finished is not None:
            sentence += f", {relative_time(finished)}"
        return sentence + "."
    if status.kind == "overdue":
        return (
            f"Routine {status.routine_id} was due at "
            f"{status.due_at.strftime('%H:%M')} and has not run — "
            f"{elapsed_precise(status.late)} late."
        )
    if status.kind == "due":
        return (
            f"Routine {status.routine_id} came due "
            f"{elapsed_precise(status.late)} ago; the dispatcher has not "
            "picked it up yet."
        )
    if status.kind == "never_run":
        return "No run on record"
    return "Healthy"


def _job_finished_at(job) -> datetime | None:
    stamp = job.completed_at or job.started_at
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().replace(tzinfo=None)


def _apply_agent_status(g: dict, agent: dict, routines, dispatch_cfg: dict) -> None:
    """Attach run timing and the health reason to one fleet entry."""
    name = agent["name"]
    now = clock_now()
    last_run = get_agent_last_run(g, name)
    last_seen = last_run["at"] if last_run else get_agent_last_seen(g, name)
    detail = compute_next_run_detail(g, name, dispatch_cfg)

    dispatch_enabled = bool(g.get("dispatch", {}).get("enabled", False))
    schedules = routine_schedules(routines or ()) if dispatch_enabled else ()
    lateness = schedule_lateness(
        schedules,
        logs_root=Path(g["logs"]),
        agent_name=name,
        now=now,
        grace=grace_window(int(g.get("dispatch_interval", 15))),
    )
    executed = latest_executed_job(tuple(g.get("job_paths", ())), name)
    status = describe_agent_health(
        has_run=last_seen is not None or executed is not None,
        last_job_failed=executed is not None and executed.status == "failed",
        lateness=lateness,
        now=now,
    )

    agent.update(
        {
            "last_run": last_run,
            "last_seen": last_seen,
            "next_run": detail["when"] if detail else None,
            "next_run_detail": detail,
            "health": status.color,
            "health_kind": status.kind,
            "health_routine": status.routine_id,
            "health_due_at": status.due_at,
            "health_late": status.late,
            "health_job": executed,
            "health_fault": _fault_line(status, now),
            "health_sentence": _health_sentence(status, executed, now),
        }
    )


def collect_agents_with_identity(g: dict) -> tuple[list[dict], list[dict]]:
    """Build configured instance info. The retired subagent list is always empty."""
    dispatch_cfg = g.get("dispatch", {})
    run_timeout = g.get("runtime", {}).get("timeout", 1800)
    agents = []
    for instance in g.get("agents_full", []):
        agent_name = instance["name"]
        identity = instance.get("identity") or {}
        info = {
            "name": agent_name,
            "display_name": identity.get("display_name") or agent_name,
            "title": identity.get("title", ""),
            "emoji": identity.get("emoji", ""),
            "open_observations": 0,
            "is_subagent": False,
            "has_headshot": False,
            "integration": instance["integration"],
            "running": is_agent_running(g, agent_name, run_timeout),
        }
        _apply_agent_status(g, info, instance.get("routines"), dispatch_cfg)
        agents.append(info)

    return agents, []


def _job_state_label(status: str) -> str:
    return {
        "waiting_for_memory": "Waiting for memory",
        "queued": "Queued",
        "running": "Running",
        "complete": "Complete",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }.get(status, status.replace("_", " ").title())


def _dashboard_memory_label(selector: dict[str, object], channels) -> str:
    scope = str(selector.get("scope") or "agent")
    if scope == "channel":
        channel_key = str(selector.get("channel") or "")
        channel = channels.get(channel_key)
        display = getattr(channel, "display_name", None) or channel_key or "Channel"
        return f"Channel: {display}"
    return scope.replace("_", " ").title()


def _newest_active_job(team_jobs: tuple[Path, ...], agent_name: str):
    jobs = sorted(
        active_jobs(team_jobs, agent_name),
        key=lambda record: (
            record.started_at or "",
            record.spec.created_at,
            record.spec.job_id,
        ),
        reverse=True,
    )
    return jobs[0] if jobs else None


def _overlay_dashboard_job_state(agent: dict, current, team_key: str) -> None:
    agent_name = agent["name"]
    agent.update(
        {
            "running": current is not None and current.status in {"running", "waiting_for_memory"},
            "queued": current is not None and current.status == "queued",
            "job_status_key": current.status if current is not None else None,
            "job_status": _job_state_label(current.status) if current is not None else None,
            "job_href": f"/{team_key}/jobs/{current.spec.job_id}" if current is not None else "",
            "activity_href": f"/{team_key}/agents/{agent_name}/activity",
            "profile_href": f"/{team_key}/agents/{agent_name}/profile",
        }
    )


def build_dashboard_fleet(g: dict) -> list[dict]:
    try:
        snapshot = _load_snapshot()
    except Exception:
        return []

    services = getattr(app.state, "services", None)
    if services is None or getattr(services, "startup_error", None) is not None or services.instances is None:
        agents, _ = collect_agents_with_identity(g)
        for agent in agents:
            current = _newest_active_job(tuple(g.get("job_paths", ())), agent["name"])
            _overlay_dashboard_job_state(agent, current, g["key"])
        return agents

    if g["key"] not in snapshot.config.teams:
        return []
    team_cfg = snapshot.config.teams[g["key"]]
    fleet: list[dict] = []
    dispatch_cfg = g.get("dispatch", {})
    for instance in team_cfg.agents.values():
        current = _newest_active_job(tuple(g.get("job_paths", ())), instance.name)
        selector = (
            current.spec.memory.selector
            if current is not None
            else (instance.default_memory.model_dump(mode="json") if instance.default_memory is not None else {"scope": "agent"})
        )
        fleet.append(
            {
                "name": instance.name,
                "display_name": instance.identity.display_name or instance.name,
                "title": instance.identity.title,
                "emoji": instance.identity.emoji,
                "blueprint": instance.blueprint,
                "integration": instance.integration,
                "open_observations": 0,
                "memory_label": _dashboard_memory_label(selector, snapshot.config.memory.channels),
            }
        )
        _apply_agent_status(g, fleet[-1], instance.routines, dispatch_cfg)
        _overlay_dashboard_job_state(fleet[-1], current, g["key"])
    return fleet


_HEALTH_LABELS = {
    "job_failed": "last run failed",
    "overdue": "overdue",
    "due": "due",
}


def build_health_items(g: dict, agents: list[dict]) -> list[dict]:
    """Turn unhealthy fleet entries into Attention Queue rows."""
    key = g["key"]
    items = []
    for agent in agents:
        label = _HEALTH_LABELS.get(agent.get("health_kind"))
        if label is None or agent.get("running"):
            continue
        job = agent.get("health_job")
        items.append(
            {
                "name": agent["name"],
                "display_name": agent.get("display_name") or agent["name"],
                "kind": agent["health_kind"],
                "label": label,
                "sentence": agent.get("health_sentence", ""),
                "last_line": _last_run_line(job),
                "routines_href": f"/{key}/agents/{agent['name']}/routines",
                "job_href": f"/{key}/jobs/{job.spec.job_id}" if job is not None else "",
                "run_href": f"/{key}/agents/{agent['name']}",
            }
        )
    return items


def _last_run_line(job) -> str:
    if job is None:
        return ""
    finished = _job_finished_at(job)
    if finished is None:
        return ""
    outcome = "failed" if job.status == "failed" else "succeeded"
    line = f"last run {finished.strftime('%Y-%m-%d %H:%M')} · {outcome}"
    if job.duration_seconds is not None:
        line += f" in {elapsed_precise(timedelta(seconds=job.duration_seconds))}"
    return line


def get_agent_logs(g: dict, agent_name: str, limit: int = 20) -> list[dict]:
    """Get recent log files for an agent, newest first."""
    logs_dir = Path(g["logs"])
    if not logs_dir.exists():
        return []
    results = []
    for date_dir in sorted(logs_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.iterdir(), reverse=True):
            if f.name.startswith(f"{agent_name}-") and f.suffix in (".out", ".err"):
                size = f.stat().st_size
                if _is_empty_error_log(f, size):
                    continue
                results.append({"name": f.name, "path": str(f), "date": date_dir.name, "size": size, "suffix": f.suffix})
                if len(results) >= limit:
                    return results
    return results


def build_agent_timeline(g: dict, agent_name: str, agent_observations: list[dict] | None = None, limit: int = 30) -> list[dict]:
    """Build an interleaved timeline of logs and observations for an agent.
    Accepts precomputed agent_observations to avoid re-reading files."""
    events = []

    # Add logs
    logs_dir = Path(g["logs"])
    if logs_dir.exists():
        for date_dir in sorted(logs_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for f in sorted(date_dir.iterdir(), reverse=True):
                if f.name.startswith(f"{agent_name}-") and f.suffix in (".out", ".err"):
                    stat = f.stat()
                    if _is_empty_error_log(f, stat.st_size):
                        continue
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                    events.append({
                        "type": "log",
                        "timestamp": mtime,
                        "name": f.name,
                        "path": str(f),
                        "date": date_dir.name,
                        "size": stat.st_size,
                        "suffix": f.suffix,
                    })

    # Add observations from precomputed list
    for c in (agent_observations or []):
        obs_date = c.get("date")
        if isinstance(obs_date, str):
            try:
                obs_date = datetime.fromisoformat(obs_date).replace(tzinfo=None)
            except (ValueError, TypeError):
                obs_date = clock_now()
        elif isinstance(obs_date, datetime):
            obs_date = obs_date.replace(tzinfo=None)
        else:
            obs_date = clock_now()
        events.append({
            "type": "observation",
            "timestamp": obs_date,
            "slug": c.get("_slug", ""),
            "status": c.get("status", "open"),
            "body_preview": c.get("_body", "")[:120],
            "float": c.get("float", False),
        })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]


# ── Routes ───────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Redirect to the default team."""
    services = _services()
    if services.startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    snapshot = services.config_store.load()
    flowgency = get_flowgency_config()
    default = flowgency.get("default_team", "")
    if default and default in snapshot.config.teams:
        return RedirectResponse(f"/{default}/", status_code=303)
    first = next(iter(snapshot.config.teams), "")
    if first:
        return RedirectResponse(f"/{first}/", status_code=303)
    return RedirectResponse("/setup", status_code=303)


@app.get("/setup/complete/{team}", response_class=HTMLResponse)
async def setup_complete(request: Request, team: str):
    """Post-setup page — tells user to come back later."""
    team_display = team
    flowgency_title = "Flowgency"
    services = _services()
    if services.startup_error is None:
        snapshot = services.config_store.load()
        flowgency_title = flowgency_settings(snapshot).get("title", "Flowgency")
        if team in snapshot.config.teams:
            team_display = snapshot.config.teams[team].name
    return templates.TemplateResponse(request, "setup_complete.html", {
        "request": request,
        "flowgency_title": flowgency_title,
        "team": team,
        "team_name": team_display,
    })


# ── Tip Routes ────────────────────────────────────────────────────────────────


@app.post("/tips/dismiss", response_class=HTMLResponse)
async def tip_dismiss(request: Request):
    """Dismiss a specific tip card."""
    form = await request.form()
    tip_id = form.get("tip_id", "").strip()
    redirect = safe_redirect(form.get("redirect", "/"))

    if tip_id:
        _update_tip_settings(lambda store, revision: dismiss_tip(store, revision, tip_id))
        refresh_services()

    return RedirectResponse(redirect, status_code=303)


@app.post("/tips/hide-all", response_class=HTMLResponse)
async def tip_hide_all(request: Request):
    """Hide all tip cards globally."""
    form = await request.form()
    redirect = safe_redirect(form.get("redirect", "/"))

    _update_tip_settings(hide_all_tips)
    refresh_services()

    return RedirectResponse(redirect, status_code=303)


# ── Admin Routes ──────────────────────────────────────────────────────────────


def admin_context(admin_page: str = "settings", dispatch_error: str = "") -> dict:
    """Build common context for admin pages."""
    snapshot = _load_snapshot()
    flowgency = flowgency_settings(snapshot)
    team_summaries = []
    for key, tcfg in snapshot.config.teams.items():
        paths = resolve_team_paths(tcfg)
        dispatch_cfg = tcfg.dispatch
        team_summaries.append({
            "key": key,
            "name": tcfg.name,
            "workspace_path": str(tcfg.workspace_path),
            "team_path": str(tcfg.path),
            "agents": list(tcfg.agents.keys()),
            "agent_count": len(tcfg.agents),
            "initialized": all(path.is_dir() for path in paths.runtime_directories),
            "workspace_exists": paths.workspace_root.exists(),
            "dispatch_enabled": dispatch_cfg.enabled,
        })
    return {
        "flowgency_title": flowgency.get("title", "Flowgency"),
        "default_team": flowgency.get("default_team", ""),
        "team_summaries": team_summaries,
        "teams": {
            key: tcfg.name for key, tcfg in snapshot.config.teams.items()
        },
        "revision": snapshot.revision,
        "admin_active": True,
        "active": "admin",
        "admin_page": admin_page,
        "dispatch": get_dispatch_status(),
        "dispatch_error": dispatch_error,
        "theme_css": get_theme_css(),
        "workflow_library": flowgency.get("workflow_library", ""),
    }


@app.get("/admin/", response_class=HTMLResponse)
async def admin_settings_page(request: Request):
    """Admin app settings page."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "admin_settings.html", {
        "request": request,
        **admin_context("settings"),
        "integrations": {name: i.display_name for name, i in REGISTRY.items() if i.supports_ai_backend},
        "ai_backend": get_flowgency_config()["ai_backend"],
        "installed_count": len(REGISTRY),
        "themes": load_themes(),
        "current_theme": get_flowgency_config()["theme"],
    })


def _read_integration_config():
    """Read integration module list from config."""
    from flowgency.integrations import _read_config
    return _read_config()


@app.get("/admin/integrations", response_class=HTMLResponse)
async def admin_integrations_page(request: Request):
    """Admin integrations management page."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    from flowgency.integrations import scan_available

    config_modules = _read_integration_config()
    module_to_author = {}
    for mod in config_modules:
        parts = mod.split(".")
        if len(parts) == 2:
            module_to_author[parts[1]] = parts[0]

    installed = []
    for name, i in REGISTRY.items():
        module_name = name.replace("-", "_")
        author = module_to_author.get(module_name, "unknown")
        projector = i.projector
        capabilities = getattr(projector, "capabilities", None)
        routine_compatibility = "None"
        if capabilities is not None:
            if capabilities.discovers_skills and capabilities.activates_selected_skill:
                routine_compatibility = "Full"
            elif capabilities.discovers_skills or capabilities.activates_selected_skill:
                routine_compatibility = "Partial"
            else:
                routine_compatibility = "Instructions only"
        installed.append({
            "name": name,
            "display_name": i.display_name,
            "module_path": f"{author}.{module_name}",
            "supports_execution": i.supports_execution,
            "supports_ai_backend": i.supports_ai_backend,
            "author": author,
            "projector_version": getattr(projector, "version", "—") if projector is not None else "—",
            "instruction_target": (
                capabilities.instruction_target.as_posix()
                if capabilities is not None
                else "—"
            ),
            "skills_target": (
                capabilities.skills_target.as_posix()
                if capabilities is not None
                else "—"
            ),
            "discovers_skills": bool(getattr(capabilities, "discovers_skills", False)),
            "activates_selected_skill": bool(getattr(capabilities, "activates_selected_skill", False)),
            "routine_compatibility": routine_compatibility,
        })

    available = scan_available()

    return templates.TemplateResponse(request, "admin_integrations.html", {
        "request": request,
        **admin_context("integrations"),
        "installed": installed,
        "available": available,
        "restart_needed": request.query_params.get("restart") == "1",
    })


@app.post("/admin/integrations/register", response_class=HTMLResponse)
async def admin_integrations_register(request: Request):
    """Register an available integration."""
    from flowgency.integrations import register_integration
    form = await request.form()
    module_path = form.get("module_path", "")
    if module_path:
        register_integration(module_path)
    return RedirectResponse("/admin/integrations?restart=1", status_code=303)


@app.post("/admin/integrations/unregister", response_class=HTMLResponse)
async def admin_integrations_unregister(request: Request):
    """Unregister an installed integration."""
    from flowgency.integrations import unregister_integration
    form = await request.form()
    module_path = form.get("module_path", "")
    if module_path:
        unregister_integration(module_path)
    return RedirectResponse("/admin/integrations?restart=1", status_code=303)


@app.post("/admin/integrations/restart", response_class=HTMLResponse)
async def admin_integrations_restart(request: Request):
    """Restart the flowgency service to apply integration changes."""
    try:
        subprocess.Popen(["systemctl", "--user", "restart", "flowgency.service"])
    except Exception:
        pass
    return RedirectResponse("/admin/integrations", status_code=303)


@app.get("/admin/dispatch", response_class=HTMLResponse)
async def admin_dispatch_page(request: Request):
    """Admin dispatch configuration page."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "admin_dispatch.html", {
        "request": request,
        **admin_context("dispatch"),
    })


@app.get("/admin/teams", response_class=HTMLResponse)
async def admin_teams_page(request: Request):
    """Admin agent teams page."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "admin_teams.html", {
        "request": request,
        **admin_context("teams"),
    })


@app.post("/admin/settings", response_class=HTMLResponse)
async def admin_save_settings(request: Request):
    """Save flowgency-level settings."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    title = form.get("title", "Flowgency").strip()
    default_team = form.get("default_team", "").strip()
    snapshot = _load_snapshot()
    settings = flowgency_settings(snapshot)
    ai_backend = form.get("ai_backend", "claude-code")
    theme = form.get("theme", "").strip()
    _THEME_CSS_CACHE.clear()  # Invalidate cached CSS

    dispatch_interval = settings.get("dispatch_interval", 15)
    dispatch_interval_raw = form.get("dispatch_interval", "")
    if dispatch_interval_raw:
        try:
            candidate_interval = int(dispatch_interval_raw)
        except (ValueError, TypeError):
            candidate_interval = 0
        if 5 <= candidate_interval <= 120:
            dispatch_interval = candidate_interval
    workflow_library_raw = str(form.get("workflow_library", "")).strip()
    store = ConfigStore(snapshot.path)
    expected_revision = revision or snapshot.revision
    try:
        with revision_bound_team_operation(
            store, all_teams=True, expected_revision=expected_revision
        ):
            patch_flowgency_settings(
                store,
                expected_revision,
                FlowgencySettingsPatch(
                    title=title or "Flowgency",
                    default_team=default_team,
                    ai_backend=ai_backend,
                    theme=theme,
                    dispatch_interval=int(dispatch_interval),
                    agent_library=settings.get("agent_library", ""),
                    compilation_cache=settings.get("compilation_cache", ""),
                    memory_store=settings.get("memory_store", ""),
                    prompt_store=settings.get("prompt_store", ""),
                    workflow_library=workflow_library_raw or None,
                ),
            )
    except ConfigConflictError:
        return templates.TemplateResponse(
            request,
            "admin_settings.html",
            {
                "request": request,
                **admin_context("settings"),
                "integrations": {
                    name: integration.display_name
                    for name, integration in REGISTRY.items()
                    if integration.supports_ai_backend
                },
                "ai_backend": ai_backend,
                "installed_count": len(REGISTRY),
                "themes": load_themes(),
                "current_theme": theme,
            },
            status_code=409,
        )
    refresh_services()
    dispatch_error = ""
    if dispatch_interval_raw:
        runtime_status = _get_timer_status(CONFIG_PATH.resolve(), int(dispatch_interval))
        if runtime_status["error"]:
            dispatch_error = runtime_status["error"]
        elif runtime_status["installed"]:
            dispatch_error = install_timer(
                str(CONFIG_PATH.resolve()),
                int(dispatch_interval),
                replace=False,
            ) or ""
    if dispatch_error:
        return templates.TemplateResponse(
            request,
            "admin_dispatch.html",
            {"request": request, **admin_context("dispatch", dispatch_error=dispatch_error)},
            status_code=409,
        )
    # Redirect back to dispatch page if interval was changed, otherwise settings
    redirect = "/admin/dispatch" if dispatch_interval_raw else "/admin/"
    return RedirectResponse(redirect, status_code=303)


@app.post("/admin/dispatch/install", response_class=HTMLResponse)
async def admin_dispatch_install(request: Request):
    """Install or repair the global platform scheduler."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    form = await request.form()
    error = install_dispatch(replace=form.get("replace") == "true")
    if error:
        return templates.TemplateResponse(
            request,
            "admin_dispatch.html",
            {"request": request, **admin_context("dispatch", dispatch_error=error)},
            status_code=409,
        )
    return RedirectResponse("/admin/dispatch", status_code=303)


@app.get("/admin/teams/new", response_class=HTMLResponse)
async def admin_team_new(request: Request):
    """Create new team form."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    flowgency = get_flowgency_config()
    snapshot = _load_snapshot()
    return templates.TemplateResponse(request, "admin_team_edit.html", {
        "request": request,
        "flowgency_title": flowgency.get("title", "Flowgency"),
        "admin_active": True,
        "active": "admin",
        "admin_page": "teams",
        "theme_css": get_theme_css(),
        "teams": {
            key: tcfg.name for key, tcfg in snapshot.config.teams.items()
        },
        "mode": "create",
        "team_key": "",
        "team_name": "",
        "team_workspace_path": "",
        "team_path": "",
        "default_integration": "claude-code",
        "team_agents": "",
        "team_workspaces_json": json_module.dumps([]),
        "workspace_types_json": _workspace_types_json(),
        "agent_infos": [],
        "warning": "",
        "revision": snapshot.revision,
    })


@app.post("/{team}/agents/{agent}/run")
async def agent_run(
    request: Request,
    team: str,
    agent: str,
    services: FlowgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    try:
        team_config = snapshot.config.teams[team]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team}") from exc
    try:
        instance = team_config.agents[agent]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent}") from exc

    form = await request.form()
    routine_id = str(form.get("routine_id") or "").strip()
    routine = None
    if routine_id:
        if "/" in routine_id or ".." in routine_id:
            raise HTTPException(status_code=400, detail="Invalid routine")

        routine = next(
            (candidate for candidate in instance.routines if candidate.id == routine_id),
            None,
        )
        if routine is None:
            raise HTTPException(status_code=404, detail="Routine not found")
        if not routine.enabled:
            raise HTTPException(
                status_code=409,
                detail=f"Routine '{routine.id}' is disabled; enable it before running.",
            )

    mode = str(form.get("mode") or "").strip()
    prompt_scope = str(form.get("prompt_scope") or "").strip()
    prompt_name = str(form.get("prompt_name") or "").strip()
    invocation_input = str(form.get("invocation_input") or "").strip()
    one_off_task_input = str(form.get("task_input") or "")

    prompt = None
    task_input = ""
    if mode == "saved":
        if not prompt_scope or not prompt_name:
            raise HTTPException(status_code=400, detail="Saved runs require prompt_scope and prompt_name")
        try:
            prompt = PromptSelector(scope=prompt_scope, name=prompt_name)
            assert services.blueprint_library is not None
            assert services.prompt_store is not None
            resolve_catalog_prompt(
                snapshot,
                services.blueprint_library,
                services.prompt_store,
                team,
                agent,
                scope=prompt.scope,
                name=prompt.name,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Prompt not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif mode == "one-off":
        if not one_off_task_input.strip():
            raise HTTPException(status_code=400, detail="One-off runs require task_input")
        if routine is not None:
            raise HTTPException(status_code=400, detail="One-off runs cannot target a saved routine")
        task_input = one_off_task_input
    else:
        raise HTTPException(status_code=400, detail="Run mode must be exactly one of saved or one-off")

    memory_scope = str(form.get("memory_scope") or "").strip()
    memory_channel = str(form.get("memory_channel") or "").strip()
    memory_override = None
    if memory_scope:
        if memory_scope == "channel":
            if not memory_channel:
                raise HTTPException(status_code=400, detail="Channel memory override requires a channel")
            if memory_channel not in snapshot.config.memory.channels:
                raise HTTPException(status_code=400, detail="Unknown memory channel")
            memory_override = MemorySelector(
                scope="channel",
                channel=memory_channel,
            )
        elif memory_scope not in {"run", "routine", "agent", "team"}:
            raise HTTPException(status_code=400, detail="Invalid memory override")
        else:
            if memory_channel:
                raise HTTPException(status_code=400, detail="memory_channel is only valid for channel memory")
            if memory_scope == "routine" and routine is None:
                raise HTTPException(status_code=400, detail="Routine memory override requires a selected routine")
            memory_override = MemorySelector(scope=memory_scope)
    elif memory_channel:
        raise HTTPException(status_code=400, detail="memory_channel is only valid for channel memory")

    try:
        request_obj = JobRequest(
            config_path=services.config_path,
            team_key=team,
            agent_name=agent,
            trigger="manual_prompt",
            task_input=task_input,
            prompt=prompt,
            invocation_input=invocation_input,
            routine_id=routine_id or None,
            memory_override=memory_override,
        )
        handle = submit_job_request(request_obj)
    except (TypeError, ValueError, JobValidationError, JobSubmissionError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({"status": "started", "job_id": handle.job_id}, status_code=202)


@app.get("/{team}/", response_class=HTMLResponse)
async def home(request: Request, team: str):
    """Dashboard home — mission control."""
    g = get_team(team)
    services = get_services(request)
    workflow_dashboard = build_ticket_dashboard(services, team)

    # Zone 1: Fleet status
    agents = build_dashboard_fleet(g)
    health_items = build_health_items(g, agents)
    needs_action_count = (
        len(health_items)
        + len(workflow_dashboard["unassigned"])
        + len(workflow_dashboard["issues"])
    )

    # Work queue strip
    try:
        snapshot = _load_snapshot()
        ms = snapshot.config.flowgency.memory_store
        if ms is not None:
            view = queue_snapshot(snapshot.config, memory_store=ms)
        else:
            view = QueueView(running=0, waiting=(), pool=snapshot.config.flowgency.jobs.pool)
    except Exception:
        view = QueueView(running=0, waiting=(), pool=4)
    work_queue = {
        "running": view.running,
        "pool": view.pool,
        "waiting": [
            {
                "position": i + 1,
                "agent": e.record.spec.agent_name,
                "routine": e.record.spec.routine_id or "task",
                "due": e.record.due_at or e.record.spec.created_at,
                "href": f"/{e.team_id}/jobs/{e.record.spec.job_id}",
            }
            for i, e in enumerate(view.waiting)
        ],
    }

    # Zone 4: Activity feed
    activity = workflow_dashboard["activity"]

    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        **team_context(g),
        # Zone 1: Fleet
        "fleet_agents": agents,
        "fleet_healthy": sum(1 for a in agents if a["health"] == "green"),
        "fleet_never_run": sum(1 for a in agents if a["health"] == "gray"),
        "fleet_attention": len(health_items),
        "fleet_running": sum(1 for a in agents if a.get("running")),
        "workflow_dashboard": workflow_dashboard,
        # Work queue
        "work_queue": work_queue,
        # Zone 3: Attention queue
        "health_items": health_items,
        "needs_action_count": needs_action_count,
        # Zone 4: Activity
        "activity_feed": activity,
    })


@app.get("/{team}/observations", response_class=HTMLResponse)
async def observations_list(request: Request, team: str, agent: str = "", status: str = ""):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.get("/{team}/observations/{slug}", response_class=HTMLResponse)
async def observation_detail(request: Request, team: str, slug: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.post("/{team}/observations/{slug}/status", response_class=HTMLResponse)
async def observation_update_status(request: Request, team: str, slug: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.get("/{team}/proposals", response_class=HTMLResponse)
async def proposals_list(request: Request, team: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.get("/{team}/proposals/{slug}", response_class=HTMLResponse)
async def proposal_detail(request: Request, team: str, slug: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.post("/{team}/proposals/{slug}/decide", response_class=HTMLResponse)
async def proposal_decide(request: Request, team: str, slug: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.get("/{team}/decisions", response_class=HTMLResponse)
async def decisions_list(request: Request, team: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.get("/{team}/decisions/{slug}", response_class=HTMLResponse)
async def decision_detail(request: Request, team: str, slug: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.post("/{team}/decisions/{slug}/retry", response_class=HTMLResponse)
async def decision_retry(request: Request, team: str, slug: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.post("/{team}/decisions/{slug}/verify", response_class=HTMLResponse)
async def decision_verify(request: Request, team: str, slug: str):
    raise HTTPException(status_code=410, detail="Retired pipeline routes are unavailable")


@app.get("/{team}/logs", response_class=HTMLResponse)
async def logs_list(request: Request, team: str):
    """Browse execution logs by date."""
    g = get_team(team)
    logs = collect_logs(g)
    return templates.TemplateResponse(request, "logs.html", {
        "request": request,
        **team_context(g),
        "logs": logs,
    })


def _log_view_context(team: str, path: str) -> dict:
    group = get_team(team)
    file_path = Path(path)
    logs_dir = Path(group["logs"]).resolve()
    validate_file_access(file_path, logs_dir)
    try:
        preview = read_log_preview(file_path)
    except FileNotFoundError:
        raise HTTPException(404, "Log not found")
    return {
        **team_context(group),
        "filename": file_path.name,
        "content_html": preview.content_html,
        "raw": preview.text,
        "truncated": preview.truncated,
    }


@app.get("/{team}/logs/view", response_class=HTMLResponse)
async def log_view(request: Request, team: str, path: str):
    context = await run_in_threadpool(_log_view_context, team, path)
    return templates.TemplateResponse(request, "log_view.html", {
        "request": request,
        **context,
    })


@app.get("/{team}/workspaces", response_class=HTMLResponse)
async def workspaces_list(request: Request, team: str):
    """List all workspaces for a team."""
    g = get_team(team)
    workspace_list = g.get("workspaces", [])
    from flowgency.workspaces import REGISTRY
    enriched = []
    for ws in workspace_list:
        plugin = REGISTRY.get(ws.get("type", "custom"))
        enriched.append({
            **ws,
            "plugin": plugin,
            "summary": plugin.render_summary(ws.get("config", {})) if plugin else "",
            "config_files": plugin.get_config_files(ws.get("config", {})) if plugin else [],
            "can_launch": plugin.supports_launch() if plugin else False,
        })
    return templates.TemplateResponse(request, "workspaces.html", {
        "request": request,
        **team_context(g),
        "enriched_workspaces": enriched,
        "active": "workspaces",
    })


@app.get("/{team}/workspaces/{idx}/file", response_class=HTMLResponse)
async def workspace_file_view(request: Request, team: str, idx: int):
    """View/edit a config file within a workspace."""
    g = get_team(team)
    workspace_list = g.get("workspaces", [])
    if idx < 0 or idx >= len(workspace_list):
        raise HTTPException(404, "Workspace not found")
    ws = workspace_list[idx]
    from flowgency.workspaces import REGISTRY
    plugin = REGISTRY.get(ws.get("type", "custom"))
    config_files = plugin.get_config_files(ws.get("config", {})) if plugin else []
    file_path = request.query_params.get("path", "")
    if not file_path and config_files:
        file_path = config_files[0]["path"]
    # Validate file is in the plugin's allowlist
    allowed_paths = [cf["path"] for cf in config_files]
    if file_path and file_path not in allowed_paths:
        raise HTTPException(403, "File not in workspace config files")
    raw = ""
    language = "text"
    if file_path:
        fpath = Path(file_path)
        if fpath.exists():
            raw = fpath.read_text()
        for cf in config_files:
            if cf["path"] == file_path:
                language = cf.get("language", "text")
                break
    return templates.TemplateResponse(request, "workspace_detail.html", {
        "request": request,
        **team_context(g),
        "ws": ws,
        "ws_idx": idx,
        "plugin": plugin,
        "config_files": config_files,
        "current_file": file_path,
        "raw": raw,
        "language": language,
        "active": "workspaces",
    })


@app.post("/{team}/workspaces/{idx}/file/save", response_class=HTMLResponse)
async def workspace_file_save(request: Request, team: str, idx: int):
    """Save edits to a workspace config file."""
    g = get_team(team)
    workspace_list = g.get("workspaces", [])
    if idx < 0 or idx >= len(workspace_list):
        raise HTTPException(404, "Workspace not found")
    form = await request.form()
    file_path = form.get("file_path", "")
    content = form.get("content", "")
    if file_path:
        ws = workspace_list[idx]
        from flowgency.workspaces import REGISTRY
        plugin = REGISTRY.get(ws.get("type", "custom"))
        allowed = [cf["path"] for cf in plugin.get_config_files(ws.get("config", {}))] if plugin else []
        if file_path not in allowed:
            raise HTTPException(403, "File not in workspace config files")
        Path(file_path).write_text(content)
    return RedirectResponse(f"/{team}/workspaces/{idx}/file?path={urllib.parse.quote(file_path, safe='')}", status_code=303)


RELOAD_INCLUDES = (
    "*.py",
    "*.html",
    "*.css",
    "*.js",
    "*.json",
    "*.yaml",
    "*.yml",
)

RELOAD_EXCLUDE_DIRS = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)


class _FlowgencyReloadFilter:
    """Select watched source files without depending on directory existence."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def __call__(self, path: Path) -> bool:
        try:
            relative_path = path.resolve().relative_to(self.root)
        except ValueError:
            return False

        directory_parts = relative_path.parts[:-1]
        if any(
            part in RELOAD_EXCLUDE_DIRS or part.endswith(".egg-info")
            for part in directory_parts
        ):
            return False
        return any(relative_path.match(pattern) for pattern in RELOAD_INCLUDES)


def _create_reload_supervisor(config, server, sockets):
    """Create Uvicorn's WatchFiles supervisor with Flowgency's path filter."""
    supervisor = WatchFilesReload(config, target=server.run, sockets=sockets)
    supervisor.watch_filter = _FlowgencyReloadFilter(config.reload_dirs[0])
    return supervisor


def _run_reload_server(host: str, port: int) -> None:
    """Run Uvicorn's reload lifecycle with Flowgency's WatchFiles filter."""
    reload_root = Path.cwd().resolve()
    config = uvicorn.Config(
        "flowgency.app:app",
        host=host,
        port=port,
        reload=True,
        reload_dirs=[str(reload_root)],
        reload_includes=list(RELOAD_INCLUDES),
    )
    config.load_app()
    server = uvicorn.Server(config=config)

    try:
        socket = config.bind_socket()
        _create_reload_supervisor(config, server, [socket]).run()
    except KeyboardInterrupt:
        pass


def run_server(host: str, port: int, reload: bool = False, log_level: str | None = None) -> None:
    """Initialize Flowgency and run the web server."""
    if not CONFIG_PATH.exists():
        print(
            f"First run: open http://localhost:{port}/setup to launch guided Flowgency setup."
        )

    refresh_services()
    if reload:
        _run_reload_server(host, port)
        return

    kwargs = {} if log_level is None else {"log_level": log_level}
    uvicorn.run(app, host=host, port=port, **kwargs)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Flowgency — Agent Management Dashboard")
    parser.add_argument("--port", type=int, default=8500, help="Port to serve on (default: 8500)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--reload", action="store_true", help="Restart when project files change")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
