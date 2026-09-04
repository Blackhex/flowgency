"""An integration that cannot enforce part of the policy must say what it did not enforce."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agency.integrations import RunResult
from agency.integrations.agency.copilot import CopilotIntegration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedPermissionRule,
)
from agency.jobs.execution import execute_job
from tests.test_job_execution import _authority, queued_job, read_metadata

NOTE_HEADING = "Permission policy not fully enforced"


# ── copilot: what the integration reports ───────────────────────────────────


class _FakeCompleted:
    returncode = 0
    stdout = "ok"
    stderr = ""


def _launch(policy, tmp_path, monkeypatch, *, real_home=None):
    import agency.integrations.agency.copilot as copilot_mod

    prompt = tmp_path / "p.prompt"
    prompt.write_text("do the thing", encoding="utf-8")

    monkeypatch.setattr(copilot_mod.subprocess, "run", lambda args, **kwargs: _FakeCompleted())
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "copilot")

    if real_home is None:
        real_home = tmp_path / "real_home"
        real_home.mkdir()
        (real_home / "config.json").write_text('{"authProvider":"github"}', encoding="utf-8")
    monkeypatch.setenv("COPILOT_HOME", str(real_home))

    launch_dir = tmp_path / "runtime"
    launch_dir.mkdir(parents=True, exist_ok=True)

    return CopilotIntegration().run(
        IntegrationRunRequest(
            workspace_root=tmp_path,
            launch_dir=launch_dir,
            task_file=prompt,
            timeout=60,
            runtime_policy=policy,
            skill=None,
            skill_arguments=(),
        )
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def test_fully_enforced_copilot_run_reports_nothing(tmp_path, monkeypatch, repo):
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    )

    result = _launch(policy, tmp_path, monkeypatch)

    assert result.unenforced_rules == []


def test_copilot_names_tools_of_a_rule_it_cannot_place_on_a_path(
    tmp_path, monkeypatch, repo
):
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(
            ResolvedPermissionRule(path=repo, tools=("read",)),
            ResolvedPermissionRule(path=None, tools=("write",)),
        ),
    )

    result = _launch(policy, tmp_path, monkeypatch)

    assert len(result.unenforced_rules) == 1
    entry = result.unenforced_rules[0]
    assert "write" in entry
    assert "no path" in entry


def test_copilot_names_the_denial_it_drops_when_the_sandbox_stays_off(
    tmp_path, monkeypatch, repo
):
    denied = tmp_path / "secrets"
    denied.mkdir()
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="unrestricted",
        rules=(ResolvedPermissionRule(path=denied, tools=()),),
    )

    result = _launch(policy, tmp_path, monkeypatch)

    assert any(str(denied) in entry for entry in result.unenforced_rules)


def test_copilot_reports_that_no_filesystem_policy_was_applied_without_credentials(
    tmp_path, monkeypatch, repo
):
    bare_home = tmp_path / "bare_home"
    bare_home.mkdir()
    policy = EffectiveRuntimePolicy(
        timeout=60,
        mode="restricted",
        rules=(ResolvedPermissionRule(path=repo, tools=("read",)),),
    )

    result = _launch(policy, tmp_path, monkeypatch, real_home=bare_home)

    assert result.copilot_home is None
    entries = result.unenforced_rules
    assert entries, "a run with no sandbox settings must report the gap"
    assert "filesystem policy was not applied" in entries[0]
    assert any(str(repo) in entry and "read" in entry for entry in entries)


# ── execution: what reaches the record ──────────────────────────────────────


def _context(tmp_path, result):
    return SimpleNamespace(
        workspace_root=tmp_path / "team",
        timeout=30,
        sandbox_root=None,
        team_root=tmp_path / "team",
        runtime_policy=EffectiveRuntimePolicy(timeout=30),
        integration=SimpleNamespace(run=lambda request: result),
    )


def test_successful_run_records_the_path_and_tools_left_unenforced(
    tmp_path, monkeypatch
):
    path, spec = queued_job(tmp_path)
    entry = (
        "Rule granting read, write on C:/vault: not enforced by the filesystem "
        "gate because the sandbox settings were not written."
    )
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: _context(
            tmp_path,
            RunResult(0, "done", "", 0.1, unenforced_rules=[entry]),
        ),
    )

    result = execute_job(_authority(spec))

    assert result.status == "complete"
    assert NOTE_HEADING in result.execution_summary
    assert "C:/vault" in result.execution_summary
    assert "read, write" in result.execution_summary


def test_fully_enforced_run_leaves_the_summary_untouched(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: _context(tmp_path, RunResult(0, "done", "", 0.1)),
    )

    result = execute_job(_authority(spec))

    assert result.status == "complete"
    assert NOTE_HEADING not in result.execution_summary
    assert "Permission" not in result.execution_summary


def test_failed_run_still_records_what_was_not_enforced(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    entry = "Rule granting write on C:/vault: dropped."
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: _context(
            tmp_path,
            RunResult(124, "partial", "timeout", 30.0, unenforced_rules=[entry]),
        ),
    )

    result = execute_job(_authority(spec))

    assert result.status == "failed"
    assert result.execution_summary.startswith("Agent timed out after 30 seconds.")
    assert entry in result.execution_summary


def test_note_reaches_the_decision_record(tmp_path, monkeypatch):
    decisions = tmp_path / "team" / "decisions"
    decisions.mkdir(parents=True)
    decision = decisions / "proposal.md"
    decision.write_text(
        "---\nexecution_job_id: queued-job\nexecution_status: pending\n---\n\nbody\n",
        encoding="utf-8",
    )
    path, spec = queued_job(
        tmp_path,
        decision_context={
            "decision_path": str(decision),
            "proposal_path": "proposal.md",
        },
    )
    entry = "Rule granting write on C:/vault: dropped."
    monkeypatch.setattr(
        "agency.jobs.execution.resolve_job_context",
        lambda ignored: _context(
            tmp_path,
            RunResult(0, "done", "", 0.1, unenforced_rules=[entry]),
        ),
    )

    execute_job(_authority(spec))

    summary = read_metadata(decision)["execution_summary"]
    assert NOTE_HEADING in summary
    assert entry in summary


def test_note_order_is_stable_across_runs(tmp_path, monkeypatch):
    entries = [
        "Rule granting write on C:/vault: dropped.",
        "Rule granting read on C:/archive: dropped.",
    ]
    summaries = []
    for index in range(2):
        root = tmp_path / f"run{index}"
        root.mkdir()
        path, spec = queued_job(root)
        monkeypatch.setattr(
            "agency.jobs.execution.resolve_job_context",
            lambda ignored, root=root: _context(
                root,
                RunResult(0, "done", "", 0.1, unenforced_rules=list(entries)),
            ),
        )
        summaries.append(execute_job(_authority(spec)).execution_summary)

    assert NOTE_HEADING in summaries[0]
    assert summaries[0] == summaries[1]
