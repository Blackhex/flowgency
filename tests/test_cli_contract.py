from __future__ import annotations

import ast
from dataclasses import dataclass
import io
import json
from pathlib import Path
import sys

import pytest
import yaml

from agency import cli
from agency.configuration import ConfigStore
from agency.configuration.effective import resolve_effective_policy
from agency.configuration.models import MemorySelector
from agency.fs.locks import exclusive_lock
from agency.jobs import JobHandle, JobSubmissionError
from agency.memory import resolve_memory_selector
from agency.web.dependencies import build_services


@dataclass(frozen=True)
class CliResult:
    exit_code: int
    stdout: str
    stderr: str


def _write_blueprint(root: Path) -> None:
    blueprint = root / "builder-blueprint"
    skill = blueprint / ".agents" / "skills" / "daily-review"
    skill.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_text("# Builder\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: daily-review\ndescription: Review current work.\n---\n\n# Daily review\n",
        encoding="utf-8",
    )


@pytest.fixture
def cli_config(tmp_path):
    group_path = tmp_path / "newsletter"
    for name in ("observations", "proposals", "decisions", "jobs", "logs"):
        (group_path / "shared" / name).mkdir(parents=True)
    _write_blueprint(tmp_path / "agent-library")
    raw = {
        "schema_version": 2,
        "agency": {
            "title": "Contract Agency",
            "default_group": "newsletter",
            "agent_library": "agent-library",
            "compilation_cache": "compiled-agents",
            "memory_store": "memory",
        },
        "memory": {"channels": {"support": {"display_name": "Support Desk"}}},
        "groups": {
            "newsletter": {
                "name": "Newsletter",
                "path": "newsletter",
                "default_integration": "script",
                "runtime": {
                    "timeout": 321,
                    "sandbox": {"mode": "unrestricted"},
                    "tools": {"mode": "all"},
                },
                "agents": [
                    {
                        "name": "builder",
                        "blueprint": "builder-blueprint",
                        "integration": "script",
                        "integration_config": {"command": "echo {prompt_file}"},
                        "identity": {"display_name": "Build Captain", "title": "Lead", "emoji": ""},
                        "default_memory": {"scope": "agent"},
                        "routines": [
                            {
                                "id": "daily-review",
                                "skill": "daily-review",
                                "arguments": ["--brief"],
                                "schedule": {"every": "6h"},
                                "memory": {"scope": "routine"},
                            }
                        ],
                    }
                ],
            }
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


@pytest.fixture
def cli_runner(capsys, monkeypatch):
    def run(*arguments: str, config: Path | str | None = None, stdin: str = "") -> CliResult:
        argv = list(arguments)
        if config is not None:
            argv = ["--config", str(config), *argv]
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        exit_code = cli.run(argv)
        captured = capsys.readouterr()
        return CliResult(exit_code, captured.out, captured.err)

    return run


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    if not root.exists():
        return ()
    rows = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        rows.append((relative, "dir" if path.is_dir() else "file", None if path.is_dir() else path.read_bytes()))
    return tuple(rows)


def _resolved_agent_memory(config_path: Path):
    services = build_services(config_path)
    assert services.startup_error is None
    snapshot = services.config_store.load()
    return services, resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="cli-preview-newsletter-builder",
        group_key="newsletter",
        agent_name="builder",
        routine_id=None,
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )


def test_cli_has_no_top_level_app_import_or_mutable_app_globals():
    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    app_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            any(alias.name == "agency.app" for alias in getattr(node, "names", ()))
            or getattr(node, "module", None) == "agency.app"
        )
    ]
    assert app_imports == []
    forbidden = {
        "CONFIG",
        "GROUPS",
        "CONFIG_PATH",
        "get_group",
        "get_agency_config",
        "collect_agents_with_identity",
    }
    assert forbidden.isdisjoint({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})


def test_no_command_returns_success_and_prints_help(cli_runner):
    result = cli_runner()
    assert result.exit_code == 0
    assert "agents" in result.stdout


