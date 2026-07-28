# Job Log Links and Session Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make job detail log paths link to the existing log viewer, and let a finished Copilot job be reopened in the CLI from the dashboard.

**Architecture:** The log paths become anchors at the already-guarded `GET /{group}/logs/view` route, reusing the href shape the agent activity tab uses. The integration session id becomes real data: a `session_id` on `RunResult`, persisted onto `JobRecord`, exposed through a `resume_command` hook on `BaseIntegration` so the CLI flag never enters the web layer. A `POST /{group}/jobs/{job_id}/resume` route spawns the CLI locally, and the equivalent command is always rendered for copying. A one-time script backfills the id onto pre-existing records from their stderr logs.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, pytest, PyYAML, dataclasses.

**Spec:** `docs/superpowers/specs/2026-07-28-job-log-links-and-session-resume-design.md`

## Global Constraints

- Work in the existing worktree `.worktrees/job-log-links-and-session-resume` on branch `feat/job-log-links-and-session-resume`. Run every command from that directory.
- Test command: `python -m pytest tests/ -q`, run from the worktree root. There is no `.venv` in this repository despite what AGENTS.md shows. Focused runs during a task, full suite before the task's commit. The full suite is around 1400 tests and takes a few minutes.
- `tests/test_repository_boundaries.py` greps the whole tracked tree for two prohibited superseded-layout terms and fails the suite if either appears anywhere, including in documentation. Run that test before committing any prose. Describe superseded concepts without naming them.
- Commit messages follow Conventional Commits with an imperative, lowercase description of at most 72 characters including the type prefix. Bodies wrap at 72 columns and explain what and why.
- Do not stage or modify `config.yaml`, `config.yaml.lock`, group-state directories, logs, or other untracked runtime data.
- The session id validation pattern is exactly `^[A-Za-z0-9_-]{1,128}$`. Use this literal in every place it appears.
- New routes use async handlers, and state-changing routes use POST plus a 303 redirect.
- No new log-serving route. `GET /{group}/logs/view` in `agency/app.py` is the only log viewer.

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `agency/web/routes/jobs.py` | Log hrefs, resume context, resume POST route | 1, 4 |
| `agency/templates/job_detail.html` | Log anchors, resume button, copy field | 1, 4 |
| `agency/integrations/__init__.py` | `RunResult.session_id`, `BaseIntegration.resume_command` | 2 |
| `agency/integrations/agency/copilot.py` | `_parse_session_id`, `resume_command`, `run` wiring | 2 |
| `agency/jobs/models.py` | `JobRecord.session_id` | 3 |
| `agency/jobs/execution.py` | Persist `session_id` on terminal transitions | 3 |
| `tools/backfill_job_session_ids.py` | One-time backfill from stderr logs | 5 |

Task order matters: Task 4 consumes `JobRecord.session_id` from Task 3, which consumes `RunResult.session_id` from Task 2. Task 1 is independent and goes first because it is the smallest. Task 5 is last because it depends on the field existing.

---

### Task 1: Link job detail logs to the viewer

**Files:**
- Modify: `agency/web/routes/jobs.py` (imports at lines 1-13; `_job_detail_context` at lines 177-212)
- Modify: `agency/templates/job_detail.html:78-83`
- Test: `tests/test_job_routes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_log_href(group_id: str, log_path: str | None) -> str` in `agency/web/routes/jobs.py`. Template context keys `stdout_href`, `stdout_name`, `stderr_href`, `stderr_name`, all `str`, empty string when the record has no path.

**Background the implementer needs:**

`GET /{group}/logs/view?path=<absolute path>` already exists in `agency/app.py` around line 2360. It calls `validate_file_access(fpath, logs_dir)` against the group's resolved logs directory before reading, so linking to it adds no new file exposure. Job logs are written by `agency/jobs/execution.py` to `<group.path>/logs/<date>/<agent>-<trigger>-<job_id>.out` and `.err`, which is inside that directory.

`agency/web/routes/agent_detail.py:222` already builds this href as `f"/{quote(group_id, safe='')}/logs/view?path={quote(str(candidate.resolve()))}"`. Match it exactly so both surfaces stay consistent. `quote` here is `urllib.parse.quote`, whose default `safe="/"` is what the activity tab relies on.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_job_routes.py`. The file already imports `replace` from `dataclasses`, `Path`, `read_job`, and `write_job`. Add `from urllib.parse import quote` to the imports at the top of the file.

```python
def test_job_detail_links_logs_to_viewer(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id="job-logs", status="queued")
    log_dir = group_root / "logs" / "2026-07-16"
    stdout_log = log_dir / "advisor-scheduled_prompt-job-logs.out"
    stderr_log = log_dir / "advisor-scheduled_prompt-job-logs.err"
    stdout_log.write_text("stdout", encoding="utf-8")
    stderr_log.write_text("stderr", encoding="utf-8")
    record = read_job(path)
    write_job(
        path,
        replace(
            record,
            status="failed",
            stdout_path=str(stdout_log.resolve()),
            stderr_path=str(stderr_log.resolve()),
        ),
    )

    response = client.get("/newsletter/jobs/job-logs")

    assert response.status_code == 200
    assert f"/newsletter/logs/view?path={quote(str(stdout_log.resolve()))}" in response.text
    assert f"/newsletter/logs/view?path={quote(str(stderr_log.resolve()))}" in response.text
    assert "advisor-scheduled_prompt-job-logs.out" in response.text
    assert "advisor-scheduled_prompt-job-logs.err" in response.text


