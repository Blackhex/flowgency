# Readable Agent Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore readable Copilot logs without allowing malformed events, historical JSONL, or embedded markup to stall or compromise the dashboard.

**Architecture:** Share small, pure event-normalization helpers between the Copilot integration and log presentation. Add a bounded log-preview module that sanitizes Markdown and escapes raw output. Run the existing log route's synchronous work in a worker thread, preserving its URL, validation, and layout.

**Tech Stack:** Python 3.11+, FastAPI/Starlette threadpool, Python-Markdown, Jinja2/MarkupSafe, nh3 HTML sanitizer, pytest/httpx, existing Playwright UI harness.

## Global Constraints

- Preserve ordinary Markdown log formatting and the existing same-tab log route.
- Keep malformed events from invalidating unrelated valid messages or metadata.
- Prevent log content from introducing executable HTML, styles, or resources.
- Bound log-view work and keep synchronous processing off the async event loop.
- Do not change the global Markdown renderer's behavior for unrelated pages.
- Do not rewrite stored logs, migrate jobs, restart the user's dashboard, or launch configured agents as part of diagnosing or verifying this fix.
- The original log file remains the authoritative artifact. Viewing it is a read-only operation and does not submit or resume jobs.
- Bound the preview to the first 4 MiB of source bytes and 64 KiB of UTF-8 display text.
- Read at most the source limit plus one byte to detect truncation, rather than reading the whole file and slicing afterwards.
- Plain text remains supported.

---

## Inputs And Execution Gates

- Approved specification: `docs/superpowers/specs/2026-09-06-readable-agent-logs-design.md`, commit `caa0ce6`; written-spec approval received on 2026-09-06.
- Worktree: `C:/Projekty/Flowgency/.worktrees/readable-agent-logs`, branch `fix/readable-agent-logs`. Continue there; do not create another worktree or edit implementation on `master`.
- Fresh full baseline already recorded: 2,047 passed, 6 skipped in 271.62 seconds. Recheck working-tree state at execution start; investigate unexpected changes without reverting them.
- All commands below run from the worktree root unless explicitly integrating into the main checkout. Do not run parallel terminal jobs: this environment shares terminal state with subagents.
- No visual mockup assets were approved. Preserve the existing log viewer layout, with only wrapping and a concise `Preview truncated` status added where necessary.
- All runtime tests use fixtures. Browser validation uses the existing disposable UI server, not the real config or a second live dashboard that drains the user's queue.

## File Structure And Interfaces

| File | Responsibility |
| --- | --- |
| Create `flowgency/integrations/flowgency/copilot_output.py` | Pure event-envelope, argument, message, and telemetry normalization; no process or filesystem operations |
| Modify `flowgency/integrations/flowgency/copilot.py` | Keep existing parser signatures and aggregation, using event-local validation |
| Create `tests/test_copilot_output.py` | Synthetic malformed-event and metadata-preservation regressions |
| Modify `tests/test_integration_sidecar.py` | Existing normal-run and timeout fixtures exercise JSON-string arguments |
| Create `flowgency/web/log_preview.py` | Bounded file preview, event-stream recognition, Markdown sanitization, inert fallback |
| Create `tests/test_log_preview.py` | Bounds, recovery, Markdown, malicious markup, and large JSONL tests |
| Modify `pyproject.toml` | Add `nh3>=0.2.18,<0.4` as a runtime dependency |
| Modify `flowgency/app.py` | Offload existing log route's synchronous work without changing other routes |
| Modify `flowgency/templates/log_view.html` | Conditional safe Markdown/plain text plus truncation status and wrapping |
| Modify `tests/test_logs.py` | Route recovery, safety, missing-file, and concurrency tests |
| Create `tests/ui/log_view.spec.ts` | Disposable browser validation across existing desktop/mobile projects |

Tasks are sequential: Task 2 consumes Task 1's pure helpers; Task 3 consumes Task 2's preview object. Keep task commits separate and review each task before dependent work.

## Task 1: Make Copilot Parsing Resilient Per Event

**Files:** Create the normalization module and its tests; modify only the parser in `copilot.py` and the two existing integration test fixtures named below.

**Interfaces:**
- `object_fields(value: object) -> dict`: only objects are mappings; other values yield an empty mapping.
- `argument_object(value: object) -> dict`: also accepts a JSON-string-encoded object.
- `event_objects(raw: str) -> Iterator[dict]`: yields object events with string `type`, ignoring malformed lines and non-object events.
- `assistant_message(event: dict) -> str | None`: complete, nonempty string `assistant.message` content only.
- `line_count(value: object) -> int`: nonnegative integer metrics; malformed values contribute zero.
- Preserve `_parse_jsonl_output_details(raw, root) -> tuple[str, list[FileChange], list[str]]` and its two-value wrapper.

