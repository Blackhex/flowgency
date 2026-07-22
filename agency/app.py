"""Agency Dashboard — multi-group agent management interface."""

import os
import re
import stat
import subprocess
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import markdown
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

import uvicorn
from uvicorn.supervisors.watchfilesreload import WatchFilesReload

from agency.clock import now as clock_now, today as clock_today
from agency.configuration import (
    AgencySettingsPatch,
    ConfigConflictError,
    ConfigStore,
    dismiss_tip,
    hide_all_tips,
    patch_agency_settings,
)
from agency.integrations import get_integration, REGISTRY
from agency.dispatch.install import install_timer, get_timer_status as _get_timer_status
from agency.jobs import (
    JobRequest,
    JobSubmissionError,
    JobValidationError,
    active_jobs,
    reconcile_jobs,
    submit_job_request,
)
from agency.jobs.atomic import atomic_write_text
from agency.jobs.prompts import build_decision_prompt, build_routine_task_input
from agency.proposals import validate_proposal_schema, validate_answers, should_execute_decision, SKIP_EXECUTION_SUMMARY
import json as json_module
from agency.workspaces import REGISTRY as WORKSPACE_REGISTRY
from agency.web import AgencyServices, build_services, get_services
from agency.web.state import agency_settings, runtime_group
from agency.web.routes import (
    admin_groups_router,
    admin_library_router,
    admin_memory_router,
    agent_detail_router,
    agents_router,
    jobs_router,
)

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(os.environ.get("AGENCY_CONFIG") or Path.cwd() / "config.yaml").expanduser().resolve()


def _parse_sandbox_roots(text: str):
    """Parse a sandbox_root textarea (one path per line) into config form.

    Returns None when empty, a single string for one path (back-compat), or a
    list of strings for multiple paths.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    return lines


def _sandbox_root_text(val) -> str:
    """Render a config sandbox_root value (str or list) as textarea text."""
    if not val:
        return ""
    if isinstance(val, list):
        return "\n".join(str(v) for v in val)
    return str(val)


def refresh_services() -> AgencyServices:
    services = build_services(CONFIG_PATH)
    app.state.services = services
    return services


def _services() -> AgencyServices:
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


def get_agency_config() -> dict:
    """Return agency-level config derived from the canonical config snapshot."""
    try:
        return agency_settings(_load_snapshot())
    except Exception as error:
        if not _has_config_file():
            return {
                "title": "Agency",
                "default_group": "",
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

    # CSS custom properties
    props_light = []
    props_dark = []
    for key, val in light.items():
        props_light.append(f"  --t-{key.replace('_', '-')}: {val};")
    for key, val in dark.items():
        props_dark.append(f"  --t-{key.replace('_', '-')}: {val};")

    # Logo properties
    logo_light = logo.get("light", {})
    logo_dark = logo.get("dark", {})
    for key, val in logo_light.items():
        props_light.append(f"  --t-logo-{key.replace('_', '-')}: {val};")
    for key, val in logo_dark.items():
        props_dark.append(f"  --t-logo-{key.replace('_', '-')}: {val};")

    # Scale properties
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

    # Structural overrides using the custom properties
    lines.append("""