def test_job_detail_omits_log_link_without_path(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id="job-nolog", status="queued")
    log_dir = group_root / "logs" / "2026-07-16"
    stdout_log = log_dir / "advisor-scheduled_prompt-job-nolog.out"
    stdout_log.write_text("stdout", encoding="utf-8")
    record = read_job(path)
    write_job(
        path,
        replace(
            record,
            status="failed",
            stdout_path=str(stdout_log.resolve()),
            stderr_path=None,
        ),
    )

    response = client.get("/newsletter/jobs/job-nolog")

    assert response.status_code == 200
    assert "Stdout log:" in response.text
    assert "Stderr log:" not in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_job_routes.py -k "links_logs_to_viewer or omits_log_link" -q`

Expected: both FAIL. The first fails because the rendered page contains the bare path but no `/newsletter/logs/view?path=` substring.

- [ ] **Step 3: Add the href helper and context keys**

In `agency/web/routes/jobs.py`, add to the imports:

```python
from urllib.parse import quote
```

Add this function immediately after `_job_path` (around line 53):

```python
def _log_href(group_id: str, log_path: str | None) -> str:
    if not log_path:
        return ""
    return f"/{quote(group_id, safe='')}/logs/view?path={quote(log_path)}"
```

In the dict returned by `_job_detail_context`, add these four keys next to `"can_cancel"`:

```python
        "stdout_href": _log_href(group_id, record.stdout_path),
        "stdout_name": Path(record.stdout_path).name if record.stdout_path else "",
        "stderr_href": _log_href(group_id, record.stderr_path),
        "stderr_name": Path(record.stderr_path).name if record.stderr_path else "",
```

- [ ] **Step 4: Render the anchors**

In `agency/templates/job_detail.html`, replace this block:

```html
  {% if job.stdout_path or job.stderr_path %}
  <div class="mb-6 text-sm text-gray-600 dark:text-gray-300">
    {% if job.stdout_path %}<div>Stdout log: {{ job.stdout_path }}</div>{% endif %}
    {% if job.stderr_path %}<div>Stderr log: {{ job.stderr_path }}</div>{% endif %}
  </div>
  {% endif %}