- [ ] **Step 1: Add failing synthetic tests in `tests/test_copilot_output.py`.**

```python
import json
from pathlib import Path

import pytest

from flowgency.integrations.flowgency.copilot import CopilotIntegration


def event(kind, **data):
    return {"type": kind, "data": data}


@pytest.mark.parametrize("encoded", [False, True])
def test_arguments_preserve_messages_changes_and_attempts(encoded):
    arguments = {"path": str(Path("/repo") / "audit.txt")}
    if encoded:
        arguments = json.dumps(arguments)
    records = [
        event("assistant.message", content="Before"),
        event("tool.execution_start", toolCallId="write", toolName="create", arguments=arguments),
        event("tool.execution_complete", toolCallId="write", success=True,
              toolTelemetry={"metrics": {"linesAdded": 2}}),
        event("assistant.message", content="After"),
    ]
    text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(
        "\n".join(map(json.dumps, records)), Path("/repo")
    )
    assert text == "Before\nAfter"
    assert attempts == ["audit.txt"]
    assert [(change.path, change.status, change.lines_added) for change in changes] == [
        ("audit.txt", "added", 2)
    ]


@pytest.mark.parametrize("bad", [
    None, [], 5, "unexpected", {"type": []},
    {"type": "assistant.message", "data": "wrong"},
    event("assistant.message", content=["wrong"]),
    event("tool.execution_start", toolCallId=[], toolName="create", arguments={}),
    event("tool.execution_start", toolCallId="bad", toolName=[], arguments={}),
    event("tool.execution_start", toolCallId="bad", toolName="create", arguments="not-json"),
    event("tool.execution_start", toolCallId="bad", toolName="create", arguments="[]"),
    event("tool.execution_start", toolCallId="bad", toolName="create", arguments={"path": []}),
    event("tool.execution_complete", toolCallId=[], toolTelemetry="wrong"),
    {"type": "result", "usage": {"codeChanges": {"filesModified": [None, {}, 3]}}},
])
def test_bad_event_does_not_erase_other_information(bad):
    records = [
        event("assistant.message", content="Before"),
        event("tool.execution_start", toolCallId="write", toolName="edit",
              arguments={"path": "/repo/kept.txt"}),
        bad,
        event("tool.execution_complete", toolCallId="write", success=True,
              toolTelemetry={"metrics": {"linesAdded": "bad", "linesRemoved": 3}}),
        event("assistant.message_delta", deltaContent="NOT DISPLAYED"),
        event("assistant.reasoning", content="NOT DISPLAYED"),
        event("assistant.message", content="After"),
    ]
    raw = "\n".join(map(json.dumps, records)) + "\n{broken"
    text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(raw, Path("/repo"))
    assert text == "Before\nAfter"
    assert attempts == ["kept.txt"]
    assert [(change.path, change.lines_added, change.lines_removed) for change in changes] == [
        ("kept.txt", 0, 3)
    ]


@pytest.mark.parametrize("telemetry", [None, [], "bad", {"properties": [], "metrics": []}])
def test_bad_telemetry_keeps_known_write_attempt(telemetry):
    raw = "\n".join(map(json.dumps, [
        event("tool.execution_start", toolCallId="write", toolName="edit",
              arguments={"path": "/repo/kept.txt"}),
        event("tool.execution_complete", toolCallId="write", success=True, toolTelemetry=telemetry),
        event("assistant.message", content="Kept"),
    ]))
    text, changes, attempts = CopilotIntegration._parse_jsonl_output_details(raw, Path("/repo"))
    assert text == "Kept"
    assert attempts == ["kept.txt"]
    assert [change.path for change in changes] == ["kept.txt"]
```

- [ ] **Step 2: Run the red check.**

```text
python -m pytest tests/test_copilot_output.py -q
```

Expect string arguments and malformed events to return raw JSON or erase metadata. Confirm those assertion failures, not an unrelated import failure.

- [ ] **Step 3: Add the pure normalization module and replace the parser body.**

Contents of `copilot_output.py`:

```python
from collections.abc import Iterator
import json


def object_fields(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def argument_object(value: object) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return object_fields(value)


def event_objects(raw: str) -> Iterator[dict]:
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            yield value


def assistant_message(event: dict) -> str | None:
    if event.get("type") != "assistant.message":
        return None
    content = object_fields(event.get("data")).get("content")
    return content if isinstance(content, str) and content else None


def line_count(value: object) -> int:
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (ValueError, OverflowError):
        return 0
```

Import these helpers in `copilot.py`. Replace `_parse_jsonl_output_details` with the following method, keeping the same public return contract:

