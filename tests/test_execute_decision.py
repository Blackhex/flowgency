from pathlib import Path

import agency.app as app_mod


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

    assert captured["sandbox_root"] == Path(str(tmp_path / "repo"))
