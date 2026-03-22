"""Agency Dashboard — multi-group agent management interface."""

import csv
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import markdown
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

import uvicorn

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.cwd() / "config.yaml"


def load_config() -> dict:
    """Read config.yaml and return dict. Returns defaults if file doesn't exist."""
    if not CONFIG_PATH.exists():
        return {"agency": {"title": "Agency", "default_group": ""}, "groups": {}}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def save_config(config: dict) -> None:
    """Atomically write config.yaml (temp file + rename)."""
    fd, tmp = tempfile.mkstemp(dir=CONFIG_PATH.parent, suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        os.unlink(tmp)
        raise


def reload_groups() -> None:
    """Reload the global GROUPS dict from config."""
    global GROUPS, CONFIG
    CONFIG = load_config()
    GROUPS = CONFIG.get("groups", {})


CONFIG = load_config()
GROUPS = CONFIG.get("groups", {})


def get_agency_config() -> dict:
    """Return agency-level config with defaults."""
    agency = CONFIG.get("agency", {})
    return {
        "title": agency.get("title", "Agency"),
        "default_group": agency.get("default_group", "") or (list(GROUPS.keys())[0] if GROUPS else ""),
        "decided_by": agency.get("decided_by", "admin"),
    }


# ── Dispatch Helpers ──────────────────────────────────────────────────────────

DISPATCH_CONF_DIR = Path.home() / ".config" / "agency"
DISPATCH_CONF_FILE = DISPATCH_CONF_DIR / "dispatch.conf"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"


def get_dispatch_status() -> dict:
    """Return dispatch installation status."""
    installed = CONFIG.get("agency", {}).get("dispatch", {}).get("installed", False)
    interval = CONFIG.get("agency", {}).get("dispatch", {}).get("interval", 15)
    timer_active = False
    if installed:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "agency-dispatch.timer"],
                capture_output=True, text=True, timeout=5,
            )
            timer_active = result.stdout.strip() == "active"
        except Exception:
            timer_active = False
    return {"installed": installed, "interval": interval, "timer_active": timer_active}


def install_dispatch(interval: int = 15) -> str | None:
    """Install dispatch systemd timer. Returns error string or None on success."""
    try:
        # 1. Create config directory
        DISPATCH_CONF_DIR.mkdir(parents=True, exist_ok=True)

        # 2. Find venv python
        venv_python = Path(__file__).parent.parent / ".venv" / "bin" / "python3"
        if not venv_python.exists():
            venv_python = Path(sys.executable)

        # 2b. Find claude CLI (systemd services may not have it in PATH)
        claude_path = shutil.which("claude") or ""

        # 3. Write dispatch.conf (values quoted for paths with spaces)
        DISPATCH_CONF_FILE.write_text(
            f'config_path="{CONFIG_PATH}"\n'
            f'venv_python="{venv_python}"\n'
            f'claude_path="{claude_path}"\n'
        )

        # 4. Copy dispatch.sh
        src_dispatch = Path(__file__).parent / "dispatch" / "dispatch.sh"
        dst_dispatch = DISPATCH_CONF_DIR / "dispatch.sh"
        shutil.copy2(src_dispatch, dst_dispatch)
        dst_dispatch.chmod(dst_dispatch.stat().st_mode | stat.S_IEXEC)

        # 4b. Fix SELinux context so systemd can execute it (Fedora Kinoite)
        try:
            subprocess.run(
                ["chcon", "-t", "bin_t", str(dst_dispatch)],
                capture_output=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # chcon not available on non-SELinux systems

        # 5. Write systemd service
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        service_file = SYSTEMD_USER_DIR / "agency-dispatch.service"
        service_file.write_text(
            "[Unit]\n"
            "Description=Agency Agent Dispatch\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "ExecStart=%h/.config/agency/dispatch.sh\n"
            "Environment=HOME=%h\n"
        )

        # 6. Write systemd timer
        write_dispatch_timer(interval)

        # 7. Enable and start timer
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "agency-dispatch.timer"],
            capture_output=True, text=True, timeout=10, check=True,
        )

        # 8. Update config
        config = load_config()
        if "agency" not in config:
            config["agency"] = {}
        if "dispatch" not in config["agency"]:
            config["agency"]["dispatch"] = {}
        config["agency"]["dispatch"]["installed"] = True
        config["agency"]["dispatch"]["interval"] = interval
        save_config(config)

        # 9. Reload
        reload_groups()

        return None
    except Exception as e:
        return str(e)