```python
    @staticmethod
    def _parse_jsonl_output_details(
        raw: str,
        root: "Path | None",
    ) -> "tuple[str, list[FileChange], list[str]]":
        """Recover messages and metadata without discarding valid earlier events."""
        tool_names: dict[str, str] = {}
        tool_paths: dict[str, str] = {}
        files: dict[str, dict] = {}
        texts: list[str] = []
        write_attempts: list[str] = []
        seen_attempts: set[str] = set()
        for obj in event_objects(raw):
            content = assistant_message(obj)
            if content is not None:
                texts.append(content)
            data = object_fields(obj.get("data"))
            event_type = obj["type"]
            call_id = data.get("toolCallId")
            if not isinstance(call_id, str):
                call_id = None
            if event_type == "tool.execution_start":
                tool_name = data.get("toolName")
                if not call_id or not isinstance(tool_name, str):
                    continue
                tool_names[call_id] = tool_name
                path = argument_object(data.get("arguments")).get("path")
                if not isinstance(path, str) or not path:
                    continue
                tool_paths[call_id] = path
                if tool_name in CopilotIntegration._WRITE_TOOLS:
                    relative = CopilotIntegration._relativize(path, root)
                    if relative not in seen_attempts:
                        seen_attempts.add(relative)
                        write_attempts.append(relative)
            elif event_type == "tool.execution_complete":
                telemetry = object_fields(data.get("toolTelemetry"))
                properties = object_fields(telemetry.get("properties"))
                metrics = object_fields(telemetry.get("metrics"))
                command = properties.get("command")
                if not isinstance(command, str) or not command:
                    command = tool_names.get(call_id, "")
                if command not in CopilotIntegration._WRITE_TOOLS or data.get("success") is False:
                    continue
                path = tool_paths.get(call_id)
                if not path:
                    continue
                relative = CopilotIntegration._relativize(path, root)
                entry = files.setdefault(relative, {"status": None, "added": 0, "removed": 0})
                entry["added"] += line_count(metrics.get("linesAdded"))
                entry["removed"] += line_count(metrics.get("linesRemoved"))
                status = CopilotIntegration._STATUS_BY_COMMAND.get(command, "modified")
                if entry["status"] is None or (entry["status"] != "added" and status == "added"):
                    entry["status"] = status
                elif entry["status"] == "modified" and status == "deleted":
                    entry["status"] = "deleted"
            elif event_type == "result":
                usage = object_fields(obj.get("usage")) or object_fields(data.get("usage"))
                modified = object_fields(usage.get("codeChanges")).get("filesModified")
                if not isinstance(modified, list):
                    continue
                for path in modified:
                    if isinstance(path, str) and path:
                        relative = CopilotIntegration._relativize(path, root)
                        files.setdefault(relative, {"status": "modified", "added": 0, "removed": 0})
        changes = [
            FileChange(path=path, status=info["status"] or "modified",
                       lines_added=info["added"], lines_removed=info["removed"])
            for path, info in files.items()
        ]
        return "\n".join(texts) if texts else raw, changes, write_attempts
```

Keep `_relativize`'s existing defensive behavior, status precedence, wrapper, and raw fallback when no assistant messages exist. Do not change session parsing or usage reporting as unrelated cleanup.

- [ ] **Step 4: Run the same focused test immediately, then extend both real integration-path fixtures.** In `test_run_emits_json_and_populates_changed_files` and `test_run_timeout_preserves_partial_output` in `tests/test_integration_sidecar.py`, replace only their write-start arguments mapping with `json.dumps({"path": str(tmp_agent_dir / "new.txt")})` and `json.dumps({"path": str(tmp_agent_dir / "partial.txt")})`, respectively. Insert this event before each fixture's valid assistant message:

```python
{"type": "assistant.message", "data": {"content": ["invalid-content"]}},
```

Their existing assertions must still verify readable output, metadata, timeout exit code, and retained write attempts. These tests mock only the external CLI boundary; no real job is launched.

```text
python -m pytest tests/test_copilot_output.py tests/test_integration_sidecar.py -q
```

- [ ] **Step 5: Review, check diagnostics and whitespace, and commit.**

```text
git add flowgency/integrations/flowgency/copilot_output.py flowgency/integrations/flowgency/copilot.py tests/test_copilot_output.py tests/test_integration_sidecar.py
git commit -m "fix(copilot): preserve output across malformed events"
```

Review metadata retention as carefully as text: write attempts inform permission enforcement. Do not start Task 2 until this task is reviewed.

## Task 2: Add Bounded Safe Log Presentation

**Files:** Create `flowgency/web/log_preview.py` and `tests/test_log_preview.py`; add nh3 to `pyproject.toml`.

**Interfaces:**
- Consumes Task 1's `event_objects` and `assistant_message`; imports no process launcher.
- Produces frozen `LogPreview(text: str, content_html: str | None, truncated: bool)`.
- `read_log_preview(path: Path) -> LogPreview` reads at most `SOURCE_LIMIT + 1` bytes and never writes the file.
- `SOURCE_LIMIT = 4 * 1024 * 1024`, `DISPLAY_LIMIT = 64 * 1024`.
- `content_html is None` means Jinja must escape `text`; otherwise the HTML has already been sanitized for a log-only allowlist.