def test_argparse_usage_returns_two_without_escaping_handler(cli_runner):
    result = cli_runner("agent", "run")
    assert result.exit_code == 2


def test_invalid_config_has_shared_human_issue_and_exit_three(tmp_path, cli_runner):
    path = tmp_path / "invalid-config.yaml"
    path.write_text("schema_version: 1\n", encoding="utf-8")
    result = cli_runner("status", config=path)
    assert result.exit_code == 3
    assert "configuration" in result.stderr.lower()
    assert "schema_version" in result.stderr


def test_invalid_config_json_shape_is_exact(tmp_path, cli_runner):
    path = tmp_path / "invalid-config.yaml"
    path.write_text("schema_version: 1\n", encoding="utf-8")
    result = cli_runner("status", "--json", config=path)
    assert result.exit_code == 3
    payload = json.loads(result.stderr)
    assert set(payload) == {"code", "message", "issues"}
    assert payload["code"] == "invalid-config"
    assert payload["message"] == "Configuration is invalid"
    assert payload["issues"]
    assert set(payload["issues"][0]) == {
        "code", "scope", "field", "message", "corrective_hint"
    }


def test_agents_json_uses_friendly_stable_fields_and_policy_parity(cli_config, cli_runner):
    result = cli_runner("agents", "--group", "newsletter", "--json", config=cli_config)
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    agent = payload[0]
    assert set(agent) >= {
        "name", "display_name", "blueprint", "integration", "health", "job",
        "routine", "memory", "cache", "effective_policy",
    }
    assert "memory_hash" not in agent
    assert "memory_hash" not in json.dumps(agent)
    snapshot = ConfigStore(cli_config).load()
    expected = resolve_effective_policy(snapshot.config, "newsletter", "builder")
    assert agent["effective_policy"] == {
        "timeout": expected.timeout,
        "sandbox": {
            "mode": expected.sandbox_mode,
            "roots": [str(path).replace("\\", "/") for path in expected.sandbox_roots],
        },
        "tools": {"mode": expected.tools.mode, "names": list(expected.tools.names)},
    }
    assert agent["routine"] == ["daily-review"]
    assert agent["memory"] == "Agent memory"


@pytest.mark.parametrize(
    "arguments",
    [
        ("status", "--json"),
        ("agents", "--group", "newsletter", "--json"),
        ("inbox", "--group", "newsletter", "--json"),
        ("observations", "--group", "newsletter", "--json"),
        ("proposals", "--group", "newsletter", "--json"),
        ("decisions", "--group", "newsletter", "--json"),
        ("jobs", "--group", "newsletter", "--json"),
        ("logs", "--group", "newsletter"),
    ],
)
def test_read_commands_do_not_change_config_cache_or_memory(cli_config, cli_runner, arguments):
    roots = [cli_config.parent, cli_config.parent / "compiled-agents", cli_config.parent / "memory"]
    before = tuple(_tree_snapshot(root) for root in roots)
    result = cli_runner(*arguments, config=cli_config)
    assert result.exit_code == 0, result.stderr
    assert tuple(_tree_snapshot(root) for root in roots) == before


def test_agent_show_reports_instance_without_hashes(cli_config, cli_runner):
    result = cli_runner("agent", "show", "builder", "--group", "newsletter", "--json", config=cli_config)
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "builder"
    assert payload["display_name"] == "Build Captain"
    assert payload["routines"][0]["id"] == "daily-review"
    assert "memory_hash" not in json.dumps(payload)


def test_agent_run_submits_existing_routine_with_allowed_memory_override(cli_config, cli_runner, monkeypatch):
    submitted = []
    monkeypatch.setattr(
        cli,
        "submit_job_request",
        lambda request: submitted.append(request) or JobHandle(request.job_id, "queued", Path("job.yaml"), None),
    )
    result = cli_runner(
        "agent", "run", "builder", "daily-review", "--group", "newsletter",
        "--memory-scope", "channel", "--memory-channel", "support", "--json",
        config=cli_config,
    )
    assert result.exit_code == 0
    assert len(submitted) == 1
    request = submitted[0]
    assert request.group_key == "newsletter"
    assert request.agent_name == "builder"
    assert request.routine_id == "daily-review"
    assert request.trigger == "manual_prompt"
    assert request.memory_override == MemorySelector(scope="channel", channel="support")
    assert json.loads(result.stdout)["status"] == "queued"


