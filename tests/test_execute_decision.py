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