- [ ] **Step 1: Add failing presentation tests.** Contents of `tests/test_log_preview.py`:

```python
import io
import json
from pathlib import Path

import pytest

from flowgency.web import log_preview


def test_historical_jsonl_recovers_messages_without_rewriting(tmp_path):
    path = tmp_path / "historic.out"
    raw = "\n".join([
        json.dumps({"type": "session.start", "data": {}}),
        '{broken',
        json.dumps({"type": "tool.execution_start", "data": {"arguments": "bad"}}),
        json.dumps({"type": "assistant.message", "data": {"content": "# Audit\n\n**Done**"}}),
    ])
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    preview = log_preview.read_log_preview(path)
    assert preview.text == "# Audit\n\n**Done**"
    assert "<h1>Audit</h1>" in preview.content_html
    assert "<strong>Done</strong>" in preview.content_html
    assert "tool.execution_start" not in preview.content_html
    assert path.read_bytes() == before


def test_unrelated_events_do_not_prevent_recovery(tmp_path):
    path = tmp_path / "mixed.out"
    path.write_text('null\n{"type":"unrelated"}\n{broken\n' + json.dumps({"type": "assistant.message", "data": {"content": "Recovered"}}), encoding="utf-8")
    preview = log_preview.read_log_preview(path)
    assert preview.text == "Recovered"
    assert preview.content_html == "<p>Recovered</p>"


def test_stream_without_messages_is_plain_text(tmp_path, monkeypatch):
    path = tmp_path / "events.out"
    raw = json.dumps({"type": "tool.execution_complete", "data": {"result": "<script>bad()</script>"}})
    path.write_text(raw, encoding="utf-8")
    def forbidden_markdown(*args, **kwargs):
        pytest.fail("Raw events must not enter Markdown")
    monkeypatch.setattr(log_preview.markdown, "Markdown", forbidden_markdown)
    preview = log_preview.read_log_preview(path)
    assert preview.content_html is None
    assert preview.text == raw


def test_markdown_with_json_example_remains_markdown(tmp_path):
    path = tmp_path / "report.out"
    path.write_text('# Report\n\n```json\n{"type":"assistant.message","data":{"content":"example"}}\n```\n\n- Item\n\n| A | B |\n|---|---|\n| 1 | 2 |', encoding="utf-8")
    html = log_preview.read_log_preview(path).content_html
    assert "<h1>Report</h1>" in html
    assert "<code" in html and "<li>Item</li>" in html and "<table>" in html


def test_markdown_sanitizes_active_markup(tmp_path):
    path = tmp_path / "unsafe.out"
    path.write_text('<script>window.logExecuted=1</script>\n<style>body{display:none}</style>\n<img src="/log-resource" onerror="bad()">\n<iframe src="/log-frame"></iframe>\n<form action="/danger"><input></form>\n<a href="javascript:bad()" onclick="bad()">bad</a>\n\n[safe](https://example.org)', encoding="utf-8")
    html = log_preview.read_log_preview(path).content_html
    for forbidden in ("<script", "<style", "<img", "<iframe", "<form", "<input", "onclick", "onerror", "javascript:"):
        assert forbidden not in html.lower()
    assert 'href="https://example.org"' in html


def test_source_reads_are_bounded_and_partial_record_ignored(monkeypatch):
    message = json.dumps({"type": "assistant.message", "data": {"content": "Kept"}}).encode() + b"\n"
    payload = message + b'{"type":"assistant.message","data":{"content":"' + b"x" * 200
    class TrackingStream(io.BytesIO):
        def read(self, size=-1):
            assert size == 129
            return super().read(size)
    monkeypatch.setattr(log_preview, "SOURCE_LIMIT", 128)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: TrackingStream(payload))
    preview = log_preview.read_log_preview(Path("bounded.out"))
    assert preview.text == "Kept"
    assert preview.truncated


def test_cut_first_json_event_stays_inert(tmp_path, monkeypatch):
    path = tmp_path / "cut.out"
    path.write_text(json.dumps({"type": "tool.execution_complete", "data": {"result": "x" * 1000}}), encoding="utf-8")
    monkeypatch.setattr(log_preview, "SOURCE_LIMIT", 128)
    preview = log_preview.read_log_preview(path)
    assert preview.content_html is None
    assert preview.truncated


@pytest.mark.parametrize("suffix", [".out", ".err"])
def test_display_limit_and_invalid_utf8(tmp_path, monkeypatch, suffix):
    path = tmp_path / f"unicode{suffix}"
    path.write_bytes(b"bad:\xff " + "\u00e9".encode() * 50)
    monkeypatch.setattr(log_preview, "DISPLAY_LIMIT", 31)
    preview = log_preview.read_log_preview(path)
    assert len(preview.text.encode("utf-8")) <= 31
    assert "\ufffd" in preview.text
    assert preview.truncated
    if suffix == ".err":
        assert preview.content_html is None


def test_large_event_stream_passes_only_message_text_to_markdown(tmp_path, monkeypatch):
    path = tmp_path / "large.out"
    envelope = json.dumps({"type": "tool.execution_complete", "data": {"result": "x" * 1024}})
    path.write_text((envelope + "\n") * 2800 + json.dumps({"type": "assistant.message", "data": {"content": "# Finished"}}), encoding="utf-8")
    original = log_preview.markdown.Markdown
    calls = []
    def converter(*args, **kwargs):
        instance = original(*args, **kwargs)
        convert = instance.convert
        def record(text):
            calls.append(text)
            return convert(text)
        instance.convert = record
        return instance
    monkeypatch.setattr(log_preview.markdown, "Markdown", converter)
    preview = log_preview.read_log_preview(path)
    assert calls == ["# Finished"]
    assert "<h1>Finished</h1>" in preview.content_html
    assert not preview.truncated
```