/* Body */
body {
  background-color: var(--t-bg) !important;
  color: var(--t-text) !important;
  font-family: var(--t-ui-font-family, "DM Sans", system-ui, sans-serif) !important;
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
        theme_key = get_agency_config().get("theme", "")
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
    interval = int(get_agency_config().get("dispatch_interval", 15))
    return _get_timer_status(CONFIG_PATH.resolve(), interval)


def install_dispatch(interval: int | None = None, replace: bool = False) -> str | None:
    """Install or repair the scheduler for the active singleton config."""
    desired_interval = interval if interval is not None else int(get_agency_config().get("dispatch_interval", 15))
    return install_timer(str(CONFIG_PATH.resolve()), desired_interval, replace=replace)


@asynccontextmanager
async def lifespan(app: FastAPI):
    services = refresh_services()
    if services.startup_error is None:
        snapshot = services.config_store.load()
        reconcile_jobs(
            {
                group_id: {"path": str(group.path)}
                for group_id, group in snapshot.config.groups.items()
            },
            memory_store_root=snapshot.config.agency.memory_store,
        )
    yield


app = FastAPI(title="Agency Dashboard", lifespan=lifespan)
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
    cfg = get_agency_config()
    data = json_module.loads((STATIC_DIR / "manifest.json").read_text())
    data["name"] = cfg.get("title", "Agency")
    data["short_name"] = cfg.get("title", "Agency")
    return data


# ── Group Resolution ──────────────────────────────────────────────────────────


def get_group(group: str) -> dict:
    """Resolve a group key to its full config dict."""
    try:
        snapshot = _load_snapshot()
    except Exception as error:
        raise HTTPException(
            status_code=409,
            detail=_config_error_message(error),
        )
    if group not in snapshot.config.groups:
        raise HTTPException(404, f"Unknown group: {group}")
    return runtime_group(snapshot, group)


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


def group_context(g: dict, observations: list[dict] | None = None, proposals: list[dict] | None = None) -> dict:
    """Return standard template context for a group. Accepts precomputed lists to avoid double-reads."""
    snapshot = _load_snapshot()
    agency = agency_settings(snapshot)
    group_cfg = snapshot.config.groups[g["key"]]
    if observations is None:
        observations = list_observations(g)
    if proposals is None:
        proposals = list_proposals(g)
    open_observation_count = sum(1 for c in observations if c.get("status") == "open")
    actionable_proposal_count = sum(1 for c in proposals if c.get("status") in ("proposed", "investigating"))
    floated_observation_count = sum(1 for c in observations if c.get("float") and c.get("status") == "open")
    needs_action_count = actionable_proposal_count + floated_observation_count
    decisions = list_decisions(g)
    running_decisions = sum(1 for d in decisions if d.get("execution_status") == "running")
    return {
        "group": g["key"],
        "group_name": g["name"],
        "groups": {
            key: value.name for key, value in snapshot.config.groups.items()
        },
        "agency_title": agency.get("title", "Agency"),
        "admin_active": False,
        "workspaces": [
            workspace.model_dump(mode="json")
            for workspace in group_cfg.workspaces
        ],
        "workspaces_available": bool(group_cfg.workspaces),
        "nav_open_observations": open_observation_count,
        "nav_actionable": needs_action_count,
        "nav_actionable_proposals": actionable_proposal_count,
        "nav_agent_count": len(g["agents"]),
        "nav_running_decisions": running_decisions,
        "show_tips": agency.get("show_tips", True),
        "tips_dismissed": agency.get("tips_dismissed", []),
        "theme_css": get_theme_css(),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text. Returns (meta, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                return meta, parts[2].strip()
            except yaml.YAMLError:
                pass
    return {}, text


def render_md(text: str) -> Markup:
    """Render markdown to HTML."""
    md.reset()
    return Markup(md.convert(text))


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


def update_frontmatter_field(filepath: Path, field: str, value: str) -> None:
    """Update a single YAML frontmatter field in a markdown file."""
    raw = filepath.read_text()
    raw = re.sub(rf'^({field}:\s*).*$', f'\\1{value}', raw, count=1, flags=re.MULTILINE)
    filepath.write_text(raw)


def update_decision_execution(decision_path: Path, field: str, value) -> None:
    """Update execution_status (or other top-level field) in a decision file."""
    raw = decision_path.read_text()
    meta, body = parse_frontmatter(raw)
    meta[field] = value
    frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    atomic_write_text(decision_path, f"---\n{frontmatter}\n---\n\n{body}\n")


def check_ttl_expired(meta: dict) -> bool:
    """Check if an item has exceeded its TTL based on date + ttl_days."""
    ttl = meta.get("ttl_days")
    if not ttl:
        return False
    item_date = meta.get("date")
    if not item_date:
        return False
    if isinstance(item_date, str):
        try:
            item_date = datetime.fromisoformat(item_date)
        except (ValueError, TypeError):
            return False
    elif not isinstance(item_date, datetime):
        try:
            # Handle date objects (not datetime)
            item_date = datetime.combine(item_date, datetime.min.time())
        except (TypeError, AttributeError):
            return False
    try:
        ttl = int(ttl)
    except (ValueError, TypeError):
        return False
    return clock_now(tz=item_date.tzinfo) > item_date + timedelta(days=ttl)


def enforce_ttl(filepath: Path, meta: dict) -> bool:
    """Auto-archive an item if its TTL has expired. Returns True if archived."""
    status = meta.get("status", "")
    if status in ("archived", "dismissed", "decided"):
        return False
    if check_ttl_expired(meta):
        update_frontmatter_field(filepath, "status", "archived")
        meta["status"] = "archived"
        return True
    return False


def extract_display_title(body: str | None, slug: str) -> str:
    """Extract a human-readable title from markdown body text.

    Looks for the first **bold text** in the body (not inside headings).
    Falls back to slug with hyphens replaced by spaces.
    Truncates to 120 chars if needed.
    """
    if not body:
        return slug.replace("-", " ")

    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.search(r"\*\*(.+?)\*\*", stripped)
        if m:
            title = m.group(1).rstrip(".,;:!?")
            if len(title) > 120:
                return title[:117] + "..."
            return title

    return slug.replace("-", " ")


def list_markdown_items(g: dict, subdir: str, apply_ttl: bool = False) -> list[dict]:
    """List markdown files from a shared subdirectory with parsed frontmatter."""
    item_dir = g["shared"] / subdir
    if not item_dir.exists():
        return []
    items = []
    for f in sorted(item_dir.glob("*.md"), reverse=True):
        raw = f.read_text()
        meta, body = parse_frontmatter(raw)
        meta.update({
            "_filename": f.name,
            "_body": body,
            "_slug": f.stem,
            "_title": extract_display_title(body, f.stem),
        })
        if apply_ttl:
            enforce_ttl(f, meta)
        items.append(meta)
    return items


def list_observations(g: dict) -> list[dict]:
    return list_markdown_items(g, "observations", apply_ttl=True)


def list_proposals(g: dict) -> list[dict]:
    return list_markdown_items(g, "proposals", apply_ttl=True)


def list_decisions(g: dict) -> list[dict]:
    return list_markdown_items(g, "decisions")


def build_pipeline_stats(observations: list[dict], proposals: list[dict],
                         decisions: list[dict]) -> dict:
    """Compute pipeline stage counts and 7-day sparkline data for dashboard."""
    today = clock_today()

    def sparkline_buckets(items: list[dict]) -> list[int]:
        buckets = [0] * 7
        for item in items:
            date_val = item.get("date", "")
            if isinstance(date_val, str):
                try:
                    date_val = datetime.fromisoformat(date_val).date()
                except (ValueError, TypeError):
                    continue
            elif isinstance(date_val, datetime):
                date_val = date_val.date()
            elif hasattr(date_val, "year"):
                pass
            else:
                continue
            days_ago = (today - date_val).days
            if 0 <= days_ago < 7:
                buckets[6 - days_ago] += 1
        return buckets

    obs_total = len(observations)
    prop_total = len(proposals)
    dec_total = len(decisions)

    if obs_total > 8 and prop_total <= 1:
        flow = "bottleneck"
    elif obs_total > 5 * max(prop_total, 1) and obs_total > 5:
        flow = "bottleneck"
    else:
        flow = "healthy"

    return {
        "observations": {"total": obs_total, "sparkline": sparkline_buckets(observations)},
        "proposals": {"total": prop_total, "sparkline": sparkline_buckets(proposals)},
        "decisions": {"total": dec_total, "sparkline": sparkline_buckets(decisions)},
        "flow_status": flow,
    }


def build_activity_feed(observations: list[dict], proposals: list[dict],
                        limit: int = 15) -> list[dict]:
    """Build a cross-agent chronological feed for the dashboard activity zone."""
    events = []

    def _parse_dt(date_val) -> datetime:
        """Parse a date value into a naive datetime for safe comparison."""
        if isinstance(date_val, str):
            try:
                dt = datetime.fromisoformat(date_val)
            except (ValueError, TypeError):
                return datetime.min
        elif isinstance(date_val, datetime):
            dt = date_val
        else:
            return datetime.min
        # Strip tzinfo so naive and aware datetimes can be compared
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

    for o in observations:
        events.append({
            "type": "observation",
            "slug": o.get("_slug", ""),
            "agent": o.get("agent", ""),
            "timestamp": _parse_dt(o.get("date", "")),
            "status": o.get("status", ""),
        })

    for p in proposals:
        events.append({
            "type": "proposal",
            "slug": p.get("_slug", ""),
            "agent": p.get("origin_agent", ""),
            "timestamp": _parse_dt(p.get("date", "")),
            "status": p.get("status", ""),
        })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]


def _is_empty_error_log(path: Path, size: int | None = None) -> bool:
    return path.suffix.lower() == ".err" and (
        path.stat().st_size if size is None else size
    ) == 0


def collect_logs(g: dict) -> dict[str, list[dict]]:
    """Collect log files grouped by date."""
    logs_dir = g["shared"] / "logs"
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


# ── Agent Helpers ─────────────────────────────────────────────────────────────


def execution_agent_options(g: dict) -> list[str]:
    """List configured writable instances whose integration supports execution."""
    options = []
    for name in g["agents"]:
        try:
            integration = get_agent_integration(g, name)
            instance = next(
                (
                    candidate
                    for candidate in g.get("agents_full", [])
                    if candidate.get("name") == name
                ),
                None,
            )
            capabilities = instance.get("capabilities", {}) if instance else {}
            if integration.supports_execution and capabilities.get("write") is True:
                options.append(name)
        except KeyError:
            continue
    return options


def get_agent_last_run(g: dict, agent_name: str) -> dict | None:
    """Return the newest stdout log path and timestamp for an agent."""
    logs_dir = g["shared"] / "logs"
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
    logs_dir = g["shared"] / "logs"
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


def compute_next_run_detail(
    g: dict,
    agent_name: str,
    dispatch_cfg: dict,
) -> dict | None:
    """Return the soonest scheduled run with its originating rule identity."""
    if not dispatch_cfg.get("enabled", False):
        return None
    rules = dispatch_cfg.get("routines", {}).get(agent_name, [])
    if not isinstance(rules, list):
        return None

    now = clock_now()
    logs_root = g["shared"] / "logs"
    candidates: list[dict] = []

    for rule_index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        routine_id = rule.get("id", "")
        if not routine_id or rule.get("condition"):
            continue

        at_time = rule.get("at", "")
        every_val = rule.get("every", "")

        if at_time:
            try:
                target = datetime.strptime(
                    f"{now.strftime('%Y-%m-%d')} {at_time}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                continue
            if target <= now:
                target += timedelta(days=1)
        elif every_val:
            match = re.fullmatch(r"(\d+)(m|h|d)", every_val)
            if not match:
                continue
            value = int(match.group(1))
            unit = match.group(2)
            seconds = value * 60 if unit == "m" else value * 3600 if unit == "h" else value * 86400
            marker = logs_root / f".last-{agent_name}-{routine_id}"
            target = (
                now
                if not marker.exists()
                else datetime.fromtimestamp(marker.stat().st_mtime)
                + timedelta(seconds=seconds)
            )
        else:
            continue

        candidates.append({
            "when": target,
            "routine_id": routine_id,
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


def relative_future(dt: datetime | None) -> str:
    """Format an upcoming datetime as '5m away', '2h away', 'tomorrow HH:MM', etc."""
    if dt is None:
        return ""
    now = clock_now()
    seconds = int((dt - now).total_seconds())
    if seconds <= 0:
        return "due now"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes}m away"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h away"
    if dt.date() == (now + timedelta(days=1)).date():
        return f"tomorrow {dt.strftime('%H:%M')}"
    return dt.strftime("%Y-%m-%d %H:%M")


templates.env.filters["relative_future"] = relative_future


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

app.include_router(admin_groups_router)
app.include_router(admin_library_router)
app.include_router(admin_memory_router)
app.include_router(agents_router)
app.include_router(agent_detail_router)
app.include_router(jobs_router)


def agent_health_status(last_seen: datetime | None) -> str:
    """Return health status based on last seen time. green/amber/red."""
    if last_seen is None:
        return "red"
    hours = (clock_now() - last_seen).total_seconds() / 3600
    if hours < 24:
        return "green"
    elif hours < 48:
        return "amber"
    return "red"


def collect_agents_with_identity(g: dict) -> tuple[list[dict], list[dict]]:
    """Build configured instance info. The retired subagent list is always empty."""
    observations = list_observations(g)
    dispatch_cfg = g.get("dispatch", {})
    run_timeout = g.get("runtime", {}).get("timeout", 1800)
    agents = []
    for instance in g.get("agents_full", []):
        agent_name = instance["name"]
        identity = instance.get("identity") or {}
        open_count = sum(1 for c in observations if c.get("agent") == agent_name and c.get("status") == "open")
        last_run = get_agent_last_run(g, agent_name)
        last_seen = (
            last_run["at"]
            if last_run
            else get_agent_last_seen(g, agent_name)
        )
        next_run_detail = compute_next_run_detail(g, agent_name, dispatch_cfg)
        info = {
            "name": agent_name,
            "display_name": identity.get("display_name") or agent_name,
            "title": identity.get("title", ""),
            "emoji": identity.get("emoji", ""),
            "last_run": last_run,
            "last_seen": last_seen,
            "health": agent_health_status(last_seen),
            "open_observations": open_count,
            "is_subagent": False,
            "has_headshot": False,
            "integration": instance["integration"],
            "running": is_agent_running(g, agent_name, run_timeout),
            "next_run": (
                next_run_detail["when"] if next_run_detail else None
            ),
            "next_run_detail": next_run_detail,
        }
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


def _newest_active_job(group_jobs: tuple[Path, ...], agent_name: str):
    jobs = sorted(
        active_jobs(group_jobs, agent_name),
        key=lambda record: (
            record.started_at or "",
            record.spec.created_at,
            record.spec.job_id,
        ),
        reverse=True,
    )
    return jobs[0] if jobs else None


def _overlay_dashboard_job_state(agent: dict, current, group_key: str) -> None:
    agent_name = agent["name"]
    agent.update(
        {
            "running": current is not None and current.status == "running",
            "job_status_key": current.status if current is not None else None,
            "job_status": _job_state_label(current.status) if current is not None else None,
            "job_href": f"/{group_key}/jobs/{current.spec.job_id}" if current is not None else "",
            "activity_href": f"/{group_key}/agents/{agent_name}/activity",
            "profile_href": f"/{group_key}/agents/{agent_name}/profile",
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

    if g["key"] not in snapshot.config.groups:
        return []
    group = snapshot.config.groups[g["key"]]
    observations = list_observations(g)
    fleet: list[dict] = []
    for instance in group.agents.values():
        last_run = get_agent_last_run(g, instance.name)
        last_seen = last_run["at"] if last_run else get_agent_last_seen(g, instance.name)
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
                "open_observations": sum(1 for item in observations if item.get("agent") == instance.name and item.get("status") == "open"),
                "health": agent_health_status(last_seen),
                "memory_label": _dashboard_memory_label(selector, snapshot.config.memory.channels),
            }
        )
        _overlay_dashboard_job_state(fleet[-1], current, g["key"])
    return fleet


def get_agent_logs(g: dict, agent_name: str, limit: int = 20) -> list[dict]:
    """Get recent log files for an agent, newest first."""
    logs_dir = g["shared"] / "logs"
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
    logs_dir = g["shared"] / "logs"
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
    """Redirect to the default group."""
    services = _services()
    if services.startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    snapshot = services.config_store.load()
    agency = get_agency_config()
    default = agency.get("default_group", "")
    if default and default in snapshot.config.groups:
        return RedirectResponse(f"/{default}/", status_code=303)
    first = next(iter(snapshot.config.groups), "")
    if first:
        return RedirectResponse(f"/{first}/", status_code=303)
    return RedirectResponse("/setup", status_code=303)


@app.get("/setup/complete/{group}", response_class=HTMLResponse)
async def setup_complete(request: Request, group: str):
    """Post-setup page — tells user to come back later."""
    group_name = group
    agency_title = "Agency"
    services = _services()
    if services.startup_error is None:
        snapshot = services.config_store.load()
        agency_title = agency_settings(snapshot).get("title", "Agency")
        if group in snapshot.config.groups:
            group_name = snapshot.config.groups[group].name
    return templates.TemplateResponse(request, "setup_complete.html", {
        "request": request,
        "agency_title": agency_title,
        "group": group,
        "group_name": group_name,
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
    agency = agency_settings(snapshot)
    orgs = []
    for key, group in snapshot.config.groups.items():
        org_path = Path(group.path)
        shared_exists = (org_path / "shared").exists()
        path_exists = org_path.exists()
        dispatch_cfg = group.dispatch
        orgs.append({
            "key": key,
            "name": group.name,
            "path": str(group.path),
            "agents": list(group.agents.keys()),
            "agent_count": len(group.agents),
            "initialized": shared_exists,
            "path_exists": path_exists,
            "dispatch_enabled": dispatch_cfg.enabled,
        })
    return {
        "agency_title": agency.get("title", "Agency"),
        "default_group": agency.get("default_group", ""),
        "orgs": orgs,
        "groups": {
            key: group.name for key, group in snapshot.config.groups.items()
        },
        "revision": snapshot.revision,
        "admin_active": True,
        "active": "admin",
        "admin_page": admin_page,
        "dispatch": get_dispatch_status(),
        "dispatch_error": dispatch_error,
        "theme_css": get_theme_css(),
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
        "ai_backend": get_agency_config()["ai_backend"],
        "installed_count": len(REGISTRY),
        "themes": load_themes(),
        "current_theme": get_agency_config()["theme"],
    })


def _read_integration_config():
    """Read integration module list from config."""
    from agency.integrations import _read_config
    return _read_config()


@app.get("/admin/integrations", response_class=HTMLResponse)
async def admin_integrations_page(request: Request):
    """Admin integrations management page."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    from agency.integrations import scan_available

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
    from agency.integrations import register_integration
    form = await request.form()
    module_path = form.get("module_path", "")
    if module_path:
        register_integration(module_path)
    return RedirectResponse("/admin/integrations?restart=1", status_code=303)


@app.post("/admin/integrations/unregister", response_class=HTMLResponse)
async def admin_integrations_unregister(request: Request):
    """Unregister an installed integration."""
    from agency.integrations import unregister_integration
    form = await request.form()
    module_path = form.get("module_path", "")
    if module_path:
        unregister_integration(module_path)
    return RedirectResponse("/admin/integrations?restart=1", status_code=303)


@app.post("/admin/integrations/restart", response_class=HTMLResponse)
async def admin_integrations_restart(request: Request):
    """Restart the agency service to apply integration changes."""
    try:
        subprocess.Popen(["systemctl", "--user", "restart", "agency.service"])
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


@app.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups_page(request: Request):
    """Admin agent groups page."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "admin_groups.html", {
        "request": request,
        **admin_context("groups"),
    })


@app.post("/admin/settings", response_class=HTMLResponse)
async def admin_save_settings(request: Request):
    """Save agency-level settings."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    form = await request.form()
    revision = str(form.get("revision", "")).strip()
    title = form.get("title", "Agency").strip()
    default_group = form.get("default_group", "").strip()
    snapshot = _load_snapshot()
    settings = agency_settings(snapshot)
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
    try:
        patch_agency_settings(
            ConfigStore(snapshot.path),
            revision or snapshot.revision,
            AgencySettingsPatch(
                title=title or "Agency",
                default_group=default_group,
                ai_backend=ai_backend,
                theme=theme,
                dispatch_interval=int(dispatch_interval),
                agent_library=settings.get("agent_library", ""),
                compilation_cache=settings.get("compilation_cache", ""),
                memory_store=settings.get("memory_store", ""),
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


@app.get("/admin/orgs/new", response_class=HTMLResponse)
async def admin_org_new(request: Request):
    """Create new org form."""
    if _services().startup_error is not None:
        return RedirectResponse("/setup", status_code=303)
    agency = get_agency_config()
    snapshot = _load_snapshot()
    return templates.TemplateResponse(request, "admin_org_edit.html", {
        "request": request,
        "agency_title": agency.get("title", "Agency"),
        "admin_active": True,
        "active": "admin",
        "admin_page": "groups",
        "theme_css": get_theme_css(),
        "groups": {
            key: group.name for key, group in snapshot.config.groups.items()
        },
        "mode": "create",
        "org_key": "",
        "org_name": "",
        "org_workspace_path": "",
        "org_path": "",
        "default_integration": "claude-code",
        "org_agents": "",
        "org_workspaces_json": json_module.dumps([]),
        "workspace_types_json": _workspace_types_json(),
        "agent_infos": [],
        "warning": "",
        "revision": snapshot.revision,
    })


@app.post("/{group}/agents/{agent}/run")
async def agent_run(
    request: Request,
    group: str,
    agent: str,
    services: AgencyServices = Depends(get_services),
):
    snapshot = services.config_store.load()
    try:
        group_config = snapshot.config.groups[group]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group}") from exc
    try:
        instance = group_config.agents[agent]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent}") from exc

    form = await request.form()
    routine_id = str(form.get("routine_id") or "").strip()
    if not routine_id or "/" in routine_id or ".." in routine_id:
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

    memory_scope = str(form.get("memory_scope") or "").strip()
    memory_override = None
    if memory_scope:
        if memory_scope == "channel":
            raise HTTPException(status_code=400, detail="Channel memory override requires a channel")
        if memory_scope not in {"run", "routine", "agent", "group"}:
            raise HTTPException(status_code=400, detail="Invalid memory override")
        memory_override = {"scope": memory_scope}

    try:
        request_obj = JobRequest(
            config_path=services.config_path,
            group_key=group,
            agent_name=agent,
            trigger="manual_prompt",
            task_input=build_routine_task_input(
                routine_id,
                tuple(routine.arguments or ()),
            ),
            routine_id=routine_id,
            memory_override=memory_override,
        )
        handle = submit_job_request(request_obj)
    except (TypeError, ValueError, JobValidationError, JobSubmissionError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({"status": "started", "job_id": handle.job_id}, status_code=202)


@app.get("/{group}/", response_class=HTMLResponse)
async def home(request: Request, group: str):
    """Dashboard home — mission control."""
    g = get_group(group)
    observations = list_observations(g)
    proposals = list_proposals(g)
    decisions = list_decisions(g)

    open_observations = [c for c in observations if c.get("status") in ("open",)]
    floated_observations = [c for c in observations if c.get("float")]
    actionable_proposals = [c for c in proposals if c.get("status") in ("proposed", "investigating")]

    floated_open_observations = [c for c in observations if c.get("float") and c.get("status") == "open"]
    needs_action_count = len(actionable_proposals) + len(floated_open_observations)

    # Zone 1: Fleet status
    agents = build_dashboard_fleet(g)

    # Zone 2: Pipeline pulse
    pipeline = build_pipeline_stats(observations, proposals, decisions)

    # Zone 4: Activity feed
    activity = build_activity_feed(observations, proposals, limit=15)

    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        **group_context(g, observations=observations, proposals=proposals),
        # Zone 1: Fleet
        "fleet_agents": agents,
        "fleet_healthy": sum(1 for a in agents if a["health"] == "green"),
        "fleet_running": sum(1 for a in agents if a.get("job_status_key") == "running"),
        # Zone 2: Pipeline
        "pipeline": pipeline,
        # Zone 3: Attention queue
        "actionable_proposals": actionable_proposals,
        "open_observations": open_observations[:7],
        "floated_observations": floated_observations,
        "needs_action_count": needs_action_count,
        # Zone 4: Activity
        "activity_feed": activity,
        # Executing decisions
        "running_decisions": [d for d in decisions if d.get("execution_status") == "running"],
    })


@app.get("/{group}/observations", response_class=HTMLResponse)
async def observations_list(request: Request, group: str, agent: str = "", status: str = ""):
    """List all observations with optional filtering."""
    g = get_group(group)
    observations = list_observations(g)
    filtered = observations
    if agent:
        filtered = [c for c in filtered if c.get("agent") == agent]
    if status:
        filtered = [c for c in filtered if c.get("status") == status]
    return templates.TemplateResponse(request, "observations.html", {
        "request": request,
        **group_context(g, observations=observations),
        "observations": filtered,
        "filter_agent": agent,
        "filter_status": status,
        "agents": g["agents"],
    })


@app.get("/{group}/observations/{slug}", response_class=HTMLResponse)
async def observation_detail(request: Request, group: str, slug: str):
    """View a single observation."""
    g = get_group(group)
    path = g["shared"] / "observations" / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Observation not found")
    raw = path.read_text()
    meta, body = parse_frontmatter(raw)

    # Resolve pipeline chain: observation → proposal → decision
    pipeline = None
    linked_proposal_slug = meta.get("linked_proposal", "")
    if linked_proposal_slug:
        proposal_slug = linked_proposal_slug.replace(".md", "")
        proposal_path = g["shared"] / "proposals" / f"{proposal_slug}.md"
        pipeline = {"proposal_slug": proposal_slug, "proposal_exists": proposal_path.exists()}
        # Check for a decision on that proposal
        decision_path = g["shared"] / "decisions" / f"{proposal_slug}.md"
        if decision_path.exists():
            dmeta, _ = parse_frontmatter(decision_path.read_text())
            pipeline["decision_slug"] = proposal_slug
            pipeline["decision_status"] = dmeta.get("execution_status", "decided")
        else:
            pipeline["decision_slug"] = None

    return templates.TemplateResponse(request, "observation_detail.html", {
        "request": request,
        **group_context(g),
        "meta": meta,
        "body_html": render_md(body),
        "body_raw": body,
        "slug": slug,
        "title": extract_display_title(body, slug),
        "filename": path.name,
        "pipeline": pipeline,
    })


@app.post("/{group}/observations/{slug}/status", response_class=HTMLResponse)
async def observation_update_status(request: Request, group: str, slug: str):
    """Update an observation's status via form submission."""
    g = get_group(group)
    path = g["shared"] / "observations" / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Observation not found")

    form = await request.form()
    new_status = form.get("status", "")
    if new_status not in ("open", "connected", "dismissed", "archived"):
        raise HTTPException(400, "Invalid status")

    update_frontmatter_field(path, "status", new_status)

    return RedirectResponse(f"/{group}/observations/{slug}", status_code=303)


@app.get("/{group}/proposals", response_class=HTMLResponse)
async def proposals_list(request: Request, group: str):
    """List all proposals."""
    g = get_group(group)
    items = list_proposals(g)
    return templates.TemplateResponse(request, "proposals.html", {
        "request": request,
        **group_context(g),
        "proposals": items,
    })


@app.get("/{group}/proposals/{slug}", response_class=HTMLResponse)
async def proposal_detail(request: Request, group: str, slug: str):
    """View a single proposal."""
    g = get_group(group)
    return render_proposal_detail(request, g, group, slug)


def render_proposal_detail(request: Request, g: dict, group: str, slug: str,
                            *, selected_execution_agent: str | None = None,
                            submitted_answers: dict | None = None,
                            decision_note: str = "",
                            decision_error: str = "", status_code: int = 200):
    """Build the proposal_detail template response. Shared by the GET route and
    the POST /decide route so validation errors can re-render the same page."""
    proposals_dir = g["shared"] / "proposals"
    observations_dir = g["shared"] / "observations"
    decisions_dir = g["shared"] / "decisions"

    path = proposals_dir / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Proposal not found")
    raw = path.read_text()
    meta, body = parse_frontmatter(raw)

    # Compute proposal-level schema errors (shown on GET and POST)
    proposal_errors = validate_proposal_schema(meta)
    declared_executor = meta.get("execution_agent", "")
    if declared_executor and declared_executor not in execution_agent_options(g):
        proposal_errors.append(
            f"Declared executor '{declared_executor}' is not an eligible agent"
        )

    # Find linked observations
    linked = []
    for c in meta.get("observations", []):
        cpath = observations_dir / c
        if cpath.exists():
            linked.append({"filename": c, "slug": cpath.stem})

    # Find related decision
    decision = None
    if decisions_dir.exists():
        for d in decisions_dir.glob("*.md"):
            dtext = d.read_text()
            if slug in dtext:
                dmeta, dbody = parse_frontmatter(dtext)
                decision = {"filename": d.name, "slug": d.stem, "meta": dmeta, "body": dbody}
                break

    # Sync proposal status if a decision exists but status is stale
    if decision and meta.get("status") != "decided":
        update_frontmatter_field(path, "status", "decided")
        meta["status"] = "decided"

    # Parse questions for template
    questions = meta.get("questions", [])
    decision_answers = decision["meta"].get("answers", {}) if decision else {}

    if selected_execution_agent is None:
        selected_execution_agent = meta.get("execution_agent") or ""

    return templates.TemplateResponse(request, "proposal_detail.html", {
        "request": request,
        **group_context(g),
        "meta": meta,
        "body_html": render_md(body),
        "body_raw": body,
        "slug": slug,
        "title": extract_display_title(body, slug),
        "linked_observations": linked,
        "decision": decision,
        "questions": questions,
        "answers": decision_answers,
        "execution_agents": execution_agent_options(g),
        "selected_execution_agent": selected_execution_agent,
        "proposal_errors": proposal_errors,
        "submitted_answers": submitted_answers if submitted_answers is not None else {},
        "decision_note": decision_note,
        "decision_error": decision_error,
    }, status_code=status_code)


@app.post("/{group}/proposals/{slug}/decide", response_class=HTMLResponse)
async def proposal_decide(request: Request, group: str, slug: str):
    """Create a decision by answering a proposal's questions and submitting a
    durable job for the selected execution agent."""
    g = get_group(group)
    decisions_dir = g["shared"] / "decisions"
    proposals_dir = g["shared"] / "proposals"

    # Read proposal to get questions
    cpath = proposals_dir / f"{slug}.md"
    if not cpath.exists():
        raise HTTPException(404, "Proposal not found")
    cmeta, proposal_body = parse_frontmatter(cpath.read_text())
    questions = cmeta.get("questions", [])

    form = await request.form()
    execution_agent = form.get("execution_agent", "")
    decision_note = str(form.get("decision_note", "")).strip()

    # Build answers from form data
    answers = {}
    for q in questions:
        key = f"answer_{q['id']}"
        if q.get("type") == "choice" and q.get("multi"):
            answers[q["id"]] = form.getlist(key)
        else:
            answers[q["id"]] = form.get(key, "")

    # 1. Schema validation — blocking before trusting answers
    schema_errors = validate_proposal_schema(cmeta)
    if schema_errors:
        return render_proposal_detail(
            request, g, group, slug,
            selected_execution_agent=execution_agent,
            submitted_answers=answers,
            decision_note=decision_note,
            status_code=400,
        )

    # 1b. Declared executor eligibility — mirrors render_proposal_detail check
    declared_executor = cmeta.get("execution_agent", "")
    if declared_executor and declared_executor not in execution_agent_options(g):
        return render_proposal_detail(
            request, g, group, slug,
            selected_execution_agent=execution_agent,
            submitted_answers=answers,
            decision_note=decision_note,
            status_code=400,
        )

    # 2. Answer validation
    all_errors = validate_answers(questions, answers)

    # 3. Executor eligibility validation
    if execution_agent not in execution_agent_options(g):
        error_msg = (
            f"Agent '{execution_agent}' does not support execution or is unavailable."
            if execution_agent else "Select an agent to implement this decision."
        )
        all_errors.append(error_msg)

    if all_errors:
        return render_proposal_detail(
            request, g, group, slug,
            selected_execution_agent=execution_agent,
            submitted_answers=answers,
            decision_note=decision_note,
            decision_error="; ".join(all_errors),
            status_code=400,
        )

    agency_cfg = get_agency_config()
    decided_by = agency_cfg.get("decided_by", "admin")
    today = clock_now().strftime("%Y-%m-%d")

    decisions_dir.mkdir(exist_ok=True)
    decision_path = decisions_dir / f"{slug}.md"

    # Shared metadata base
    meta = {
        "proposal": f"{slug}.md",
        "decided_by": decided_by,
        "date": today,
        "answers": answers,
        "decision_note": decision_note,
        "execution_agent": execution_agent,
        "execution_job_history": [],
    }

    # 4. Determine execution vs skip
    if should_execute_decision(questions, answers, decision_note):
        request_obj = JobRequest(
            config_path=CONFIG_PATH,
            group_key=group,
            agent_name=execution_agent,
            trigger="decision",
            task_input=build_decision_prompt(proposal_body, answers, decision_note),
            trigger_context={
                "decision_path": str(decision_path.resolve()),
                "proposal_path": str(cpath.resolve()),
            },
        )
        meta["execution_status"] = "pending"
        meta["execution_job_id"] = request_obj.job_id

        frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
        atomic_write_text(decision_path, f"---\n{frontmatter}\n---\n")

        try:
            submit_job_request(request_obj)
        except JobSubmissionError as error:
            decision_path.unlink(missing_ok=True)
            return render_proposal_detail(
                request, g, group, slug,
                selected_execution_agent=execution_agent,
                submitted_answers=answers,
                decision_note=decision_note,
                decision_error=str(error),
                status_code=400,
            )
    else:
        meta["execution_status"] = "skipped"
        meta["execution_summary"] = SKIP_EXECUTION_SUMMARY
        frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
        atomic_write_text(decision_path, f"---\n{frontmatter}\n---\n")

    # Update proposal status to decided
    update_frontmatter_field(cpath, "status", "decided")

    return RedirectResponse(f"/{group}/decisions/{slug}", status_code=303)


@app.get("/{group}/decisions", response_class=HTMLResponse)
async def decisions_list(request: Request, group: str):
    """List all decisions."""
    g = get_group(group)
    items = list_decisions(g)
    return templates.TemplateResponse(request, "decisions.html", {
        "request": request,
        **group_context(g),
        "decisions": items,
    })


@app.get("/{group}/decisions/{slug}", response_class=HTMLResponse)
async def decision_detail(request: Request, group: str, slug: str):
    """View a single decision."""
    g = get_group(group)
    return render_decision_detail(request, g, group, slug)


def render_decision_detail(request: Request, g: dict, group: str, slug: str,
                            *, decision_error: str = "", status_code: int = 200):
    """Build the decision_detail template response. Shared by the GET route and
    the POST /retry route so validation/submission errors can re-render the
    same page instead of returning a bare JSON error."""
    path = g["shared"] / "decisions" / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Decision not found")
    raw = path.read_text()
    meta, body = parse_frontmatter(raw)

    # Resolve pipeline chain: observations → proposal → this decision
    pipeline_observations = []
    proposal_slug = (meta.get("proposal", "") or "").replace(".md", "")
    pmeta = {}
    if proposal_slug:
        proposal_path = g["shared"] / "proposals" / f"{proposal_slug}.md"
        if proposal_path.exists():
            pmeta, _ = parse_frontmatter(proposal_path.read_text())
            for obs_file in pmeta.get("observations", []):
                obs_slug = obs_file.replace(".md", "")
                obs_path = g["shared"] / "observations" / obs_file
                if obs_path.exists():
                    pipeline_observations.append({"slug": obs_slug, "filename": obs_file})

    execution_status = meta.get("execution_status", "")
    execution_summary = meta.get("execution_summary", "")
    executed_by = meta.get("executed_by", "")
    execution_log = meta.get("execution_log", "")
    execution_job_id = meta.get("execution_job_id", "")
    changed_files = meta.get("changed_files", []) or []
    decision_note = meta.get("decision_note", "")
    verification_status = meta.get("verification_status", "")
    verified_by = meta.get("verified_by", "")
    verified_at = meta.get("verified_at", "")
    follow_up_observation = meta.get("follow_up_observation", "")

    selected_execution_agent = (
        meta.get("execution_agent")
        or pmeta.get("execution_agent")
        or ""
    )

    return templates.TemplateResponse(request, "decision_detail.html", {
        "request": request,
        **group_context(g),
        "meta": meta,
        "body_html": render_md(body),
        "slug": slug,
        "title": extract_display_title(body, slug),
        "pipeline_observations": pipeline_observations,
        "proposal_slug": proposal_slug,
        "execution_status": execution_status,
        "execution_summary": execution_summary,
        "executed_by": executed_by,
        "execution_log": execution_log,
        "execution_job_id": execution_job_id,
        "changed_files": changed_files,
        "decision_note": decision_note,
        "verification_status": verification_status,
        "verified_by": verified_by,
        "verified_at": verified_at,
        "follow_up_observation": (follow_up_observation or "").replace(".md", ""),
        "questions": pmeta.get("questions", []),
        "answers": meta.get("answers", {}),
        "execution_agents": execution_agent_options(g),
        "selected_execution_agent": selected_execution_agent,
        "decision_error": decision_error,
    }, status_code=status_code)


@app.post("/{group}/decisions/{slug}/retry", response_class=HTMLResponse)
async def decision_retry(request: Request, group: str, slug: str):
    """Retry execution of a failed decision by submitting a new durable job."""
    g = get_group(group)
    decision_path = g["shared"] / "decisions" / f"{slug}.md"
    if not decision_path.exists():
        raise HTTPException(404, "Decision not found")

    original_text = decision_path.read_text()
    meta, body = parse_frontmatter(original_text)

    # Only failed or cancelled decisions may be retried
    current_status = meta.get("execution_status", "")
    if current_status not in {"failed", "cancelled"}:
        return render_decision_detail(
            request, g, group, slug,
            decision_error=f"Cannot retry a decision with status \u2018{current_status}\u2019. Only failed or cancelled decisions can be retried.",
            status_code=400,
        )

    proposal_slug = (meta.get("proposal", "") or "").replace(".md", "")
    proposal_path = g["shared"] / "proposals" / f"{proposal_slug}.md"
    if not proposal_slug or not proposal_path.exists():
        raise HTTPException(400, "Decision has no linked proposal")
    pmeta, proposal_body = parse_frontmatter(proposal_path.read_text())

    default_agent = (
        meta.get("execution_agent")
        or pmeta.get("execution_agent", "")
    )

    form = await request.form()
    execution_agent = form.get("execution_agent") or default_agent

    if execution_agent not in execution_agent_options(g):
        return render_decision_detail(
            request, g, group, slug,
            decision_error=f"Agent '{execution_agent}' does not support execution or is not writable.",
            status_code=400,
        )

    request_obj = JobRequest(
        config_path=CONFIG_PATH,
        group_key=group,
        agent_name=execution_agent,
        trigger="decision_retry",
        task_input=build_decision_prompt(proposal_body, meta.get("answers", {}), meta.get("decision_note", "")),
        trigger_context={
            "decision_path": str(decision_path.resolve()),
            "proposal_path": str(proposal_path.resolve()),
        },
    )

    previous_job_id = meta.get("execution_job_id") or ""
    history = list(meta.get("execution_job_history") or [])
    if previous_job_id:
        history.append(previous_job_id)

    updated_meta = dict(meta)
    updated_meta.pop("execution_summary", None)
    updated_meta.pop("changed_files", None)
    updated_meta.update({
        "execution_status": "pending",
        "execution_agent": execution_agent,
        "execution_job_id": request_obj.job_id,
        "execution_job_history": history,
    })

    frontmatter = yaml.dump(updated_meta, default_flow_style=False, sort_keys=False).strip()
    atomic_write_text(decision_path, f"---\n{frontmatter}\n---\n\n{body}\n")

    try:
        submit_job_request(request_obj)
    except JobSubmissionError as error:
        atomic_write_text(decision_path, original_text)
        return render_decision_detail(
            request, g, group, slug,
            decision_error=str(error),
            status_code=400,
        )

    return RedirectResponse(f"/{group}/decisions/{slug}", status_code=303)


@app.post("/{group}/decisions/{slug}/verify", response_class=HTMLResponse)
async def decision_verify(request: Request, group: str, slug: str):
    """Record whether an executed decision satisfied its originating proposal.

    This is a thin, governance-only outcome state on the existing decision
    record — Agency observes and governs the result, it does not execute. When
    the outcome did not satisfy the intent, this opens a follow-up observation
    (floated, linked back to the decision) so the loop stays connected.
    """
    g = get_group(group)
    decision_path = g["shared"] / "decisions" / f"{slug}.md"
    if not decision_path.exists():
        raise HTTPException(404, "Decision not found")

    meta, body = parse_frontmatter(decision_path.read_text())

    form = await request.form()
    outcome = form.get("verification_status", "")
    if outcome not in ("verified", "needs_follow_up"):
        return render_decision_detail(
            request, g, group, slug,
            decision_error="Choose 'Verified' or 'Needs follow-up'.",
            status_code=400,
        )

    agency_cfg = get_agency_config()
    verifier = agency_cfg.get("decided_by", "admin")
    now = clock_now().isoformat(timespec="seconds")

    meta["verification_status"] = outcome
    meta["verified_by"] = verifier
    meta["verified_at"] = now

    if outcome == "needs_follow_up":
        follow_up_slug = _create_follow_up_observation(g, slug, meta, body)
        meta["follow_up_observation"] = f"{follow_up_slug}.md"
    else:
        meta.pop("follow_up_observation", None)

    frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    atomic_write_text(decision_path, f"---\n{frontmatter}\n---\n\n{body}\n")

    if outcome == "needs_follow_up":
        return RedirectResponse(
            f"/{group}/observations/{meta['follow_up_observation'].replace('.md', '')}",
            status_code=303,
        )
    return RedirectResponse(f"/{group}/decisions/{slug}", status_code=303)


def _create_follow_up_observation(g: dict, decision_slug: str, meta: dict, body: str) -> str:
    """Create a floated follow-up observation linked back to a decision whose
    outcome did not satisfy its proposal. Returns the new observation slug."""
    observations_dir = g["shared"] / "observations"
    observations_dir.mkdir(parents=True, exist_ok=True)

    stamp = clock_now().strftime("%Y%m%d-%H%M%S")
    follow_up_slug = f"{decision_slug}-follow-up-{stamp}"

    agent = (
        meta.get("executed_by")
        or meta.get("execution_agent")
        or get_agency_config().get("decided_by", "admin")
    )
    proposal = meta.get("proposal", "")
    obs_meta = {
        "agent": agent,
        "date": clock_now().isoformat(timespec="seconds"),
        "category": "verification",
        "status": "open",
        "float": True,
        "linked_observations": [],
        "linked_proposal": proposal or None,
        "follow_up_of_decision": f"{decision_slug}.md",
    }
    frontmatter = yaml.dump(obs_meta, default_flow_style=False, sort_keys=False).strip()
    proposal_ref = f" (proposal `{proposal}`)" if proposal else ""
    obs_body = (
        f"# Follow-up needed: {decision_slug}\n\n"
        f"The executed decision `{decision_slug}.md`{proposal_ref} did not satisfy "
        "its originating proposal. Verification marked this outcome as needing "
        "follow-up.\n\n"
        "Describe what is still wrong or incomplete so it can be re-proposed and "
        "re-dispatched.\n"
    )
    obs_path = observations_dir / f"{follow_up_slug}.md"
    atomic_write_text(obs_path, f"---\n{frontmatter}\n---\n\n{obs_body}")
    return follow_up_slug


@app.get("/{group}/logs", response_class=HTMLResponse)
async def logs_list(request: Request, group: str):
    """Browse execution logs by date."""
    g = get_group(group)
    logs = collect_logs(g)
    return templates.TemplateResponse(request, "logs.html", {
        "request": request,
        **group_context(g),
        "logs": logs,
    })


@app.get("/{group}/logs/view", response_class=HTMLResponse)
async def log_view(request: Request, group: str, path: str):
    """View a log file."""
    g = get_group(group)
    fpath = Path(path)
    logs_dir = g["shared"] / "logs"
    validate_file_access(fpath, logs_dir)
    if not fpath.exists():
        raise HTTPException(404, "Log not found")

    raw = fpath.read_text()
    content_html = render_md(raw) if fpath.suffix == ".out" else f"<pre class='whitespace-pre-wrap text-sm text-red-700'>{raw}</pre>"

    return templates.TemplateResponse(request, "log_view.html", {
        "request": request,
        **group_context(g),
        "filename": fpath.name,
        "content_html": content_html,
        "raw": raw,
    })


@app.get("/{group}/workspaces", response_class=HTMLResponse)
async def workspaces_list(request: Request, group: str):
    """List all workspaces for a group."""
    g = get_group(group)
    workspace_list = g.get("workspaces", [])
    from agency.workspaces import REGISTRY
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
        **group_context(g),
        "enriched_workspaces": enriched,
        "active": "workspaces",
    })


@app.get("/{group}/workspaces/{idx}/file", response_class=HTMLResponse)
async def workspace_file_view(request: Request, group: str, idx: int):
    """View/edit a config file within a workspace."""
    g = get_group(group)
    workspace_list = g.get("workspaces", [])
    if idx < 0 or idx >= len(workspace_list):
        raise HTTPException(404, "Workspace not found")
    ws = workspace_list[idx]
    from agency.workspaces import REGISTRY
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
        **group_context(g),
        "ws": ws,
        "ws_idx": idx,
        "plugin": plugin,
        "config_files": config_files,
        "current_file": file_path,
        "raw": raw,
        "language": language,
        "active": "workspaces",
    })


