# Serve Hot Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `christag-agency serve --reload` mode that restarts for project code, UI assets, themes, and `config.yaml` while ignoring Agency runtime records.

**Architecture:** `agency.app` will own one `run_server(host, port, reload=False)` entry point shared by both command-line parsers. Normal mode will continue to pass the in-memory FastAPI application to `uvicorn.run()`. Reload mode will build Uvicorn's `Config`, `Server`, and `WatchFilesReload` directly, rooted at the current working directory, and replace only the supervisor's `watch_filter` with Agency's component-based policy. The console CLI will delegate directly to this function instead of rewriting `sys.argv`.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, WatchFiles, argparse, pytest, VS Code JSONC tasks.

## Global Constraints

- Python remains `>=3.11` as declared in `pyproject.toml`.
- Reload remains opt-in; `christag-agency serve` keeps host `0.0.0.0`, port `8500`, and non-reloading behavior.
- Reload watches `Path.cwd().resolve()` only; source installed outside that root is out of scope.
- Included patterns are exactly `*.py`, `*.html`, `*.css`, `*.js`, `*.json`, `*.yaml`, and `*.yml`.
- The root `config.yaml` is watched, so both manual edits and admin saves restart a reload-mode worker.
- VCS metadata, virtual environments, Python/test/tool caches, package metadata, and Agency `shared/` runtime records must not trigger reloads.
- Keep the existing platform-specific Uvicorn declarations. Add `watchfiles>=0.20` only for Windows, where plain `uvicorn` otherwise falls back to a Python-only stat watcher and ignores custom includes/excludes.
- Do not add browser refresh, state-preserving hot module replacement, custom watcher CLI flags, or production reload behavior.
- Include the user's existing `.vscode/tasks.json` edits in this feature. Preserve the normal `Serve dashboard` task changes, update only the separate hot-reload task during Task 3, and commit the completed task file on the feature branch.

---

## File Structure

- Modify `pyproject.toml`: guarantee the WatchFiles backend on Windows without undoing the platform-specific Uvicorn dependency split.
- Modify `agency/app.py`: define the reload policy, add the shared server launcher, retain first-run setup, and expose `--reload` through `python -m agency.app`.
- Create `tests/test_server.py`: isolate launcher mode, first-run, and real Uvicorn file-filter behavior.
- Modify `agency/cli.py`: expose `serve --reload` and call the shared launcher directly.
- Modify `tests/test_cli.py`: cover help text, argument forwarding, and the removal of `sys.argv` mutation.
- Modify `.vscode/tasks.json`: route the existing hot-reload task through the public CLI while preserving the user's current task edits.
- Modify `README.md`: show the supported executable and development reload command near Quick Start.
- Modify `kb/getting-started.md`: document reload scope and the expected config-save restart.

### Task 1: Shared Server Launcher And Reload Policy

**Files:**
- Modify: `pyproject.toml:5-16`
- Modify: `agency/app.py:3307-3330`
- Create: `tests/test_server.py`

**Interfaces:**
- Consumes: existing `agency.app.app`, `CONFIG_PATH`, `save_config(config: dict) -> None`, and `reload_groups() -> None`.
- Produces: `run_server(host: str, port: int, reload: bool = False) -> None`, `_AgencyReloadFilter`, `_create_reload_supervisor()`, `_run_reload_server()`, and immutable reload-policy tuples used to configure and test Uvicorn.

- [ ] **Step 1: Write failing server-launcher tests**

Create `tests/test_server.py` with this complete content:

```python
"""Tests for web server startup and reload configuration."""

from pathlib import Path

import pytest
import uvicorn
import yaml

from agency import app as app_mod


def _configure_existing_config(tmp_path: Path, monkeypatch) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "agency:\n  title: Agency\n  default_group: ''\ngroups: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    monkeypatch.setattr(app_mod, "reload_groups", lambda: None)
    return config_path


def test_run_server_normal_mode_uses_in_memory_app(tmp_path, monkeypatch):
    _configure_existing_config(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        app_mod.uvicorn,
        "run",
        lambda application, **options: calls.append((application, options)),
    )

    app_mod.run_server(host="127.0.0.1", port=8600)

    assert calls == [(app_mod.app, {"host": "127.0.0.1", "port": 8600})]


def test_run_server_reload_mode_uses_import_string_and_project_policy(
    tmp_path, monkeypatch
):
    _configure_existing_config(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    events = []

    class FakeConfig:
        def __init__(self, application, **options):
            self.reload_dirs = [Path(path) for path in options["reload_dirs"]]
            events.append(("config", application, options))

        def load_app(self):
            events.append("load_app")

        def bind_socket(self):
            events.append("bind_socket")
            return "socket"

    class FakeServer:
        def __init__(self, config):
            events.append(("server", config))

        def run(self, sockets=None):
            raise AssertionError("worker target must not run in the launcher process")

    class FakeSupervisor:
        def __init__(self, config, target, sockets):
            self.watch_filter = None
            events.append(("supervisor", config, target, sockets))

        def run(self):
            events.append(("run", self.watch_filter))

    monkeypatch.setattr(app_mod.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(app_mod.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(app_mod, "WatchFilesReload", FakeSupervisor)

    app_mod.run_server(host="127.0.0.1", port=8601, reload=True)

    assert events[0] == (
        "config",
        "agency.app:app",
        {
            "host": "127.0.0.1",
            "port": 8601,
            "reload": True,
            "reload_dirs": [str(tmp_path.resolve())],
            "reload_includes": list(app_mod.RELOAD_INCLUDES),
        },
    )
    assert events[1] == "load_app"
    assert events[2][0] == "server"
    assert events[3] == "bind_socket"
    assert events[4][0] == "supervisor"
    assert events[4][3] == ["socket"]
    assert events[5][0] == "run"
    assert isinstance(events[5][1], app_mod._AgencyReloadFilter)
    assert events[5][1].root == tmp_path.resolve()


def test_reload_supervisor_rejects_future_artifacts_at_any_depth(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    config = uvicorn.Config(
        "agency.app:app",
        reload=True,
        reload_dirs=[str(root.resolve())],
        reload_includes=list(app_mod.RELOAD_INCLUDES),
    )
    server = uvicorn.Server(config)
    supervisor = app_mod._create_reload_supervisor(config, server, [])
    assert supervisor.reloader_name == "WatchFiles"

    watched_paths = [
        root / "agency" / "app.py",
        root / "agency" / "templates" / "base.html",
        root / "agency" / "static" / "app.css",
        root / "agency" / "static" / "sw.js",
        root / "agency" / "static" / "manifest.json",
        root / "agency" / "themes" / "workshop.yaml",
        root / "agency" / "themes" / "local.yml",
        root / "config.yaml",
    ]
    excluded_paths = [
        root / "deep" / ".git" / "state.json",
        root / "deep" / ".venv" / "Lib" / "site-packages" / "tool.py",
        root / "deep" / "venv" / "Lib" / "tool.py",
        root / "deep" / "__pycache__" / "module.py",
        root / "deep" / ".pytest_cache" / "state.json",
        root / "deep" / ".mypy_cache" / "state.json",
        root / "deep" / ".ruff_cache" / "state.json",
        root / "deep" / "shared" / "jobs" / "job.yaml",
        root / "deep" / "package.egg-info" / "metadata.json",
    ]
    for path in [*watched_paths, *excluded_paths]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("probe", encoding="utf-8")

    for path in watched_paths:
        assert supervisor.watch_filter(path.resolve()), path
    for path in excluded_paths:
        assert not supervisor.watch_filter(path.resolve()), path

    assert not supervisor.watch_filter((tmp_path / "outside.py").resolve())
    assert not supervisor.watch_filter((root / "README.md").resolve())


def test_run_server_creates_config_before_starting_uvicorn(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.yaml"
    events = []
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)

    def fake_reload_groups():
        assert config_path.exists()
        events.append("reload_groups")

    def fake_uvicorn_run(application, **options):
        assert config_path.exists()
        assert application is app_mod.app
        assert options == {"host": "127.0.0.1", "port": 8602}
        events.append("uvicorn.run")

    monkeypatch.setattr(app_mod, "reload_groups", fake_reload_groups)
    monkeypatch.setattr(app_mod.uvicorn, "run", fake_uvicorn_run)

    app_mod.run_server(host="127.0.0.1", port=8602)

    assert events == ["reload_groups", "uvicorn.run"]
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "agency": {"title": "Agency", "default_group": ""},
        "groups": {},
    }
    output = capsys.readouterr().out
    assert f"First run — created config.yaml in {tmp_path}" in output
    assert "Visit http://localhost:8602/admin/" in output
```

