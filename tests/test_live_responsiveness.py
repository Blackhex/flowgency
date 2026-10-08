from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from flowgency import app as app_mod
from flowgency.web import team_navigation
from flowgency.web.routes import jobs as jobs_routes

DELAY = 0.6
MAX_LOOP_GAP = 0.3


async def _get_with_heartbeat(path: str) -> tuple[httpx.Response, float]:
    """One request against the app plus the longest pause its event loop suffered meanwhile."""
    stop = asyncio.Event()
    worst = 0.0

    async def beat() -> None:
        nonlocal worst
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            worst = max(worst, now - last)
            last = now

    transport = httpx.ASGITransport(app=app_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        heartbeat = asyncio.create_task(beat())
        await asyncio.sleep(0.05)
        try:
            response = await client.get(path)
        finally:
            stop.set()
            await heartbeat
    return response, worst


def _slow(label: str, seen: dict[str, threading.Thread], result):
    def run(*_args, **_kwargs):
        seen[label] = threading.current_thread()
        time.sleep(DELAY)
        return result

    return run


def _assert_responsive(path: str, seen: dict[str, threading.Thread], label: str) -> None:
    loop_thread = threading.current_thread()

    response, worst = asyncio.run(_get_with_heartbeat(path))

    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    assert label in seen, f"the delayed {label} read never ran for {path}"
    assert seen[label] is not loop_thread, f"{label} ran on the event loop thread for {path}"
    assert worst < MAX_LOOP_GAP, f"the event loop stalled for {worst:.2f}s while {label} was delayed"


def test_a_delayed_scheduler_inspection_does_not_stall_the_loop(workflow_web_env, monkeypatch):
    seen: dict[str, threading.Thread] = {}
    monkeypatch.setattr(app_mod, "get_dispatch_status", _slow("scheduler", seen, {
        "installed": False, "enabled": False, "timer_active": False, "state": "inactive",
        "config_conflict": False, "config_path": None, "interval": None, "mismatches": [],
        "definition_matches": False, "error": None,
    }))

    _assert_responsive("/admin/dispatch?__live=1", seen, "scheduler")


def test_a_delayed_log_listing_does_not_stall_the_loop(workflow_web_env, monkeypatch):
    seen: dict[str, threading.Thread] = {}
    monkeypatch.setattr(app_mod, "collect_logs", _slow("logs", seen, {}))

    _assert_responsive("/newsletter/logs?__live=1", seen, "logs")


def test_a_delayed_job_listing_does_not_stall_the_loop(workflow_web_env, monkeypatch):
    seen: dict[str, threading.Thread] = {}
    monkeypatch.setattr(jobs_routes, "_job_rows", _slow("jobs", seen, []))

    _assert_responsive("/newsletter/jobs?__live=1", seen, "jobs")


@pytest.mark.parametrize(
    "path",
    [
        "/newsletter/?__live=1",
        "/newsletter/jobs?__live=1",
        "/newsletter/logs?__live=1",
        "/newsletter/agents?__live=1",
        "/newsletter/agents/builder/profile?__live=1",
        "/newsletter/agents/builder/memory?__live=1",
        "/newsletter/agents/builder/permissions?__live=1",
        "/newsletter/workflows/board-a",
        "/newsletter/workflows/board-a/snapshot",
    ],
)
def test_delayed_navigation_ticket_enumeration_does_not_stall_the_loop(workflow_web_env, monkeypatch, path):
    seen: dict[str, threading.Thread] = {}
    monkeypatch.setattr(team_navigation, "build_workflow_nav", _slow("navigation", seen, ([], False)))

    _assert_responsive(path, seen, "navigation")
