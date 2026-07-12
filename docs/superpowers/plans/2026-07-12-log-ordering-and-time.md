# Log Ordering and Time Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each execution log's local modification time and order logs within each day newest-first, with OUT before ERR for equal timestamps.

**Architecture:** Enrich the existing `collect_logs()` view model with the same local modification-time `datetime` used by the agent activity timeline. Keep filesystem work in Python, sort the enriched entries deterministically, and let the Jinja template only format the supplied time.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, pytest

## Global Constraints

- Date groups remain ordered newest first.
- Use `Path.stat().st_mtime` converted with `datetime.fromtimestamp()` as the timestamp source.
- Display only `HH:MM` because the surrounding section already displays the date.
- Sort entries within each date newest first.
- For equal timestamps, show OUT before ERR.
- Continue hiding zero-byte ERR files.
- Do not add template-side filesystem access or filename timestamp parsing.

---

## File Structure

- Modify `agency/app.py`: add timestamp metadata and deterministic within-day ordering in `collect_logs()`.
- Modify `agency/templates/logs.html`: display the supplied time before the OUT/ERR badge.
- Modify `tests/test_logs.py`: cover timestamp metadata, ordering, tie-breaking, rendered output, and the existing empty-ERR behavior.

### Task 1: Add Timestamp Metadata and Ordering

**Files:**
- Modify: `agency/app.py:713-737`
- Test: `tests/test_logs.py`

**Interfaces:**
- Consumes: `g["shared"]` as a `Path`-compatible shared directory root.
- Produces: `collect_logs(g: dict) -> dict[str, list[dict]]`, where each entry includes `name: str`, `path: str`, `suffix: str`, `size: int`, and `timestamp: datetime`.

- [ ] **Step 1: Add imports and a failing ordering test**

Update `tests/test_logs.py` imports and add a test that controls file mtimes:

```python
import os
from datetime import datetime

from agency.app import build_agent_timeline, collect_logs, get_agent_logs


def test_collect_logs_orders_by_mtime_and_prefers_out_for_ties(tmp_path):
    logs_dir = tmp_path / "shared" / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    older = logs_dir / "agent-z-older.out"
    newer_out = logs_dir / "agent-a-newer.out"
    newer_err = logs_dir / "agent-a-newer.err"
    older.write_text("older")
    newer_out.write_text("newer output")
    newer_err.write_text("newer error")

    older_epoch = datetime(2026, 7, 12, 19, 45).timestamp()
    newer_epoch = datetime(2026, 7, 12, 20, 6).timestamp()
    os.utime(older, (older_epoch, older_epoch))
    os.utime(newer_out, (newer_epoch, newer_epoch))
    os.utime(newer_err, (newer_epoch, newer_epoch))

    entries = collect_logs({"shared": tmp_path / "shared"})["2026-07-12"]

    assert [entry["name"] for entry in entries] == [
        "agent-a-newer.out",
        "agent-a-newer.err",
        "agent-z-older.out",
    ]
    assert entries[0]["timestamp"] == datetime.fromtimestamp(newer_out.stat().st_mtime)
    assert entries[2]["timestamp"] == datetime.fromtimestamp(older.stat().st_mtime)
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
python -m pytest tests\test_logs.py::test_collect_logs_orders_by_mtime_and_prefers_out_for_ties -v
```

Expected: FAIL because entries are still sorted by filename and do not contain `timestamp`.

- [ ] **Step 3: Enrich and sort entries in `collect_logs()`**

Replace the inner collection logic in `agency/app.py` with:

```python
        entries = []
        for f in date_dir.iterdir():
            if f.name.startswith("."):
                continue
            file_stat = f.stat()
            if _is_empty_error_log(f, file_stat.st_size):
                continue
            entries.append({
                "name": f.name,
                "path": str(f),
                "suffix": f.suffix,
                "size": file_stat.st_size,
                "timestamp": datetime.fromtimestamp(file_stat.st_mtime),
            })
        entries.sort(
            key=lambda entry: (
                entry["timestamp"],
                entry["suffix"].lower() == ".out",
            ),
            reverse=True,
        )
```

