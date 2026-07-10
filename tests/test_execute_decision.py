from pathlib import Path

import agency.app as app_mod
from agency.config import SandboxSpec
from agency.integrations import RunResult, FileChange


def test_execute_decision_passes_sandbox_root(tmp_path, monkeypatch):
    """Verify execute_decision resolves sandbox_root from group config and passes it to integration.run"""
    group_path = tmp_path / "agents"
    (group_path / "shared" / "decisions").mkdir(parents=True)
    (group_path / "shared" / "logs").mkdir(parents=True)
    agent_dir = group_path / "advisor"
    agent_dir.mkdir()

    decision = group_path / "shared" / "decisions" / "prop.md"
    decision.write_text("---\nexecution_status: pending\n---\n")

    captured = {}

    class FakeIntegration:
        name = "copilot"
        supports_execution = True
        supports_sandbox = True

        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            captured["sandbox_root"] = sandbox_root
            class R:
                exit_code = 0
                stdout = "ok"
                stderr = ""
                duration_seconds = 0.1
            return R()

    monkeypatch.setitem(
        app_mod.GROUPS,
        "grp",
        {"path": str(group_path), "sandbox_root": str(tmp_path / "repo"),
         "_agents_normalized": [{"name": "advisor", "integration": "copilot"}]},
    )
    monkeypatch.setattr(app_mod, "get_agent_integration", lambda g, a: FakeIntegration())

    app_mod.execute_decision(decision, group_path, "advisor", "prop", group_key="grp")

    assert captured["sandbox_root"] == SandboxSpec(
        roots=(Path(str(tmp_path / "repo")),), allowed_tools=()
    )


def test_execute_decision_persists_agent_log_and_changes(tmp_path, monkeypatch):
    """Verify execute_decision persists executed_by, execution_log, and changed_files to decision frontmatter."""
    group_path = tmp_path / "agents"
    (group_path / "shared" / "decisions").mkdir(parents=True)
    (group_path / "shared" / "logs").mkdir(parents=True)
    agent_dir = group_path / "worker"
    agent_dir.mkdir()

    decision = group_path / "shared" / "decisions" / "test-decision.md"
    decision.write_text("---\nexecution_status: pending\n---\n")

    class FakeIntegration:
        name = "copilot"
        supports_execution = True

        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            return RunResult(
                exit_code=0,
                stdout="did work",
                stderr="",
                duration_seconds=1.0,
                changed_files=[FileChange("a.txt", "modified", 2, 1)],
            )

    monkeypatch.setitem(
        app_mod.GROUPS,
        "test-grp",
        {"path": str(group_path),
         "_agents_normalized": [{"name": "worker", "integration": "copilot"}]},
    )
    monkeypatch.setattr(app_mod, "get_agent_integration", lambda g, a: FakeIntegration())

    app_mod.execute_decision(decision, group_path, "worker", "test-proposal", group_key="test-grp")

    meta, _ = app_mod.parse_frontmatter(decision.read_text())
    assert meta["executed_by"] == "worker"
    # (c): Strengthen to verify absolute path
    assert Path(meta["execution_log"]).is_absolute()
    assert meta["execution_log"].endswith(".out")
    assert meta["changed_files"] == [
        {"path": "a.txt", "status": "modified", "lines_added": 2, "lines_removed": 1}
    ]


def test_execute_decision_persists_empty_changed_files(tmp_path, monkeypatch):
    """M1: Verify execute_decision always writes changed_files, including empty list on retry."""
    group_path = tmp_path / "agents"
    (group_path / "shared" / "decisions").mkdir(parents=True)
    (group_path / "shared" / "logs").mkdir(parents=True)
    agent_dir = group_path / "worker"
    agent_dir.mkdir()

    decision = group_path / "shared" / "decisions" / "retry.md"
    decision.write_text("---\nexecution_status: pending\n---\n")

    class FakeIntegration:
        name = "copilot"
        supports_execution = True

        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            return RunResult(
                exit_code=0,
                stdout="no changes made",
                stderr="",
                duration_seconds=0.5,
                changed_files=[],  # Empty list — no changes
            )

    monkeypatch.setitem(
        app_mod.GROUPS,
        "test-grp",
        {"path": str(group_path),
         "_agents_normalized": [{"name": "worker", "integration": "copilot"}]},
    )
    monkeypatch.setattr(app_mod, "get_agent_integration", lambda g, a: FakeIntegration())

    app_mod.execute_decision(decision, group_path, "worker", "retry-proposal", group_key="test-grp")

    meta, _ = app_mod.parse_frontmatter(decision.read_text())
    assert meta["executed_by"] == "worker"
    assert meta["execution_log"].endswith(".out")
    assert meta["changed_files"] == []  # Empty list persisted