- [ ] **Step 2: Run the red check and install the dependency.**

```text
python -m pytest tests/test_log_preview.py -q
```

Expected initial failure: missing `log_preview` module. Add `"nh3>=0.2.18,<0.4",` next to `"markdown",` in runtime dependencies, then run:

```text
python -m pip install "nh3>=0.2.18,<0.4"
```

Use this targeted installation to avoid moving an existing editable installation away from the main checkout. The declared runtime dependency must be present in packaging too.

- [ ] **Step 3: Implement `log_preview.py`.**

```python
from dataclasses import dataclass
import json
from pathlib import Path

import markdown
import nh3

from flowgency.integrations.flowgency.copilot_output import assistant_message, event_objects


SOURCE_LIMIT = 4 * 1024 * 1024
DISPLAY_LIMIT = 64 * 1024
LOG_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em",
    "del", "a", "table", "thead", "tbody", "tr", "th", "td",
}


@dataclass(frozen=True)
class LogPreview:
    text: str
    content_html: str | None
    truncated: bool


def _is_event_stream(raw: str) -> bool:
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except (ValueError, RecursionError):
            if not stripped.startswith(("{", "[")):
                return False
            continue
        if not isinstance(value, dict):
            continue
        kind = value.get("type")
        if not isinstance(kind, str):
            continue
        if kind == "result" or kind.startswith(("session.", "assistant.", "tool.", "user.", "system.", "model.")):
            return True
    return False


def read_log_preview(path: Path) -> LogPreview:
    with path.open("rb") as stream:
        source = stream.read(SOURCE_LIMIT + 1)
    truncated = len(source) > SOURCE_LIMIT
    raw = source[:SOURCE_LIMIT].decode("utf-8", errors="replace")
    render_markdown = path.suffix == ".out"
    is_event_stream = render_markdown and _is_event_stream(raw)
    if render_markdown and truncated and not is_event_stream and raw.lstrip().startswith(("{", "[")):
        render_markdown = False
    if is_event_stream:
        complete = raw
        if truncated and not raw.endswith("\n"):
            complete = raw.rpartition("\n")[0]
        messages = [message for event in event_objects(complete)
                    if (message := assistant_message(event)) is not None]
        if messages:
            raw = "\n".join(messages)
        else:
            render_markdown = False
    display = raw.encode("utf-8")
    truncated = truncated or len(display) > DISPLAY_LIMIT
    text = display[:DISPLAY_LIMIT].decode("utf-8", errors="ignore")
    content_html = None
    if render_markdown:
        converted = markdown.Markdown(extensions=["tables", "fenced_code", "nl2br"]).convert(text)
        content_html = nh3.clean(
            converted,
            tags=LOG_TAGS,
            attributes={"a": {"href", "title"}, "code": {"class"}},
            clean_content_tags={"script", "style", "iframe", "object", "embed", "form"},
            url_schemes={"http", "https", "mailto"},
            link_rel="noopener noreferrer",
        )
    return LogPreview(text=text, content_html=content_html, truncated=truncated)
```

Do not enable the Markdown `meta` extension here: log text resembling frontmatter must not disappear from the displayed artifact. This does not change the global renderer. A source truncation with no complete event falls back to escaped raw preview, never Markdown.

- [ ] **Step 4: Run the same tests, then the parser/presentation group.**

```text
python -m pytest tests/test_log_preview.py -q
python -m pytest tests/test_copilot_output.py tests/test_log_preview.py tests/test_integration_sidecar.py -q
```

Use one terminal command at a time. Also add and run the following boundary characterization tests in the same module before review:

```python
def test_missing_log_raises_for_route_to_map(tmp_path):
    with pytest.raises(FileNotFoundError):
        log_preview.read_log_preview(tmp_path / "missing.out")


def test_exact_limits_do_not_claim_truncation(tmp_path, monkeypatch):
    path = tmp_path / "exact.err"
    path.write_bytes(b"12345678")
    monkeypatch.setattr(log_preview, "SOURCE_LIMIT", 8)
    monkeypatch.setattr(log_preview, "DISPLAY_LIMIT", 8)
    preview = log_preview.read_log_preview(path)
    assert preview.text == "12345678"
    assert not preview.truncated


def test_plain_text_source_cut_inside_unicode_is_safe(tmp_path, monkeypatch):
    path = tmp_path / "partial.err"
    path.write_bytes(b"1234567" + "\u00e9".encode())
    monkeypatch.setattr(log_preview, "SOURCE_LIMIT", 8)
    preview = log_preview.read_log_preview(path)
    assert preview.text == "1234567\ufffd"
    assert preview.truncated
```

- [ ] **Step 5: Review sanitizer allowlist, bounds, and event detection; commit.**

```text
git add pyproject.toml flowgency/web/log_preview.py tests/test_log_preview.py
git commit -m "fix(logs): recover and sanitize bounded previews"
```

Review whether ordinary JSON code examples remain Markdown, whether malicious markup remains inert, and whether no-message event streams bypass Markdown. These are contract gates, not optional polish.

## Task 3: Wire The Existing Viewer Without Blocking Requests

**Files:** Modify `flowgency/app.py`, `flowgency/templates/log_view.html`, and `tests/test_logs.py`; create `tests/ui/log_view.spec.ts`.

**Interfaces:**
- Consumes `read_log_preview(Path) -> LogPreview` from Task 2.
- Add `_log_view_context(team: str, path: str) -> dict` in `app.py`, adjacent to `log_view`; it resolves team, validates the log path, reads/render previews, and builds team context synchronously in a worker.
- `log_view` awaits `run_in_threadpool(_log_view_context, team, path)` and renders the existing template using `filename`, `raw`, `content_html`, and `truncated`.
- No new route or config field; no browser-side parsing, job submission, or streaming.

- [ ] **Step 1: Append route regression tests.** Add `asyncio`, `json`, `threading`, `httpx`, and `pytest` imports to `tests/test_logs.py` as needed; do not reformat the existing tests.

```python
@pytest.fixture
def preview_team(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    group = {"key": "test", "name": "Test", "logs": logs}
    monkeypatch.setattr(app_mod, "get_team", lambda team: group)
    monkeypatch.setattr(app_mod, "team_context", lambda group: {
        "team": "test", "team_name": "Test", "teams": {"test": "Test"},
        "flowgency_title": "Flowgency", "workspaces": [], "workspaces_available": False,
        "nav_open_observations": 0, "nav_actionable": 0, "nav_actionable_proposals": 0,
        "nav_agent_count": 0, "nav_running_decisions": 0, "show_tips": False,
        "tips_dismissed": [], "theme_css": "",
    })
    return logs


def test_log_route_recovers_historical_json(preview_team):
    path = preview_team / "historic.out"
    path.write_text(json.dumps({"type": "assistant.message", "data": {"content": "## Readable\n\n**Restored**"}}), encoding="utf-8")
    before = path.read_bytes()
    response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(path)})
    assert response.status_code == 200
    assert "<h2>Readable</h2>" in response.text
    assert "<strong>Restored</strong>" in response.text
    assert '"type": "assistant.message"' not in response.text
    assert path.read_bytes() == before


@pytest.mark.parametrize("suffix", [".out", ".err"])
def test_log_route_does_not_inject_markup(preview_team, suffix):
    path = preview_team / f"malicious{suffix}"
    path.write_text('<img src="/log-resource" onerror="window.logExecuted=1">', encoding="utf-8")
    response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(path)})
    assert response.status_code == 200
    assert '<img src="/log-resource"' not in response.text


def test_log_route_missing_file_is_404(preview_team):
    response = TestClient(app_mod.app).get("/test/logs/view", params={"path": str(preview_team / "missing.out")})
    assert response.status_code == 404


def test_log_work_does_not_block_event_loop(preview_team, monkeypatch):
    path = preview_team / "slow.out"
    path.write_text("Readable", encoding="utf-8")
    entered = threading.Event()
    released = threading.Event()
    get_team = app_mod.get_team
    def blocked_team(team):
        entered.set()
        if not released.wait(2):
            raise AssertionError("Log processing blocked the event loop")
        return get_team(team)
    monkeypatch.setattr(app_mod, "get_team", blocked_team)
    async def check():
        transport = httpx.ASGITransport(app=app_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pending = asyncio.create_task(client.get("/test/logs/view", params={"path": str(path)}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                response = await asyncio.wait_for(client.get("/static/manifest.json"), 1)
                assert response.status_code == 200
                assert not pending.done()
            finally:
                released.set()
                await pending
    asyncio.run(check())
```