def test_agent_run_rejects_unknown_routine_without_submission(cli_config, cli_runner, monkeypatch):
    submitted = []
    monkeypatch.setattr(cli, "submit_job_request", submitted.append)
    result = cli_runner(
        "agent", "run", "builder", "missing", "--group", "newsletter",
        config=cli_config,
    )
    assert result.exit_code == 3
    assert submitted == []


def test_agent_run_job_failure_returns_operational_exit(cli_config, cli_runner, monkeypatch):
    def fail(request):
        raise JobSubmissionError("launcher failed", Path("job.yaml"))

    monkeypatch.setattr(cli, "submit_job_request", fail)
    result = cli_runner(
        "agent", "run", "builder", "daily-review", "--group", "newsletter",
        config=cli_config,
    )
    assert result.exit_code == 1
    assert "launcher failed" in result.stderr


def test_memory_show_json_exposes_revision_and_content_without_hash(cli_config, cli_runner):
    services, resolved = _resolved_agent_memory(cli_config)
    snapshot = services.memory_store.ensure(resolved)
    result = cli_runner(
        "memory", "show", "builder", "--group", "newsletter", "--scope", "agent", "--json",
        config=cli_config,
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "scope": "Agent memory",
        "revision": snapshot.revision,
        "files": {"memory.md": ""},
    }


def test_memory_save_uses_expected_revision(cli_config, cli_runner):
    services, resolved = _resolved_agent_memory(cli_config)
    snapshot = services.memory_store.ensure(resolved)
    result = cli_runner(
        "memory", "save", "builder", "--group", "newsletter", "--scope", "agent",
        "--file", "memory.md", "--revision", snapshot.revision, "--json",
        config=cli_config,
        stdin="# Updated\n",
    )
    assert result.exit_code == 0
    saved = services.memory_store.read(resolved)
    assert saved.files == {"memory.md": b"# Updated\n"}
    assert json.loads(result.stdout) == {"revision": saved.revision, "file": "memory.md"}


def test_memory_save_stale_revision_preserves_newer_content(cli_config, cli_runner):
    services, resolved = _resolved_agent_memory(cli_config)
    stale = services.memory_store.ensure(resolved)
    newer = services.memory_store.try_save(resolved, stale.revision, {"memory.md": b"# Newer\n"})
    result = cli_runner(
        "memory", "save", "builder", "--group", "newsletter", "--scope", "agent",
        "--file", "memory.md", "--revision", stale.revision,
        config=cli_config,
        stdin="# Stale\n",
    )
    assert result.exit_code == 1
    assert "changed" in result.stderr.lower()
    assert services.memory_store.read(resolved).revision == newer.revision


def test_memory_save_busy_returns_resource_busy_exit(cli_config, cli_runner):
    services, resolved = _resolved_agent_memory(cli_config)
    snapshot = services.memory_store.ensure(resolved)
    with exclusive_lock(services.memory_store._lock_path(resolved), wait=True):
        result = cli_runner(
            "memory", "save", "builder", "--group", "newsletter", "--scope", "agent",
            "--file", "memory.md", "--revision", snapshot.revision,
            config=cli_config,
            stdin="# Busy\n",
        )
    assert result.exit_code == 4
    assert "busy" in result.stderr.lower()


def test_lock_cancellation_maps_to_resource_busy_exit():
    from agency.cli_output import ExitCode, exit_code_for
    from agency.fs.locks import LockCancelledError

    assert exit_code_for(LockCancelledError("cancelled")) == ExitCode.RESOURCE_BUSY