@app.post("/{group}/workspaces/{idx}/file/save", response_class=HTMLResponse)
async def workspace_file_save(request: Request, group: str, idx: int):
    """Save edits to a workspace config file."""
    g = get_group(group)
    workspace_list = g.get("workspaces", [])
    if idx < 0 or idx >= len(workspace_list):
        raise HTTPException(404, "Workspace not found")
    form = await request.form()
    file_path = form.get("file_path", "")
    content = form.get("content", "")
    if file_path:
        ws = workspace_list[idx]
        from agency.workspaces import REGISTRY
        plugin = REGISTRY.get(ws.get("type", "custom"))
        allowed = [cf["path"] for cf in plugin.get_config_files(ws.get("config", {}))] if plugin else []
        if file_path not in allowed:
            raise HTTPException(403, "File not in workspace config files")
        Path(file_path).write_text(content)
    return RedirectResponse(f"/{group}/workspaces/{idx}/file?path={urllib.parse.quote(file_path, safe='')}", status_code=303)


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
    "shared",
)


class _AgencyReloadFilter:
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
    """Create Uvicorn's WatchFiles supervisor with Agency's path filter."""
    supervisor = WatchFilesReload(config, target=server.run, sockets=sockets)
    supervisor.watch_filter = _AgencyReloadFilter(config.reload_dirs[0])
    return supervisor


def _run_reload_server(host: str, port: int) -> None:
    """Run Uvicorn's reload lifecycle with Agency's WatchFiles filter."""
    reload_root = Path.cwd().resolve()
    config = uvicorn.Config(
        "agency.app:app",
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


def run_server(host: str, port: int, reload: bool = False) -> None:
    """Initialize Agency and run the web server."""
    if not CONFIG_PATH.exists():
        print(
            f"First run: open http://localhost:{port}/setup to launch guided Agency setup."
        )

    refresh_services()
    if reload:
        _run_reload_server(host, port)
        return

    uvicorn.run(app, host=host, port=port)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agency — Agent Management Dashboard")
    parser.add_argument("--port", type=int, default=8500, help="Port to serve on (default: 8500)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--reload", action="store_true", help="Restart when project files change")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