These tests deliberately bypass production service setup for the new route slice, use no lifespan context, and retain the existing real-config traversal test unchanged. If the base template requires additional context fields, derive them from its existing inputs without invoking live services.

- [ ] **Step 2: Run the red route slice.**

```text
python -m pytest tests/test_logs.py -q -k "log_route or log_work"
```

Expect recovery, injection, and concurrency failures; missing-file behavior may already pass. Do not use the real multi-megabyte log as a unit-test fixture.

- [ ] **Step 3: Offload the route and update the template.** Add imports for `run_in_threadpool` from `starlette.concurrency` and `read_log_preview` from `flowgency.web.log_preview`. Replace only the existing log-view implementation with:

```python
def _log_view_context(team: str, path: str) -> dict:
    group = get_team(team)
    file_path = Path(path)
    logs_dir = Path(group["logs"]).resolve()
    validate_file_access(file_path, logs_dir)
    try:
        preview = read_log_preview(file_path)
    except FileNotFoundError:
        raise HTTPException(404, "Log not found")
    return {
        **team_context(group),
        "filename": file_path.name,
        "content_html": preview.content_html,
        "raw": preview.text,
        "truncated": preview.truncated,
    }


@app.get("/{team}/logs/view", response_class=HTMLResponse)
async def log_view(request: Request, team: str, path: str):
    context = await run_in_threadpool(_log_view_context, team, path)
    return templates.TemplateResponse(request, "log_view.html", {
        "request": request,
        **context,
    })
```

Retain `validate_file_access` semantics and the existing list route. Do not move any log reads before access validation. Do not modify other Markdown consumers or startup queue-drain behavior.

In `log_view.html`, keep existing layout and back link. Add `min-w-0` to the filename header container and `[overflow-wrap:anywhere]` to the filename heading. Replace the content block with:

```html
  {% if truncated %}
  <p role="status" class="mb-3 text-sm text-amber-700 dark:text-amber-300">Preview truncated</p>
  {% endif %}
  <div data-log-content class="prose max-w-none min-w-0 [overflow-wrap:anywhere] [&_pre]:overflow-x-auto">
    {% if content_html is not none %}
    {{ content_html | safe }}
    {% else %}
    <pre class="whitespace-pre-wrap text-sm">{{ raw }}</pre>
    {% endif %}
  </div>
```

Only sanitized HTML is marked safe. Plain-text fallback is escaped by Jinja. Do not use `safe` on `raw` or add a JSON `<script>` blob.

- [ ] **Step 4: Run focused checks immediately, then add UI coverage.**

```text
python -m pytest tests/test_logs.py -q
python -m pytest tests/test_copilot_output.py tests/test_log_preview.py tests/test_logs.py tests/test_integration_sidecar.py tests/test_job_routes.py tests/test_dashboard.py -q
```

Create `tests/ui/log_view.spec.ts`:

```typescript
import { expect, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { assertNoLayoutIssues } from './layout';

test('logs preserve readable output without active log markup', async ({ page }, testInfo) => {
  const logs = path.resolve('tests/ui/.runtime/current/teams/newsletter/logs/2026-07-16');
  await mkdir(logs, { recursive: true });
  const unsafe = '<script>window.logExecuted=1</script><img src="/log-resource" onerror="window.logExecuted=1">';
  const cases = [
    { name: 'markdown.out', content: '# Readable\n\n**Restored**\n\n' + unsafe, truncated: false },
    { name: 'events.out', content: JSON.stringify({ type: 'tool.execution_start', data: { arguments: 'bad' } }) + '\n' + JSON.stringify({ type: 'assistant.message', data: { content: '# Readable\n\n**Restored**\n\n' + unsafe } }), truncated: false },
    { name: 'long.out', content: '# Readable\n\n' + 'Preview content\n'.repeat(10000), truncated: true },
    { name: 'stderr.err', content: unsafe, truncated: false },
  ];
  await page.addInitScript(() => { (window as Window & { logExecuted?: number }).logExecuted = 0; });
  const injectedRequests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/log-resource')) injectedRequests.push(request.url());
  });
  for (const sample of cases) {
    const filePath = path.join(logs, `log-view-${testInfo.project.name}-${sample.name}`);
    await writeFile(filePath, sample.content, 'utf8');
    await page.goto('/newsletter/logs/view?path=' + encodeURIComponent(filePath));
    const content = page.locator('[data-log-content]');
    await expect(content).toBeVisible();
    await expect(content.locator('script, img, iframe, form, style')).toHaveCount(0);
    expect(await page.evaluate(() => (window as Window & { logExecuted?: number }).logExecuted)).toBe(0);
    expect(injectedRequests).toEqual([]);
    await expect(page.getByRole('status')).toHaveCount(sample.truncated ? 1 : 0);
    if (sample.name !== 'stderr.err') await expect(content.locator('h1')).toHaveText('Readable');
    if (sample.name === 'events.out' || sample.name === 'markdown.out') {
      await expect(content.locator('strong')).toHaveText('Restored');
    }
    await assertNoLayoutIssues(page);
    await page.screenshot({ path: testInfo.outputPath(sample.name + '.png'), fullPage: false });
  }
});
```

