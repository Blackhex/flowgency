from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from html.parser import HTMLParser
import math
from multiprocessing import Event, Process
from pathlib import Path
import re
import shutil
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import yaml
from fastapi.testclient import TestClient

from flowgency import app as app_mod
from flowgency.configuration import ConfigStore
from flowgency.configuration.models import MemorySelector
from flowgency.jobs.store import read_job, write_job
from flowgency.memory import resolve_memory_selector
from flowgency.tickets.models import TicketEvent
from tests._lock_helpers import hold_exclusive_lock
from tests.test_local_ticket_storage import _make_reparse
from tests.test_job_routes import _seed_app as seed_job_app, _write_job_record


_ACTIVITY_ROW_RE = re.compile(
    r'<li data-live-key="[^"]+" class="grid grid-cols-\[3rem_1\.75rem_minmax\(0,1fr\)\] gap-x-3 pb-6 last:pb-0">(.*?)</li>',
    re.S,
)


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _write_blueprint(root: Path, key: str, title: str) -> None:
    blueprint = root / key
    skill = blueprint / ".agents" / "skills" / "daily-review"
    prompt_dir = blueprint / ".agents" / "prompts"
    skill.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text(f"# {title}\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )
    (prompt_dir / "pr-review.prompt.md").write_text(
        "---\nname: pr-review\ndescription: Review\n---\n\nRun.\n",
        encoding="utf-8",
    )


def _local_triage_source(body: str = "Review local work.\n") -> str:
    return (
        "---\nname: local-triage\ndescription: Local triage.\n---\n\n"
        + body
    )


def _seed_app(monkeypatch, tmp_path, raw_config):
    raw = deepcopy(raw_config)
    source_agent = raw_config["teams"]["newsletter"].get("agents", [{}])[0]
    library_root = tmp_path / "agent-library"
    cache_root = tmp_path / "compiled-agents"
    memory_root = tmp_path / "memory-store"
    prompt_root = tmp_path / "prompts"
    team_root = tmp_path / "groups" / "newsletter"
    (tmp_path / "Research" / "editorial").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Research" / "additional").mkdir(parents=True, exist_ok=True)
    (team_root / "logs").mkdir(parents=True, exist_ok=True)
    (team_root / "observations").mkdir(parents=True, exist_ok=True)
    (team_root / "proposals").mkdir(parents=True, exist_ok=True)
    (team_root / "decisions").mkdir(parents=True, exist_ok=True)
    (team_root / "locks").mkdir(parents=True, exist_ok=True)
    _write_blueprint(library_root, "advisor", "Advisor")
    local_prompt_dir = prompt_root / "newsletter" / "advisor"
    local_prompt_dir.mkdir(parents=True, exist_ok=True)
    (local_prompt_dir / "local-triage.prompt.md").write_text(
        _local_triage_source(),
        encoding="utf-8",
    )

    raw["flowgency"]["agent_library"] = str(library_root)
    raw["flowgency"]["compilation_cache"] = str(cache_root)
    raw["flowgency"]["memory_store"] = str(memory_root)
    raw["flowgency"]["prompt_store"] = str(prompt_root)
    raw["teams"]["newsletter"]["name"] = "Newsletter"
    raw["teams"]["newsletter"]["path"] = str(team_root)
    raw["teams"]["newsletter"]["default_integration"] = "copilot"
    raw["teams"]["newsletter"]["runtime"] = {"timeout": 2400}
    raw["teams"]["newsletter"]["permissions"] = {
        "mode": "restricted",
        "rules": [
            {"path": str((tmp_path / "Research" / "editorial").resolve()), "tools": ["read", "shell", "write"]},
        ],
    }
    raw["teams"]["newsletter"]["agents"] = [
        {
            "name": "advisor",
            "blueprint": "advisor",
            "integration": "copilot",
            "integration_config": deepcopy(source_agent.get("integration_config", {})),
            "identity": {
                "display_name": "Advisor",
                "title": "Blueprint Librarian",
                "emoji": ":)",
            },
            "runtime": {"timeout": 1200},
            "permissions": {
                "rules": [
                    {"path": str((tmp_path / "Research" / "additional").resolve()), "tools": ["read", "shell", "write"]},
                    {"path": str(raw_config["teams"]["newsletter"]["workspace_path"]), "tools": ["read", "shell", "write"]},
                ],
            },
            "default_memory": {"scope": "agent"},
            "routines": [
                {
                    "id": "daily-review",
                    "prompt": {"scope": "blueprint", "name": "pr-review"},
                    "arguments": ["--brief"],
                    "schedule": {"at": "09:00"},
                    "memory": {"scope": "routine"},
                }
            ],
            "prompts": ["local-triage"],
        }
    ]

    config_path = _write_yaml(tmp_path / "config.yaml", raw)
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app), config_path