def test_execute_decision_sets_executed_by_before_run_completes(tmp_path, monkeypatch):
    """executed_by must be persisted while the run is in progress, so the UI can
    show which agent is working before execution finishes."""
    group_path = tmp_path / "agents"
    (group_path / "shared" / "decisions").mkdir(parents=True)
    (group_path / "shared" / "logs").mkdir(parents=True)
    agent_dir = group_path / "worker"
    agent_dir.mkdir()

    decision = group_path / "shared" / "decisions" / "inflight.md"
    decision.write_text("---\nexecution_status: pending\n---\n")

    seen = {}

    class FakeIntegration:
        name = "copilot"
        supports_execution = True

        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            # Inspect the decision file mid-run: executed_by should already be set.
            meta, _ = app_mod.parse_frontmatter(decision.read_text())
            seen["executed_by"] = meta.get("executed_by")
            seen["execution_status"] = meta.get("execution_status")
            return RunResult(
                exit_code=0, stdout="ok", stderr="", duration_seconds=0.1,
                changed_files=[],
            )

    monkeypatch.setitem(
        app_mod.GROUPS,
        "test-grp",
        {"path": str(group_path),
         "_agents_normalized": [{"name": "worker", "integration": "copilot"}]},
    )
    monkeypatch.setattr(app_mod, "get_agent_integration", lambda g, a: FakeIntegration())

    app_mod.execute_decision(decision, group_path, "worker", "inflight-proposal", group_key="test-grp")

    assert seen["executed_by"] == "worker"
    assert seen["execution_status"] == "running"


def test_recover_orphaned_executions_resets_running_to_failed(tmp_path, monkeypatch):
    """A decision left at execution_status 'running' after a restart/crash is
    orphaned (its in-process background task is gone). Startup recovery must
    reset it to 'failed' so it surfaces a retry action, while leaving decisions
    in any other state untouched."""
    group_path = tmp_path / "agents"
    decisions = group_path / "shared" / "decisions"
    decisions.mkdir(parents=True)

    stuck = decisions / "stuck.md"
    stuck.write_text("---\nexecution_status: running\n---\n")
    done = decisions / "done.md"
    done.write_text("---\nexecution_status: complete\n---\n")
    fresh = decisions / "fresh.md"
    fresh.write_text("---\ndecided_by: admin\n---\n")

    monkeypatch.setattr(app_mod, "GROUPS", {"g": {"path": str(group_path)}})

    recovered = app_mod.recover_orphaned_executions()

    assert recovered == 1
    meta_stuck, _ = app_mod.parse_frontmatter(stuck.read_text())
    assert meta_stuck["execution_status"] == "failed"
    assert "interrupted" in meta_stuck["execution_summary"].lower()

    meta_done, _ = app_mod.parse_frontmatter(done.read_text())
    assert meta_done["execution_status"] == "complete"
    meta_fresh, _ = app_mod.parse_frontmatter(fresh.read_text())
    assert "execution_status" not in meta_fresh


def test_recover_orphaned_executions_handles_missing_dir(tmp_path, monkeypatch):
    """Groups without a decisions directory must not raise during recovery."""
    monkeypatch.setattr(
        app_mod, "GROUPS",
        {"g": {"path": str(tmp_path / "nonexistent")}, "h": {}},
    )
    assert app_mod.recover_orphaned_executions() == 0