```

with:

```html
  {% if stdout_href or stderr_href %}
  <div class="mb-6 text-sm text-gray-600 dark:text-gray-300">
    {% if stdout_href %}
    <div>Stdout log: <a href="{{ stdout_href }}" title="{{ job.stdout_path }}" class="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400">{{ stdout_name }}</a></div>
    {% endif %}
    {% if stderr_href %}
    <div>Stderr log: <a href="{{ stderr_href }}" title="{{ job.stderr_path }}" class="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400">{{ stderr_name }}</a></div>
    {% endif %}
  </div>
  {% endif %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_job_routes.py -q`

Expected: PASS, including the pre-existing `test_job_detail_uses_friendly_memory_and_artifacts`.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -q`

Expected: PASS with no new failures.

- [ ] **Step 7: Commit**

```bash
git add agency/web/routes/jobs.py agency/templates/job_detail.html tests/test_job_routes.py
git commit -m "feat(jobs): link job detail logs to the log viewer"
```

Body to include:

```
The job detail page printed the stdout and stderr paths as inert text,
so reading a job's output meant copying an absolute path out of the
browser by hand. The agent activity tab already links log files at the
existing viewer, which validates every request against the group's logs
directory before reading.

Render the same href from job detail, showing the file's basename with
the full path kept in a title attribute. No new route and no new path
validation is introduced, because job logs are written inside the
directory the viewer already guards.
```

---

### Task 2: Capture the session id in the integration layer

**Files:**
- Modify: `agency/integrations/__init__.py` (`RunResult` at lines 37-46; `BaseIntegration` at lines 74-90)
- Modify: `agency/integrations/agency/copilot.py` (add methods near `_usage_summary`; wire both return paths of `run` at lines 398-500)
- Test: `tests/test_integration_sidecar.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `RunResult.session_id: str | None`, defaulting to `None`.
  - `BaseIntegration.resume_command(self, session_id: str) -> tuple[str, ...] | None`, returning `None` on the base class.
  - `CopilotIntegration._parse_session_id(raw: str) -> str | None`, a `@staticmethod`.
  - `CopilotIntegration.resume_command` returning `(self.cli_command, "--resume", session_id)`.

**Background the implementer needs:**

The Copilot CLI is invoked with `--output-format json`, so its stdout is JSONL: one JSON object per line. The final line has `{"type": "result", "sessionId": "<uuid>", ...}`. `_usage_summary` at line 310 already locates that event, but it goes on to read the session-state directory, which is why a separate, narrower parser is needed.

Two rules the existing parsers follow and this one must too: malformed lines are skipped rather than raised on, and a missing event returns a neutral value. A run must never fail because of output parsing.

`run` has two return paths — the normal one and the `subprocess.TimeoutExpired` handler, which parses `error.stdout` into `partial_stdout`. Both must set `session_id`, so a job that exhausts its timeout is still resumable.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_integration_sidecar.py`, inside the same class that holds `test_usage_summary_reads_session_shutdown_metrics`:

```python
    def test_parse_session_id_reads_result_event(self):
        import json
        from agency.integrations.agency.copilot import CopilotIntegration

        raw = "\n".join([
            json.dumps({"type": "assistant", "data": {"text": "hi"}}),
            "not json at all",
            json.dumps({"type": "result", "sessionId": "5980e47a-b681-4e22"}),
        ])

        assert CopilotIntegration._parse_session_id(raw) == "5980e47a-b681-4e22"

    def test_parse_session_id_returns_none_without_result(self):
        import json
        from agency.integrations.agency.copilot import CopilotIntegration

        raw = json.dumps({"type": "assistant", "data": {"text": "hi"}})

        assert CopilotIntegration._parse_session_id(raw) is None

    def test_base_integration_has_no_resume_command(self):
        from agency.integrations import BaseIntegration

        assert BaseIntegration().resume_command("abc") is None

    def test_copilot_resume_command_uses_resume_flag(self):
        assert CopilotIntegration().resume_command("abc") == (
            "copilot",
            "--resume",
            "abc",
        )

    def test_copilot_run_captures_session_id(self, tmp_agent_dir, monkeypatch):
        import json
        import agency.integrations.agency.copilot as copilot_mod

        prompt = tmp_agent_dir / "p.prompt"
        prompt.write_text("do the thing")

        class FakeCompleted:
            returncode = 0
            stdout = json.dumps({"type": "result", "sessionId": "sess-42"})
            stderr = ""

        monkeypatch.setattr(copilot_mod.subprocess, "run", lambda *a, **k: FakeCompleted())
        monkeypatch.setattr(
            CopilotIntegration,
            "resolve_executable",
            lambda self: "copilot",
        )

        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt,
            timeout=60,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=60,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )

        assert CopilotIntegration().run(request).session_id == "sess-42"

    def test_copilot_run_captures_session_id_on_timeout(self, tmp_agent_dir, monkeypatch):
        import json
        import subprocess
        import agency.integrations.agency.copilot as copilot_mod

        prompt = tmp_agent_dir / "p.prompt"
        prompt.write_text("do the thing")

        partial = json.dumps({"type": "result", "sessionId": "sess-timeout"})

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(
                cmd="copilot",
                timeout=60,
                output=partial,
                stderr="",
            )

        monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(
            CopilotIntegration,
            "resolve_executable",
            lambda self: "copilot",
        )

        request = IntegrationRunRequest(
            workspace_root=tmp_agent_dir,
            launch_dir=tmp_agent_dir / "runtime",
            task_file=prompt,
            timeout=60,
            runtime_policy=EffectiveRuntimePolicy(
                timeout=60,
                sandbox_mode="unrestricted",
                sandbox_roots=(),
                tools=ResolvedToolPolicy("all", ()),
            ),
            skill=None,
            skill_arguments=(),
        )

        result = CopilotIntegration().run(request)

        assert result.exit_code == 124
        assert result.session_id == "sess-timeout"
```

If `IntegrationRunRequest`, `EffectiveRuntimePolicy`, or `ResolvedToolPolicy` are not already imported at the top of the file, copy the import lines used by `test_copilot_run_unset_sandbox_uses_allow_all_paths` around line 520.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_integration_sidecar.py -k "session_id or resume_command" -q`

Expected: FAIL with `AttributeError: type object 'CopilotIntegration' has no attribute '_parse_session_id'` and `'BaseIntegration' object has no attribute 'resume_command'`.

- [ ] **Step 3: Extend RunResult and BaseIntegration**

In `agency/integrations/__init__.py`, add a field to `RunResult`:

```python
@dataclass
class RunResult:
    """Result of running an agent via an integration."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    changed_files: list["FileChange"] = field(default_factory=list)
    write_attempts: list[str] = field(default_factory=list)
    session_id: str | None = None
```

Add this method to `BaseIntegration`, immediately after `run`:

```python
    def resume_command(self, session_id: str) -> tuple[str, ...] | None:
        """Argv that reopens a past session, or None when unsupported."""
        return None
```

- [ ] **Step 4: Parse and return the session id in Copilot**

In `agency/integrations/agency/copilot.py`, add these two methods immediately before `_usage_summary`:

```python
    @staticmethod
    def _parse_session_id(raw: str) -> str | None:
        """Return the sessionId of the last result event, or None."""
        session_id: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict) or event.get("type") != "result":
                continue
            candidate = event.get("sessionId")
            if isinstance(candidate, str) and candidate:
                session_id = candidate
        return session_id

    def resume_command(self, session_id: str) -> tuple[str, ...] | None:
        return (self.cli_command, "--resume", session_id)
```

In `run`, the normal return becomes:

```python
            return RunResult(
                exit_code=result.returncode,
                stdout=parsed_text,
                stderr=stderr,
                duration_seconds=duration,
                changed_files=changed_files,
                write_attempts=write_attempts,
                session_id=self._parse_session_id(result.stdout),
            )
```

and the `TimeoutExpired` return becomes:

```python
            return RunResult(
                exit_code=124,
                stdout=parsed_text,
                stderr=stderr,
                duration_seconds=duration,
                changed_files=changed_files,
                write_attempts=write_attempts,
                session_id=self._parse_session_id(partial_stdout),
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_integration_sidecar.py -q`

Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agency/integrations/__init__.py agency/integrations/agency/copilot.py tests/test_integration_sidecar.py
git commit -m "feat(integrations): return the session id from a run"
```

Body to include:

```
The Copilot CLI emits a session id that copilot --resume accepts, and
the integration already parsed it, but only to look up token metrics.
The id then survived solely as text inside a formatted summary line, so
nothing downstream could act on it.

Carry the id on RunResult instead, parsed by a narrow reader that scans
the JSONL output for the last result event and tolerates malformed
lines the way the existing output parsers do. Populate it on the
timeout path as well, so a run that exhausts its budget stays
resumable.

Add resume_command to BaseIntegration, returning None by default, so
the CLI flag lives with the integration that owns it rather than in a
caller. Claude Code and Codex can opt in later by overriding it.
```

---

### Task 3: Persist the session id on the job record

**Files:**
- Modify: `agency/jobs/models.py` (`JobRecord` at lines 295-312)
- Modify: `agency/jobs/execution.py` (`_terminalize_failure` at lines 145-177; `_merge_failed_terminal_metadata` at lines 181-224; the success `replace` at lines 500-513; the three failure call sites that have a `result` in scope)
- Test: `tests/test_job_models.py`

**Interfaces:**
- Consumes: `RunResult.session_id` from Task 2.
- Produces: `JobRecord.session_id: str | None`, defaulting to `None`, round-tripped through `to_dict`/`from_dict` and the job YAML.

**Background the implementer needs:**

`JobRecord.from_dict` does `cls(spec=spec, **values)`. A field with a default therefore loads correctly from YAML written before the field existed, and no read migration is needed. Do not add one.

`_terminalize_failure` is called from several places. Only the call sites inside the run block have a `result` in scope; the ones handling `JobAuthorityError` and similar do not, and must keep the default `None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_job_models.py`, using the existing `make_spec` helper:

```python
def test_job_record_round_trips_session_id(tmp_path):
    spec = make_spec(tmp_path)
    record = JobRecord.from_spec(spec)
    record = JobRecord(
        spec=record.spec,
        authority_digest=record.authority_digest,
        status="queued",
        session_id="sess-7",
    )

    restored = JobRecord.from_dict(yaml.safe_load(yaml.safe_dump(record.to_dict())))

    assert restored.session_id == "sess-7"


def test_job_record_loads_payload_without_session_id(tmp_path):
    spec = make_spec(tmp_path)
    payload = JobRecord.from_spec(spec).to_dict()
    payload.pop("session_id", None)

    assert JobRecord.from_dict(payload).session_id is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_job_models.py -k session_id -q`

Expected: FAIL with `TypeError: JobRecord.__init__() got an unexpected keyword argument 'session_id'`.

- [ ] **Step 3: Add the field**

In `agency/jobs/models.py`, add the field as the last entry of `JobRecord`, after `memory_publication`:

```python
    memory_publication: dict[str, Any] | None = None
    session_id: str | None = None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_job_models.py -k session_id -q`

Expected: PASS.

- [ ] **Step 5: Thread the value through execution**

In `agency/jobs/execution.py`, add a keyword parameter to `_terminalize_failure`, after `memory_publication`:

```python
    memory_publication: dict[str, object] | None = None,
    session_id: str | None = None,
```

and pass it in that function's `transition_job` call, after `memory_publication=memory_publication`:

```python
        memory_publication=memory_publication,
        session_id=session_id,
```

Add the same parameter to `_merge_failed_terminal_metadata`, and inside its `replace` call add, after the `memory_publication` line:

```python
        session_id=current.session_id or session_id,
```

The `or` preserves an already-written id, matching how that function merges every other field.

In the success path, add `session_id=result.session_id` to the `replace` call, after `base_sha=base_sha`.

Add `session_id=result.session_id` to the three `_terminalize_failure` and `_merge_failed_terminal_metadata` call sites that sit inside the run block and have `result` in scope: the non-zero exit branch, and both branches of the `MemoryPublicationError` handler. Leave every other `_terminalize_failure` call unchanged.

- [ ] **Step 6: Run the job execution tests**

Run: `python -m pytest tests/test_job_execution.py tests/test_job_models.py tests/test_job_store_terminal.py -q`

Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/ -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add agency/jobs/models.py agency/jobs/execution.py tests/test_job_models.py
git commit -m "feat(jobs): persist the run session id on the record"
```

Body to include:

```
A completed job could not be traced back to the CLI session that
produced it, because the session id lived only in the integration's
return value and was dropped when the worker wrote the terminal record.

Store it on JobRecord and write it from every terminal transition that
has a run result in scope. The failure-merge path keeps an existing id
rather than overwriting it, matching how that path already merges the
other terminal fields.

The field defaults to None, and from_dict expands the payload as
keyword arguments, so records written before this change still load.
No read migration is required.
```

---

### Task 4: Resume the session from job detail

**Files:**
- Modify: `agency/web/routes/jobs.py` (imports; `_job_detail_context`; `job_detail`; new POST route after `job_detail`)
- Modify: `agency/templates/job_detail.html` (button row at lines 35-50; new copy block; new notice)
- Test: `tests/test_job_routes.py`

**Interfaces:**
- Consumes: `JobRecord.session_id` from Task 3; `BaseIntegration.resume_command` from Task 2.
- Produces: `POST /{group}/jobs/{job_id}/resume` returning 303 to `/{group}/jobs/{job_id}?resume=launched` or `?resume=failed`. Template context keys `resume_command` (`str`, empty when unavailable), `resume_available` (`bool`), `resume_label` (`str`), `resume_notice` (`str`).

**Background the implementer needs:**

`spawn_interactive_terminal(command: Sequence[str], cwd: Path) -> str` is re-exported from `agency.integrations`. It raises `IntegrationError` when no terminal is available, and on POSIX it can route the argv through `shlex.join` into `xterm -e`. That is why the session id must be validated against `^[A-Za-z0-9_-]{1,128}$` before it reaches the argv: it is the only attacker-influenceable element of the command, and an id from a tampered job record would otherwise be a shell-injection vector. Reject rather than sanitize.

`get_integration(name)` in `agency.integrations` resolves an integration by name and raises `KeyError` for an unknown one.

`format_interactive_command(argv)`, also re-exported from `agency.integrations`, renders argv for display. Use it for the copyable text so the shown command matches what the spawn would run.

`require_executable()` raises `IntegrationError` when the CLI is not installed. Tests must monkeypatch `CopilotIntegration.resolve_executable` so their result does not depend on whether the Copilot CLI happens to exist on the machine running the suite.

The `POST /setup/launch` handler in `agency/web/routes/admin_groups.py` around line 413 is the pattern to follow, including `await run_in_threadpool(...)` around the blocking spawn.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_job_routes.py`:

```python
def _write_resumable_job(group_root, config_path, *, job_id, session_id):
    path = _write_job_record(group_root, config_path, job_id=job_id, status="queued")
    record = read_job(path)
    write_job(path, replace(record, status="complete", session_id=session_id))
    return path


def test_job_detail_offers_resume_when_session_known(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-resume", session_id="sess-1")

    response = client.get("/newsletter/jobs/job-resume")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-resume/resume" in response.text
    assert "--resume" in response.text
    assert "sess-1" in response.text


def test_job_detail_hides_resume_without_session(monkeypatch, tmp_path, raw_config):
    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id="job-nosess", status="queued")
    write_job(path, replace(read_job(path), status="complete"))

    response = client.get("/newsletter/jobs/job-nosess")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-nosess/resume" not in response.text


def test_job_detail_hides_resume_without_integration_support(monkeypatch, tmp_path, raw_config):
    from agency.integrations.agency.copilot import CopilotIntegration

    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-unsup", session_id="sess-9")
    monkeypatch.setattr(CopilotIntegration, "resume_command", lambda self, session_id: None)

    response = client.get("/newsletter/jobs/job-unsup")

    assert response.status_code == 200
    assert "/newsletter/jobs/job-unsup/resume" not in response.text


def test_resume_spawns_terminal_and_redirects(monkeypatch, tmp_path, raw_config):
    import agency.web.routes.jobs as jobs_mod
    from agency.integrations.agency.copilot import CopilotIntegration

    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-spawn", session_id="sess-2")
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "/opt/copilot")
    captured = {}

    def fake_spawn(command, cwd):
        captured["command"] = list(command)
        captured["cwd"] = cwd
        return "copilot --resume sess-2"

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fake_spawn)

    response = client.post("/newsletter/jobs/job-spawn/resume", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/jobs/job-spawn?resume=launched"
    assert captured["command"] == ["/opt/copilot", "--resume", "sess-2"]


def test_resume_reports_failure(monkeypatch, tmp_path, raw_config):
    import agency.web.routes.jobs as jobs_mod
    from agency.integrations import IntegrationError
    from agency.integrations.agency.copilot import CopilotIntegration

    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(group_root, config_path, job_id="job-nospawn", session_id="sess-3")
    monkeypatch.setattr(CopilotIntegration, "resolve_executable", lambda self: "/opt/copilot")

    def fake_spawn(command, cwd):
        raise IntegrationError("no terminal")

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fake_spawn)

    response = client.post("/newsletter/jobs/job-nospawn/resume", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/newsletter/jobs/job-nospawn?resume=failed"


def test_resume_rejects_unsafe_session_id(monkeypatch, tmp_path, raw_config):
    import agency.web.routes.jobs as jobs_mod

    client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    _write_resumable_job(
        group_root,
        config_path,
        job_id="job-evil",
        session_id="a; rm -rf ~",
    )

    def fail_spawn(command, cwd):
        raise AssertionError("spawn must not be reached")

    monkeypatch.setattr(jobs_mod, "spawn_interactive_terminal", fail_spawn)

    response = client.post("/newsletter/jobs/job-evil/resume", follow_redirects=False)

    assert response.status_code == 400


def test_resume_unknown_job_is_not_found(monkeypatch, tmp_path, raw_config):
    client, _config_path, _group_root = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.post("/newsletter/jobs/job-missing/resume", follow_redirects=False)

    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_job_routes.py -k resume -q`

Expected: FAIL. The POST tests return 405 because the route does not exist; the detail tests fail on the missing substrings.

- [ ] **Step 3: Add the resume context**

In `agency/web/routes/jobs.py`, extend the imports:

```python
import re

from starlette.concurrency import run_in_threadpool

from agency.integrations import (
    IntegrationError,
    format_interactive_command,
    get_integration,
    spawn_interactive_terminal,
)
```

Import `spawn_interactive_terminal` into the module namespace exactly as written, so tests can monkeypatch `jobs_mod.spawn_interactive_terminal`.

Add near `_log_href`:

```python
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _resume_argv(record) -> tuple[str, ...] | None:
    session_id = record.session_id
    if not session_id or not _SAFE_SESSION_ID.match(session_id):
        return None
    try:
        integration = get_integration(record.spec.integration_name)
    except KeyError:
        return None
    return integration.resume_command(session_id)
```

In `_job_detail_context`, compute the argv once, next to the existing `publication = record.memory_publication or {}` line:

```python
    resume_argv = _resume_argv(record)
```

and add these keys to the returned dict:

```python
        "resume_command": format_interactive_command(resume_argv) if resume_argv else "",
        "resume_available": resume_argv is not None,
        "resume_label": f"Resume in {_integration_display_name(record)}",
```

and add this helper next to `_resume_argv`:

```python
def _integration_display_name(record) -> str:
    try:
        integration = get_integration(record.spec.integration_name)
    except KeyError:
        return record.spec.integration_name
    return integration.display_name or record.spec.integration_name
```

- [ ] **Step 4: Add the notice and the POST route**

Change the `job_detail` signature to accept the query flag:

```python
async def job_detail(request: Request, group: str, job_id: str, artifact: str = "", resume: str = "", services: AgencyServices = Depends(get_services)):
```

and add to the template payload, alongside `**context`:

```python
            "resume_notice": {
                "launched": "Opening the session in a new terminal.",
                "failed": "Could not open a terminal. Copy the command below and run it yourself.",
            }.get(resume, ""),
```

Add this route immediately after `job_detail`:

```python
@router.post("/{group}/jobs/{job_id}/resume")
async def job_resume(request: Request, group: str, job_id: str, services: AgencyServices = Depends(get_services)):
    snapshot = services.config_store.load()
    if group not in snapshot.config.groups:
        raise HTTPException(status_code=404, detail="Unknown group")
    if services.job_store is None:
        raise HTTPException(status_code=409, detail="Job store unavailable")
    path = _job_path(services.job_store, group, job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    record = read_job(path)
    argv = _resume_argv(record)
    if argv is None:
        raise HTTPException(status_code=400, detail="Job cannot be resumed")
    outcome = "launched"
    try:
        integration = get_integration(record.spec.integration_name)
        command = (integration.require_executable(), *argv[1:])
        await run_in_threadpool(
            spawn_interactive_terminal,
            command,
            Path(record.spec.workspace_root),
        )
    except (IntegrationError, OSError):
        outcome = "failed"
    return RedirectResponse(f"/{group}/jobs/{job_id}?resume={outcome}", status_code=303)
```

Note the deliberate ordering: the record is loaded and validated before anything is spawned, the working directory comes from the record rather than the request, and only `argv[1:]` is reused so the executable always comes from integration resolution.

- [ ] **Step 5: Render the button, command, and notice**

In `agency/templates/job_detail.html`, add inside the button row, after the Routines link and before the cancel form:

```html
    {% if resume_available %}
    <form method="POST" action="/{{ group }}/jobs/{{ job.spec.job_id }}/resume">
      <button type="submit" class="px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors">{{ resume_label }}</button>
    </form>
    {% endif %}
```

Add this block immediately after the closing `</div>` of the button row:

```html
  {% if resume_notice %}
  <div class="mb-4 text-sm text-gray-600 dark:text-gray-300">{{ resume_notice }}</div>
  {% endif %}

  {% if resume_command %}
  <div class="mb-6 space-y-2">
    <label for="resume-command" class="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">Resume command</label>
    <input id="resume-command" type="text" readonly value="{{ resume_command }}" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono text-gray-900 bg-gray-50">
    <div class="flex flex-wrap gap-3 items-center">
      <button type="button" id="copy-resume" class="px-3 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors">Copy command</button>
      <span id="copy-resume-feedback" class="text-sm text-gray-500"></span>
    </div>
  </div>
  {% endif %}
```

Add this script at the end of the `{% block content %}`, before `{% endblock %}`:

```html
<script>
  (function () {
    const field = document.getElementById("resume-command");
    const button = document.getElementById("copy-resume");
    const feedback = document.getElementById("copy-resume-feedback");
    if (!field || !button || !feedback) {
      return;
    }
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(field.value);
        feedback.textContent = "Copied.";
      } catch (error) {
        field.focus();
        field.select();
        feedback.textContent = "Copy failed. Select the command and copy it manually.";
      }
    });
  })();
</script>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_job_routes.py -q`

Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/ -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add agency/web/routes/jobs.py agency/templates/job_detail.html tests/test_job_routes.py
git commit -m "feat(jobs): resume a finished session from job detail"
```

Body to include:

```
A finished job recorded the session that produced it but offered no way
to reopen it, so continuing a review meant reconstructing the CLI
invocation by hand.

Add a resume control that posts to a new endpoint, resolves the argv
from the job's integration, and spawns a terminal in the job's recorded
workspace. Render the equivalent command beside it at all times, so a
browser that is not on the dashboard host still gets something usable.

The endpoint starts a local process from an HTTP request, so it is POST
only and rejects any session id outside [A-Za-z0-9_-]{1,128}. That
guard is load-bearing: on POSIX the interactive spawn can route argv
through shlex.join into xterm -e, which would make an id from a
tampered record a shell-injection vector. The executable comes from
integration resolution and the working directory from the record, so
neither is influenced by the request.
```

---

### Task 5: Backfill the session id onto existing records

**Files:**
- Create: `tools/backfill_job_session_ids.py`
- Test: `tests/test_backfill_job_session_ids.py`

**Interfaces:**
- Consumes: `JobRecord.session_id` from Task 3.
- Produces: `backfill(config_path: Path, group: str | None = None, dry_run: bool = False) -> list[tuple[str, str]]`, returning `(job_id, outcome)` pairs where outcome is one of `"filled"`, `"skipped"`, `"unchanged"`. `main(argv: list[str] | None = None) -> int` as the CLI entry point.

**Background the implementer needs:**

The write cannot use `transition_job`. That function requires a status change and rejects terminal-to-terminal transitions, and these records are already terminal. Use `exclusive_lock(job_lock_path(path), wait=True)` from `agency.jobs.store`, re-read the record inside the lock, apply `dataclasses.replace`, and call `write_job`. Re-reading inside the lock is what makes a concurrent worker write safe; do not reuse a record read before acquiring it.

The id is recovered from the stderr log, because `_usage_summary` appends a line reading `Resume     copilot --resume=<id>` to stderr, and `agency/jobs/execution.py` writes stderr to `<stem>.err`.

Enumerate work with `JobStore(snapshot.config.agency.memory_store).paths(group_id)`, and load the config with the same read-only helper the CLI uses. `agency/cli.py:146` defines `_snapshot_read_only(path)`; import and reuse it rather than reimplementing config parsing.

This script is deliberately not registered in `agency/cli.py`. It is a one-time utility for existing local data.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill_job_session_ids.py`. It reuses the config and job builders from `tests/test_job_routes.py`, because `backfill` parses a real config through the same read-only path the CLI uses, and those helpers already produce a valid one. Do not hand-roll a minimal config here: `make_spec` in `tests/test_job_models.py` writes its own stub `config.yaml` into `tmp_path` and would overwrite yours.

```python
from dataclasses import replace

from agency.jobs.store import read_job, write_job
from tests.test_job_routes import _seed_app, _write_job_record
from tools.backfill_job_session_ids import backfill


STDERR = "Changes    +1 -0\nResume     copilot --resume=abc-123\n"


def _seed(monkeypatch, tmp_path, raw_config, *, job_id, session_id=None):
    _client, config_path, group_root = _seed_app(monkeypatch, tmp_path, raw_config)
    path = _write_job_record(group_root, config_path, job_id=job_id, status="queued")
    stderr_path = tmp_path / f"{job_id}.err"
    stderr_path.write_text(STDERR, encoding="utf-8")
    write_job(
        path,
        replace(
            read_job(path),
            status="complete",
            session_id=session_id,
            stderr_path=str(stderr_path),
        ),
    )
    return config_path, path


def test_backfill_fills_empty_session_id(monkeypatch, tmp_path, raw_config):
    config_path, path = _seed(monkeypatch, tmp_path, raw_config, job_id="job-fill")

    backfill(config_path, group="newsletter")

    assert read_job(path).session_id == "abc-123"


def test_backfill_leaves_populated_session_id(monkeypatch, tmp_path, raw_config):
    config_path, path = _seed(
        monkeypatch,
        tmp_path,
        raw_config,
        job_id="job-keep",
        session_id="already",
    )

    backfill(config_path, group="newsletter")

    assert read_job(path).session_id == "already"


def test_backfill_dry_run_writes_nothing(monkeypatch, tmp_path, raw_config):
    config_path, path = _seed(monkeypatch, tmp_path, raw_config, job_id="job-dry")

    results = backfill(config_path, group="newsletter", dry_run=True)

    assert read_job(path).session_id is None
    assert ("job-dry", "filled") in results
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_backfill_job_session_ids.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'tools.backfill_job_session_ids'`.

- [ ] **Step 3: Write the script**

Create `tools/backfill_job_session_ids.py`:

```python
"""One-time backfill of JobRecord.session_id from persisted stderr logs."""

from __future__ import annotations

import argparse
import re
from dataclasses import replace
from pathlib import Path

from agency.cli import _snapshot_read_only
from agency.fs.locks import exclusive_lock
from agency.jobs.authority import JobStore
from agency.jobs.store import job_lock_path, read_job, write_job


_RESUME = re.compile(r"--resume[= ]([A-Za-z0-9_-]{1,128})")


def _session_id_from_stderr(stderr_path: str | None) -> str | None:
    if not stderr_path:
        return None
    try:
        text = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _RESUME.search(text)
    return match.group(1) if match else None


def backfill(
    config_path: Path,
    group: str | None = None,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    snapshot = _snapshot_read_only(Path(config_path))
    store = JobStore(snapshot.config.agency.memory_store)
    group_ids = [group] if group else list(snapshot.config.groups)
    results: list[tuple[str, str]] = []
    for group_id in group_ids:
        for path in store.paths(group_id):
            try:
                record = read_job(path)
            except Exception:
                continue
            if record.session_id:
                results.append((record.spec.job_id, "skipped"))
                continue
            session_id = _session_id_from_stderr(record.stderr_path)
            if not session_id:
                results.append((record.spec.job_id, "unchanged"))
                continue
            results.append((record.spec.job_id, "filled"))
            if dry_run:
                continue
            with exclusive_lock(job_lock_path(path), wait=True):
                current = read_job(path)
                if current.session_id:
                    continue
                write_job(path, replace(current, session_id=session_id))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--group")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    results = backfill(args.config, args.group, args.dry_run)
    for job_id, outcome in results:
        print(f"{outcome:<10} {job_id}")
    filled = sum(1 for _, outcome in results if outcome == "filled")
    verb = "would fill" if args.dry_run else "filled"
    print(f"\n{verb} {filled} of {len(results)} record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The re-read inside the lock is not redundant with the one above it. The outer read decides whether there is work to do; the inner read is the one the write is based on, so a worker that populated the field in between is not clobbered.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_backfill_job_session_ids.py -q`

Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`

Expected: PASS.

- [ ] **Step 6: Verify against real data with a dry run**

Run: `python -m tools.backfill_job_session_ids --config ../../config.yaml --dry-run`

Expected: a per-record listing and a count, with no file modified. Confirm with `git status` that nothing changed and that no runtime file was touched.

- [ ] **Step 7: Commit**

```bash
git add tools/backfill_job_session_ids.py tests/test_backfill_job_session_ids.py
git commit -m "chore(jobs): backfill session ids from stderr logs"
```

Body to include:

```
Records written before the session id became a field cannot offer the
resume control, even though the id is recoverable: the usage summary
appended a resume line to stderr, and the worker persisted stderr next
to the job log.

Recover it with a one-time script. It fills only an empty field, so it
is idempotent and cannot clobber a record produced by the new run path,
and it supports a dry run.

The write cannot use transition_job, which requires a status change and
rejects terminal-to-terminal transitions. The script takes the job lock,
re-reads the record inside it, and writes the merged value, so a
concurrent worker cannot be overwritten.

The script is not registered in the CLI. It operates on existing local
data and is not a shipped command.
```

---

## Verification before completion

- [ ] Run `python -m pytest tests/ -q` from the worktree root and confirm it is green.
- [ ] Start the dashboard, open a completed job, and confirm: both log links open the viewer; the resume command is shown and copies; and, for a job with a session id, the resume button appears.
- [ ] Confirm `git status` shows no changes to `config.yaml`, `config.yaml.lock`, or any group-state directory.