def _seed_activity_app(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    team_root = tmp_path / "groups" / "newsletter-workspace"
    raw["flowgency"]["default_team"] = "newsletter-prod"
    raw["teams"] = {
        "newsletter-prod": {
            **raw["teams"]["newsletter"],
            "path": str(team_root),
            "name": "Newsletter Prod",
            "agents": [
                {
                    **raw["teams"]["newsletter"]["agents"][0],
                    "name": "advisor",
                }
            ],
        }
    }
    raw["teams"]["newsletter-prod"]["agents"][0]["name"] = "advisor"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    team_root.joinpath("logs", "2026-07-16").mkdir(parents=True, exist_ok=True)
    team_root.joinpath("observations").mkdir(parents=True, exist_ok=True)
    team_root.joinpath("proposals").mkdir(parents=True, exist_ok=True)
    team_root.joinpath("decisions").mkdir(parents=True, exist_ok=True)
    team_root.joinpath("locks").mkdir(parents=True, exist_ok=True)
    team_root.joinpath("observations", "status.md").write_text(
        "---\nagent: advisor\nstatus: open\n---\n\nObservation.\n",
        encoding="utf-8",
    )
    log_file = team_root.joinpath("logs", "2026-07-16", "advisor-run.out")
    log_file.write_text("# log\n", encoding="utf-8")
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    return TestClient(app_mod.app), config_path, log_file


def _revision(config_path: Path) -> str:
    return ConfigStore(config_path).load().revision


class _HrefParser(HTMLParser):
    def __init__(self, label: str):
        super().__init__()
        self._label = label
        self._capture = False
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._capture = True
            self._current_href = dict(attrs).get("href", "")

    def handle_data(self, data):
        if self._capture and data.strip() == self._label:
            self.hrefs.append(self._current_href)

    def handle_endtag(self, tag):
        if tag == "a":
            self._capture = False
            self._current_href = ""


def _hrefs_for_label(body: str, label: str) -> list[str]:
    parser = _HrefParser(label)
    parser.feed(body)
    return parser.hrefs


def _activity_rows(body: str) -> list[str]:
    return _ACTIVITY_ROW_RE.findall(body)


def _activity_row(body: str, marker: str) -> str:
    for row in _activity_rows(body):
        if marker in row:
            return row
    raise AssertionError(f"No activity row contained marker {marker!r}")


def _rewrite_job(path: Path, *, spec_updates: dict[str, object] | None = None, **changes) -> None:
    record = read_job(path)
    if spec_updates:
        record.spec = replace(record.spec, **spec_updates)
        record.authority_digest = record.spec.immutable_digest()
    for field_name, value in changes.items():
        setattr(record, field_name, value)
    write_job(path, record)


def test_agent_detail_base_redirects_to_profile(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/agents/advisor/profile"


def test_agent_detail_tabs_have_stable_urls(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    for tab, label in [
        ("profile", "Profile"),
        ("blueprint", "Blueprint"),
        ("runtime", "Runtime"),
        ("permissions", "Permissions"),
        ("prompts", "Prompts"),
        ("routines", "Routines"),
        ("memory", "Memory"),
        ("activity", "Activity"),
        ("logs", "Logs"),
    ]:
        response = client.get(f"/newsletter/agents/advisor/{tab}")
        assert response.status_code == 200
        assert f'aria-current="page">{label}' in response.text


def test_agent_logs_tab_uses_execution_logs(monkeypatch, tmp_path, raw_config):
    client, config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    before = config_path.read_bytes()

    response = client.get("/newsletter-prod/agents/advisor/logs")

    assert response.status_code == 200
    assert 'aria-current="page">Logs' in response.text
    assert "Execution Logs" in response.text
    assert log_file.name in response.text
    assert "1 file" in response.text
    assert config_path.read_bytes() == before


def test_agent_logs_tab_uses_exact_empty_copy(monkeypatch, tmp_path, raw_config):
    client, config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    before = config_path.read_bytes()
    log_file.unlink()

    response = client.get("/newsletter-prod/agents/advisor/logs")

    assert response.status_code == 200
    assert "Execution Logs" in response.text
    assert "No logs found." in response.text
    assert "No logs yet" not in response.text
    assert config_path.read_bytes() == before


def test_agent_logs_tab_surfaces_job_warnings_without_hiding_readable_logs(monkeypatch, tmp_path, raw_config):
    client, config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    team_root = tmp_path / "groups" / "newsletter-workspace"
    broken_path = (tmp_path / "memory-store" / ".jobs" / "newsletter-prod" / "broken-logs.yaml")
    broken_path.parent.mkdir(parents=True, exist_ok=True)
    broken_path.write_text("not: [valid", encoding="utf-8")
    misplaced_source = _write_job_record(
        team_root,
        config_path,
        team_id="newsletter-prod",
        job_id="job-misplaced",
        status="queued",
    )
    misplaced_path = misplaced_source.with_name("misplaced.yaml")
    misplaced_path.write_bytes(misplaced_source.read_bytes())
    misplaced_source.unlink()
    before_config = config_path.read_bytes()
    before_log = log_file.read_bytes()
    before_broken = broken_path.read_bytes()
    before_misplaced = misplaced_path.read_bytes()

    response = client.get("/newsletter-prod/agents/advisor/logs")

    assert response.status_code == 200
    assert log_file.name in response.text
    assert "1 file" in response.text
    assert "Skipped unreadable job record: broken-logs.yaml" in response.text
    assert "Skipped misplaced job record: misplaced.yaml" in response.text
    assert config_path.read_bytes() == before_config
    assert log_file.read_bytes() == before_log
    assert broken_path.read_bytes() == before_broken
    assert misplaced_path.read_bytes() == before_misplaced


def test_agent_logs_tab_keeps_warnings_visible_when_no_logs_remain(monkeypatch, tmp_path, raw_config):
    client, config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    broken_path = tmp_path / "memory-store" / ".jobs" / "newsletter-prod" / "broken-logs.yaml"
    broken_path.parent.mkdir(parents=True, exist_ok=True)
    broken_path.write_text("not: [valid", encoding="utf-8")
    before_config = config_path.read_bytes()
    before_broken = broken_path.read_bytes()
    before_log = log_file.read_bytes()
    log_file.unlink()

    response = client.get("/newsletter-prod/agents/advisor/logs")

    assert response.status_code == 200
    assert "No logs found." in response.text
    assert "Skipped unreadable job record: broken-logs.yaml" in response.text
    assert config_path.read_bytes() == before_config
    assert broken_path.read_bytes() == before_broken
    assert before_log.strip() == b"# log"


def test_agent_logs_tab_is_not_truncated_to_eight_files(monkeypatch, tmp_path, raw_config):
    client, _config_path, log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    day = log_file.parent
    for index in range(8):
        day.joinpath(f"advisor-run-{index}.out").write_text(f"log {index}", encoding="utf-8")

    response = client.get("/newsletter-prod/agents/advisor/logs")

    assert response.status_code == 200
    assert "9 files" in response.text
    assert "advisor-run-7.out" in response.text


def test_profile_tab_uses_config_identity_fields(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.get("/newsletter/agents/advisor/profile")

    assert response.status_code == 200
    assert "Advisor" in response.text
    assert "Blueprint Librarian" in response.text
    assert revision in response.text
    assert "Headshot" not in response.text
    assert "Subagent" not in response.text


def test_runtime_tab_renders_team_default_timeout_and_permissions_link(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/runtime")

    assert response.status_code == 200
    assert "Timeout: 2400s" in response.text
    assert "/newsletter/agents/advisor/permissions" in response.text
    assert "permission_rules_yaml" not in response.text


def test_runtime_tab_keeps_timeout_editor_only(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/runtime")

    assert response.status_code == 200
    assert 'name="timeout"' in response.text
    assert ">Mode<" not in response.text
    assert "permission_rules_yaml" not in response.text


def test_runtime_tab_renders_copilot_local_network_consent(monkeypatch, tmp_path, raw_config):
    raw_config["teams"]["newsletter"]["agents"][0]["integration_config"] = {
        "model": "gpt-5.4"
    }
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/runtime")

    assert response.status_code == 200
    assert 'name="integration_config.allow_local_network__present"' in response.text
    assert 'name="integration_config.allow_local_network"' in response.text
    assert "Allow local-network access" in response.text
    assert "Allows connections to local services and LAN hosts, not only Flowgency." in response.text


def test_blueprint_tab_is_read_only(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/blueprint")

    assert response.status_code == 200
    assert "daily-review" in response.text
    assert "cache" in response.text.lower()
    assert "Open in Agent Library" in response.text
    assert "View skills in Agent Library" in response.text
    assert "/admin/agent-library/blueprints/advisor" in response.text
    assert "/admin/agent-library/blueprints/advisor/skills" in response.text
    assert '<form' not in response.text


def test_agent_prompts_tab_separates_shared_and_private(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/prompts")

    assert response.status_code == 200
    assert "Shared from blueprint" in response.text
    assert "Private to this instance" in response.text
    assert "pr-review" in response.text
    assert "local-triage" in response.text
    assert "/admin/agent-library/blueprints/advisor/prompts" in response.text


def test_agent_prompts_create_registers_private_prompt(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/prompts/create",
        data={
            "revision": revision,
            "name": "daily-triage",
            "source": "---\nname: daily-triage\ndescription: Daily triage.\n---\n\nTriage now.\n",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    agent = saved["teams"]["newsletter"]["agents"][0]
    assert "daily-triage" in agent["prompts"]


def test_agent_prompts_create_stale_revision_preserves_source(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    stale_revision = _revision(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["flowgency"]["title"] = "Changed elsewhere"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    source = "---\nname: stale-check\ndescription: stale\n---\n\nKeep me\n"
    response = client.post(
        "/newsletter/agents/advisor/prompts/create",
        data={"revision": stale_revision, "name": "stale-check", "source": source},
    )

    assert response.status_code == 409
    assert "config.yaml changed" in response.text
    assert source in response.text


def test_agent_prompts_save_rejects_stale_digest_and_preserves_source(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    source = _local_triage_source("Updated body.\n")
    response = client.post(
        "/newsletter/agents/advisor/prompts/local-triage/save",
        data={"digest": "0" * 64, "source": source},
    )

    assert response.status_code == 409
    assert "prompt changed; reload and retry" in response.text
    assert source in response.text


def test_agent_prompts_delete_rejects_prompt_in_use(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "local-review",
            "prompt": {"scope": "instance", "name": "local-triage"},
            "schedule": {"every": "6h"},
        }
    )
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()
    source = client.get("/newsletter/agents/advisor/prompts").text
    match = re.search(r'name="digest" value="([0-9a-f]{64})"', source)
    assert match is not None

    response = client.post(
        "/newsletter/agents/advisor/prompts/local-triage/delete",
        data={"revision": revision, "digest": match.group(1)},
    )

    assert response.status_code == 409
    assert "local-triage" in response.text
    assert "local-review" in response.text


def test_agent_prompts_unknown_agent_and_prompt(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    missing_agent = client.get("/newsletter/agents/missing/prompts")
    assert missing_agent.status_code == 404

    missing_prompt = client.post(
        "/newsletter/agents/advisor/prompts/missing/save",
        data={"digest": "0" * 64, "source": "---\nname: missing\ndescription: missing\n---\n\nMissing\n"},
    )
    assert missing_prompt.status_code == 404

    missing_delete = client.post(
        "/newsletter/agents/advisor/prompts/missing/delete",
        data={"revision": revision, "digest": "0" * 64},
    )
    assert missing_delete.status_code == 404


def test_memory_tab_shows_selector_without_hash(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/memory")

    assert response.status_code == 200
    assert "Default memory" in response.text
    assert "Agent memory" in response.text
    assert "memory.md" in response.text
    assert "sha256" not in response.text.lower()
    assert "a" * 64 not in response.text


def test_activity_tab_is_read_only(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/activity")

    assert response.status_code == 200
    assert "Activity" in response.text
    assert '<form' not in response.text


def test_activity_includes_completed_job(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = seed_job_app(monkeypatch, tmp_path, raw_config)
    job_path = _write_job_record(team_root, config_path, job_id="activity-complete")
    record = read_job(job_path)
    record.status = "complete"
    record.completed_at = "2026-07-16T13:00:00+00:00"
    record.execution_summary = "Retained completed run summary"
    write_job(job_path, record)

    response = client.get("/newsletter/agents/advisor/activity")

    assert response.status_code == 200
    assert "Retained completed run summary" in response.text
    assert "Complete" in response.text
    assert "1 record" in response.text


def test_activity_keeps_completed_job_with_nonfinite_duration(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = seed_job_app(monkeypatch, tmp_path, raw_config)
    job_path = _write_job_record(team_root, config_path, job_id="activity-nan-duration")
    record = read_job(job_path)
    record.status = "complete"
    record.completed_at = "2026-07-16T13:00:00+00:00"
    record.duration_seconds = math.nan
    record.execution_summary = "Duration is malformed but row survives"
    write_job(job_path, record)

    response = client.get("/newsletter/agents/advisor/activity")

    assert response.status_code == 200
    assert "Duration is malformed but row survives" in response.text
    assert "Complete" in response.text
    assert "1 record" in response.text


@pytest.mark.parametrize(
    ("status", "status_label", "no_logs_label"),
    [
        ("queued", "Queued", "No logs yet"),
        ("waiting_for_memory", "Waiting for memory", "No logs yet"),
        ("running", "Running", "No logs yet"),
        ("complete", "Complete", "No logs available"),
        ("failed", "Failed", "No logs available"),
        ("cancelled", "Cancelled", "No logs available"),
    ],
)
def test_activity_job_status_labels_and_log_availability(monkeypatch, tmp_path, raw_config, status, status_label, no_logs_label):
    client, config_path, team_root = seed_job_app(monkeypatch, tmp_path, raw_config)
    job_path = _write_job_record(team_root, config_path, job_id=f"activity-{status}")
    _rewrite_job(job_path, status=status, execution_summary=f"{status} status row")

    response = client.get("/newsletter/agents/advisor/activity")

    assert response.status_code == 200
    row = _activity_row(response.text, f"{status} status row")
    assert status_label in row
    assert no_logs_label in row


def test_activity_links_use_ticket_events_not_retired_records(workflow_web_env):
    env = workflow_web_env
    created = env.create(title="Alpha review")
    env.service.assign(env.user, created.version, "builder", env.operation("assign-alpha"))
    builder = env.agent("builder", "run-alpha")
    env.service.start_work(
        builder,
        env.read(created.ref).version,
        env.operation("start-alpha", actor_name=builder.agent_name),
    )
    (env.team_root / "observations").mkdir(parents=True, exist_ok=True)
    (env.team_root / "proposals").mkdir(parents=True, exist_ok=True)
    (env.team_root / "observations" / "status.md").write_text(
        "---\nagent: builder\nstatus: open\n---\n\nRetired observation\n",
        encoding="utf-8",
    )
    (env.team_root / "proposals" / "old.md").write_text(
        "---\norigin_agent: builder\nstatus: proposed\n---\n\nRetired proposal\n",
        encoding="utf-8",
    )

    response = env.client.get("/newsletter/agents/builder/activity")

    assert response.status_code == 200
    body = response.text
    assert "Alpha review" in body
    assert "Agent started work" in body
    assert f"/newsletter/workflows/board-a?ticket={created.ref.ticket_id}" in body
    assert "/newsletter/observations/" not in body
    assert "/newsletter/proposals/" not in body
    assert "Retired observation" not in body
    assert "Retired proposal" not in body


def test_activity_report_event_renders_escaped_disclosure_markup(workflow_web_env):
    env = workflow_web_env
    created = env.create(title="Alpha review")
    env.service.assign(env.user, created.version, "builder", env.operation("assign-alpha"))
    builder = env.agent("builder", "run-alpha")
    env.running_job("builder", "run-alpha")
    env.service.start_work(
        builder,
        env.read(created.ref).version,
        env.operation("start-alpha", actor_name=builder.agent_name),
    )
    env.service.report(
        builder,
        env.read(created.ref).version,
        env.ticket_report("Line <strong>one</strong>\nLine two\nLine three"),
        env.operation("report-alpha", actor_name=builder.agent_name),
    )

    response = env.client.get("/newsletter/agents/builder/activity")

    assert response.status_code == 200
    assert "Report added" in response.text
    assert "data-activity-report" in response.text
    assert "data-report-text" in response.text
    assert "data-report-toggle" in response.text
    assert re.search(r'aria-controls="[^"]+-report"', response.text)
    assert "&lt;strong&gt;one&lt;/strong&gt;" in response.text
    assert "<strong>one</strong>" not in response.text


def test_activity_transition_keeps_historical_state_names_after_workflow_rename(workflow_web_env):
    env = workflow_web_env
    created = env.create(title="Alpha review", values={"verdict": True})
    builder = env.agent("builder", "run-alpha")
    env.running_job("builder", "run-alpha")
    env.service.start_work(
        builder,
        env.read(created.ref).version,
        env.operation("start-alpha", actor_name=builder.agent_name),
    )
    env.service.transition(
        builder,
        env.read(created.ref).version,
        env.transition_request(outputs={"summary": "Verified existing work"}),
        env.operation("complete-alpha", actor_name=builder.agent_name),
    )

    source = env.library.inspect(env.blueprint_id)
    renamed_states = tuple(
        state.model_copy(update={"name": "Backlog"})
        if state.id == "review"
        else state.model_copy(update={"name": "Closed"})
        if state.id == "done"
        else state
        for state in source.definition.states
    )
    renamed_definition = source.definition.model_copy(update={"states": renamed_states})
    env.configuration_service.save_blueprint(
        env.store.load().revision,
        env.blueprint_id,
        source.digest,
        renamed_definition,
    )

    response = env.client.get("/newsletter/agents/builder/activity")

    assert response.status_code == 200
    body = response.text
    assert "Review -&gt; Done" in body
    assert "Transition Complete accepted" in body
    assert body.count("Transition Complete accepted") == 1
    assert "Backlog -&gt; Closed" not in body


def test_activity_uses_semantic_time_markup_for_known_and_unknown_times(monkeypatch, tmp_path, raw_config):
    client, config_path, team_root = seed_job_app(monkeypatch, tmp_path, raw_config)
    known_job_path = _write_job_record(team_root, config_path, job_id="activity-known-time")
    known_record = read_job(known_job_path)
    known_record.status = "running"
    known_record.started_at = "2026-07-16T13:00:00"
    known_record.execution_summary = "Known timestamp row"
    write_job(known_job_path, known_record)

    unknown_job_path = _write_job_record(team_root, config_path, job_id="activity-unknown-time")
    unknown_record = read_job(unknown_job_path)
    unknown_record.status = "complete"
    unknown_record.completed_at = "not-a-time"
    unknown_record.execution_summary = "Unknown timestamp row"
    write_job(unknown_job_path, unknown_record)

    response = client.get("/newsletter/agents/advisor/activity")

    assert response.status_code == 200
    assert re.search(
        r'<time[^>]*datetime="2026-07-16T13:00:00"[^>]*>13:00</time>',
        response.text,
    )
    assert "Unknown time" in response.text
    assert not re.search(r'<time[^>]*datetime="[^"]*"[^>]*>Unknown time</time>', response.text)


def test_activity_suppresses_duplicate_report_summary_when_equal_to_title(workflow_web_env):
    env = workflow_web_env
    created = env.create(title="Alpha review")
    builder = env.agent("builder", "run-alpha")
    env.running_job("builder", "run-alpha")
    env.service.start_work(
        builder,
        env.read(created.ref).version,
        env.operation("start-alpha", actor_name=builder.agent_name),
    )
    env.service.report(
        builder,
        env.read(created.ref).version,
        env.ticket_report("Report added"),
        env.operation("report-alpha", actor_name=builder.agent_name),
    )

    response = env.client.get("/newsletter/agents/builder/activity")

    assert response.status_code == 200
    assert response.text.count("Report added") == 1


def test_activity_bounds_and_orders_mixed_history_with_unknowns_last(workflow_web_env):
    env = workflow_web_env
    shared_ticket = env.create(title="Shared mixed ticket")
    shared_authority = env.running_job("builder", "shared-run")
    _rewrite_job(
        shared_authority.path,
        status="running",
        started_at="2026-09-08T00:00:00",
        execution_summary="Shared job row",
    )
    builder = env.agent("builder", "shared-run")
    env.service.start_work(
        builder,
        env.read(shared_ticket.ref).version,
        env.operation("start-shared", actor_name=builder.agent_name),
    )
    env.service.report(
        builder,
        env.read(shared_ticket.ref).version,
        env.ticket_report("Shared builder note"),
        env.operation("report-shared", actor_name=builder.agent_name),
    )

    for index in range(40):
        authority = env.running_job("builder", f"fill-{index:02d}")
        _rewrite_job(
            authority.path,
            started_at=f"2026-09-09T10:{index:02d}:00",
            execution_summary=f"Fill summary {index:02d}",
        )

    aware_authority = env.running_job("builder", "aware-time")
    aware_time = "2026-09-08T01:30:00+00:00"
    _rewrite_job(
        aware_authority.path,
        status="complete",
        completed_at=aware_time,
        started_at="2026-09-08T01:45:00",
        execution_summary="Aware time summary",
    )

    complete_priority = env.running_job("builder", "priority-complete")
    _rewrite_job(
        complete_priority.path,
        spec_updates={"created_at": "2026-09-08T23:59:00"},
        status="complete",
        started_at="2026-09-08T23:58:00",
        completed_at="2026-09-08T00:10:00",
        execution_summary="Complete priority summary",
    )

    start_priority = env.running_job("builder", "priority-start")
    _rewrite_job(
        start_priority.path,
        spec_updates={"created_at": "2026-09-08T23:57:00"},
        status="running",
        started_at="2026-09-08T00:11:00",
        completed_at=None,
        execution_summary="Start priority summary",
    )

    created_priority = env.running_job("builder", "priority-created")
    _rewrite_job(
        created_priority.path,
        spec_updates={"created_at": "2026-09-08T00:12:00"},
        status="queued",
        started_at=None,
        completed_at=None,
        execution_summary="Created priority summary",
    )

    tie_z = env.running_job("builder", "tie-z")
    _rewrite_job(
        tie_z.path,
        started_at="2026-09-08T00:20:00",
        execution_summary="Tie z summary",
    )
    tie_a = env.running_job("builder", "tie-a")
    _rewrite_job(
        tie_a.path,
        started_at="2026-09-08T00:20:00",
        execution_summary="Tie a summary",
    )

    unknown_z = env.running_job("builder", "unknown-z")
    _rewrite_job(
        unknown_z.path,
        status="complete",
        completed_at="not-a-time",
        execution_summary="Unknown timestamp kept",
    )
    unknown_a = env.running_job("builder", "unknown-a")
    _rewrite_job(
        unknown_a.path,
        status="complete",
        completed_at="not-a-time",
        execution_summary="Unknown timestamp dropped",
    )

    response = env.client.get("/newsletter/agents/builder/activity")

    assert response.status_code == 200
    body = response.text
    assert "50 records" in body
    assert len(_activity_rows(body)) == 50
    assert "Shared builder note" in body
    assert "Started work" in body
    assert "Unknown date" in body
    assert "Unknown timestamp kept" in body
    assert "Unknown timestamp dropped" not in body
    assert body.index("Tie z summary") < body.index("Tie a summary")
    assert body.index("Shared builder note") < body.index("Unknown timestamp kept")

    complete_row = _activity_row(body, "Complete priority summary")
    assert 'datetime="2026-09-08T00:10:00"' in complete_row
    assert ">00:10</time>" in complete_row

    start_row = _activity_row(body, "Start priority summary")
    assert 'datetime="2026-09-08T00:11:00"' in start_row
    assert ">00:11</time>" in start_row

    created_row = _activity_row(body, "Created priority summary")
    assert 'datetime="2026-09-08T00:12:00"' in created_row
    assert ">00:12</time>" in created_row

    aware_row = _activity_row(body, "Aware time summary")
    normalized_aware = datetime.fromisoformat(aware_time).astimezone().replace(tzinfo=None)
    assert f'datetime="{normalized_aware.isoformat(timespec="seconds")}"' in aware_row
    assert f">{normalized_aware.strftime('%H:%M')}</time>" in aware_row

    unknown_row = _activity_row(body, "Unknown timestamp kept")
    assert "Unknown time" in unknown_row
    assert "<time" not in unknown_row


def test_activity_event_log_links_use_exact_originating_job_and_never_guess(workflow_web_env):
    env = workflow_web_env
    created = env.create(title="Alpha review")
    log_day = env.team_root / "logs" / "2026-09-08"
    log_day.mkdir(parents=True, exist_ok=True)
    shared_output = log_day / "builder-shared-run.out"
    shared_output.write_text("shared output", encoding="utf-8")
    shared_error = log_day / "builder-shared-run.err"
    shared_error.write_text("", encoding="utf-8")
    shared_authority = env.running_job("builder", "shared-run")
    _rewrite_job(
        shared_authority.path,
        stdout_path=str(shared_output.resolve()),
        stderr_path=str(shared_error.resolve()),
        execution_summary="Shared builder run",
    )

    builder = env.agent("builder", "shared-run")
    env.service.start_work(
        builder,
        env.read(created.ref).version,
        env.operation("start-shared", actor_name=builder.agent_name),
    )
    env.service.report(
        builder,
        env.read(created.ref).version,
        env.ticket_report("Shared builder note"),
        env.operation("report-shared", actor_name=builder.agent_name),
    )
    env.service.end_work(
        builder,
        env.read(created.ref).version,
        env.operation("end-shared", actor_name=builder.agent_name),
    )
    env.service.assign(
        env.user,
        env.read(created.ref).version,
        "observer",
        env.operation("assign-observer"),
    )

    latest_output = log_day / "builder-latest-run.out"
    latest_output.write_text("latest output", encoding="utf-8")
    latest_authority = env.running_job("builder", "latest-run")
    _rewrite_job(
        latest_authority.path,
        stdout_path=str(latest_output.resolve()),
        execution_summary="Latest builder run",
    )

    rogue_output = log_day / "observer-rogue-job.out"
    rogue_output.write_text("rogue output", encoding="utf-8")
    rogue_authority = env.running_job("observer", "rogue-job")
    _rewrite_job(
        rogue_authority.path,
        stdout_path=str(rogue_output.resolve()),
        execution_summary="Observer rogue run",
    )

    current_record = env.provider.read(created.ref)
    current_record = current_record.model_copy(
        update={
            "events": current_record.events
            + (
                TicketEvent(
                    id="historic-builder-note",
                    kind="reported",
                    actor="builder",
                    summary="Historic builder note",
                    data={},
                    at=current_record.updated_at,
                ),
                TicketEvent(
                    id="forged-builder-note",
                    kind="reported",
                    actor="builder",
                    summary="Forged builder note",
                    data={"job_id": "rogue-job"},
                    at=current_record.updated_at,
                ),
            )
        }
    )
    env.provider.write_record(current_record)

    escaped_output = env.tmp_path / "escaped.out"
    escaped_output.write_text("escape", encoding="utf-8")
    escaped_authority = env.running_job("builder", "escaped-terminal")
    _rewrite_job(
        escaped_authority.path,
        status="complete",
        stdout_path=str(escaped_output.resolve()),
        stderr_path=None,
        execution_summary="Escaped terminal job",
    )

    response = env.client.get("/newsletter/agents/builder/activity")

    assert response.status_code == 200
    start_row = _activity_row(response.text, "Started work")
    report_row = _activity_row(response.text, "Shared builder note")
    historic_row = _activity_row(response.text, "Historic builder note")
    forged_row = _activity_row(response.text, "Forged builder note")
    escaped_row = _activity_row(response.text, "Escaped terminal job")

    start_output_hrefs = _hrefs_for_label(start_row, "Output")
    report_output_hrefs = _hrefs_for_label(report_row, "Output")
    assert len(start_output_hrefs) == 1
    assert report_output_hrefs == start_output_hrefs
    shared_params = parse_qs(urlparse(report_output_hrefs[0]).query)
    assert Path(unquote(shared_params["path"][0])) == shared_output.resolve()
    assert Path(unquote(shared_params["path"][0])) != latest_output.resolve()
    assert not _hrefs_for_label(start_row, "Error")
    assert not _hrefs_for_label(report_row, "Error")
    assert "No logs available" in historic_row
    assert not _hrefs_for_label(historic_row, "Output")
    assert "No logs available" in forged_row
    assert not _hrefs_for_label(forged_row, "Output")
    assert "No logs available" in escaped_row
    assert not _hrefs_for_label(escaped_row, "Output")


def test_activity_warnings_do_not_hide_readable_history(workflow_web_env):
    env = workflow_web_env
    readable_authority = env.running_job("builder", "readable-job")
    _rewrite_job(
        readable_authority.path,
        status="complete",
        completed_at="2026-09-08T12:00:00",
        execution_summary="Readable job survives",
    )
    broken_job_path = env.job_store.path(env.team_id, "broken-activity")
    broken_job_path.write_text("not: [valid", encoding="utf-8")
    missing_root = env.root_a
    backup_root = env.tmp_path / "tickets-root-backup"
    missing_root.rename(backup_root)

    response = env.client.get("/newsletter/agents/builder/activity")

    assert response.status_code == 200
    assert "Readable job survives" in response.text
    assert "Skipped unreadable job record: broken-activity.yaml" in response.text
    assert "Skipped unreadable workflow activity:" in response.text


def test_activity_tab_uses_safe_agent_log_projection(monkeypatch, tmp_path, raw_config):
    client, config_path, safe_log = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter-prod"]["agents"].append(
        {
            **raw["teams"]["newsletter-prod"]["agents"][0],
            "name": "advisor-extra",
        }
    )
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    log_day = safe_log.parent
    misleading_owner = log_day / "advisor-wrong-owner.out"
    misleading_owner.write_text("other agent", encoding="utf-8")
    overlapping = log_day / "advisor-extra-run.out"
    overlapping.write_text("overlap", encoding="utf-8")
    escaping_target = tmp_path / "escaped.out"
    escaping_target.write_text("escape", encoding="utf-8")
    escaping = log_day / "advisor-escape.out"
    _make_reparse(escaping, escaping_target)
    (log_day / "advisor-dir.out").mkdir()

    services = app_mod.build_services(config_path)
    advisor_record = type(
        "Record",
        (),
        {
            "spec": type(
                "Spec",
                (),
                {
                    "team_key": "newsletter-prod",
                    "agent_name": "advisor",
                    "job_id": "job-safe",
                    "trigger": "scheduled_prompt",
                    "routine_id": "daily-review",
                    "prompt_source": {"title": "Daily review"},
                },
            )(),
            "status": "running",
            "duration_seconds": None,
            "execution_summary": "Safe output only",
            "completed_at": None,
            "started_at": "2026-07-16T13:00:00+00:00",
            "stdout_path": str(safe_log.resolve()),
            "stderr_path": str(escaping),
        },
    )()
    wrong_owner_record = type(
        "Record",
        (),
        {
            "spec": type("Spec", (), {"team_key": "newsletter-prod", "agent_name": "advisor-extra"})(),
            "status": "running",
            "duration_seconds": None,
            "execution_summary": "Wrong owner",
            "completed_at": None,
            "started_at": "2026-07-16T13:00:00+00:00",
            "stdout_path": str(misleading_owner.resolve()),
            "stderr_path": None,
        },
    )()
    from flowgency.web import agent_activity as agent_activity_mod

    monkeypatch.setattr(
        agent_activity_mod,
        "load_team_jobs",
        lambda job_store, team_id: ((advisor_record, wrong_owner_record), ()),
    )
    app_mod.refresh_services()
    app_mod.app.state.services = services

    response = client.get("/newsletter-prod/agents/advisor/activity")

    assert response.status_code == 200
    body = response.text
    output_hrefs = _hrefs_for_label(body, "Output")
    assert len(output_hrefs) == 1
    params = parse_qs(urlparse(output_hrefs[0]).query)
    assert params["agent"] == ["advisor"]
    assert params["source"] == ["activity"]
    assert Path(unquote(params["path"][0])) == safe_log.resolve()
    assert "Safe output only" in body
    assert misleading_owner.name not in body
    assert overlapping.name not in body
    assert "advisor-dir.out" not in body
    assert "advisor-escape.out" not in body
    assert "Error" not in body


def test_activity_logs_and_viewer_gets_do_not_mutate_config_job_or_ticket_files(workflow_web_env):
    env = workflow_web_env
    created = env.create(title="Alpha review")
    authority = env.running_job("builder", "job-alpha")
    builder = env.agent("builder", "run-alpha")
    env.service.start_work(
        builder,
        env.read(created.ref).version,
        env.operation("start-alpha", actor_name=builder.agent_name),
    )
    log_day = env.team_root / "logs" / "2026-09-12"
    log_day.mkdir(parents=True, exist_ok=True)
    log_file = log_day / "builder-run.out"
    log_file.write_text("hello", encoding="utf-8")
    ticket_path = env.provider._ticket_path(created.ref)
    before_config = env.store.path.read_bytes()
    before_job = authority.path.read_bytes()
    before_ticket = ticket_path.read_bytes()
    before_log = log_file.read_bytes()

    activity_response = env.client.get(f"/{env.team_id}/agents/builder/activity")
    logs_response = env.client.get(f"/{env.team_id}/agents/builder/logs")
    hrefs = _hrefs_for_label(logs_response.text, log_file.name)
    assert len(hrefs) == 1
    view_response = env.client.get(hrefs[0])

    assert activity_response.status_code == 200
    assert logs_response.status_code == 200
    assert view_response.status_code == 200
    assert env.store.path.read_bytes() == before_config
    assert authority.path.read_bytes() == before_job
    assert ticket_path.read_bytes() == before_ticket
    assert log_file.read_bytes() == before_log


def test_agent_logs_rendered_href_round_trips_windows_path(monkeypatch, tmp_path, raw_config):
    client, _config_path, _log_file = _seed_activity_app(monkeypatch, tmp_path, raw_config)
    special = tmp_path / "groups" / "newsletter-workspace" / "logs" / "2026-07-16" / "advisor-demo & łog.out"
    special.write_text("special", encoding="utf-8")

    page = client.get("/newsletter-prod/agents/advisor/logs")

    assert page.status_code == 200
    hrefs = _hrefs_for_label(page.text, special.name)
    assert len(hrefs) == 1
    parsed = urlparse(hrefs[0])
    params = parse_qs(parsed.query)
    assert params["agent"] == ["advisor"]
    assert params["source"] == ["logs"]
    rendered_path = unquote(params["path"][0])
    assert "\\" in rendered_path
    assert Path(rendered_path) == special.resolve()

    response = client.get(hrefs[0])

    assert response.status_code == 200
    assert "special" in response.text
    assert "Back to Logs" in response.text
    assert "/newsletter-prod/agents/advisor/logs" in response.text


def test_profile_post_updates_config_revision_owned_fields(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/profile",
        data={
            "revision": revision,
            "display_name": "Senior Advisor",
            "title": "Runtime Curator",
            "emoji": ":D",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    agent = saved["teams"]["newsletter"]["agents"][0]
    assert agent["identity"]["display_name"] == "Senior Advisor"
    assert agent["identity"]["title"] == "Runtime Curator"


def test_runtime_post_updates_override_and_effective_preview(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "1801",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    runtime = saved["teams"]["newsletter"]["agents"][0]["runtime"]
    assert runtime["timeout"] == 1801


def test_runtime_post_unchecked_checkbox_persists_false_without_dropping_model_key(monkeypatch, tmp_path, raw_config):
    raw_config["teams"]["newsletter"]["agents"][0]["integration_config"] = {
        "model": "gpt-5.4"
    }
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "1801",
            "integration_config.allow_local_network__present": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    agent = saved["teams"]["newsletter"]["agents"][0]
    assert agent["integration_config"] == {
        "model": "gpt-5.4",
        "allow_local_network": False,
    }


def test_runtime_post_stale_revision_preserves_timeout_and_checkbox_state(monkeypatch, tmp_path, raw_config):
    raw_config["teams"]["newsletter"]["agents"][0]["integration_config"] = {
        "model": "gpt-5.4"
    }
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    stale_revision = _revision(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["flowgency"]["title"] = "Changed elsewhere"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": stale_revision,
            "timeout": "1801",
            "integration_config.allow_local_network__present": "1",
            "integration_config.allow_local_network": "on",
        },
    )

    assert response.status_code == 409
    assert "config.yaml changed" in response.text
    assert 'name="timeout" value="1801"' in response.text
    assert 'name="integration_config.allow_local_network"' in response.text
    assert 'checked' in response.text


def test_runtime_post_ignores_forged_local_network_checkbox_for_non_copilot_agent(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["integration"] = "script"
    raw["teams"]["newsletter"]["agents"][0]["integration_config"] = {
        "command": "echo ok"
    }
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "1801",
            "integration_config.allow_local_network__present": "1",
            "integration_config.allow_local_network": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    agent = saved["teams"]["newsletter"]["agents"][0]
    assert agent["integration_config"] == {"command": "echo ok"}


def test_runtime_post_rejects_previous_permission_rules_field(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    before = config_path.read_bytes()
    revision = _revision(config_path)

    response = client.post(
        "/newsletter/agents/advisor/runtime",
        data={
            "revision": revision,
            "timeout": "1801",
            "permission_rules_yaml": "[]",
        },
    )

    assert response.status_code == 409
    assert "Permission rules moved to the dedicated Permissions tab." in response.text
    assert "/newsletter/agents/advisor/permissions" in response.text
    assert 'value="1801"' in response.text
    assert config_path.read_bytes() == before


def test_memory_post_selector_updates_only_config(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    memory_store = app_mod.app.state.services.memory_store
    before = memory_store.ensure(resolved)
    revision = snapshot.revision

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "action": "selector",
            "revision": revision,
            "default_memory_scope": "team",
            "default_memory_channel": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["teams"]["newsletter"]["agents"][0]["default_memory"] == {"scope": "team"}
    after = memory_store.read(resolved)
    assert after.revision == before.revision
    assert after.files == before.files


def test_memory_post_content_updates_only_selected_memory(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    memory_store = app_mod.app.state.services.memory_store
    before_config_bytes = config_path.read_bytes()
    seeded = memory_store.ensure(resolved)

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "action": "content",
            "content_revision": seeded.revision,
            "selector_token": "agent",
            "filename": "memory.md",
            "content": "Updated memory",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert config_path.read_bytes() == before_config_bytes
    current = memory_store.read(resolved)
    assert current.files["memory.md"] == b"Updated memory"


def test_memory_post_returns_409_for_stale_content_revision(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    memory_store = app_mod.app.state.services.memory_store
    seeded = memory_store.ensure(resolved)
    current = memory_store.try_save(resolved, seeded.revision, {"memory.md": b"server"})
    before_config_bytes = config_path.read_bytes()

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "action": "content",
            "content_revision": seeded.revision,
            "selector_token": "agent",
            "filename": "memory.md",
            "content": "client",
        },
    )

    assert response.status_code == 409
    assert current.revision in response.text
    assert seeded.revision in response.text
    assert "server" in response.text
    assert "client" in response.text
    assert config_path.read_bytes() == before_config_bytes


def test_memory_post_selector_returns_409_for_stale_config_without_mutating_memory(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    memory_store = app_mod.app.state.services.memory_store
    seeded = memory_store.ensure(resolved)
    stale_revision = snapshot.revision

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["flowgency"]["title"] = "Changed elsewhere"
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    response = client.post(
        "/newsletter/agents/advisor/memory",
        data={
            "action": "selector",
            "revision": stale_revision,
            "default_memory_scope": "team",
            "default_memory_channel": "",
        },
    )

    assert response.status_code == 409
    current = memory_store.read(resolved)
    assert current.revision == seeded.revision
    assert current.files == seeded.files


def test_memory_post_returns_423_when_memory_is_busy(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    store = ConfigStore(config_path)
    snapshot = store.load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    memory_store = app_mod.app.state.services.memory_store
    seeded = memory_store.ensure(resolved)
    before_config_bytes = config_path.read_bytes()
    lock_path = memory_store._lock_path(resolved)
    acquired, release = Event(), Event()
    process = Process(
        target=hold_exclusive_lock,
        args=(str(lock_path), acquired, release, 30),
    )
    process.start()

    try:
        assert acquired.wait(15)
        response = client.post(
            "/newsletter/agents/advisor/memory",
            data={
                "action": "content",
                "content_revision": seeded.revision,
                "selector_token": "agent",
                "filename": "memory.md",
                "content": "blocked",
            },
            follow_redirects=False,
        )
    finally:
        release.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(15)
        assert not process.is_alive()
        assert process.exitcode == 0

    assert response.status_code == 423
    assert "Memory is busy" in response.text
    assert config_path.read_bytes() == before_config_bytes

# ── Conflict baselines ───────────────────────────────────────────────────────


def _conflict_hidden(html: str, name: str) -> str:
    return re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', html).group(1)


def _change_config_elsewhere(config_path: Path) -> None:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["flowgency"]["title"] = "Changed elsewhere"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()


def test_runtime_conflict_keeps_the_submitted_revision_so_a_resubmit_conflicts_again(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    loaded = _revision(config_path)
    _change_config_elsewhere(config_path)
    remote = config_path.read_bytes()
    submitted = loaded

    for _attempt in range(3):
        response = client.post("/newsletter/agents/advisor/runtime", data={"revision": submitted, "timeout": "1801"})
        assert response.status_code == 409
        assert "config.yaml changed" in response.text
        submitted = _conflict_hidden(response.text, "revision")
        assert submitted == loaded
        assert config_path.read_bytes() == remote

    marker = _revision_markers(response.text)["config"]
    assert marker["data-live-revision"] == _revision(config_path) != loaded
    assert marker["data-live-baseline-input"] == "revision"


def test_prompt_create_conflict_keeps_the_submitted_revision(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    loaded = _revision(config_path)
    _change_config_elsewhere(config_path)
    remote = config_path.read_bytes()
    source = "---\nname: stale-check\ndescription: stale\n---\n\nKeep me\n"
    submitted = loaded

    for _attempt in range(2):
        response = client.post(
            "/newsletter/agents/advisor/prompts/create",
            data={"revision": submitted, "name": "stale-check", "source": source},
        )
        assert response.status_code == 409
        assert source in response.text
        submitted = _conflict_hidden(response.text, "revision")
        assert submitted == loaded
        assert config_path.read_bytes() == remote


def test_prompt_save_conflict_keeps_the_submitted_digest_and_the_source_unchanged(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    prompt_file = tmp_path / "prompts" / "newsletter" / "advisor" / "local-triage.prompt.md"
    page = client.get("/newsletter/agents/advisor/prompts").text
    loaded = re.search(r'name="digest" value="([0-9a-f]{64})" data-live-baseline="prompt:local-triage"', page).group(1)
    prompt_file.write_text(_local_triage_source("Edited elsewhere.\n"), encoding="utf-8")
    remote = prompt_file.read_bytes()
    submitted = loaded

    for attempt in range(3):
        response = client.post(
            "/newsletter/agents/advisor/prompts/local-triage/save",
            data={"digest": submitted, "source": _local_triage_source(f"My draft {attempt}.\n")},
        )
        assert response.status_code == 409
        assert f"My draft {attempt}." in response.text
        submitted = re.search(
            r'name="digest" value="([0-9a-f]{64})" data-live-baseline="prompt:local-triage"', response.text
        ).group(1)
        assert submitted == loaded
        assert prompt_file.read_bytes() == remote

    marker = _revision_markers(response.text)["prompt:local-triage"]
    assert marker["data-live-revision"] != loaded
    assert marker["data-live-baseline-key"] == "prompt:local-triage"


def _delete_form_baseline(html: str, name: str) -> tuple[str, str]:
    form = re.search(rf'<form[^>]*/prompts/{name}/delete".*?</form>', html, re.S).group(0)
    return (
        re.search(r'name="revision" value="([^"]*)"', form).group(1),
        re.search(r'name="digest" value="([^"]*)"', form).group(1),
    )


def _local_prompt_file(tmp_path: Path, name: str = "local-triage") -> Path:
    return tmp_path / "prompts" / "newsletter" / "advisor" / f"{name}.prompt.md"


def _register_second_prompt(config_path: Path, tmp_path: Path) -> None:
    _local_prompt_file(tmp_path, "second-triage").write_text(
        "---\nname: second-triage\ndescription: Second.\n---\n\nSecond body.\n", encoding="utf-8"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["prompts"].append("second-triage")
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()


@pytest.mark.parametrize("change_config, change_source", [(True, True), (True, False), (False, True)])
def test_prompt_delete_conflict_keeps_the_loaded_revision_and_digest(
    monkeypatch, tmp_path, raw_config, change_config, change_source
):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    prompt_file = _local_prompt_file(tmp_path)
    loaded_revision, loaded_digest = _delete_form_baseline(
        client.get("/newsletter/agents/advisor/prompts").text, "local-triage"
    )
    if change_config:
        _change_config_elsewhere(config_path)
    if change_source:
        prompt_file.write_text(_local_triage_source("Edited elsewhere.\n"), encoding="utf-8")
    remote_config = config_path.read_bytes()
    remote_source = prompt_file.read_bytes()
    submitted = (loaded_revision, loaded_digest)

    for _attempt in range(3):
        response = client.post(
            "/newsletter/agents/advisor/prompts/local-triage/delete",
            data={"revision": submitted[0], "digest": submitted[1]},
            follow_redirects=False,
        )
        assert response.status_code == 409
        submitted = _delete_form_baseline(response.text, "local-triage")
        assert submitted == (loaded_revision, loaded_digest)
        assert config_path.read_bytes() == remote_config
        assert prompt_file.read_bytes() == remote_source
        assert "local-triage" in yaml.safe_load(config_path.read_text(encoding="utf-8"))["teams"]["newsletter"]["agents"][0]["prompts"]

    assert _revision_markers(response.text)["config"]["data-live-revision"] == _revision(config_path)

    reloaded = _delete_form_baseline(client.get("/newsletter/agents/advisor/prompts").text, "local-triage")
    assert reloaded == (_revision(config_path), reloaded[1])
    deleted = client.post(
        "/newsletter/agents/advisor/prompts/local-triage/delete",
        data={"revision": reloaded[0], "digest": reloaded[1]},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert "local-triage" not in yaml.safe_load(config_path.read_text(encoding="utf-8"))["teams"]["newsletter"]["agents"][0]["prompts"]


def test_prompt_delete_conflict_does_not_lend_its_digest_to_another_prompt(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    _register_second_prompt(config_path, tmp_path)
    page = client.get("/newsletter/agents/advisor/prompts").text
    loaded_first = _delete_form_baseline(page, "local-triage")
    loaded_second = _delete_form_baseline(page, "second-triage")
    _local_prompt_file(tmp_path).write_text(_local_triage_source("First edited.\n"), encoding="utf-8")
    _local_prompt_file(tmp_path, "second-triage").write_text(
        "---\nname: second-triage\ndescription: Second.\n---\n\nSecond edited.\n", encoding="utf-8"
    )

    response = client.post(
        "/newsletter/agents/advisor/prompts/local-triage/delete",
        data={"revision": loaded_first[0], "digest": loaded_first[1]},
    )

    assert response.status_code == 409
    assert _delete_form_baseline(response.text, "local-triage") == loaded_first
    assert _delete_form_baseline(response.text, "second-triage")[1] != loaded_first[1]
    assert _delete_form_baseline(response.text, "second-triage")[1] != loaded_second[1]


def test_prompt_delete_validation_failure_keeps_the_loaded_baseline(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {"id": "local-review", "prompt": {"scope": "instance", "name": "local-triage"}, "schedule": {"every": "6h"}}
    )
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app_mod.refresh_services()
    loaded = _delete_form_baseline(client.get("/newsletter/agents/advisor/prompts").text, "local-triage")
    _change_config_elsewhere(config_path)
    _local_prompt_file(tmp_path).write_text(_local_triage_source("Edited elsewhere.\n"), encoding="utf-8")

    response = client.post(
        "/newsletter/agents/advisor/prompts/local-triage/delete",
        data={"revision": loaded[0], "digest": loaded[1]},
    )

    assert response.status_code == 409
    assert _delete_form_baseline(response.text, "local-triage") == loaded


def test_memory_content_conflict_keeps_the_submitted_revision(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    snapshot = ConfigStore(config_path).load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=(tmp_path / "memory-store"),
    )
    memory_store = app_mod.app.state.services.memory_store
    seeded = memory_store.ensure(resolved)
    current = memory_store.try_save(resolved, seeded.revision, {"memory.md": b"server"})
    submitted = seeded.revision

    for _attempt in range(3):
        response = client.post(
            "/newsletter/agents/advisor/memory",
            data={
                "action": "content",
                "content_revision": submitted,
                "selector_token": "agent",
                "filename": "memory.md",
                "content": "client",
            },
        )
        assert response.status_code == 409
        submitted = _conflict_hidden(response.text, "content_revision")
        assert submitted == seeded.revision
        assert memory_store.read(resolved).revision == current.revision
        assert memory_store.read(resolved).files["memory.md"] == b"server"

    assert _revision_markers(response.text)["memory"]["data-live-revision"] == current.revision


def test_prompt_cards_carry_a_per_prompt_marker_bound_to_the_loaded_digest(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    page = client.get("/newsletter/agents/advisor/prompts").text

    marker = _revision_markers(page)["prompt:local-triage"]
    loaded = re.search(r'name="digest" value="([0-9a-f]{64})" data-live-baseline="prompt:local-triage"', page).group(1)
    assert marker["data-live-revision"] == loaded
    assert marker["data-live-baseline-key"] == "prompt:local-triage"
    assert marker["data-live-changed"] == "Prompt local-triage changed since this form loaded. Reload to see the latest."


def test_a_source_only_prompt_change_moves_only_that_prompts_marker(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    before = live_regions(_live(client, "prompts"))
    loaded = _revision_markers(before["agent-prompts-edit"])["prompt:local-triage"]["data-live-revision"]

    (tmp_path / "prompts" / "newsletter" / "advisor" / "local-triage.prompt.md").write_text(
        _local_triage_source("Source-only change.\n"), encoding="utf-8"
    )
    after = live_regions(_live(client, "prompts"))

    assert after["agent-status"] == before["agent-status"]
    assert _revision_markers(after["agent-prompts-edit"])["prompt:local-triage"]["data-live-revision"] != loaded


# ── Live detail snapshots ────────────────────────────────────────────────────

from tests._live_helpers import (  # noqa: E402
    assert_snapshot_matches_page,
    filesystem_tree,
    live_regions,
    page_regions,
    registration,
)
from tests.test_agent_roster import _roster_job_spec  # noqa: E402
from flowgency.jobs.authority import JobStore  # noqa: E402
from flowgency.jobs.models import JobRecord  # noqa: E402

_LIVE_TABS = ("profile", "blueprint", "runtime", "permissions", "prompts", "routines", "memory", "activity", "logs")
_COMMON_REGIONS = ["agent-header", "agent-status"]
_TAB_REGIONS = {
    "profile": [],
    "blueprint": ["agent-blueprint"],
    "runtime": ["agent-runtime-summary"],
    "permissions": ["agent-permissions-saved"],
    "prompts": ["agent-prompts-catalog", "agent-prompts-edit"],
    "routines": ["agent-routines-saved"],
    "memory": ["agent-memory-status"],
    "activity": ["agent-activity-count", "agent-activity-entries"],
    "logs": ["agent-logs-count", "agent-logs-list"],
}
_NAV_REGIONS = ["navigation-teams", "navigation-primary", "navigation-workflows", "navigation-workspace"]


def _live_url(tab: str, agent: str = "advisor", team: str = "newsletter") -> str:
    return f"/{team}/agents/{agent}/{tab}?__live=1"


def _live(client, tab: str):
    response = client.get(_live_url(tab))
    assert response.status_code == 200, response.text
    return response


def _config_patch(config_path: Path, patch) -> None:
    ConfigStore(config_path).patch(_revision(config_path), patch)
    app_mod.refresh_services()


def _advisor(raw: dict) -> dict:
    return raw["teams"]["newsletter"]["agents"][0]


def _write_live_job(tmp_path: Path, job_id: str, status: str, **fields) -> None:
    team_root = tmp_path / "groups" / "newsletter"
    record = JobRecord.from_spec(
        _roster_job_spec(tmp_path, team_root, job_id=job_id, created_at="2026-07-16T00:00:00+00:00")
    )
    record.status = status
    for name, value in fields.items():
        setattr(record, name, value)
    write_job(JobStore(tmp_path / "memory-store").path("newsletter", job_id), record)


@pytest.mark.parametrize("tab", _LIVE_TABS)
def test_live_detail_snapshot_matches_the_initial_page_and_names_its_binding(monkeypatch, tmp_path, raw_config, tab):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get(f"/newsletter/agents/advisor/{tab}")
    response = _live(client, tab)
    body = response.json()

    assert body["binding"] == {
        "page": "agent-detail", "team": "newsletter", "entity": "advisor", "tab": tab, "query": {},
    }
    assert body["structure"].startswith(f"agent-detail-{tab}")
    assert list(live_regions(response)) == [*_NAV_REGIONS, *_COMMON_REGIONS, *_TAB_REGIONS[tab]]
    assert_snapshot_matches_page(page.text, response)
    data = registration(page.text)
    assert data["url"] == _live_url(tab)
    assert data["structure"] == body["structure"]
    assert response.headers["cache-control"] == "private, no-cache"
    assert client.get(_live_url(tab), headers={"If-None-Match": response.headers["etag"]}).status_code == 304


def test_live_detail_structure_names_the_integration_for_runtime_and_permissions(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    assert _live(client, "runtime").json()["structure"] == "agent-detail-runtime.copilot.local-network:1"
    assert _live(client, "permissions").json()["structure"] == "agent-detail-permissions.copilot:1"
    assert _live(client, "profile").json()["structure"] == "agent-detail-profile:1"


def test_live_detail_structure_changes_when_the_integration_changes_the_loaded_form(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    before = _live(client, "runtime").json()["structure"]

    _config_patch(config_path, lambda raw: _advisor(raw).update(integration="script", integration_config={}))

    after = _live(client, "runtime").json()["structure"]
    assert after != before
    assert after == "agent-detail-runtime.script.fixed:1"


@pytest.mark.parametrize("tab", _LIVE_TABS)
def test_live_detail_snapshot_creates_no_runtime_state(monkeypatch, tmp_path, raw_config, tab):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    before = filesystem_tree(tmp_path)

    _live(client, tab)

    assert filesystem_tree(tmp_path) == before


@pytest.mark.parametrize("tab", _LIVE_TABS)
def test_ordinary_detail_get_creates_no_runtime_state(monkeypatch, tmp_path, raw_config, tab):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    before = filesystem_tree(tmp_path)

    page = client.get(f"/newsletter/agents/advisor/{tab}")

    assert page.status_code == 200
    assert filesystem_tree(tmp_path) == before


def test_ordinary_memory_get_with_a_missing_store_shows_empty_memory_and_creates_nothing(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    memory_root = tmp_path / "memory-store"
    shutil.rmtree(memory_root, ignore_errors=True)

    page = client.get("/newsletter/agents/advisor/memory")
    snapshot = client.get(_live_url("memory"))

    assert page.status_code == 200 and snapshot.status_code == 200
    assert not memory_root.exists()


@pytest.mark.parametrize("tab", _LIVE_TABS)
def test_live_detail_regions_hold_no_editor_forms_except_owned_prompt_cards(monkeypatch, tmp_path, raw_config, tab):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    regions = live_regions(_live(client, tab))

    for key, html in regions.items():
        if key == "agent-prompts-edit":
            assert "<form" in html
        else:
            assert "<form" not in html, key
            assert "<textarea" not in html, key


def test_live_detail_unavailable_agent_or_team_is_not_found(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)

    for tab in _LIVE_TABS:
        assert client.get(_live_url(tab, agent="ghost")).status_code == 404, tab
        assert client.get(_live_url(tab, team="missing")).status_code == 404, tab

    _config_patch(config_path, lambda raw: raw["teams"]["newsletter"]["agents"].clear())
    for tab in _LIVE_TABS:
        gone = client.get(_live_url(tab))
        assert gone.status_code == 404, tab
        assert "advisor" not in gone.text.replace("/newsletter/agents/advisor", "")


def test_live_detail_status_follows_identity_active_job_and_config_revision(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    before = live_regions(_live(client, "profile"))
    assert "job-live" not in before["agent-status"]
    assert "Active jobs: 0" in before["agent-status"]

    _write_live_job(tmp_path, "job-live", "running")
    _config_patch(config_path, lambda raw: _advisor(raw)["identity"].update(title="Renamed Librarian"))

    after = live_regions(_live(client, "profile"))
    assert 'data-live-key="job:job-live"' in after["agent-status"]
    assert "Running job job-live" in after["agent-status"]
    assert "Active jobs: 1" in after["agent-status"]
    assert "Renamed Librarian" in after["agent-header"]
    assert after["agent-status"] != before["agent-status"]


def test_live_detail_blueprint_and_prompt_digests_follow_external_source_edits(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    blueprint = live_regions(_live(client, "blueprint"))["agent-blueprint"]
    prompts = live_regions(_live(client, "prompts"))["agent-prompts-edit"]

    (tmp_path / "agent-library" / "advisor" / "AGENTS.md").write_text("# Advisor\n\nEdited externally.\n", encoding="utf-8")
    (tmp_path / "prompts" / "newsletter" / "advisor" / "local-triage.prompt.md").write_text(
        _local_triage_source("Edited externally.\n"), encoding="utf-8"
    )

    assert live_regions(_live(client, "blueprint"))["agent-blueprint"] != blueprint
    after = live_regions(_live(client, "prompts"))["agent-prompts-edit"]
    assert after != prompts
    assert re.findall(r"Digest: ([0-9a-f]{64})", after) != re.findall(r"Digest: ([0-9a-f]{64})", prompts)
    assert 'data-live-key="prompt:local-triage"' in after


def test_live_detail_prompt_save_with_a_loaded_digest_still_hits_the_conflict_path(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get("/newsletter/agents/advisor/prompts").text
    loaded_digest = re.search(r'name="digest" value="([0-9a-f]{64})"', page).group(1)
    (tmp_path / "prompts" / "newsletter" / "advisor" / "local-triage.prompt.md").write_text(
        _local_triage_source("Edited externally.\n"), encoding="utf-8"
    )
    refreshed = live_regions(_live(client, "prompts"))["agent-prompts-edit"]
    assert loaded_digest not in refreshed

    response = client.post(
        "/newsletter/agents/advisor/prompts/local-triage/save",
        data={"digest": loaded_digest, "source": _local_triage_source("Local working draft.\n")},
    )

    assert response.status_code == 409
    assert "Reload the latest prompt source before saving." in response.text
    assert "Local working draft." in response.text


def test_live_detail_runtime_summary_follows_the_team_default(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    assert "Timeout: 2400s" in live_regions(_live(client, "runtime"))["agent-runtime-summary"]

    _config_patch(config_path, lambda raw: raw["teams"]["newsletter"]["runtime"].update(timeout=3000))

    assert "Timeout: 3000s" in live_regions(_live(client, "runtime"))["agent-runtime-summary"]


def test_live_detail_routines_and_permissions_status_follow_remote_config(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    routines = live_regions(_live(client, "routines"))["agent-routines-saved"]
    permissions = live_regions(_live(client, "permissions"))["agent-permissions-saved"]
    assert 'data-live-key="routine:daily-review"' in routines
    assert "weekly-sweep" not in routines

    def patch(raw: dict) -> None:
        _advisor(raw)["routines"].append(
            {
                "id": "weekly-sweep",
                "prompt": {"scope": "blueprint", "name": "pr-review"},
                "schedule": {"every": "7d"},
                "memory": {"scope": "agent"},
            }
        )
        _advisor(raw)["permissions"]["rules"].append(
            {"path": str((tmp_path / "Research" / "editorial").resolve()), "tools": ["read"]}
        )

    _config_patch(config_path, patch)

    assert 'data-live-key="routine:weekly-sweep"' in live_regions(_live(client, "routines"))["agent-routines-saved"]
    assert live_regions(_live(client, "permissions"))["agent-permissions-saved"] != permissions


def test_live_detail_memory_status_follows_the_content_revision_without_creating_memory(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    services = app_mod.app.state.services
    before_tree = filesystem_tree(tmp_path / "memory-store")
    first = live_regions(_live(client, "memory"))["agent-memory-status"]
    assert filesystem_tree(tmp_path / "memory-store") == before_tree
    assert "Default memory: <strong>Agent memory</strong>" in first

    snapshot = ConfigStore(config_path).load()
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="detail-newsletter-advisor",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )
    current = services.memory_store.ensure(resolved)
    services.memory_store.try_update(resolved, current.revision, lambda cur: {**cur.files, "memory.md": b"Changed elsewhere\n"})

    second = live_regions(_live(client, "memory"))["agent-memory-status"]
    assert second != first
    assert current.revision[:12] in first
    assert current.revision[:12] not in second


def test_live_detail_log_membership_and_report_history_update_in_place(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    logs = live_regions(_live(client, "logs"))
    activity = live_regions(_live(client, "activity"))
    assert "advisor-live.out" not in logs["agent-logs-list"]
    assert "0 files" in logs["agent-logs-count"]

    day = tmp_path / "groups" / "newsletter" / "logs" / "2026-07-16"
    day.mkdir(parents=True, exist_ok=True)
    (day / "advisor-live.out").write_text("live\n", encoding="utf-8")
    _write_live_job(
        tmp_path,
        "advisor-live-report",
        "complete",
        started_at="2026-07-16T11:40:00+00:00",
        completed_at="2026-07-16T11:50:00+00:00",
        duration_seconds=600,
        execution_summary="Published the live refresh handoff report.",
    )

    after_logs = live_regions(_live(client, "logs"))
    assert 'data-live-key="log:2026-07-16:advisor-live.out"' in after_logs["agent-logs-list"]
    assert "1 file" in after_logs["agent-logs-count"]
    after_activity = live_regions(_live(client, "activity"))
    assert 'data-live-key="activity:job:advisor-live-report"' in after_activity["agent-activity-entries"]
    assert "Published the live refresh handoff report." in after_activity["agent-activity-entries"]
    assert "data-live-local=\"style\"" in after_activity["agent-activity-entries"]
    assert after_activity["agent-activity-count"] != activity["agent-activity-count"]


def test_live_detail_post_error_pages_embed_the_canonical_snapshot_url(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)

    runtime = client.post(
        "/newsletter/agents/advisor/runtime",
        data={"revision": "stale", "timeout": "900"},
    )
    profile = client.post(
        "/newsletter/agents/advisor/profile",
        data={"revision": "stale", "display_name": "Draft name", "title": "", "emoji": ""},
    )
    permissions = client.post("/newsletter/agents/advisor/permissions", data={"payload": "{"})
    routines = client.post("/newsletter/agents/advisor/routines", data={"payload": "{"})

    for tab, response in (("runtime", runtime), ("profile", profile), ("permissions", permissions), ("routines", routines)):
        assert response.status_code in {409, 422}, tab
        data = registration(response.text)
        assert data["url"] == _live_url(tab), tab
        assert data["binding"]["tab"] == tab
        assert "?__live=1" in data["url"] and "POST" not in data["url"]
        assert set(page_regions(response.text)) == set(live_regions(_live(client, tab))), tab
    assert 'value="900"' in runtime.text


def test_live_detail_post_never_answers_with_a_snapshot(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.post(
        "/newsletter/agents/advisor/runtime?__live=1",
        data={"revision": "stale", "timeout": "900"},
    )

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")


# ── External-change notices ──────────────────────────────────────────────────

_ANNOUNCED = 'role="status" aria-live="polite" aria-atomic="true"'
_CONFIG_NOTICE_TABS = ("profile", "runtime", "prompts", "memory")
_CONFIG_NOTICE = "Agent configuration changed since this form loaded. Reload to see the latest."
_PERMISSIONS_NOTICE = "Saved permissions changed since this form loaded. Reload to see the latest."
_ROUTINES_NOTICE = "Saved routines changed since this form loaded. Reload to see the latest."
_MEMORY_NOTICE = "Memory content changed since this form loaded. Reload to see the latest."


def _revision_markers(html: str) -> dict[str, dict[str, str]]:
    markers: dict[str, dict[str, str]] = {}
    for tag in re.findall(r"<[a-z]+ [^>]*data-live-revision=[^>]*>", html):
        attributes = dict(re.findall(r'([a-z-]+)="([^"]*)"', tag))
        markers[attributes["data-live-scope"]] = attributes
    return markers


def _hidden_value(html: str, name: str) -> str:
    return re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', html).group(1)


@pytest.mark.parametrize("tab", _LIVE_TABS)
def test_live_detail_page_has_an_empty_announced_notice_host_and_its_controller(monkeypatch, tmp_path, raw_config, tab):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    page = client.get(f"/newsletter/agents/advisor/{tab}").text

    assert '<div data-live-notices role="status" aria-live="polite"></div>' in page
    assert '<script src="/static/agent-detail-notices.js"></script>' in page
    assert page.index("agent-detail-notices.js") < page.index("live-refresh.js")


@pytest.mark.parametrize("tab", _LIVE_TABS)
def test_live_detail_status_regions_are_announced_politely(monkeypatch, tmp_path, raw_config, tab):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    page = client.get(f"/newsletter/agents/advisor/{tab}").text

    assert f'<div data-live-region="agent-status" class="sr-only" {_ANNOUNCED}>' in page
    if tab == "permissions":
        assert f'<div data-live-region="agent-permissions-saved" class="sr-only" {_ANNOUNCED}>' in page
    if tab == "routines":
        assert f'<div data-live-region="agent-routines-saved" class="sr-only" {_ANNOUNCED}>' in page
    if tab == "memory":
        assert f'<p class="sr-only" {_ANNOUNCED} data-live-key="memory:revision"' in page


@pytest.mark.parametrize("tab", _LIVE_TABS)
def test_live_detail_config_revision_marker_matches_the_loaded_form_and_snapshot(monkeypatch, tmp_path, raw_config, tab):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get(f"/newsletter/agents/advisor/{tab}").text
    snapshot = live_regions(_live(client, tab))["agent-status"]

    marker = _revision_markers(snapshot).get("config")

    if tab not in _CONFIG_NOTICE_TABS:
        assert marker is None
        return
    assert marker["data-live-revision"] == _hidden_value(page, "revision")
    assert marker["data-live-baseline-input"] == "revision"
    assert marker["data-live-changed"] == _CONFIG_NOTICE
    assert _revision_markers(page)["config"] == marker


def test_live_detail_config_marker_follows_the_config_revision_not_the_loaded_form(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get("/newsletter/agents/advisor/profile").text
    loaded = _hidden_value(page, "revision")
    assert _revision_markers(page)["config"]["data-live-revision"] == loaded

    _config_patch(config_path, lambda raw: _advisor(raw)["identity"].update(title="Renamed Librarian"))

    refreshed = _revision_markers(live_regions(_live(client, "profile"))["agent-status"])["config"]
    assert refreshed["data-live-revision"] != loaded
    assert refreshed["data-live-revision"] == ConfigStore(config_path).load().revision


def test_live_detail_permissions_marker_changes_only_with_saved_permissions(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get("/newsletter/agents/advisor/permissions").text
    loaded = _revision_markers(page)["permissions"]
    assert loaded["data-live-changed"] == _PERMISSIONS_NOTICE
    assert "data-live-baseline-input" not in loaded

    _config_patch(config_path, lambda raw: _advisor(raw)["identity"].update(title="Renamed Librarian"))
    unrelated = _revision_markers(live_regions(_live(client, "permissions"))["agent-permissions-saved"])["permissions"]
    assert unrelated["data-live-revision"] == loaded["data-live-revision"]

    _config_patch(
        config_path,
        lambda raw: _advisor(raw)["permissions"]["rules"].append(
            {"path": str((tmp_path / "Research" / "editorial").resolve()), "tools": ["read"]}
        ),
    )
    changed = _revision_markers(live_regions(_live(client, "permissions"))["agent-permissions-saved"])["permissions"]
    assert changed["data-live-revision"] != loaded["data-live-revision"]


def test_live_detail_routines_marker_follows_the_saved_definitions_only(monkeypatch, tmp_path, raw_config):
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get("/newsletter/agents/advisor/routines").text
    loaded = _revision_markers(page)["routines"]
    assert loaded["data-live-changed"] == _ROUTINES_NOTICE
    assert "data-live-baseline-input" not in loaded

    _config_patch(config_path, lambda raw: _advisor(raw)["identity"].update(title="Renamed Librarian"))
    unrelated = _revision_markers(live_regions(_live(client, "routines"))["agent-routines-saved"])["routines"]
    assert unrelated["data-live-revision"] == loaded["data-live-revision"]

    _config_patch(
        config_path,
        lambda raw: _advisor(raw)["routines"].append(
            {
                "id": "weekly-sweep",
                "prompt": {"scope": "blueprint", "name": "pr-review"},
                "schedule": {"every": "7d"},
                "memory": {"scope": "agent"},
            }
        ),
    )
    changed = _revision_markers(live_regions(_live(client, "routines"))["agent-routines-saved"])["routines"]
    assert changed["data-live-revision"] != loaded["data-live-revision"]


def test_live_detail_memory_marker_is_the_loaded_content_revision(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get("/newsletter/agents/advisor/memory").text
    snapshot = live_regions(_live(client, "memory"))["agent-memory-status"]

    marker = _revision_markers(snapshot)["memory"]

    assert marker["data-live-revision"] == _hidden_value(page, "content_revision")
    assert marker["data-live-baseline-input"] == "content_revision"
    assert marker["data-live-changed"] == _MEMORY_NOTICE
    assert _revision_markers(page)["memory"] == marker


@pytest.mark.parametrize("tab", _LIVE_TABS)
def test_live_detail_jobs_marker_follows_the_active_job_set_and_adds_no_visible_markup(monkeypatch, tmp_path, raw_config, tab):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)
    page = client.get(f"/newsletter/agents/advisor/{tab}").text
    loaded = _revision_markers(page)["jobs"]
    assert loaded["data-live-revision"] == ""
    assert loaded["data-live-changed"] == "Active jobs changed since this page loaded. Now 0 active."
    assert "active job" not in live_regions(_live(client, tab))["agent-header"]

    _write_live_job(tmp_path, "job-live", "running")
    one = _revision_markers(live_regions(_live(client, tab))["agent-status"])["jobs"]
    _write_live_job(tmp_path, "job-live-2", "queued")
    two = _revision_markers(live_regions(_live(client, tab))["agent-status"])["jobs"]

    assert one["data-live-revision"] == "job-live"
    assert one["data-live-changed"] == "Active jobs changed since this page loaded. Now 1 active."
    assert two["data-live-revision"] == "job-live,job-live-2"
    assert two["data-live-changed"] == "Active jobs changed since this page loaded. Now 2 active."