The existing harness uses a disposable `.runtime/current` root and port 8765, and fails rather than reusing another server. Do not seed these files into the user's real logs. The test fixture has no eligible new scheduled work from this change. No new snapshot baselines are needed: inspect the generated screenshots for desktop/mobile light/dark projects.

If the existing `.venv` or Node dependencies were removed, initialize only the test environment from the worktree:

```text
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[test]"
npm install
npx playwright install chromium
npm run test:ui -- tests/ui/log_view.spec.ts
```

Do not recreate an existing venv unnecessarily; do not commit dependency-lock churn unless it is required for the new declared runtime dependency. The nh3 dependency must be installed in whichever interpreter runs the UI server.

- [ ] **Step 5: Commit and review the integrated viewer.**

```text
git add flowgency/app.py flowgency/templates/log_view.html tests/test_logs.py tests/ui/log_view.spec.ts
git commit -m "fix(logs): render previews without blocking requests"
```

Review same-tab navigation, route path validation, test isolation, independent Markdown instances, raw escaping, and the concurrency test's actual single-event-loop behavior before whole-branch verification.

## Whole-Branch Verification And Handoff

- [ ] Run `python -m pytest tests/ -q` from the active worktree. Record actual counts and failure causes; no baseline waivers are in effect for this branch.
- [ ] Run `npm run test:ui -- tests/ui/log_view.spec.ts` and inspect each project's screenshots. Record UI test results, layout safety, and absence of log-origin resource requests. If environment setup is blocked, disclose the unverified gate; do not claim browser verification.
- [ ] Record performance on the synthetic large-log test with `python -m pytest tests/test_log_preview.py -q --durations=5`. The structural assertion must prove raw telemetry is not handed to Markdown; wall-clock output is supplementary evidence.
- [ ] Optionally replay the originally reported log through `read_log_preview` in a read-only diagnostic command, printing only byte counts, duration, and extraction success. Never copy its content into tracked fixtures or mutate it.
- [ ] Request whole-branch review of the full merge-base-to-HEAD diff with task review evidence, test results, and the approved spec. Address actual regressions within the defined scope. Record remaining caveats honestly.
- [ ] Mark completed plan steps as complete and record final verification in a documentation-only commit before integration. Do not claim the live server is updated until it has actually loaded the new code; do not restart it without permission.

## Integration Procedure

Follow the repository's pre-authorized integration sequence after review and green gates; do not offer a merge-choice menu.

1. If local `master` advanced, rebase the feature onto `master` from the worktree, resolve only feature conflicts, rerun the full suite, and review the rebased result before proceeding.
2. Inspect the main checkout with `git -C C:/Projekty/Flowgency status --short --branch`. Preserve any unrelated edits in a specifically named stash, record its exact object ID, and leave untracked runtime data untouched.
3. Fast-forward only: `git -C C:/Projekty/Flowgency merge --ff-only fix/readable-agent-logs`.
4. Restore any recorded stash, preserving both user edits and feature changes. Resolve only unambiguous conflicts; do not silently discard user content or include it in the feature commit.
5. From the main checkout, install its declared dependencies if needed and run `python -m pytest tests/ -q` on the fast-forwarded `master`. Stop on failures; do not push or remove the worktree until green.
6. Publish both branches with `git -C C:/Projekty/Flowgency push origin master fix/readable-agent-logs`.
7. Once pushed and verified, remove only this clean worktree with `git -C C:/Projekty/Flowgency worktree remove .worktrees/readable-agent-logs`, then prune. Keep the feature branch. Do not force-remove a dirty worktree, delete runtime artifacts, or use `git clean -fdx`.

## Self-Review Coverage

- New-run parsing, nested event shapes, string arguments, write attempts, aggregation, and timeout path: Task 1 plus existing integration tests.
- Read-only historical recovery, ordinary Markdown/code examples, no-message fallback, sanitization, malformed UTF-8, partial JSONL, source/display bounds: Task 2.
- Existing route, traversal/404, off-event-loop work, safe template output, responsive browser rendering, and resource/script inertness: Task 3.
- Synthetic performance evidence and optional local replay, full suite, browser checks, review, and safe integration: final gates.
- No global renderer changes, production log rewrites, configured agent launches, migrations, retries, new scheduler behavior, or live-server restart are authorized.