Keep the existing `if entries:` block that stores the date group.

- [ ] **Step 4: Run focused log tests**

Run:

```powershell
python -m pytest tests\test_logs.py -v
```

Expected: all tests in `tests\test_logs.py` PASS, including the pre-existing empty ERR assertions.

- [ ] **Step 5: Commit timestamp collection and ordering**

```powershell
git add agency\app.py tests\test_logs.py
git commit -m "fix(logs): sort daily entries by modification time" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 2: Render the Time in the Execution Log List

**Files:**
- Modify: `agency/templates/logs.html:15-28`
- Test: `tests/test_logs.py`

**Interfaces:**
- Consumes: the `timestamp: datetime` field produced by `collect_logs()`.
- Produces: an execution log row with a fixed-width `HH:MM` label before its OUT/ERR badge.

- [ ] **Step 1: Add a failing route-rendering test**

Append this test to `tests/test_logs.py`:

```python
def test_logs_page_displays_local_modification_time(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import agency.app as app_mod

    group_path = tmp_path / "group"
    shared = group_path / "shared"
    for directory in ("observations", "proposals", "decisions", "prompts"):
        (shared / directory).mkdir(parents=True)
    logs_dir = shared / "logs" / "2026-07-12"
    logs_dir.mkdir(parents=True)
    log_file = logs_dir / "agent-run.out"
    log_file.write_text("completed")
    displayed_time = datetime(2026, 7, 12, 20, 6)
    epoch = displayed_time.timestamp()
    os.utime(log_file, (epoch, epoch))

    monkeypatch.setattr(
        app_mod,
        "CONFIG",
        {"agency": {}, "groups": {"test": {"name": "Test Group", "path": str(group_path)}}},
    )
    monkeypatch.setattr(
        app_mod,
        "GROUPS",
        {
            "test": {
                "key": "test",
                "name": "Test Group",
                "path": group_path,
                "agents": [],
                "_agents_normalized": [],
            }
        },
    )

    response = TestClient(app_mod.app).get("/test/logs")

    assert response.status_code == 200
    assert "20:06" in response.text
    assert response.text.index("20:06") < response.text.index("OUT")
```

- [ ] **Step 2: Run the rendering test and verify it fails**

Run:

```powershell
python -m pytest tests\test_logs.py::test_logs_page_displays_local_modification_time -v
```

Expected: FAIL because `logs.html` does not render the entry timestamp.

- [ ] **Step 3: Render the time before the log-type badge**

In `agency/templates/logs.html`, update the row's left-side content:

```html
        <div class="flex items-center gap-3 min-w-0">
          <span class="w-12 shrink-0 text-xs font-mono text-gray-400">{{ e.timestamp.strftime('%H:%M') }}</span>
          {% if e.suffix == ".err" %}
          <span class="text-xs font-mono px-1.5 py-0.5 rounded bg-red-100 text-red-700">ERR</span>
          {% else %}
          <span class="text-xs font-mono px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">OUT</span>
          {% endif %}
          <span class="text-sm font-medium text-gray-900 font-mono truncate">{{ e.name }}</span>
        </div>
```

Keep the existing size label on the right. The `min-w-0` and `truncate` classes prevent the added time column from causing long filenames to overflow.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests\test_logs.py -v
```

Expected: all log tests PASS.

- [ ] **Step 5: Run the complete test suite**

Run:

```powershell
python -m pytest tests -v
```

Expected: the complete existing suite PASS with no regressions.

- [ ] **Step 6: Check the final diff**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only `agency/templates/logs.html` and `tests/test_logs.py` are changed since the Task 1 commit.

- [ ] **Step 7: Commit the rendered timestamp**

```powershell
git add agency\templates\logs.html tests\test_logs.py
git commit -m "feat(logs): show execution time in daily lists" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```
