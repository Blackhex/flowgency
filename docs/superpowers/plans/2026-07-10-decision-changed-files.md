# Decision Executing Agent, Log Link, and Changed Files — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the executing agent, a link to its execution log, and a git-status-style list of changed files on the decision detail page.

**Architecture:** Add a generic `FileChange` record and a `changed_files` field to the integration `RunResult` contract; implement population for the Copilot CLI only by parsing its `--output-format json` JSONL stream. Persist the executing agent, log path, and changed files into the decision frontmatter in `execute_decision`, then render them in the decision detail template.

**Tech Stack:** Python 3, FastAPI, Jinja2, PyYAML, pytest. GitHub Copilot CLI (`copilot -p --output-format json`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-10-decision-changed-files-design.md`.
- `changed_files` renders **only when non-empty**; no support or no change → no list.
- Changed-file support is implemented for the **Copilot integration only**; all other integrations keep the empty default.
- A parse failure must never break a run: fall back to raw stdout + empty `changed_files`.
- Preserve Copilot's existing least-privilege flag matrix and the Windows `CREATE_NO_WINDOW` / `stdin=DEVNULL` launch fix.
- `FileChange.status` values are exactly `"added" | "modified" | "deleted"`.
- Run tests with: `python -m pytest tests/ -q`.
- Git: fast-forward only, frequent commits, never push without confirmation.

---

## File Structure

- `agency/integrations/__init__.py` — add `FileChange` dataclass; add `changed_files` field to `RunResult`. (interface)
- `agency/integrations/agency/copilot.py` — switch `run()` to `--output-format json`; add `_parse_jsonl_output`. (Copilot implementation)
- `agency/app.py` — `execute_decision` persists `executed_by`, `execution_log`, `changed_files`; `decision_detail` reads and passes them. (persistence + route)
- `agency/templates/decision_detail.html` — render agent badge, log link, changed-files list inside the Execution block. (view)
- `tests/test_integration_sidecar.py` — Copilot JSONL parsing tests.
- `tests/test_execute_decision.py` — persistence tests.

---

### Task 1: Add `FileChange` record and `changed_files` to `RunResult`

**Files:**
- Modify: `agency/integrations/__init__.py:15-21` (imports at top + `RunResult` dataclass)
- Test: `tests/test_integration_contract.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `FileChange(path: str, status: str, lines_added: int, lines_removed: int)` — dataclass.
  - `RunResult(exit_code: int, stdout: str, stderr: str, duration_seconds: float, changed_files: list[FileChange] = [])`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_integration_contract.py` (create the file if it does not exist; if it exists, append the test function and ensure the import line is present):

```python
from agency.integrations import RunResult, FileChange


def test_runresult_changed_files_defaults_empty():
    r = RunResult(exit_code=0, stdout="", stderr="", duration_seconds=1.0)
    assert r.changed_files == []


def test_filechange_fields():
    fc = FileChange(path="a.txt", status="modified", lines_added=2, lines_removed=1)
    assert fc.path == "a.txt"
    assert fc.status == "modified"
    assert fc.lines_added == 2
    assert fc.lines_removed == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration_contract.py -q`
Expected: FAIL with `ImportError: cannot import name 'FileChange'`.

- [ ] **Step 3: Write minimal implementation**

In `agency/integrations/__init__.py`, change the import line:

```python
from dataclasses import dataclass
```

to:

```python
from dataclasses import dataclass, field
```

Then replace the `RunResult` dataclass:

```python
@dataclass
class RunResult:
    """Result of running an agent via an integration."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
```

with:

```python
@dataclass
class FileChange:
    """A single file change reported by an integration after a run."""
    path: str            # relative to sandbox root when possible; absolute fallback
    status: str          # "added" | "modified" | "deleted"
    lines_added: int
    lines_removed: int


@dataclass
class RunResult:
    """Result of running an agent via an integration."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    changed_files: list["FileChange"] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_integration_contract.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/integrations/__init__.py tests/test_integration_contract.py
git commit -m "feat(integrations): add FileChange and RunResult.changed_files"
```

---

### Task 2: Parse Copilot JSONL into changed files

**Files:**
- Modify: `agency/integrations/agency/copilot.py:1-13` (imports), add `_parse_jsonl_output` static method, and `run()` at `agency/integrations/agency/copilot.py:38-107`
- Test: `tests/test_integration_sidecar.py` (class `TestCopilot`, append after existing tests)

**Interfaces:**
- Consumes: `FileChange`, `RunResult` from Task 1.
- Produces:
  - `CopilotIntegration._parse_jsonl_output(raw: str, root: "Path | None") -> tuple[str, list[FileChange]]` — static method. Returns reconstructed human-readable text and the changed-files list. On any error, returns `(raw, [])`.
  - `run()` now passes `--output-format json` and returns a `RunResult` whose `stdout` is the reconstructed text and `changed_files` is populated.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integration_sidecar.py` inside `class TestCopilot`:

```python
    def test_parse_jsonl_extracts_native_edits(self):
        import json
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        root = Path("C:/repo") if False else Path("/repo")
        lines = [
            {"type": "tool.execution_start",
             "data": {"toolCallId": "t1", "toolName": "create",
                      "arguments": {"path": str(root / "greeting.txt")}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t1", "success": True,
                      "toolTelemetry": {"properties": {"command": "create"},
                                        "metrics": {"linesAdded": 1, "linesRemoved": 0}}}},
            {"type": "tool.execution_start",
             "data": {"toolCallId": "t2", "toolName": "edit",
                      "arguments": {"path": str(root / "existing.txt")}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t2", "success": True,
                      "toolTelemetry": {"properties": {"command": "edit"},
                                        "metrics": {"linesAdded": 1, "linesRemoved": 1}}}},
            {"type": "assistant.message", "data": {"content": "Done."}},
        ]
        raw = "\n".join(json.dumps(l) for l in lines)
        text, changes = CopilotIntegration._parse_jsonl_output(raw, root)
        assert "Done." in text
        by_path = {c.path: c for c in changes}
        assert by_path["greeting.txt"].status == "added"
        assert by_path["greeting.txt"].lines_added == 1
        assert by_path["existing.txt"].status == "modified"
        assert by_path["existing.txt"].lines_added == 1
        assert by_path["existing.txt"].lines_removed == 1

    def test_parse_jsonl_skips_readonly_view(self):
        import json
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        lines = [
            {"type": "tool.execution_start",
             "data": {"toolCallId": "v1", "toolName": "view",
                      "arguments": {"path": "/repo/a.txt"}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "v1", "success": True,
                      "toolTelemetry": {"properties": {"command": "view"},
                                        "metrics": {}}}},
        ]
        raw = "\n".join(json.dumps(l) for l in lines)
        _text, changes = CopilotIntegration._parse_jsonl_output(raw, Path("/repo"))
        assert changes == []

    def test_parse_jsonl_malformed_falls_back(self):
        from pathlib import Path
        from agency.integrations.agency.copilot import CopilotIntegration
        raw = "this is not json\nok"
        text, changes = CopilotIntegration._parse_jsonl_output(raw, Path("/repo"))
        assert text == raw
        assert changes == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration_sidecar.py::TestCopilot::test_parse_jsonl_extracts_native_edits -q`
Expected: FAIL with `AttributeError: type object 'CopilotIntegration' has no attribute '_parse_jsonl_output'`.

- [ ] **Step 3: Write minimal implementation**

In `agency/integrations/agency/copilot.py`, add `import json` to the imports block and import the new types. Change:

```python
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agency.config import SandboxSpec
from agency.integrations import (
    BaseIntegration, RunResult, AgentIdentity, IntegrationError, _register,
)
```

to:

```python
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agency.config import SandboxSpec
from agency.integrations import (
    BaseIntegration, RunResult, FileChange, AgentIdentity, IntegrationError, _register,
)
```

Add this static method to the `CopilotIntegration` class (place it directly above `run`):

```python
    # Copilot native file-edit tools that mutate the filesystem. Read-only
    # tools like "view" are intentionally excluded. Shell edits are not
    # tracked by the CLI and cannot be recovered here.
    _WRITE_TOOLS = {"create", "edit", "str_replace", "delete"}
    _STATUS_BY_COMMAND = {
        "create": "added",
        "edit": "modified",
        "str_replace": "modified",
        "delete": "deleted",
    }

    @staticmethod
    def _parse_jsonl_output(raw: str, root: "Path | None") -> "tuple[str, list[FileChange]]":
        """Parse Copilot --output-format json (JSONL) into (text, changes).

        Reconstructs human-readable text from assistant messages and extracts
        per-file changes from native file-edit tool calls. Any structural
        problem falls back to (raw, []); a run must never break on parsing.
        """
        try:
            tool_names: dict[str, str] = {}
            tool_paths: dict[str, str] = {}
            # path -> {"status": str, "added": int, "removed": int}
            files: dict[str, dict] = {}
            texts: list[str] = []
            saw_json = False

            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    continue
                saw_json = True
                etype = obj.get("type")
                data = obj.get("data") or {}

                if etype == "tool.execution_start":
                    tcid = data.get("toolCallId")
                    if tcid:
                        tool_names[tcid] = data.get("toolName", "")
                        path = (data.get("arguments") or {}).get("path")
                        if path:
                            tool_paths[tcid] = path
                elif etype == "tool.execution_complete":
                    tcid = data.get("toolCallId")
                    telemetry = data.get("toolTelemetry") or {}
                    props = telemetry.get("properties") or {}
                    metrics = telemetry.get("metrics") or {}
                    command = props.get("command") or tool_names.get(tcid, "")
                    if command not in CopilotIntegration._WRITE_TOOLS:
                        continue
                    path = tool_paths.get(tcid)
                    if not path:
                        continue
                    rel = CopilotIntegration._relativize(path, root)
                    entry = files.setdefault(rel, {"status": None, "added": 0, "removed": 0})
                    entry["added"] += int(metrics.get("linesAdded") or 0)
                    entry["removed"] += int(metrics.get("linesRemoved") or 0)
                    new_status = CopilotIntegration._STATUS_BY_COMMAND.get(command, "modified")
                    # "added" wins (a file created this run stays added); then
                    # "deleted"; otherwise "modified".
                    if entry["status"] is None:
                        entry["status"] = new_status
                    elif entry["status"] != "added" and new_status == "added":
                        entry["status"] = "added"
                    elif entry["status"] == "modified" and new_status == "deleted":
                        entry["status"] = "deleted"
                elif etype == "assistant.message":
                    content = data.get("content")
                    if content:
                        texts.append(content)
                elif etype == "result":
                    # Fallback source for file list if no per-tool edits parsed.
                    usage = data.get("usage") or {}
                    code_changes = usage.get("codeChanges") or {}
                    for p in code_changes.get("filesModified") or []:
                        rel = CopilotIntegration._relativize(p, root)
                        if rel not in files:
                            files[rel] = {"status": "modified", "added": 0, "removed": 0}

            if not saw_json:
                return raw, []

            changes = [
                FileChange(
                    path=path,
                    status=info["status"] or "modified",
                    lines_added=info["added"],
                    lines_removed=info["removed"],
                )
                for path, info in files.items()
            ]
            text = "\n".join(texts) if texts else raw
            return text, changes
        except Exception:
            return raw, []

    @staticmethod
    def _relativize(path: str, root: "Path | None") -> str:
        """Return path relative to root when possible, else the original."""
        if not root:
            return path
        try:
            return str(Path(path).resolve().relative_to(Path(root).resolve()))
        except (ValueError, OSError):
            return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_integration_sidecar.py::TestCopilot -q -k parse_jsonl`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agency/integrations/agency/copilot.py tests/test_integration_sidecar.py
git commit -m "feat(copilot): parse JSONL output into changed files"
```

---

### Task 3: Wire Copilot `run()` to emit JSON and populate `changed_files`

**Files:**
- Modify: `agency/integrations/agency/copilot.py` (the `run()` method, cmd args + return)
- Test: `tests/test_integration_sidecar.py` (class `TestCopilot`)

**Interfaces:**
- Consumes: `_parse_jsonl_output` from Task 2.
- Produces: `run()` returns `RunResult` with `changed_files` populated from parsed JSONL; `--output-format json` present in argv.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_integration_sidecar.py` inside `class TestCopilot`:

```python
    def test_run_emits_json_and_populates_changed_files(self, integration, tmp_agent_dir, monkeypatch):
        import json
        import agency.integrations.agency.copilot as mod

        jsonl = "\n".join(json.dumps(l) for l in [
            {"type": "tool.execution_start",
             "data": {"toolCallId": "t1", "toolName": "create",
                      "arguments": {"path": str(tmp_agent_dir / "new.txt")}}},
            {"type": "tool.execution_complete",
             "data": {"toolCallId": "t1", "success": True,
                      "toolTelemetry": {"properties": {"command": "create"},
                                        "metrics": {"linesAdded": 3, "linesRemoved": 0}}}},
            {"type": "assistant.message", "data": {"content": "Created new.txt"}},
        ])

        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = jsonl
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeCompleted()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        prompt_file = tmp_agent_dir / "prompt.md"
        prompt_file.write_text("Do the thing")
        result = integration.run(tmp_agent_dir, prompt_file, timeout=30)

        assert "--output-format" in captured["cmd"]
        assert "json" in captured["cmd"]
        assert result.stdout == "Created new.txt"
        assert len(result.changed_files) == 1
        assert result.changed_files[0].path == "new.txt"
        assert result.changed_files[0].status == "added"
        assert result.changed_files[0].lines_added == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration_sidecar.py::TestCopilot::test_run_emits_json_and_populates_changed_files -q`
Expected: FAIL — `--output-format` not in cmd and `changed_files` empty.

- [ ] **Step 3: Write minimal implementation**

In `run()`, add the output-format flag. Change:

```python
        cmd_args = [
            cmd, "-p", prompt_text,
            "--no-custom-instructions",
            "--no-ask-user",
            "--no-color",
            "--experimental",
        ]
```

to:

```python
        cmd_args = [
            cmd, "-p", prompt_text,
            "--output-format", "json",
            "--no-custom-instructions",
            "--no-ask-user",
            "--no-color",
            "--experimental",
        ]
```

Then change the success return. Replace:

```python
            duration = time.monotonic() - start
            return RunResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=duration,
            )
```

with:

```python
            duration = time.monotonic() - start
            parse_root = roots[0] if roots else agent_dir
            text, changed_files = self._parse_jsonl_output(result.stdout, parse_root)
            return RunResult(
                exit_code=result.returncode,
                stdout=text,
                stderr=result.stderr,
                duration_seconds=duration,
                changed_files=changed_files,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_integration_sidecar.py::TestCopilot -q`
Expected: PASS (all Copilot tests, including the existing flag-matrix and `test_run_builds_command` — the latter still passes because non-JSON `"ok"` stdout falls back to raw text).

- [ ] **Step 5: Commit**

```bash
git add agency/integrations/agency/copilot.py tests/test_integration_sidecar.py
git commit -m "feat(copilot): run with --output-format json and report changed files"
```

---

### Task 4: Persist executing agent, log path, and changed files in `execute_decision`

**Files:**
- Modify: `agency/app.py` — `execute_decision` (`agency/app.py:462-544`)
- Test: `tests/test_execute_decision.py`

**Interfaces:**
- Consumes: `RunResult.changed_files` (list of `FileChange`).
- Produces: after a run, the decision frontmatter contains `executed_by` (str), `execution_log` (absolute path str), and `changed_files` (list of dicts `{path, status, lines_added, lines_removed}`, written only when non-empty).

- [ ] **Step 1: Read the existing test file to match fixtures**

Run: open `tests/test_execute_decision.py` and note the fixtures used to invoke `execute_decision` (group path, decision file, monkeypatched integration). Reuse the same helper/fixture pattern in the new test.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_execute_decision.py` a test that runs `execute_decision` with a fake integration whose `run` returns a `RunResult` with `changed_files`, then asserts the frontmatter. Use the existing module's helpers for building the group and decision; the assertion core is:

```python
def test_execute_decision_persists_agent_log_and_changes(tmp_path, monkeypatch):
    import agency.app as app
    from agency.integrations import RunResult, FileChange

    # ... build group_path, decision_path, proposal with origin_agent="worker"
    # using the same helpers as the other tests in this file ...

    def fake_get_agent_integration(grp, agent):
        class FakeIntegration:
            name = "copilot"
            supports_execution = True
            def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
                return RunResult(
                    exit_code=0, stdout="did work", stderr="",
                    duration_seconds=1.0,
                    changed_files=[FileChange("a.txt", "modified", 2, 1)],
                )
        return FakeIntegration()

    monkeypatch.setattr(app, "get_agent_integration", fake_get_agent_integration)

    app.execute_decision(decision_path, group_path, "worker", proposal_slug, group_key="")

    meta, _ = app.parse_frontmatter(decision_path.read_text())
    assert meta["executed_by"] == "worker"
    assert meta["execution_log"].endswith(".out")
    assert meta["changed_files"] == [
        {"path": "a.txt", "status": "modified", "lines_added": 2, "lines_removed": 1}
    ]
```

Fill in the group/decision/proposal setup by copying the arrangement from the nearest existing test in the same file.

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_execute_decision.py::test_execute_decision_persists_agent_log_and_changes -q`
Expected: FAIL — `KeyError: 'executed_by'`.

- [ ] **Step 4: Write minimal implementation**

In `execute_decision`, after the successful run and before/around the exit-code status update, record the new fields. Locate:

```python
        sandbox_root = get_sandbox_root(g)
        result = agent_integration.run(agent_dir, prompt_file, timeout=timeout, sandbox_root=sandbox_root)
        out_path.write_text(result.stdout)
        err_path.write_text(result.stderr)
```

and insert immediately after `err_path.write_text(result.stderr)`:

```python
        update_decision_execution(decision_path, "executed_by", agent)
        update_decision_execution(decision_path, "execution_log", str(out_path))
        changed = [
            {
                "path": fc.path,
                "status": fc.status,
                "lines_added": fc.lines_added,
                "lines_removed": fc.lines_removed,
            }
            for fc in result.changed_files
        ]
        if changed:
            update_decision_execution(decision_path, "changed_files", changed)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_execute_decision.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agency/app.py tests/test_execute_decision.py
git commit -m "feat(decisions): persist executed_by, execution_log, changed_files"
```

---

### Task 5: Pass new fields from `decision_detail` route to template

**Files:**
- Modify: `agency/app.py` — `decision_detail` (`agency/app.py:2826-2875`)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: decision frontmatter fields `executed_by`, `execution_log`, `changed_files`.
- Produces: template context keys `executed_by`, `execution_log`, `changed_files`, and `execution_log_rel` (path relative to `shared/logs` for the log-view query param... see below — actually the route accepts the absolute path directly, so pass the raw stored value).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dashboard.py` a test that creates a decision file with the three fields and requests `/{group}/decisions/{slug}`, asserting the response HTML contains the agent name, a link to `/{group}/logs/view`, and the changed file path. Use the existing dashboard `client` fixture and group setup in that file:

```python
def test_decision_detail_shows_agent_log_and_changes(client, ...):
    # write a decision .md with frontmatter:
    #   executed_by: worker
    #   execution_log: <abs path under shared/logs/.../worker-exec-x.out>
    #   changed_files:
    #     - {path: a.txt, status: modified, lines_added: 2, lines_removed: 1}
    resp = client.get(f"/{group}/decisions/{slug}")
    html = resp.text
    assert "worker" in html
    assert "/logs/view" in html
    assert "a.txt" in html
```

Match the fixture names and group-construction helper already used by other tests in `tests/test_dashboard.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dashboard.py::test_decision_detail_shows_agent_log_and_changes -q`
Expected: FAIL — the fields are not in context/template yet.

- [ ] **Step 3: Write minimal implementation**

In `decision_detail`, after:

```python
    execution_status = meta.get("execution_status", "")
    execution_summary = meta.get("execution_summary", "")
```

add:

```python
    executed_by = meta.get("executed_by", "")
    execution_log = meta.get("execution_log", "")
    changed_files = meta.get("changed_files", []) or []
```

Then in the `TemplateResponse` context dict, after:

```python
        "execution_status": execution_status,
        "execution_summary": execution_summary,
```

add:

```python
        "executed_by": executed_by,
        "execution_log": execution_log,
        "changed_files": changed_files,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dashboard.py::test_decision_detail_shows_agent_log_and_changes -q`
Expected: PASS (after Task 6 template edit; if the template does not yet render the fields, this test fails on the assertions — implement Task 6 before re-running, or combine Steps: since the route change alone will not emit the HTML, proceed to Task 6 then re-run this test).

> Note: the HTML assertions depend on Task 6. Keep this test but expect it to pass only once Task 6 is done. Run the full suite at the end of Task 6.

- [ ] **Step 5: Commit**

```bash
git add agency/app.py tests/test_dashboard.py
git commit -m "feat(decisions): pass executed_by, execution_log, changed_files to template"
```

---

### Task 6: Render agent badge, log link, and changed-files list in the template

**Files:**
- Modify: `agency/templates/decision_detail.html` (the Execution block)
- Test: `tests/test_dashboard.py::test_decision_detail_shows_agent_log_and_changes` (from Task 5)

**Interfaces:**
- Consumes: context keys `executed_by`, `execution_log`, `changed_files`, plus `group`, and the `agent_badge` Jinja filter (defined in `agency/app.py:917`).
- Produces: rendered HTML — agent badge, "View log" link to `/{{ group }}/logs/view?path={{ execution_log }}`, and a changed-files list when non-empty.

- [ ] **Step 1: Add the markup**

In `agency/templates/decision_detail.html`, inside the Execution block, locate:

```html
    {% if execution_summary %}
    <div class="prose dark:prose-invert prose-sm max-w-none">
      {{ execution_summary | render_md }}
    </div>
    {% endif %}
```

and insert immediately **before** it:

```html
    {% if executed_by or execution_log %}
    <div class="flex flex-wrap items-center gap-3 mb-3 text-sm">
      {% if executed_by %}
      <span class="text-gray-500 dark:text-gray-400">Agent:</span>
      {{ executed_by | agent_badge }}
      {% endif %}
      {% if execution_log %}
      <a href="/{{ group }}/logs/view?path={{ execution_log | urlencode }}"
         class="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400">View log</a>
      {% endif %}
    </div>
    {% endif %}

    {% if changed_files %}
    <div class="mt-3">
      <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
        Files changed
      </div>
      <div class="font-mono text-sm space-y-1">
        {% for f in changed_files %}
        <div class="flex items-center gap-2">
          <span class="inline-flex items-center justify-center w-5 h-5 rounded text-xs font-bold
            {% if f.status == 'added' %}bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300
            {% elif f.status == 'deleted' %}bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300
            {% else %}bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300{% endif %}">
            {% if f.status == 'added' %}A{% elif f.status == 'deleted' %}D{% else %}M{% endif %}
          </span>
          <span class="text-gray-800 dark:text-gray-200">{{ f.path }}</span>
          {% if f.lines_added %}<span class="text-emerald-600 dark:text-emerald-400">+{{ f.lines_added }}</span>{% endif %}
          {% if f.lines_removed %}<span class="text-red-600 dark:text-red-400">&minus;{{ f.lines_removed }}</span>{% endif %}
        </div>
        {% endfor %}
      </div>
    </div>
    {% endif %}
```

- [ ] **Step 2: Run the template test**

Run: `python -m pytest tests/test_dashboard.py::test_decision_detail_shows_agent_log_and_changes -q`
Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (all tests; previously 341 passed plus the new tests).

- [ ] **Step 4: Commit**

```bash
git add agency/templates/decision_detail.html
git commit -m "feat(decisions): render agent, log link, and changed files in detail view"
```

---

## Self-Review

**Spec coverage:**
- Executing agent shown → Tasks 4 (persist), 5 (route), 6 (badge). ✓
- Log link → Tasks 4, 5, 6. ✓
- Changed-files A/M/D + line counts → Tasks 1 (record), 2/3 (Copilot parse), 4 (persist), 6 (render). ✓
- Copilot-only implementation, empty default for others → Task 1 default + Tasks 2-3 scope. ✓
- No list when empty/unsupported → Task 4 writes `changed_files` only when non-empty; Task 6 `{% if changed_files %}`. ✓
- Never break a run on parse failure → Task 2 try/except fallback + `test_parse_jsonl_malformed_falls_back`. ✓
- Preserve least-privilege flags + Windows launch fix → Task 3 only adds `--output-format json` and changes the return; launch code untouched. ✓

**Placeholder scan:** Task 4 and Task 5 tests reference "use the existing helpers/fixtures" for group/decision/proposal setup rather than inlining them, because those fixtures already exist in the target test files and differ from what a fresh reviewer would invent — the implementer must read the file (Task 4 Step 1) and copy the established arrangement. The assertion cores are fully specified.

**Type consistency:** `FileChange(path, status, lines_added, lines_removed)` is used identically in Tasks 1, 2, 3, 4. Persisted dict keys `{path, status, lines_added, lines_removed}` match between Task 4 (write) and Task 6 (`f.path`, `f.status`, `f.lines_added`, `f.lines_removed`). `_parse_jsonl_output(raw, root)` signature matches between Task 2 (def) and Task 3 (call).
