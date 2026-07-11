"""Tests for web server startup and reload configuration."""

from pathlib import Path

import uvicorn
import yaml
from uvicorn.supervisors.watchfilesreload import FileFilter

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
    calls = []
    monkeypatch.setattr(
        app_mod.uvicorn,
        "run",
        lambda application, **options: calls.append((application, options)),
    )

    app_mod.run_server(host="127.0.0.1", port=8601, reload=True)

    assert calls == [
        (
            "agency.app:app",
            {
                "host": "127.0.0.1",
                "port": 8601,
                "reload": True,
                "reload_dirs": [str(tmp_path.resolve())],
                "reload_includes": list(app_mod.RELOAD_INCLUDES),
                "reload_excludes": app_mod._reload_excludes(tmp_path.resolve()),
            },
        )
    ]


def test_reload_policy_watches_project_files_and_ignores_runtime_data(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    watched_paths = [
        tmp_path / "agency" / "app.py",
        tmp_path / "agency" / "templates" / "base.html",
        tmp_path / "agency" / "static" / "app.css",
        tmp_path / "agency" / "static" / "sw.js",
        tmp_path / "agency" / "static" / "manifest.json",
        tmp_path / "agency" / "themes" / "workshop.yaml",
        tmp_path / "agency" / "themes" / "local.yml",
        tmp_path / "config.yaml",
    ]
    for path in watched_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("probe", encoding="utf-8")

    existing_job = tmp_path / "existing" / "shared" / "jobs" / "job.yaml"
    existing_job.parent.mkdir(parents=True)
    existing_job.write_text("status: running\n", encoding="utf-8")
    artifact_paths = [
        tmp_path / ".git" / "state.json",
        tmp_path / ".venv" / "Lib" / "site-packages" / "tool.py",
        tmp_path / ".pytest_cache" / "state.json",
        tmp_path / "christag_agency.egg-info" / "metadata.json",
    ]
    for path in artifact_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("probe", encoding="utf-8")

    config = uvicorn.Config(
        "agency.app:app",
        reload=True,
        reload_dirs=[str(tmp_path.resolve())],
        reload_includes=list(app_mod.RELOAD_INCLUDES),
        reload_excludes=app_mod._reload_excludes(tmp_path.resolve()),
    )
    file_filter = FileFilter(config)

    future_job = tmp_path / "future" / "shared" / "jobs" / "job.yaml"
    future_job.parent.mkdir(parents=True)
    future_job.write_text("status: queued\n", encoding="utf-8")

    for path in watched_paths:
        assert file_filter(path.resolve()), path
    assert not file_filter(existing_job.resolve())
    assert not file_filter(future_job.resolve())
    for path in artifact_paths:
        assert not file_filter(path.resolve()), path


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