app = FastAPI(title="Agency Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

md = markdown.Markdown(extensions=["tables", "fenced_code", "meta", "nl2br"])


# ── Group Resolution ──────────────────────────────────────────────────────────


def get_group(group: str) -> dict:
    """Resolve a group key to its full config dict."""
    if group not in GROUPS:
        raise HTTPException(404, f"Unknown group: {group}")
    g = GROUPS[group]
    return {
        "key": group,
        "name": g["name"],
        "path": Path(g["path"]),
        "agents": g["agents"],
        "shared": Path(g["path"]) / "shared",
    }


def safe_redirect(url: str, fallback: str = "/") -> str:
    """Validate a redirect URL is a safe relative path."""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return fallback


def group_context(g: dict, clues: list[dict] | None = None, curiosities: list[dict] | None = None) -> dict:
    """Return standard template context for a group. Accepts precomputed lists to avoid double-reads."""
    agency = get_agency_config()
    group_cfg = GROUPS.get(g["key"], {})
    if clues is None:
        clues = list_clues(g)
    if curiosities is None:
        curiosities = list_curiosities(g)
    open_clue_count = sum(1 for c in clues if c.get("status") == "open")
    actionable_curiosity_count = sum(1 for c in curiosities if c.get("status") in ("proposed", "investigating"))
    return {
        "group": g["key"],
        "group_name": g["name"],
        "groups": {k: v["name"] for k, v in GROUPS.items()},
        "agency_title": agency.get("title", "Agency"),
        "admin_active": False,
        "tmux_config_available": bool(group_cfg.get("tmux_config")),
        "nav_open_clues": open_clue_count,
        "nav_actionable": actionable_curiosity_count,
        "nav_agent_count": len(g["agents"]),
        "show_tips": CONFIG.get("agency", {}).get("show_tips", True) is not False,
        "tips_dismissed": CONFIG.get("agency", {}).get("tips_dismissed", []),
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


def read_file(path: Path) -> str:
    """Read a file, return empty string if missing."""
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def validate_file_access(fpath: Path, base_path: Path) -> None:
    """Validate file is within base_path. Raises HTTPException(403) if not."""
    try:
        fpath.resolve().relative_to(base_path.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")


def update_frontmatter_field(filepath: Path, field: str, value: str) -> None:
    """Update a single YAML frontmatter field in a markdown file."""
    raw = filepath.read_text()
    raw = re.sub(rf'^({field}:\s*).*$', f'\\1{value}', raw, count=1, flags=re.MULTILINE)
    filepath.write_text(raw)


def write_dispatch_timer(interval: int) -> None:
    """Write the agency-dispatch.timer systemd unit file."""
    timer_file = SYSTEMD_USER_DIR / "agency-dispatch.timer"
    timer_file.write_text(
        "[Unit]\n"
        "Description=Agency Agent Dispatch Timer\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* *:0/{interval}\n"
        "Persistent=true\n"
        "RandomizedDelaySec=60\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def parse_csv_to_rows(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse CSV text to header + rows, skipping comment lines."""
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return [], []
    reader = csv.reader(io.StringIO("\n".join(lines)))
    rows = list(reader)
    return rows[0], rows[1:]


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
    return datetime.now(tz=item_date.tzinfo) > item_date + timedelta(days=ttl)


def enforce_ttl(filepath: Path, meta: dict) -> bool:
    """Auto-archive an item if its TTL has expired. Returns True if archived."""
    status = meta.get("status", "")
    if status in ("archived", "dismissed", "approved", "rejected", "deferred"):
        return False
    if check_ttl_expired(meta):
        update_frontmatter_field(filepath, "status", "archived")
        meta["status"] = "archived"
        return True
    return False


def list_markdown_items(g: dict, subdir: str, apply_ttl: bool = False) -> list[dict]:
    """List markdown files from a shared subdirectory with parsed frontmatter."""
    item_dir = g["shared"] / subdir
    if not item_dir.exists():
        return []
    items = []
    for f in sorted(item_dir.glob("*.md"), reverse=True):
        raw = f.read_text()
        meta, body = parse_frontmatter(raw)
        meta.update({"_filename": f.name, "_body": body, "_slug": f.stem})
        if apply_ttl:
            enforce_ttl(f, meta)
        items.append(meta)
    return items


def list_clues(g: dict) -> list[dict]:
    return list_markdown_items(g, "clues", apply_ttl=True)


def list_curiosities(g: dict) -> list[dict]:
    return list_markdown_items(g, "curiosities", apply_ttl=True)


def list_decisions(g: dict) -> list[dict]:
    return list_markdown_items(g, "decisions")


def collect_documents(g: dict) -> list[dict]:
    """Collect standalone documents from agent directories."""
    docs = []
    skip_dirs = {"clues", "curiosities", "decisions", "prompts", "logs", "archive",
                 "ad-skills", "social-posts", "templates", "dashboard"}

    for agent in g["agents"]:
        agent_dir = g["path"] / agent
        if not agent_dir.exists():
            continue
        for f in sorted(agent_dir.rglob("*")):
            if f.is_dir():
                continue
            if f.name.startswith(".") or f.name == "CLAUDE.md":
                continue
            rel = f.relative_to(agent_dir)
            if any(part in skip_dirs for part in rel.parts[:-1]):
                continue
            suffix = f.suffix.lower()
            if suffix in (".md", ".csv", ".html", ".txt", ".py"):
                docs.append({
                    "agent": agent,
                    "path": str(f),
                    "rel_path": str(rel),
                    "name": f.name,
                    "suffix": suffix,
                })

    # Also add shared standalone files
    shared = g["shared"]
    if shared.exists():
        for f in sorted(shared.iterdir()):
            if f.is_file() and f.suffix in (".md", ".html", ".csv") and f.name != "memory.md":
                docs.append({
                    "agent": "shared",
                    "path": str(f),
                    "rel_path": f.name,
                    "name": f.name,
                    "suffix": f.suffix.lower(),
                })

    return docs


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
        for f in sorted(date_dir.iterdir(), reverse=True):
            if f.name.startswith("."):
                continue
            entries.append({
                "name": f.name,
                "path": str(f),
                "suffix": f.suffix,
                "size": f.stat().st_size,
            })
        if entries:
            result[date_dir.name] = entries
    return result


def collect_prompts(g: dict) -> list[dict]:
    """List prompt files."""
    prompts_dir = g["shared"] / "prompts"
    if not prompts_dir.exists():
        return []
    items = []
    for f in sorted(prompts_dir.glob("*.md")):
        items.append({
            "name": f.name,
            "path": str(f),
            "slug": f.stem,
        })
    return items


def collect_memory_files(g: dict) -> list[dict]:
    """Collect all memory.md files."""
    items = []
    # Shared memory
    sm = g["shared"] / "memory.md"
    if sm.exists():
        items.append({"agent": "shared", "path": str(sm), "name": "memory.md"})
    # Per-agent
    for agent in g["agents"]:
        mf = g["path"] / agent / "memory.md"
        if mf.exists():
            items.append({"agent": agent, "path": str(mf), "name": "memory.md"})
    return items


def status_badge(status: str) -> Markup:
    """Return a colored badge for clue/curiosity status."""
    colors = {
        "open": "bg-amber-100 text-amber-800",
        "connected": "bg-blue-100 text-blue-800",
        "investigating": "bg-purple-100 text-purple-800",
        "proposed": "bg-green-100 text-green-800",
        "approved": "bg-emerald-100 text-emerald-800",
        "dismissed": "bg-gray-100 text-gray-500",
        "archived": "bg-gray-100 text-gray-400",
    }
    cls = colors.get(status or "", "bg-gray-100 text-gray-600")
    return Markup(f'<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium {cls}">{status or "unknown"}</span>')


def agent_badge(agent: str) -> Markup:
    """Return a colored badge for agent name."""
    palette = [
        "bg-rose-100 text-rose-700",
        "bg-indigo-100 text-indigo-700",
        "bg-sky-100 text-sky-700",
        "bg-teal-100 text-teal-700",
        "bg-lime-100 text-lime-700",
        "bg-orange-100 text-orange-700",
        "bg-fuchsia-100 text-fuchsia-700",
        "bg-yellow-100 text-yellow-700",
        "bg-violet-100 text-violet-700",
        "bg-cyan-100 text-cyan-700",
        "bg-amber-100 text-amber-700",
        "bg-pink-100 text-pink-700",
        "bg-emerald-100 text-emerald-700",
        "bg-slate-100 text-slate-700",
    ]
    idx = hash(agent or "") % len(palette)
    cls = palette[idx]
    return Markup(f'<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium {cls}">{agent}</span>')


# Register template filters
templates.env.filters["status_badge"] = status_badge
templates.env.filters["agent_badge"] = agent_badge
templates.env.filters["render_md"] = render_md


# ── Agent Helpers ─────────────────────────────────────────────────────────────


def resolve_agent_dir(g: dict, agent_name: str) -> Path:
    """Find an agent's directory, checking root and _subagents/. Raises 404 if not found."""
    if "/" in agent_name or ".." in agent_name:
        raise HTTPException(400, "Invalid agent name")
    agent_dir = g["path"] / agent_name
    if agent_dir.is_dir():
        return agent_dir
    sub_dir = g["path"] / "_subagents" / agent_name
    if sub_dir.is_dir():
        return sub_dir
    raise HTTPException(404, f"Agent not found: {agent_name}")


def parse_agent_identity(agent_dir: Path) -> dict:
    """Read CLAUDE.md and return identity fields + body."""
    claude_md = agent_dir / "CLAUDE.md"
    if not claude_md.exists():
        return {"display_name": agent_dir.name, "title": "", "emoji": "", "body": "", "frontmatter": {}}
    raw = claude_md.read_text()
    meta, body = parse_frontmatter(raw)
    return {
        "display_name": meta.get("display_name", agent_dir.name),
        "title": meta.get("title", ""),
        "emoji": meta.get("emoji", ""),
        "body": body,
        "frontmatter": meta,
    }


def save_agent_identity(agent_dir: Path, fields: dict) -> None:
    """Merge identity fields into CLAUDE.md frontmatter, preserving other fields."""
    claude_md = agent_dir / "CLAUDE.md"
    if claude_md.exists():
        raw = claude_md.read_text()
        meta, body = parse_frontmatter(raw)
    else:
        meta, body = {}, ""
    for key in ("display_name", "title", "emoji"):
        if key in fields and fields[key]:
            meta[key] = fields[key]
        elif key in fields and not fields[key] and key in meta:
            del meta[key]
    if meta:
        front = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
        claude_md.write_text(f"---\n{front}\n---\n\n{body}")
    else:
        claude_md.write_text(body)


def save_agent_definition(agent_dir: Path, new_body: str) -> None:
    """Write CLAUDE.md body preserving all existing frontmatter."""
    claude_md = agent_dir / "CLAUDE.md"
    if claude_md.exists():
        raw = claude_md.read_text()
        meta, _ = parse_frontmatter(raw)
    else:
        meta = {}
    if meta:
        front = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
        claude_md.write_text(f"---\n{front}\n---\n\n{new_body}")
    else:
        claude_md.write_text(new_body)


def find_headshot(agent_dir: Path) -> Path | None:
    """Find headshot file by checking extensions in order."""
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = agent_dir / f"headshot.{ext}"
        if p.exists():
            return p
    return None


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


def relative_time(dt: datetime | None) -> str:
    """Format datetime as relative string."""
    if dt is None:
        return "No activity recorded"
    now = datetime.now()
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


def agent_health_status(last_seen: datetime | None) -> str:
    """Return health status based on last seen time. green/amber/red."""
    if last_seen is None:
        return "red"
    hours = (datetime.now() - last_seen).total_seconds() / 3600
    if hours < 24:
        return "green"
    elif hours < 48:
        return "amber"
    return "red"


def collect_agents_with_identity(g: dict) -> tuple[list[dict], list[dict]]:
    """Build full agent info lists. Returns (agents, subagents)."""
    clues = list_clues(g)
    agents = []
    subagents = []

    for agent_name in g["agents"]:
        agent_dir = g["path"] / agent_name
        if not agent_dir.is_dir():
            continue
        identity = parse_agent_identity(agent_dir)
        open_count = sum(1 for c in clues if c.get("agent") == agent_name and c.get("status") == "open")
        last_seen = get_agent_last_seen(g, agent_name)
        info = {
            "name": agent_name, "dir": agent_dir, **identity,
            "last_seen": last_seen,
            "health": agent_health_status(last_seen),
            "open_clues": open_count,
            "is_subagent": identity["frontmatter"].get("subagent", False),
            "has_headshot": find_headshot(agent_dir) is not None,
        }
        if info["is_subagent"]:
            subagents.append(info)
        else:
            agents.append(info)

    subagents_dir = g["path"] / "_subagents"
    if subagents_dir.is_dir():
        for d in sorted(subagents_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if any(s["name"] == d.name for s in subagents):
                continue
            identity = parse_agent_identity(d)
            open_count = sum(1 for c in clues if c.get("agent") == d.name and c.get("status") == "open")
            last_seen = get_agent_last_seen(g, d.name)
            subagents.append({
                "name": d.name, "dir": d, **identity,
                "last_seen": last_seen,
                "health": agent_health_status(last_seen),
                "open_clues": open_count, "is_subagent": True,
                "has_headshot": find_headshot(d) is not None,
            })

    return agents, subagents


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
                results.append({"name": f.name, "path": str(f), "date": date_dir.name, "size": f.stat().st_size, "suffix": f.suffix})
                if len(results) >= limit:
                    return results
    return results


def build_agent_timeline(g: dict, agent_name: str, agent_clues: list[dict] | None = None, limit: int = 30) -> list[dict]:
    """Build an interleaved timeline of logs and clues for an agent.
    Accepts precomputed agent_clues to avoid re-reading files."""
    events = []

    # Add logs
    logs_dir = g["shared"] / "logs"
    if logs_dir.exists():
        for date_dir in sorted(logs_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for f in sorted(date_dir.iterdir(), reverse=True):
                if f.name.startswith(f"{agent_name}-") and f.suffix in (".out", ".err"):
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    events.append({
                        "type": "log",
                        "timestamp": mtime,
                        "name": f.name,
                        "path": str(f),
                        "date": date_dir.name,
                        "size": f.stat().st_size,
                        "suffix": f.suffix,
                    })

    # Add clues from precomputed list
    for c in (agent_clues or []):
        clue_date = c.get("date")
        if isinstance(clue_date, str):
            try:
                clue_date = datetime.fromisoformat(clue_date)
            except (ValueError, TypeError):
                clue_date = datetime.now()
        elif not isinstance(clue_date, datetime):
            clue_date = datetime.now()
        events.append({
            "type": "clue",
            "timestamp": clue_date,
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
    agency = get_agency_config()
    default = agency.get("default_group", list(GROUPS.keys())[0] if GROUPS else "")
    if default and default in GROUPS:
        return RedirectResponse(f"/{default}/", status_code=303)
    # Fallback to first group
    first = list(GROUPS.keys())[0] if GROUPS else ""
    if first:
        return RedirectResponse(f"/{first}/", status_code=303)
    return RedirectResponse("/setup", status_code=303)


# ── Setup Routes ─────────────────────────────────────────────────────────────


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """First-run wizard page."""
    if GROUPS:
        return RedirectResponse("/", status_code=303)
    suggestion = str(Path.home() / ".claude" / "agents")
    return templates.TemplateResponse("setup.html", {
        "request": request,
        "agency_title": get_agency_config().get("title", "Agency"),
        "suggestion": suggestion,
        "error": "",
        "path_value": "",
    })


@app.post("/setup", response_class=HTMLResponse)
async def setup_process(request: Request):
    """Process first-run setup: scan path, create group, initialize, redirect."""
    if GROUPS:
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    path_str = form.get("path", "").strip()
    suggestion = str(Path.home() / ".claude" / "agents")
    agency_title = get_agency_config().get("title", "Agency")

    # Expand ~ and validate
    path = Path(path_str).expanduser()
    if not path.is_dir():
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "agency_title": agency_title,
            "suggestion": suggestion,
            "error": "That path doesn't exist or isn't a directory. Check the path and try again.",
            "path_value": path_str,
        })

    # Scan for agents
    detected = []
    for d in sorted(path.iterdir()):
        if d.is_dir() and d.name not in ("shared", "_subagents") and not d.name.startswith("."):
            if (d / "CLAUDE.md").exists():
                detected.append(d.name)

    if not detected:
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "agency_title": agency_title,
            "suggestion": suggestion,
            "error": 'No agents found at this path. Agency looks for subdirectories containing a CLAUDE.md file. <a href="/admin/" class="underline">Set up manually in Settings</a>.',
            "path_value": path_str,
        })

    # Derive group key (deduplicate)
    base_key = path.name.lower().replace(" ", "-")
    key = base_key
    config = load_config()
    counter = 2
    while key in config.get("groups", {}):
        key = f"{base_key}-{counter}"
        counter += 1

    name = path.name.replace("-", " ").title()

    if "groups" not in config:
        config["groups"] = {}
    config["groups"][key] = {
        "name": name,
        "path": str(path),
        "agents": detected,
    }
    if "agency" not in config:
        config["agency"] = {}
    config["agency"]["default_group"] = key

    save_config(config)
    reload_groups()

    # Initialize shared folder structure
    shared = path / "shared"
    for subdir in ["clues", "curiosities", "decisions", "prompts", "logs"]:
        (shared / subdir).mkdir(parents=True, exist_ok=True)
    memory_path = shared / "memory.md"
    if not memory_path.exists():
        memory_path.write_text(f"# {name} — Shared Memory\n\nCollective knowledge and decisions.\n")

    # Copy _clue-system-steps.md from an existing group if available
    clue_steps_target = shared / "prompts" / "_clue-system-steps.md"
    if not clue_steps_target.exists():
        for other_key, other_g in config.get("groups", {}).items():
            if other_key == key:
                continue
            source = Path(other_g["path"]) / "shared" / "prompts" / "_clue-system-steps.md"
            if source.exists():
                shutil.copy2(source, clue_steps_target)
                break

    for agent in detected:
        (path / agent).mkdir(exist_ok=True)

    return RedirectResponse(f"/{key}/", status_code=303)


# ── Tip Routes ────────────────────────────────────────────────────────────────


@app.post("/tips/dismiss", response_class=HTMLResponse)
async def tip_dismiss(request: Request):
    """Dismiss a specific tip card."""
    form = await request.form()
    tip_id = form.get("tip_id", "").strip()
    redirect = safe_redirect(form.get("redirect", "/"))

    if tip_id:
        config = load_config()
        if "agency" not in config:
            config["agency"] = {}
        dismissed = config["agency"].get("tips_dismissed", [])
        if tip_id not in dismissed:
            dismissed.append(tip_id)
            config["agency"]["tips_dismissed"] = dismissed
            save_config(config)
            reload_groups()

    return RedirectResponse(redirect, status_code=303)


@app.post("/tips/hide-all", response_class=HTMLResponse)
async def tip_hide_all(request: Request):
    """Hide all tip cards globally."""
    form = await request.form()
    redirect = safe_redirect(form.get("redirect", "/"))

    config = load_config()
    if "agency" not in config:
        config["agency"] = {}
    config["agency"]["show_tips"] = False
    save_config(config)
    reload_groups()

    return RedirectResponse(redirect, status_code=303)


# ── Admin Routes ──────────────────────────────────────────────────────────────


def admin_context(admin_page: str = "settings", dispatch_error: str = "") -> dict:
    """Build common context for admin pages."""
    config = load_config()
    agency = config.get("agency", {"title": "Agency", "default_group": ""})
    groups = config.get("groups", {})
    orgs = []
    for key, g in groups.items():
        org_path = Path(g["path"])
        shared_exists = (org_path / "shared").exists()
        path_exists = org_path.exists()
        dispatch_cfg = g.get("dispatch", {})
        orgs.append({
            "key": key,
            "name": g["name"],
            "path": g["path"],
            "agents": g.get("agents", []),
            "agent_count": len(g.get("agents", [])),
            "initialized": shared_exists,
            "path_exists": path_exists,
            "dispatch_enabled": dispatch_cfg.get("enabled", False),
        })
    return {
        "agency_title": agency.get("title", "Agency"),
        "default_group": agency.get("default_group", ""),
        "orgs": orgs,
        "groups": {k: v["name"] for k, v in groups.items()},
        "admin_active": True,
        "active": "admin",
        "admin_page": admin_page,
        "dispatch": get_dispatch_status(),
        "dispatch_error": dispatch_error,
    }


@app.get("/admin/", response_class=HTMLResponse)
async def admin_settings_page(request: Request):
    """Admin app settings page."""
    return templates.TemplateResponse("admin_settings.html", {
        "request": request,
        **admin_context("settings"),
    })


@app.get("/admin/dispatch", response_class=HTMLResponse)
async def admin_dispatch_page(request: Request):
    """Admin dispatch configuration page."""
    return templates.TemplateResponse("admin_dispatch.html", {
        "request": request,
        **admin_context("dispatch"),
    })


@app.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups_page(request: Request):
    """Admin agent groups page."""
    return templates.TemplateResponse("admin_groups.html", {
        "request": request,
        **admin_context("groups"),
    })


@app.post("/admin/settings", response_class=HTMLResponse)
async def admin_save_settings(request: Request):
    """Save agency-level settings."""
    form = await request.form()
    title = form.get("title", "Agency").strip()
    default_group = form.get("default_group", "").strip()

    config = load_config()
    if "agency" not in config:
        config["agency"] = {}
    config["agency"]["title"] = title or "Agency"
    if default_group:
        config["agency"]["default_group"] = default_group

    # Handle dispatch interval update
    dispatch_interval_raw = form.get("dispatch_interval", "")
    if dispatch_interval_raw:
        try:
            new_interval = int(dispatch_interval_raw)
            if 5 <= new_interval <= 120:
                if "dispatch" not in config["agency"]:
                    config["agency"]["dispatch"] = {}
                old_interval = config["agency"]["dispatch"].get("interval", 15)
                config["agency"]["dispatch"]["interval"] = new_interval
                # If interval changed and dispatch is installed, rewrite timer
                if new_interval != old_interval and config["agency"]["dispatch"].get("installed", False):
                    write_dispatch_timer(new_interval)
                    subprocess.run(
                        ["systemctl", "--user", "daemon-reload"],
                        capture_output=True, text=True, timeout=10,
                    )
                    subprocess.run(
                        ["systemctl", "--user", "restart", "agency-dispatch.timer"],
                        capture_output=True, text=True, timeout=10,
                    )
        except (ValueError, TypeError):
            pass

    save_config(config)
    reload_groups()
    # Redirect back to dispatch page if interval was changed, otherwise settings
    redirect = "/admin/dispatch" if dispatch_interval_raw else "/admin/"
    return RedirectResponse(redirect, status_code=303)


@app.post("/admin/dispatch/install", response_class=HTMLResponse)
async def admin_dispatch_install(request: Request):
    """Install the dispatch systemd timer."""
    error = install_dispatch()
    if error:
        return templates.TemplateResponse("admin_dispatch.html", {
            "request": request,
            **admin_context("dispatch", dispatch_error=error),
        })
    return RedirectResponse("/admin/dispatch", status_code=303)


@app.get("/admin/orgs/new", response_class=HTMLResponse)
async def admin_org_new(request: Request):
    """Create new org form."""
    agency = get_agency_config()
    config = load_config()
    return templates.TemplateResponse("admin_org_edit.html", {
        "request": request,
        "agency_title": agency.get("title", "Agency"),
        "admin_active": True,
        "active": "admin",
        "admin_page": "groups",
        "groups": {k: v["name"] for k, v in config.get("groups", {}).items()},
        "mode": "create",
        "org_key": "",
        "org_name": "",
        "org_path": "",
        "org_agents": "",
        "org_tmux_config": "",
        "agent_infos": [],
        "warning": "",
    })


@app.post("/admin/orgs/create", response_class=HTMLResponse)
async def admin_org_create(request: Request):
    """Create a new org."""
    form = await request.form()
    key = form.get("key", "").strip().lower().replace(" ", "-")
    name = form.get("name", "").strip()
    path = form.get("path", "").strip()
    agents_raw = form.get("agents", "").strip()
    agents = [a.strip() for a in agents_raw.splitlines() if a.strip()]
    tmux_config = form.get("tmux_config", "").strip()

    if not key or not name or not path:
        agency = get_agency_config()
        config = load_config()
        return templates.TemplateResponse("admin_org_edit.html", {
            "request": request,
            "agency_title": agency.get("title", "Agency"),
            "admin_active": True,
            "active": "admin",
            "groups": {k: v["name"] for k, v in config.get("groups", {}).items()},
            "mode": "create",
            "org_key": key,
            "org_name": name,
            "org_path": path,
            "org_agents": agents_raw,
            "org_tmux_config": tmux_config,
            "agent_infos": [],
            "warning": "Key, name, and path are required.",
        })

    config = load_config()
    if "groups" not in config:
        config["groups"] = {}

    warning = ""
    if not Path(path).exists():
        warning = f"Warning: Path {path} does not exist on disk. You can create it later via Initialize."

    group_cfg = {
        "name": name,
        "path": path,
        "agents": agents,
    }
    if tmux_config:
        group_cfg["tmux_config"] = tmux_config
    config["groups"][key] = group_cfg

    save_config(config)
    reload_groups()

    if warning:
        agency = get_agency_config()
        return templates.TemplateResponse("admin_org_edit.html", {
            "request": request,
            "agency_title": agency.get("title", "Agency"),
            "admin_active": True,
            "active": "admin",
            "groups": {k: v["name"] for k, v in config.get("groups", {}).items()},
            "mode": "edit",
            "org_key": key,
            "org_name": name,
            "org_path": path,
            "org_agents": "\n".join(agents),
            "org_tmux_config": tmux_config,
            "agent_infos": [get_agent_info(Path(path), a) for a in agents] if Path(path).exists() else [],
            "warning": warning + " Org saved successfully.",
        })

    return RedirectResponse("/admin/groups", status_code=303)


@app.get("/admin/orgs/{org}/edit", response_class=HTMLResponse)
async def admin_org_edit(request: Request, org: str):
    """Edit org form."""
    config = load_config()
    groups = config.get("groups", {})
    if org not in groups:
        raise HTTPException(404, f"Unknown org: {org}")

    g = groups[org]
    agency = get_agency_config()
    base = Path(g["path"])

    # Build rich agent info
    agent_infos = [get_agent_info(base, a) for a in g.get("agents", [])]

    # Dispatch config for this group
    dispatch_cfg = g.get("dispatch", {})
    prompts = []
    prompts_dir = Path(g["path"]) / "shared" / "prompts"
    if prompts_dir.exists():
        prompts = sorted(f.name for f in prompts_dir.glob("*.md"))

    return templates.TemplateResponse("admin_org_edit.html", {
        "request": request,
        "agency_title": agency.get("title", "Agency"),
        "admin_active": True,
        "active": "admin",
        "admin_page": "groups",
        "groups": {k: v["name"] for k, v in groups.items()},
        "mode": "edit",
        "org_key": org,
        "org_name": g["name"],
        "org_path": g["path"],
        "org_agents": "\n".join(g.get("agents", [])),
        "org_tmux_config": g.get("tmux_config", ""),
        "agent_infos": agent_infos,
        "dispatch_enabled": dispatch_cfg.get("enabled", False),
        "dispatch_timeout": dispatch_cfg.get("timeout", 300),
        "dispatch_daily_limit": dispatch_cfg.get("daily_limit", 20),
        "dispatch_agents": dispatch_cfg.get("agents", {}),
        "dispatch_installed": CONFIG.get("agency", {}).get("dispatch", {}).get("installed", False),
        "available_prompts": prompts,
        "warning": "",
    })


@app.post("/admin/orgs/{org}/save", response_class=HTMLResponse)
async def admin_org_save(request: Request, org: str):
    """Save org changes."""
    form = await request.form()
    name = form.get("name", "").strip()
    path = form.get("path", "").strip()
    agents_raw = form.get("agents", "").strip()
    agents = [a.strip() for a in agents_raw.splitlines() if a.strip()]
    tmux_config = form.get("tmux_config", "").strip()

    config = load_config()
    if org not in config.get("groups", {}):
        raise HTTPException(404, f"Unknown org: {org}")

    warning = ""
    if path and not Path(path).exists():
        warning = f"Warning: Path {path} does not exist on disk."

    config["groups"][org]["name"] = name or config["groups"][org]["name"]
    if path:
        config["groups"][org]["path"] = path
    config["groups"][org]["agents"] = agents
    if tmux_config:
        config["groups"][org]["tmux_config"] = tmux_config
    elif "tmux_config" in config["groups"][org]:
        del config["groups"][org]["tmux_config"]

    save_config(config)
    reload_groups()

    if warning:
        agency = get_agency_config()
        return templates.TemplateResponse("admin_org_edit.html", {
            "request": request,
            "agency_title": agency.get("title", "Agency"),
            "admin_active": True,
            "active": "admin",
            "groups": {k: v["name"] for k, v in config.get("groups", {}).items()},
            "mode": "edit",
            "org_key": org,
            "org_name": config["groups"][org]["name"],
            "org_path": config["groups"][org]["path"],
            "org_agents": "\n".join(agents),
            "org_tmux_config": tmux_config,
            "agent_infos": [get_agent_info(Path(config["groups"][org]["path"]), a) for a in agents],
            "dispatch_enabled": config["groups"][org].get("dispatch", {}).get("enabled", False),
            "dispatch_timeout": config["groups"][org].get("dispatch", {}).get("timeout", 300),
            "dispatch_daily_limit": config["groups"][org].get("dispatch", {}).get("daily_limit", 20),
            "dispatch_agents": config["groups"][org].get("dispatch", {}).get("agents", {}),
            "dispatch_installed": CONFIG.get("agency", {}).get("dispatch", {}).get("installed", False),
            "available_prompts": sorted(f.name for f in (Path(path) / "shared" / "prompts").glob("*.md")) if (Path(path) / "shared" / "prompts").exists() else [],
            "warning": warning + " Changes saved.",
        })

    return RedirectResponse("/admin/", status_code=303)


@app.post("/admin/orgs/{org}/dispatch", response_class=HTMLResponse)
async def admin_org_dispatch_save(request: Request, org: str):
    """Save dispatch config for an org."""
    config = load_config()
    if org not in config.get("groups", {}):
        raise HTTPException(404, f"Unknown org: {org}")

    form = await request.form()
    enabled = form.get("enabled") == "on"
    timeout = int(form.get("timeout", 300))
    daily_limit = int(form.get("daily_limit", 20))

    g = config["groups"][org]
    agents_list = g.get("agents", [])

    agents_dispatch = {}
    for agent in agents_list:
        rules = []
        idx = 0
        while True:
            rule_type = form.get(f"rule_type_{agent}_{idx}")
            if rule_type is None:
                break
            rule_value = form.get(f"rule_value_{agent}_{idx}", "").strip()
            rule_prompt = form.get(f"rule_prompt_{agent}_{idx}", "").strip()
            if rule_value and rule_prompt:
                rule = {"prompt": rule_prompt}
                if rule_type == "at":
                    rule["at"] = rule_value
                else:
                    rule["every"] = rule_value
                rules.append(rule)
            idx += 1
        if rules:
            agents_dispatch[agent] = rules

    config["groups"][org]["dispatch"] = {
        "enabled": enabled,
        "timeout": timeout,
        "daily_limit": daily_limit,
        "agents": agents_dispatch,
    }

    save_config(config)
    reload_groups()
    return RedirectResponse(f"/admin/orgs/{org}/edit", status_code=303)


@app.post("/admin/orgs/{org}/delete", response_class=HTMLResponse)
async def admin_org_delete(request: Request, org: str):
    """Remove org from config (does not delete files on disk)."""
    config = load_config()
    if org not in config.get("groups", {}):
        raise HTTPException(404, f"Unknown org: {org}")

    del config["groups"][org]

    # If the deleted group was the default, update default
    agency = config.get("agency", {})
    if agency.get("default_group") == org:
        remaining = list(config.get("groups", {}).keys())
        agency["default_group"] = remaining[0] if remaining else ""
        config["agency"] = agency

    save_config(config)
    reload_groups()
    return RedirectResponse("/admin/groups", status_code=303)


@app.post("/admin/orgs/{org}/initialize", response_class=HTMLResponse)
async def admin_org_initialize(request: Request, org: str):
    """Create the folder structure for an org. Idempotent."""
    config = load_config()
    if org not in config.get("groups", {}):
        raise HTTPException(404, f"Unknown org: {org}")

    g = config["groups"][org]
    base = Path(g["path"])

    # Create base dir if needed
    base.mkdir(parents=True, exist_ok=True)

    # Create shared structure
    shared = base / "shared"
    for subdir in ["clues", "curiosities", "decisions", "prompts", "logs"]:
        (shared / subdir).mkdir(parents=True, exist_ok=True)

    # Create shared memory.md if it doesn't exist
    memory_path = shared / "memory.md"
    if not memory_path.exists():
        memory_path.write_text(f"# {g['name']} — Shared Memory\n\nCollective knowledge and decisions.\n")

    # Copy _clue-system-steps.md from an existing group if available
    clue_steps_target = shared / "prompts" / "_clue-system-steps.md"
    if not clue_steps_target.exists():
        # Try to find an existing one to copy
        for other_key, other_g in config.get("groups", {}).items():
            if other_key == org:
                continue
            source = Path(other_g["path"]) / "shared" / "prompts" / "_clue-system-steps.md"
            if source.exists():
                shutil.copy2(source, clue_steps_target)
                break

    # Create agent directories
    for agent in g.get("agents", []):
        (base / agent).mkdir(exist_ok=True)

    return RedirectResponse("/admin/groups", status_code=303)


@app.post("/admin/orgs/{org}/autodetect", response_class=HTMLResponse)
async def admin_org_autodetect(request: Request, org: str):
    """Auto-detect agents by scanning for directories containing CLAUDE.md."""
    config = load_config()
    if org not in config.get("groups", {}):
        raise HTTPException(404, f"Unknown org: {org}")

    g = config["groups"][org]
    base = Path(g["path"])
    agency = get_agency_config()

    detected = []
    if base.exists():
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name != "shared" and not d.name.startswith("."):
                if (d / "CLAUDE.md").exists():
                    detected.append(d.name)

    # Update config with detected agents
    agent_names = detected if detected else g.get("agents", [])
    if detected:
        config["groups"][org]["agents"] = detected
        save_config(config)
        reload_groups()

    agent_infos = [get_agent_info(base, a) for a in agent_names]

    return templates.TemplateResponse("admin_org_edit.html", {
        "request": request,
        "agency_title": agency.get("title", "Agency"),
        "admin_active": True,
        "active": "admin",
        "admin_page": "groups",
        "groups": {k: v["name"] for k, v in config.get("groups", {}).items()},
        "mode": "edit",
        "org_key": org,
        "org_name": g["name"],
        "org_path": g["path"],
        "org_agents": "\n".join(agent_names),
        "agent_infos": agent_infos,
        "warning": f"Auto-detected {len(detected)} agents." if detected else "No agents with CLAUDE.md found in path.",
    })


# ── Agent CRUD Routes ────────────────────────────────────────────────────────


def get_agent_info(base: Path, agent_name: str) -> dict:
    """Gather filesystem info about an individual agent."""
    agent_dir = base / agent_name
    info = {
        "name": agent_name,
        "dir_exists": agent_dir.is_dir(),
        "has_claude_md": (agent_dir / "CLAUDE.md").exists(),
        "has_memory": (agent_dir / "memory.md").exists(),
        "has_mcp": (agent_dir / ".mcp.json").exists(),
        "files": [],
    }
    if agent_dir.is_dir():
        info["files"] = sorted(f.name for f in agent_dir.iterdir() if f.is_file())
    return info


@app.get("/admin/orgs/{org}/agents/{agent}", response_class=HTMLResponse)
async def admin_agent_detail(request: Request, org: str, agent: str):
    """View/edit an individual agent."""
    config = load_config()
    groups = config.get("groups", {})
    if org not in groups:
        raise HTTPException(404, f"Unknown org: {org}")

    g = groups[org]
    base = Path(g["path"])
    agency = get_agency_config()

    if agent not in g.get("agents", []):
        raise HTTPException(404, f"Agent '{agent}' not in group '{org}'")

    agent_info = get_agent_info(base, agent)
    agent_dir = base / agent

    # Read editable files
    claude_md = ""
    memory_md = ""
    if agent_dir.is_dir():
        claude_path = agent_dir / "CLAUDE.md"
        memory_path = agent_dir / "memory.md"
        if claude_path.exists():
            claude_md = claude_path.read_text()
        if memory_path.exists():
            memory_md = memory_path.read_text()

    return templates.TemplateResponse("admin_agent_detail.html", {
        "request": request,
        "agency_title": agency.get("title", "Agency"),
        "admin_active": True,
        "active": "admin",
        "admin_page": "groups",
        "groups": {k: v["name"] for k, v in groups.items()},
        "org_key": org,
        "org_name": g["name"],
        "agent": agent_info,
        "claude_md": claude_md,
        "memory_md": memory_md,
        "warning": "",
    })


@app.post("/admin/orgs/{org}/agents/{agent}/save", response_class=HTMLResponse)
async def admin_agent_save(request: Request, org: str, agent: str):
    """Save agent CLAUDE.md and/or memory.md."""
    config = load_config()
    groups = config.get("groups", {})
    if org not in groups:
        raise HTTPException(404, f"Unknown org: {org}")

    g = groups[org]
    base = Path(g["path"])
    agent_dir = base / agent

    # Security: validate path
    agent_dir.resolve().relative_to(base.resolve())

    form = await request.form()
    file_type = form.get("file_type", "claude_md")
    content = form.get("content", "")

    # Create agent dir if it doesn't exist
    agent_dir.mkdir(parents=True, exist_ok=True)

    if file_type == "claude_md":
        (agent_dir / "CLAUDE.md").write_text(content)
    elif file_type == "memory_md":
        (agent_dir / "memory.md").write_text(content)

    return RedirectResponse(f"/admin/orgs/{org}/agents/{agent}", status_code=303)


@app.post("/admin/orgs/{org}/agents/create", response_class=HTMLResponse)
async def admin_agent_create(request: Request, org: str):
    """Add a new agent to a group."""
    config = load_config()
    if org not in config.get("groups", {}):
        raise HTTPException(404, f"Unknown org: {org}")

    form = await request.form()
    agent_name = form.get("name", "").strip().lower().replace(" ", "-")
    agent_name = re.sub(r"[^a-z0-9\-]", "", agent_name)

    if not agent_name:
        return RedirectResponse(f"/admin/orgs/{org}/edit", status_code=303)

    g = config["groups"][org]
    agents = g.get("agents", [])

    if agent_name not in agents:
        agents.append(agent_name)
        config["groups"][org]["agents"] = agents
        save_config(config)
        reload_groups()

    # Create directory + scaffold
    base = Path(g["path"])
    agent_dir = base / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    claude_path = agent_dir / "CLAUDE.md"
    if not claude_path.exists():
        claude_path.write_text(f"# {agent_name.replace('-', ' ').title()} Agent\n\nRole definition goes here.\n")
    memory_path = agent_dir / "memory.md"
    if not memory_path.exists():
        memory_path.write_text(f"# {agent_name.replace('-', ' ').title()} Memory\n\n")

    return RedirectResponse(f"/admin/orgs/{org}/edit", status_code=303)


@app.post("/admin/orgs/{org}/agents/{agent}/rename", response_class=HTMLResponse)
async def admin_agent_rename(request: Request, org: str, agent: str):
    """Rename an agent (config + directory)."""
    config = load_config()
    if org not in config.get("groups", {}):
        raise HTTPException(404, f"Unknown org: {org}")

    form = await request.form()
    new_name = form.get("new_name", "").strip().lower().replace(" ", "-")
    new_name = re.sub(r"[^a-z0-9\-]", "", new_name)

    if not new_name or new_name == agent:
        return RedirectResponse(f"/admin/orgs/{org}/agents/{agent}", status_code=303)

    g = config["groups"][org]
    agents = g.get("agents", [])
    base = Path(g["path"])

    # Update config
    if agent in agents:
        idx = agents.index(agent)
        agents[idx] = new_name
        config["groups"][org]["agents"] = agents
        save_config(config)
        reload_groups()

    # Rename directory if it exists
    old_dir = base / agent
    new_dir = base / new_name
    if old_dir.is_dir() and not new_dir.exists():
        old_dir.rename(new_dir)

    return RedirectResponse(f"/admin/orgs/{org}/agents/{new_name}", status_code=303)


@app.post("/admin/orgs/{org}/agents/{agent}/delete", response_class=HTMLResponse)
async def admin_agent_delete(request: Request, org: str, agent: str):
    """Remove an agent from config. Optionally delete directory."""
    config = load_config()
    if org not in config.get("groups", {}):
        raise HTTPException(404, f"Unknown org: {org}")

    form = await request.form()
    delete_files = form.get("delete_files", "") == "true"

    g = config["groups"][org]
    agents = g.get("agents", [])

    # Remove from config
    if agent in agents:
        agents.remove(agent)
        config["groups"][org]["agents"] = agents
        save_config(config)
        reload_groups()

    # Optionally delete directory
    if delete_files:
        agent_dir = Path(g["path"]) / agent
        agent_dir.resolve().relative_to(Path(g["path"]).resolve())
        if agent_dir.is_dir():
            shutil.rmtree(agent_dir)

    return RedirectResponse(f"/admin/orgs/{org}/edit", status_code=303)


# ── Group Routes ──────────────────────────────────────────────────────────────


@app.get("/{group}/agents", response_class=HTMLResponse)
async def agents_list(request: Request, group: str):
    """List all agents with identity and health info."""
    g = get_group(group)
    agents, subagents = collect_agents_with_identity(g)
    return templates.TemplateResponse("agents.html", {
        "request": request,
        **group_context(g),
        "agents": agents,
        "subagents": subagents,
    })


@app.get("/{group}/agents/{agent}", response_class=HTMLResponse)
async def agent_profile(request: Request, group: str, agent: str):
    """View an agent's profile with identity, logs, clues, and memory."""
    g = get_group(group)
    agent_dir = resolve_agent_dir(g, agent)
    identity = parse_agent_identity(agent_dir)
    is_subagent = (g["path"] / "_subagents" / agent).is_dir() or identity["frontmatter"].get("subagent", False)
    last_seen = get_agent_last_seen(g, agent)
    logs = get_agent_logs(g, agent)
    all_clues = list_clues(g)
    agent_clues = [c for c in all_clues if c.get("agent") == agent]
    clues = agent_clues[:10]
    timeline = build_agent_timeline(g, agent, agent_clues=agent_clues)
    has_headshot = find_headshot(agent_dir) is not None
    has_memory = (agent_dir / "memory.md").exists()
    memory_path = str(agent_dir / "memory.md") if has_memory else ""

    # Get dispatch schedule for this agent
    group_cfg = GROUPS.get(g["key"], {})
    dispatch_cfg = group_cfg.get("dispatch", {})
    agent_schedule = dispatch_cfg.get("agents", {}).get(agent, [])
    dispatch_enabled = dispatch_cfg.get("enabled", False)

    return templates.TemplateResponse("agent_profile.html", {
        "request": request,
        **group_context(g),
        "agent": agent,
        "identity": identity,
        "is_subagent": is_subagent,
        "last_seen": last_seen,
        "logs": logs,
        "clues": clues,
        "timeline": timeline,
        "has_headshot": has_headshot,
        "has_memory": has_memory,
        "memory_path": memory_path,
        "agent_schedule": agent_schedule,
        "dispatch_enabled": dispatch_enabled,
    })


@app.post("/{group}/agents/{agent}/identity", response_class=HTMLResponse)
async def agent_save_identity(request: Request, group: str, agent: str):
    """Save identity fields to CLAUDE.md frontmatter."""
    g = get_group(group)
    agent_dir = resolve_agent_dir(g, agent)
    form = await request.form()
    fields = {
        "display_name": form.get("display_name", "").strip(),
        "title": form.get("title", "").strip(),
        "emoji": form.get("emoji", "").strip(),
    }
    save_agent_identity(agent_dir, fields)
    return RedirectResponse(f"/{group}/agents/{agent}", status_code=303)


@app.post("/{group}/agents/{agent}/definition", response_class=HTMLResponse)
async def agent_save_definition(request: Request, group: str, agent: str):
    """Save CLAUDE.md body preserving frontmatter."""
    g = get_group(group)
    agent_dir = resolve_agent_dir(g, agent)
    form = await request.form()
    body = form.get("body", "")
    save_agent_definition(agent_dir, body)
    return RedirectResponse(f"/{group}/agents/{agent}", status_code=303)


@app.post("/{group}/agents/{agent}/upload-headshot", response_class=HTMLResponse)
async def agent_upload_headshot(request: Request, group: str, agent: str):
    """Upload a headshot image for an agent."""
    g = get_group(group)
    agent_dir = resolve_agent_dir(g, agent)
    form = await request.form()
    upload = form.get("headshot")
    if not upload or not hasattr(upload, 'filename') or not upload.filename:
        return RedirectResponse(f"/{group}/agents/{agent}", status_code=303)
    ext = Path(upload.filename).suffix.lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "webp"):
        raise HTTPException(400, "Invalid image format. Use PNG, JPG, or WebP.")
    content = await upload.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(400, "Image too large. Maximum 2MB.")
    # Remove any existing headshots
    for old_ext in ("png", "jpg", "jpeg", "webp"):
        old = agent_dir / f"headshot.{old_ext}"
        if old.exists():
            old.unlink()
    (agent_dir / f"headshot.{ext}").write_bytes(content)
    return RedirectResponse(f"/{group}/agents/{agent}", status_code=303)


@app.get("/{group}/agents/{agent}/headshot")
async def agent_headshot(group: str, agent: str):
    """Serve an agent's headshot image."""
    g = get_group(group)
    agent_dir = resolve_agent_dir(g, agent)
    headshot = find_headshot(agent_dir)
    if not headshot:
        raise HTTPException(404, "No headshot")
    return FileResponse(headshot)


@app.post("/{group}/agents/{agent}/toggle-subagent", response_class=HTMLResponse)
async def agent_toggle_subagent(request: Request, group: str, agent: str):
    """Toggle an agent between regular and subagent status."""
    g = get_group(group)
    if "/" in agent or ".." in agent:
        raise HTTPException(400, "Invalid agent name")
    root_dir = g["path"] / agent
    sub_dir = g["path"] / "_subagents" / agent
    is_currently_subagent = sub_dir.is_dir()

    config = load_config()
    group_config = config["groups"][g["key"]]

    if is_currently_subagent:
        if root_dir.exists():
            raise HTTPException(409, f"Cannot move: {root_dir} already exists")
        shutil.move(str(sub_dir), str(root_dir))
        if agent not in group_config.get("agents", []):
            group_config.setdefault("agents", []).append(agent)
    else:
        if not root_dir.is_dir():
            raise HTTPException(404, f"Agent directory not found: {agent}")
        if sub_dir.exists():
            raise HTTPException(409, f"Cannot move: {sub_dir} already exists")
        (g["path"] / "_subagents").mkdir(exist_ok=True)
        shutil.move(str(root_dir), str(sub_dir))
        if agent in group_config.get("agents", []):
            group_config["agents"].remove(agent)

    save_config(config)
    reload_groups()
    return RedirectResponse(f"/{group}/agents/{agent}", status_code=303)


@app.get("/{group}/", response_class=HTMLResponse)
async def home(request: Request, group: str):
    """Dashboard home — inbox of items needing attention."""
    g = get_group(group)
    clues = list_clues(g)
    curiosities = list_curiosities(g)
    decisions = list_decisions(g)

    open_clues = [c for c in clues if c.get("status") in ("open",)]
    floated_clues = [c for c in clues if c.get("float")]
    actionable_curiosities = [c for c in curiosities if c.get("status") in ("proposed", "investigating")]

    return templates.TemplateResponse("home.html", {
        "request": request,
        **group_context(g, clues=clues, curiosities=curiosities),
        "open_clues": open_clues,
        "floated_clues": floated_clues,
        "actionable_curiosities": actionable_curiosities,
        "recent_decisions": decisions[:5],
        "total_clues": len(clues),
        "total_curiosities": len(curiosities),
        "total_decisions": len(decisions),
        "now": datetime.now().strftime("%B %d, %Y"),
    })


@app.get("/{group}/clues", response_class=HTMLResponse)
async def clues_list(request: Request, group: str, agent: str = "", status: str = ""):
    """List all clues with optional filtering."""
    g = get_group(group)
    clues = list_clues(g)
    filtered = clues
    if agent:
        filtered = [c for c in filtered if c.get("agent") == agent]
    if status:
        filtered = [c for c in filtered if c.get("status") == status]
    return templates.TemplateResponse("clues.html", {
        "request": request,
        **group_context(g, clues=clues),
        "clues": filtered,
        "filter_agent": agent,
        "filter_status": status,
        "agents": g["agents"],
    })


@app.get("/{group}/clues/{slug}", response_class=HTMLResponse)
async def clue_detail(request: Request, group: str, slug: str):
    """View a single clue."""
    g = get_group(group)
    path = g["shared"] / "clues" / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Clue not found")
    raw = path.read_text()
    meta, body = parse_frontmatter(raw)

    # Resolve pipeline chain: clue → curiosity → decision
    pipeline = None
    linked_curiosity_slug = meta.get("linked_curiosity", "")
    if linked_curiosity_slug:
        curiosity_slug = linked_curiosity_slug.replace(".md", "")
        curiosity_path = g["shared"] / "curiosities" / f"{curiosity_slug}.md"
        pipeline = {"curiosity_slug": curiosity_slug, "curiosity_exists": curiosity_path.exists()}
        # Check for a decision on that curiosity
        decision_path = g["shared"] / "decisions" / f"{curiosity_slug}.md"
        if decision_path.exists():
            dmeta, _ = parse_frontmatter(decision_path.read_text())
            pipeline["decision_slug"] = curiosity_slug
            pipeline["decision_status"] = dmeta.get("decision", "")
        else:
            pipeline["decision_slug"] = None

    return templates.TemplateResponse("clue_detail.html", {
        "request": request,
        **group_context(g),
        "meta": meta,
        "body_html": render_md(body),
        "body_raw": body,
        "slug": slug,
        "filename": path.name,
        "pipeline": pipeline,
    })


@app.post("/{group}/clues/{slug}/status", response_class=HTMLResponse)
async def clue_update_status(request: Request, group: str, slug: str):
    """Update a clue's status via form submission."""
    g = get_group(group)
    path = g["shared"] / "clues" / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Clue not found")

    form = await request.form()
    new_status = form.get("status", "")
    if new_status not in ("open", "connected", "dismissed", "archived"):
        raise HTTPException(400, "Invalid status")

    update_frontmatter_field(path, "status", new_status)

    return RedirectResponse(f"/{group}/clues/{slug}", status_code=303)


@app.get("/{group}/curiosities", response_class=HTMLResponse)
async def curiosities_list(request: Request, group: str):
    """List all curiosities."""
    g = get_group(group)
    items = list_curiosities(g)
    return templates.TemplateResponse("curiosities.html", {
        "request": request,
        **group_context(g),
        "curiosities": items,
    })


@app.get("/{group}/curiosities/{slug}", response_class=HTMLResponse)
async def curiosity_detail(request: Request, group: str, slug: str):
    """View a single curiosity."""
    g = get_group(group)
    curiosities_dir = g["shared"] / "curiosities"
    clues_dir = g["shared"] / "clues"
    decisions_dir = g["shared"] / "decisions"

    path = curiosities_dir / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Curiosity not found")
    raw = path.read_text()
    meta, body = parse_frontmatter(raw)

    # Find linked clues
    linked = []
    for c in meta.get("clues", []):
        cpath = clues_dir / c
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

    # Sync curiosity status if a decision exists but status is stale
    # (handles decisions created outside Agency, e.g., by agents directly)
    if decision and meta.get("status") not in ("approved", "deferred", "rejected"):
        new_status = decision["meta"].get("decision", "approved")
        update_frontmatter_field(path, "status", new_status)
        meta["status"] = new_status

    return templates.TemplateResponse("curiosity_detail.html", {
        "request": request,
        **group_context(g),
        "meta": meta,
        "body_html": render_md(body),
        "body_raw": body,
        "slug": slug,
        "linked_clues": linked,
        "decision": decision,
    })


@app.post("/{group}/curiosities/{slug}/decide", response_class=HTMLResponse)
async def curiosity_decide(request: Request, group: str, slug: str):
    """Create a decision for a curiosity."""
    g = get_group(group)
    decisions_dir = g["shared"] / "decisions"
    curiosities_dir = g["shared"] / "curiosities"

    form = await request.form()
    decision_text = form.get("decision", "approved")
    notes = form.get("notes", "")

    agency_cfg = get_agency_config()
    decided_by = agency_cfg.get("decided_by", "admin")
    today = datetime.now().strftime("%Y-%m-%d")
    decision_content = f"""---
curiosity: {slug}.md
decided_by: {decided_by}
date: {today}
decision: {decision_text}
---

{notes}
"""
    decisions_dir.mkdir(exist_ok=True)
    decision_path = decisions_dir / f"{slug}.md"
    decision_path.write_text(decision_content)

    # Update curiosity status
    cpath = curiosities_dir / f"{slug}.md"
    if cpath.exists():
        update_frontmatter_field(cpath, "status", decision_text)

    return RedirectResponse(f"/{group}/curiosities/{slug}", status_code=303)


@app.get("/{group}/decisions", response_class=HTMLResponse)
async def decisions_list(request: Request, group: str):
    """List all decisions."""
    g = get_group(group)
    items = list_decisions(g)
    return templates.TemplateResponse("decisions.html", {
        "request": request,
        **group_context(g),
        "decisions": items,
    })


@app.get("/{group}/decisions/{slug}", response_class=HTMLResponse)
async def decision_detail(request: Request, group: str, slug: str):
    """View a single decision."""
    g = get_group(group)
    path = g["shared"] / "decisions" / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Decision not found")
    raw = path.read_text()
    meta, body = parse_frontmatter(raw)

    # Resolve pipeline chain: clues → curiosity → this decision
    pipeline_clues = []
    curiosity_slug = (meta.get("curiosity", "") or "").replace(".md", "")
    if curiosity_slug:
        curiosity_path = g["shared"] / "curiosities" / f"{curiosity_slug}.md"
        if curiosity_path.exists():
            cmeta, _ = parse_frontmatter(curiosity_path.read_text())
            for clue_file in cmeta.get("clues", []):
                clue_slug = clue_file.replace(".md", "")
                clue_path = g["shared"] / "clues" / clue_file
                if clue_path.exists():
                    pipeline_clues.append({"slug": clue_slug, "filename": clue_file})

    return templates.TemplateResponse("decision_detail.html", {
        "request": request,
        **group_context(g),
        "meta": meta,
        "body_html": render_md(body),
        "slug": slug,
        "pipeline_clues": pipeline_clues,
        "curiosity_slug": curiosity_slug,
    })


@app.get("/{group}/documents", response_class=HTMLResponse)
async def documents_list(request: Request, group: str, agent: str = ""):
    """Browse documents by agent."""
    g = get_group(group)
    docs = collect_documents(g)
    if agent:
        docs = [d for d in docs if d["agent"] == agent]
    by_agent = {}
    for d in docs:
        by_agent.setdefault(d["agent"], []).append(d)
    return templates.TemplateResponse("documents.html", {
        "request": request,
        **group_context(g),
        "by_agent": by_agent,
        "filter_agent": agent,
        "agents": g["agents"] + ["shared"],
    })


@app.get("/{group}/documents/view", response_class=HTMLResponse)
async def document_view(request: Request, group: str, path: str):
    """View a document file."""
    g = get_group(group)
    fpath = Path(path)
    # Security: must be under this group's agents dir
    validate_file_access(fpath, g["path"])
    if not fpath.exists():
        raise HTTPException(404, "File not found")

    raw = fpath.read_text()
    suffix = fpath.suffix.lower()

    content_html = ""
    is_csv = False
    csv_headers = []
    csv_rows = []

    if suffix == ".csv":
        is_csv = True
        csv_headers, csv_rows = parse_csv_to_rows(raw)
    elif suffix == ".html":
        content_html = raw
    elif suffix == ".md":
        _, body = parse_frontmatter(raw)
        content_html = render_md(body)
    else:
        content_html = f"<pre class='whitespace-pre-wrap text-sm'>{raw}</pre>"

    return templates.TemplateResponse("document_view.html", {
        "request": request,
        **group_context(g),
        "filename": fpath.name,
        "filepath": str(fpath),
        "raw": raw,
        "content_html": content_html,
        "is_csv": is_csv,
        "csv_headers": csv_headers,
        "csv_rows": csv_rows,
        "suffix": suffix,
        "is_editable": suffix in (".md", ".csv"),
    })


@app.post("/{group}/documents/save", response_class=HTMLResponse)
async def document_save(request: Request, group: str):
    """Save edits to a document."""
    g = get_group(group)
    form = await request.form()
    path = form.get("path", "")
    content = form.get("content", "")
    fpath = Path(path)

    validate_file_access(fpath, g["path"])

    fpath.write_text(content)
    return RedirectResponse(f"/{group}/documents/view?path={path}", status_code=303)


@app.get("/{group}/logs", response_class=HTMLResponse)
async def logs_list(request: Request, group: str):
    """Browse execution logs by date."""
    g = get_group(group)
    logs = collect_logs(g)
    return templates.TemplateResponse("logs.html", {
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

    return templates.TemplateResponse("log_view.html", {
        "request": request,
        **group_context(g),
        "filename": fpath.name,
        "content_html": content_html,
        "raw": raw,
    })


@app.get("/{group}/prompts", response_class=HTMLResponse)
async def prompts_list(request: Request, group: str):
    """Browse and edit agent prompts."""
    g = get_group(group)
    items = collect_prompts(g)
    return templates.TemplateResponse("prompts.html", {
        "request": request,
        **group_context(g),
        "prompts": items,
    })


@app.get("/{group}/prompts/{slug}", response_class=HTMLResponse)
async def prompt_detail(request: Request, group: str, slug: str):
    """View/edit a prompt."""
    g = get_group(group)
    path = g["shared"] / "prompts" / f"{slug}.md"
    if not path.exists():
        raise HTTPException(404, "Prompt not found")
    raw = path.read_text()
    content_html = render_md(raw)
    return templates.TemplateResponse("prompt_detail.html", {
        "request": request,
        **group_context(g),
        "slug": slug,
        "raw": raw,
        "content_html": content_html,
    })


@app.post("/{group}/prompts/{slug}/save", response_class=HTMLResponse)
async def prompt_save(request: Request, group: str, slug: str):
    """Save edits to a prompt."""
    g = get_group(group)
    path = g["shared"] / "prompts" / f"{slug}.md"
    form = await request.form()
    content = form.get("content", "")
    path.write_text(content)
    return RedirectResponse(f"/{group}/prompts/{slug}", status_code=303)


@app.get("/{group}/memory", response_class=HTMLResponse)
async def memory_list(request: Request, group: str):
    """Browse and edit agent memory files."""
    g = get_group(group)
    items = collect_memory_files(g)
    return templates.TemplateResponse("memory.html", {
        "request": request,
        **group_context(g),
        "memory_files": items,
    })


@app.get("/{group}/memory/view", response_class=HTMLResponse)
async def memory_view(request: Request, group: str, path: str):
    """View/edit a memory file."""
    g = get_group(group)
    fpath = Path(path)
    validate_file_access(fpath, g["path"])
    if not fpath.exists():
        raise HTTPException(404, "Memory file not found")

    raw = fpath.read_text()
    content_html = render_md(raw)
    agent = fpath.parent.name

    return templates.TemplateResponse("memory_view.html", {
        "request": request,
        **group_context(g),
        "agent": agent,
        "path": str(fpath),
        "raw": raw,
        "content_html": content_html,
    })


@app.post("/{group}/memory/save", response_class=HTMLResponse)
async def memory_save(request: Request, group: str):
    """Save edits to a memory file."""
    g = get_group(group)
    form = await request.form()
    path = form.get("path", "")
    content = form.get("content", "")
    fpath = Path(path)

    validate_file_access(fpath, g["path"])

    fpath.write_text(content)
    return RedirectResponse(f"/{group}/memory/view?path={path}", status_code=303)


@app.get("/{group}/tmux-config", response_class=HTMLResponse)
async def tmux_config_view(request: Request, group: str):
    """View/edit the tmux config file for a group."""
    g = get_group(group)
    tmux_path = GROUPS.get(group, {}).get("tmux_config", "")
    if not tmux_path:
        raise HTTPException(404, "No tmux config set for this group")
    fpath = Path(tmux_path)
    if not fpath.exists():
        raise HTTPException(404, f"Tmux config file not found: {tmux_path}")
    raw = fpath.read_text()
    return templates.TemplateResponse("tmux_config.html", {
        "request": request,
        **group_context(g),
        "raw": raw,
        "filepath": tmux_path,
    })


@app.post("/{group}/tmux-config/save", response_class=HTMLResponse)
async def tmux_config_save(request: Request, group: str):
    """Save edits to the tmux config file."""
    g = get_group(group)
    tmux_path = GROUPS.get(group, {}).get("tmux_config", "")
    if not tmux_path:
        raise HTTPException(404, "No tmux config set for this group")
    form = await request.form()
    content = form.get("content", "")
    Path(tmux_path).write_text(content)
    return RedirectResponse(f"/{group}/tmux-config", status_code=303)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agency — Agent Management Dashboard")
    parser.add_argument("--port", type=int, default=8500, help="Port to serve on (default: 8500)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    args = parser.parse_args()

    # First-run: create default config
    if not CONFIG_PATH.exists():
        save_config({"agency": {"title": "Agency", "default_group": ""}, "groups": {}})
        print(f"First run — created config.yaml in {CONFIG_PATH.parent}")
        print(f"Visit http://localhost:{args.port}/admin/ to set up your first agent group.")

    reload_groups()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