- [ ] **Step 2: Run the new tests to verify they fail for the missing launcher contract**

Run:

```powershell
python -m pytest tests/test_server.py -v
```

Expected: normal and first-run tests pass while reload tests fail because `WatchFilesReload`, `_AgencyReloadFilter`, or `_create_reload_supervisor` are not yet exposed by `agency.app`. If collection instead reports that `watchfiles` is missing, that is also the expected pre-dependency failure on a clean Windows install.

- [ ] **Step 3: Guarantee WatchFiles on Windows**

In `pyproject.toml`, keep both existing Uvicorn declarations and add the direct Windows watcher dependency immediately after them:

```toml
dependencies = [
    "fastapi>=0.116",
    "starlette<1.0",
    "uvicorn; sys_platform == 'win32'",
    "uvicorn[standard]; sys_platform != 'win32'",
    "watchfiles>=0.20; sys_platform == 'win32'",
    "jinja2",
    "markdown",
    "pyyaml",
    "markupsafe",
    "python-multipart",
    "pywin32; sys_platform == 'win32'",
]
```

- [ ] **Step 4: Refresh the editable install and verify the watcher backend is importable**

Run:

```powershell
python -m pip install -e .
python -c "import watchfiles; print(watchfiles.__version__)"
```

Expected: editable installation succeeds and the second command prints a WatchFiles version at least `0.20`.

- [ ] **Step 5: Implement the shared launcher and module flag**

Import `WatchFilesReload` next to the existing Uvicorn import:

```python
from uvicorn.supervisors.watchfilesreload import WatchFilesReload
```

Replace the existing server-only `main()` block at the end of `agency/app.py` with:

```python
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
        save_config({"agency": {"title": "Agency", "default_group": ""}, "groups": {}})
        print(f"First run — created config.yaml in {CONFIG_PATH.parent}")
        print(f"Visit http://localhost:{port}/admin/ to set up your first agent group.")

    reload_groups()
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
```

- [ ] **Step 6: Run the focused server tests**

Run:

```powershell
python -m pytest tests/test_server.py -v
```

Expected: all focused server tests pass, including future deep artifact rejection, WatchFiles supervisor identity, normal mode, first-run setup, and error propagation.

- [ ] **Step 7: Commit the independently working server launcher**

Run:

```powershell
git add pyproject.toml agency/app.py tests/test_server.py
git commit -m "feat(server): add opt-in reload launcher"
```

Expected: one commit containing the watcher dependency, shared launcher, and four focused tests. `.vscode/tasks.json` remains unchanged from the committed feature baseline.

### Task 2: Public CLI Wiring

**Files:**
- Modify: `agency/cli.py:8-17,81-91,369-374`
- Modify: `tests/test_cli.py:1-25`

**Interfaces:**
- Consumes: `run_server(host: str, port: int, reload: bool = False) -> None` from Task 1.
- Produces: `christag-agency serve [--host HOST] [--port PORT] [--reload]`, with direct argument forwarding and no process-global argument mutation.

- [ ] **Step 1: Add failing CLI contract tests**

Replace `tests/test_cli.py` with:

```python
"""Tests for the CLI interface."""

from argparse import Namespace
import subprocess
import sys

from agency import cli


def test_cli_help_shows_subcommands():
    """Running agency --help should list available subcommands."""
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "inbox" in result.stdout
    assert "status" in result.stdout


def test_cli_no_args_shows_help():
    """Running agency with no args should show help."""
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert "serve" in output or result.returncode == 0


def test_cli_serve_help_shows_reload():
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "serve", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--reload" in result.stdout


def test_cmd_serve_forwards_arguments_without_mutating_sys_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_server", lambda **options: calls.append(options))
    original_argv = sys.argv.copy()

    cli.cmd_serve(Namespace(host="127.0.0.1", port=8700, reload=True))

    assert calls == [{"host": "127.0.0.1", "port": 8700, "reload": True}]
    assert sys.argv == original_argv
```

- [ ] **Step 2: Run the CLI tests to verify the new contract fails**

Run:

```powershell
python -m pytest tests/test_cli.py -v
```

Expected: the existing two tests pass; `test_cli_serve_help_shows_reload` fails because help lacks `--reload`, and the forwarding test fails because `agency.cli` has no imported `run_server` attribute.

- [ ] **Step 3: Replace the argument bridge with direct launcher delegation**

Add `run_server` to the existing `agency.app` import in `agency/cli.py`:

```python
from agency.app import (
    load_config, reload_groups, get_agency_config, get_group,
    list_observations, list_proposals, list_decisions,
    collect_agents_with_identity, extract_display_title,
    parse_frontmatter, update_frontmatter_field,
    GROUPS, CONFIG, CONFIG_PATH, run_server,
)
```

Replace `cmd_serve()` with:

```python
def cmd_serve(args):
    """Start the web server."""
    run_server(host=args.host, port=args.port, reload=args.reload)
```

Add the new flag to the existing `serve` parser:

```python
    p = sub.add_parser("serve", help="Start the web dashboard")
    p.add_argument("--port", type=int, default=8500)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--reload", action="store_true", help="Restart when project files change")
```

Do not remove `sys`; the rest of `agency/cli.py` still uses it for errors and process exits.

- [ ] **Step 4: Run both server and CLI tests**

Run:

```powershell
python -m pytest tests/test_cli.py tests/test_server.py -v
```

Expected: `10 passed`.

- [ ] **Step 5: Verify the installed command surface**

Run:

```powershell
christag-agency serve --help
python -m agency.app --help
```

Expected: both help screens list `--host`, `--port`, and `--reload`; neither command starts a server.

- [ ] **Step 6: Commit the public CLI behavior**

Run:

```powershell
git add agency/cli.py tests/test_cli.py
git commit -m "feat(cli): expose serve reload option"
```

Expected: one commit containing CLI delegation and its tests. `.vscode/tasks.json` remains unchanged from the committed feature baseline.

### Task 3: Development Task, Documentation, And End-To-End Verification

**Files:**
- Modify: `.vscode/tasks.json:106-135` while preserving the already committed normal-task edits
- Modify: `README.md:112-125`
- Modify: `kb/getting-started.md:9-18`

**Interfaces:**
- Consumes: the installed `christag-agency serve --reload` command from Task 2.
- Produces: one VS Code hot-reload task using the supported CLI and concise development documentation describing watcher scope and config-save restarts.

- [ ] **Step 1: Capture the existing task-file diff before editing**

Run:

```powershell
git diff $(git merge-base master HEAD) HEAD -- .vscode/tasks.json
```

Expected: the feature diff includes the existing changes to the normal `Serve dashboard` task. Keep those lines exactly as they are and edit only the separate `Serve dashboard (hot-reload)` object.

- [ ] **Step 2: Run a task/docs contract check and verify it fails before the edits**

Run:

```powershell
python -c "from pathlib import Path; task=Path('.vscode/tasks.json').read_text(); readme=Path('README.md').read_text(); guide=Path('kb/getting-started.md').read_text(); assert '\"--reload\"' in task and 'agency.app:app' not in task and 'christag-agency serve --reload' in readme and 'christag-agency serve --reload' in guide"
```

Expected: `AssertionError` because the task still bypasses the CLI and the development command is not documented.

- [ ] **Step 3: Route the hot-reload VS Code task through the public command**

In `.vscode/tasks.json`, replace only the complete `Serve dashboard (hot-reload)` task object with:

```jsonc
    {
      "args": [
        "serve",
        "--reload",
        "--host",
        "127.0.0.1"
      ],
      "command": "christag-agency",
      "detail": "Start the Agency dashboard with hot-reload on http://127.0.0.1:8500",
      "group": "none",
      "isBackground": true,
      "label": "Serve dashboard (hot-reload)",
      "problemMatcher": {
        "background": {
          "activeOnStart": true,
          "beginsPattern": ".*Started server process.*",
          "endsPattern": ".*Application startup complete.*"
        },
        "pattern": {
          "file": 1,
          "location": 1,
          "message": 1,
          "regexp": "^(.*)$"
        }
      },
      "type": "shell"
    },
```

- [ ] **Step 4: Document normal and development startup**

In `README.md`, replace the Quick Start command block and the paragraph through the dashboard URL with:

````markdown
## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/christag-agency serve
```

On first run, a setup wizard walks you through pointing Agency at your agent directory. It auto-detects your agents, creates the shared folder structure, and drops you into your dashboard.

Visit `http://localhost:8500`.

For development, start the same server with reload enabled:

```bash
.venv/bin/christag-agency serve --reload
```

Reload mode watches project code, templates, static assets, themes, and `config.yaml`. Saving Agency runtime records under a group's `shared/` directory does not restart the server.
````

In `kb/getting-started.md`, replace the First Run command with `christag-agency serve`, then insert this section after the first-run explanation:

````markdown
## Development Reload

```bash
christag-agency serve --reload
```

Reload mode watches the current working directory for Python code, templates, static assets, themes, and YAML/JSON configuration. Changes to `config.yaml`, including saves from the admin UI, restart the development server. Runtime records under group `shared/` directories are excluded.
````

- [ ] **Step 5: Rerun the task/docs contract check**

Run:

```powershell
python -c "from pathlib import Path; task=Path('.vscode/tasks.json').read_text(); readme=Path('README.md').read_text(); guide=Path('kb/getting-started.md').read_text(); assert '\"--reload\"' in task and 'agency.app:app' not in task and 'christag-agency serve --reload' in readme and 'christag-agency serve --reload' in guide; print('reload task and docs verified')"
```

Expected: prints `reload task and docs verified`.

- [ ] **Step 6: Run the complete automated test suite**

Run:

```powershell
python -m pytest tests/ -q
```

Expected: the suite completes with zero failures.

- [ ] **Step 7: Smoke-test a real Windows worker restart**

In terminal A, run:

```powershell
christag-agency serve --reload --host 127.0.0.1 --port 8501
```

Expected: Uvicorn reports a reloader process using `WatchFiles`, followed by application startup on `http://127.0.0.1:8501`.

In terminal B, run:

```powershell
Set-Content -Path reload_smoke_probe.py -Value '# reload smoke probe'
```

Expected in terminal A: WatchFiles reports `reload_smoke_probe.py` changed, the worker shuts down, and a new server process reaches application startup. Then clean up in terminal B:

```powershell
Remove-Item reload_smoke_probe.py
```

Stop terminal A with `Ctrl+C` after the replacement worker starts. Confirm `reload_smoke_probe.py` no longer exists.

- [ ] **Step 8: Commit the development task and documentation**

Run:

```powershell
git add .vscode/tasks.json README.md kb/getting-started.md
git commit -m "docs: document serve hot reload"
git status --short
```

Expected: the commit includes the completed hot-reload task and documentation. No source, test, dependency, task, probe, or documentation files remain uncommitted.
