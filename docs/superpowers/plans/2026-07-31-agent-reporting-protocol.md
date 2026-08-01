# Agent Reporting Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every agent — including those with `capabilities.write: false` — record observations, create proposals, and update its own memory, through a per-job outbox that Agency validates and ingests.

**Architecture:** Agency creates `<launch_view>/.agency/outbox/{observations,proposals}/` and `<launch_view>/.agency/memory/` before each run, seeds the memory directory with the agent's canonical memory, and tells the agent about both in the immutable task input. After a zero-exit run the worker validates the outbox, ingests valid records into `<group.path>/observations/` and `proposals/` with Agency-assigned filenames and Agency-stamped `agent`/`date`/`status`, then copies the memory directory into the existing staging directory so the untouched publication machinery can publish it.

**Tech Stack:** Python 3.11+, FastAPI/Jinja2 app, Pydantic config models, pytest. No new third-party dependencies.

## Global Constraints

- Run tests from the worktree root: `python -m pytest tests/ -q`. There is no
  `.venv` in this repository despite what `AGENTS.md` says; the interpreter on
  `PATH` is Python 3.13. The full suite takes about five minutes, so run the
  focused test file while iterating and the full suite once before committing.
- Baseline before this plan: 1630 passed, 2 skipped.
- Work happens in the worktree `.worktrees/agent-reporting-protocol` on branch `agent-reporting-protocol`.
- Commit messages follow Conventional Commits; subject ≤ 72 chars, imperative, lowercase, no trailing period; body wrapped at 72.
- No new third-party dependencies.
- `schema_version` stays `4`. No config-file format changes in this plan.
- All filesystem writes that land in Agency-owned storage use `atomic_write_text` / `atomic_write_bytes` from `agency/fs/atomic.py`.
- Windows path safety: compare paths case-insensitively, reject reparse points and symlinks, reject reserved filenames.
- `MAX_RECORDS_PER_KIND = 20`, `MAX_RECORD_BYTES = 65536` — exact values, used verbatim.
- Slug pattern is exactly `[a-z0-9-]{1,60}`.
- Do not modify `config.yaml`, `config.yaml.lock`, group-state directories, or logs.

---

### Task 1: Shared front-matter module

Move `parse_frontmatter` and `extract_display_title` out of `agency/app.py` so worker-side code can use them without importing the FastAPI layer. Add `slugify`, which later tasks need.

**Files:**
- Create: `agency/records/__init__.py`
- Create: `agency/records/frontmatter.py`
- Modify: `agency/app.py` (delete the two function bodies at lines 492-503 and 585-607, import from the new module)
- Test: `tests/test_records_frontmatter.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_frontmatter(text: str) -> tuple[dict, str]`
  - `extract_display_title(body: str | None, slug: str) -> str`
  - `slugify(value: str) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_records_frontmatter.py`:

```python
from __future__ import annotations

import subprocess
import sys

from agency.records.frontmatter import (
    extract_display_title,
    parse_frontmatter,
    slugify,
)


def test_parse_frontmatter_returns_meta_and_body():
    text = "---\nstatus: open\n---\n\nSome **Important Thing** here."
    meta, body = parse_frontmatter(text)
    assert meta == {"status": "open"}
    assert body == "Some **Important Thing** here."


def test_parse_frontmatter_without_frontmatter_returns_empty_meta():
    meta, body = parse_frontmatter("no frontmatter here")
    assert meta == {}
    assert body == "no frontmatter here"


def test_parse_frontmatter_with_invalid_yaml_returns_empty_meta():
    meta, body = parse_frontmatter("---\n: : :\n---\nbody")
    assert meta == {}


def test_extract_display_title_prefers_first_bold():
    assert extract_display_title("intro **Real Title.** rest", "fallback-slug") == "Real Title"


def test_extract_display_title_falls_back_to_slug():
    assert extract_display_title("", "my-slug") == "my slug"


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Suite Health: 3 Failures!") == "suite-health-3-failures"


def test_slugify_truncates_to_sixty_characters():
    assert len(slugify("x" * 200)) == 60


def test_slugify_returns_empty_string_when_nothing_survives():
    assert slugify("!!! ???") == ""


def test_importing_frontmatter_does_not_import_the_web_app():
    """The worker imports this module; it must not drag in the FastAPI layer."""
    code = (
        "import sys, agency.records.frontmatter; "
        "sys.exit(1 if 'agency.app' in sys.modules else 0)"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True)

    assert completed.returncode == 0, completed.stderr.decode()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_frontmatter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.records'`

- [ ] **Step 3: Create the package and module**

Create `agency/records/__init__.py`:

```python
from .frontmatter import extract_display_title, parse_frontmatter, slugify

__all__ = ["extract_display_title", "parse_frontmatter", "slugify"]
```

Create `agency/records/frontmatter.py`:

```python
"""Markdown record parsing shared by the web layer and the job worker."""

from __future__ import annotations

import re

import yaml

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 60


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text. Returns (meta, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                return meta, parts[2].strip()
            except yaml.YAMLError:
                pass
    return {}, text


def extract_display_title(body: str | None, slug: str) -> str:
    """Extract a human-readable title from markdown body text.

    Looks for the first **bold text** in the body (not inside headings).
    Falls back to slug with hyphens replaced by spaces.
    Truncates to 120 chars if needed.
    """
    if not body:
        return slug.replace("-", " ")

    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.search(r"\*\*(.+?)\*\*", stripped)
        if m:
            title = m.group(1).rstrip(".,;:!?")
            if len(title) > 120:
                return title[:117] + "..."
            return title

    return slug.replace("-", " ")


def slugify(value: str) -> str:
    """Reduce arbitrary text to the `[a-z0-9-]{1,60}` slug alphabet."""
    collapsed = _SLUG_STRIP.sub("-", value.strip().casefold()).strip("-")
    return collapsed[:_SLUG_MAX].strip("-")
```

- [ ] **Step 4: Run the new test**

Run: `python -m pytest tests/test_records_frontmatter.py -v`
Expected: PASS

- [ ] **Step 5: Re-point `agency/app.py` at the shared module**

In `agency/app.py`, delete the `parse_frontmatter` definition (currently at line 492) and the `extract_display_title` definition (currently at line 585), then add to the import block near the other `agency.` imports:

```python
from agency.records.frontmatter import extract_display_title, parse_frontmatter
```

Leave every call site unchanged — the names stay in `agency.app`'s namespace, so `from agency.app import parse_frontmatter` keeps working for existing tests.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS, same count as the pre-task baseline.

- [ ] **Step 7: Commit**

```bash
git add agency/records/__init__.py agency/records/frontmatter.py agency/app.py tests/test_records_frontmatter.py
git commit -m "refactor(records): extract front-matter helpers from app"
```

---

### Task 2: Outbox construction

**Files:**
- Create: `agency/records/outbox.py`
- Modify: `agency/records/__init__.py`
- Test: `tests/test_records_outbox.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `OutboxPaths` frozen dataclass with fields `root: Path`, `observations: Path`, `proposals: Path`, `memory: Path`
  - `create_outbox(launch_view: Path, *, memory_files: Mapping[str, bytes]) -> OutboxPaths`
  - `OUTBOX_RELATIVE_OBSERVATIONS = ".agency/outbox/observations"`
  - `OUTBOX_RELATIVE_PROPOSALS = ".agency/outbox/proposals"`
  - `OUTBOX_RELATIVE_MEMORY = ".agency/memory"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_records_outbox.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agency.records.outbox import (
    OUTBOX_RELATIVE_MEMORY,
    OUTBOX_RELATIVE_OBSERVATIONS,
    OUTBOX_RELATIVE_PROPOSALS,
    create_outbox,
)


def test_create_outbox_creates_all_directories(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()

    outbox = create_outbox(launch, memory_files={})

    assert outbox.observations.is_dir()
    assert outbox.proposals.is_dir()
    assert outbox.memory.is_dir()
    assert outbox.observations == launch.joinpath(*OUTBOX_RELATIVE_OBSERVATIONS.split("/"))
    assert outbox.proposals == launch.joinpath(*OUTBOX_RELATIVE_PROPOSALS.split("/"))
    assert outbox.memory == launch.joinpath(*OUTBOX_RELATIVE_MEMORY.split("/"))


def test_create_outbox_seeds_memory_files(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()

    outbox = create_outbox(launch, memory_files={"memory.md": b"prior knowledge"})

    assert (outbox.memory / "memory.md").read_bytes() == b"prior knowledge"


def test_create_outbox_replaces_a_previous_outbox(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()
    first = create_outbox(launch, memory_files={"memory.md": b"old"})
    (first.observations / "stale.md").write_text("stale", encoding="utf-8")

    second = create_outbox(launch, memory_files={"memory.md": b"new"})

    assert not (second.observations / "stale.md").exists()
    assert (second.memory / "memory.md").read_bytes() == b"new"


def test_create_outbox_rejects_a_missing_launch_view(tmp_path: Path):
    with pytest.raises(ValueError, match="launch view"):
        create_outbox(tmp_path / "nope", memory_files={})


def test_create_outbox_rejects_memory_file_names_with_separators(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()

    with pytest.raises(ValueError, match="memory file name"):
        create_outbox(launch, memory_files={"../escape.md": b"x"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_outbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.records.outbox'`

- [ ] **Step 3: Implement the module**

Create `agency/records/outbox.py`:

```python
"""Per-job outbox that agents write records and memory into."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agency.fs.atomic import atomic_write_bytes

OUTBOX_RELATIVE_OBSERVATIONS = ".agency/outbox/observations"
OUTBOX_RELATIVE_PROPOSALS = ".agency/outbox/proposals"
OUTBOX_RELATIVE_MEMORY = ".agency/memory"

_AGENCY_DIRNAME = ".agency"


@dataclass(frozen=True)
class OutboxPaths:
    root: Path
    observations: Path
    proposals: Path
    memory: Path


def create_outbox(
    launch_view: Path,
    *,
    memory_files: Mapping[str, bytes],
) -> OutboxPaths:
    launch_view = Path(launch_view)
    if not launch_view.is_dir():
        raise ValueError(f"launch view does not exist: {launch_view}")

    for name in memory_files:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError(f"invalid memory file name: {name!r}")

    root = launch_view / _AGENCY_DIRNAME
    if root.exists():
        shutil.rmtree(root)

    paths = OutboxPaths(
        root=root,
        observations=launch_view.joinpath(*OUTBOX_RELATIVE_OBSERVATIONS.split("/")),
        proposals=launch_view.joinpath(*OUTBOX_RELATIVE_PROPOSALS.split("/")),
        memory=launch_view.joinpath(*OUTBOX_RELATIVE_MEMORY.split("/")),
    )
    for directory in (paths.observations, paths.proposals, paths.memory):
        directory.mkdir(parents=True, exist_ok=True)

    for name, payload in memory_files.items():
        atomic_write_bytes(paths.memory / name, payload)

    return paths
```

- [ ] **Step 4: Export from the package**

Append to `agency/records/__init__.py`:

```python
from .outbox import OutboxPaths, create_outbox

__all__ += ["OutboxPaths", "create_outbox"]
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_records_outbox.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/records/outbox.py agency/records/__init__.py tests/test_records_outbox.py
git commit -m "feat(records): add per-job outbox construction"
```

---

### Task 3: Outbox validation

**Files:**
- Create: `agency/records/validation.py`
- Modify: `agency/records/__init__.py`
- Test: `tests/test_records_validation.py`

**Interfaces:**
- Consumes: `OutboxPaths` from Task 2; `parse_frontmatter` from Task 1.
- Produces:
  - `MAX_RECORDS_PER_KIND = 20`, `MAX_RECORD_BYTES = 65536`
  - `RecordCandidate(kind: str, source_name: str, meta: dict, body: str)`
  - `OutboxRejection(kind: str, source_name: str, reason: str)`
  - `OutboxValidation(accepted: tuple[RecordCandidate, ...], rejected: tuple[OutboxRejection, ...])` with property `ok: bool`
  - `validate_outbox(outbox: OutboxPaths, *, writable_agents: frozenset[str]) -> OutboxValidation`

`kind` is exactly `"observation"` or `"proposal"`. `writable_agents` is the set of configured instance names that are executable and have `capabilities.write` true; validation stays pure and does not import config.

- [ ] **Step 1: Write the failing test**

Create `tests/test_records_validation.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agency.records.outbox import create_outbox
from agency.records.validation import (
    MAX_RECORD_BYTES,
    MAX_RECORDS_PER_KIND,
    validate_outbox,
)

WRITABLE = frozenset({"paul"})


@pytest.fixture
def outbox(tmp_path: Path):
    launch = tmp_path / "launch"
    launch.mkdir()
    return create_outbox(launch, memory_files={})


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


OBSERVATION = "---\nstatus: open\n---\n\n**Suite is red.** Three tests fail.\n"

PROPOSAL = (
    "---\n"
    "execution_agent: paul\n"
    "questions:\n"
    "  - id: fix\n"
    "    prompt: Fix the failing tests?\n"
    "    type: boolean\n"
    "---\n\n"
    "**Fix the suite.**\n"
)


def test_valid_observation_is_accepted(outbox):
    write(outbox.observations / "a.md", OBSERVATION)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert result.ok
    assert [c.kind for c in result.accepted] == ["observation"]
    assert result.accepted[0].body.startswith("**Suite is red.**")


def test_valid_proposal_is_accepted(outbox):
    write(outbox.proposals / "p.md", PROPOSAL)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert result.ok
    assert [c.kind for c in result.accepted] == ["proposal"]


def test_empty_outbox_is_valid_and_empty(outbox):
    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert result.ok
    assert result.accepted == ()


def test_non_markdown_file_is_rejected(outbox):
    write(outbox.observations / "notes.txt", "hello")

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "not a markdown file" in result.rejected[0].reason


def test_subdirectory_is_rejected(outbox):
    (outbox.observations / "nested").mkdir()

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "subdirectories" in result.rejected[0].reason


def test_oversized_record_is_rejected(outbox):
    write(outbox.observations / "big.md", "x" * (MAX_RECORD_BYTES + 1))

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "exceeds" in result.rejected[0].reason


def test_too_many_records_are_rejected(outbox):
    for index in range(MAX_RECORDS_PER_KIND + 1):
        write(outbox.observations / f"r{index:02d}.md", OBSERVATION)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert any("too many" in item.reason for item in result.rejected)


def test_proposal_without_execution_agent_is_rejected(outbox):
    write(outbox.proposals / "p.md", PROPOSAL.replace("execution_agent: paul\n", ""))

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "execution_agent" in result.rejected[0].reason


def test_proposal_naming_a_non_writable_executor_is_rejected(outbox):
    write(outbox.proposals / "p.md", PROPOSAL.replace("paul", "gurney"))

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "not a writable" in result.rejected[0].reason


def test_record_with_unparsable_frontmatter_is_rejected(outbox):
    write(outbox.observations / "a.md", "---\n: : :\n---\n\nbody\n")

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "front matter" in result.rejected[0].reason


def test_record_with_empty_body_is_rejected(outbox):
    write(outbox.observations / "a.md", "---\nstatus: open\n---\n\n   \n")

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "empty" in result.rejected[0].reason


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlinked_record_is_rejected(outbox, tmp_path: Path):
    target = tmp_path / "outside.md"
    write(target, OBSERVATION)
    (outbox.observations / "link.md").symlink_to(target)

    result = validate_outbox(outbox, writable_agents=WRITABLE)

    assert not result.ok
    assert "not a regular file" in result.rejected[0].reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_validation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.records.validation'`

- [ ] **Step 3: Implement the module**

Create `agency/records/validation.py`:

```python
"""Validation of a populated per-job outbox."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from agency.proposals import validate_proposal_schema

from .frontmatter import parse_frontmatter
from .outbox import OutboxPaths

MAX_RECORDS_PER_KIND = 20
MAX_RECORD_BYTES = 65536


@dataclass(frozen=True)
class RecordCandidate:
    kind: str
    source_name: str
    meta: dict
    body: str


@dataclass(frozen=True)
class OutboxRejection:
    kind: str
    source_name: str
    reason: str


@dataclass(frozen=True)
class OutboxValidation:
    accepted: tuple[RecordCandidate, ...]
    rejected: tuple[OutboxRejection, ...]

    @property
    def ok(self) -> bool:
        return not self.rejected


def _is_reparse_point(entry_stat: os.stat_result) -> bool:
    attributes = getattr(entry_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _validate_kind(
    directory: Path,
    kind: str,
    *,
    writable_agents: frozenset[str],
    accepted: list[RecordCandidate],
    rejected: list[OutboxRejection],
) -> None:
    entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
    markdown_entries = [item for item in entries if item.suffix.casefold() == ".md"]
    if len(markdown_entries) > MAX_RECORDS_PER_KIND:
        rejected.append(
            OutboxRejection(
                kind=kind,
                source_name=directory.name,
                reason=(
                    f"too many records: {len(markdown_entries)} exceeds the "
                    f"limit of {MAX_RECORDS_PER_KIND}"
                ),
            )
        )
        return

    for entry in entries:
        entry_stat = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(entry_stat.st_mode):
            rejected.append(
                OutboxRejection(kind, entry.name, "subdirectories are not allowed")
            )
            continue
        if not stat.S_ISREG(entry_stat.st_mode) or _is_reparse_point(entry_stat):
            rejected.append(
                OutboxRejection(kind, entry.name, "not a regular file")
            )
            continue
        if entry.suffix.casefold() != ".md":
            rejected.append(
                OutboxRejection(kind, entry.name, "not a markdown file")
            )
            continue
        if entry_stat.st_size > MAX_RECORD_BYTES:
            rejected.append(
                OutboxRejection(
                    kind,
                    entry.name,
                    f"record size {entry_stat.st_size} exceeds {MAX_RECORD_BYTES}",
                )
            )
            continue

        raw = entry.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(raw)
        if raw.startswith("---") and not meta:
            rejected.append(
                OutboxRejection(kind, entry.name, "front matter did not parse")
            )
            continue
        if not isinstance(meta, dict):
            rejected.append(
                OutboxRejection(kind, entry.name, "front matter must be a mapping")
            )
            continue
        if not body.strip():
            rejected.append(OutboxRejection(kind, entry.name, "record body is empty"))
            continue

        if kind == "proposal":
            errors = validate_proposal_schema(meta)
            if errors:
                rejected.append(OutboxRejection(kind, entry.name, "; ".join(errors)))
                continue
            executor = str(meta.get("execution_agent", "")).strip()
            if executor not in writable_agents:
                rejected.append(
                    OutboxRejection(
                        kind,
                        entry.name,
                        f"execution_agent '{executor}' is not a writable, "
                        "executable configured agent",
                    )
                )
                continue

        accepted.append(
            RecordCandidate(kind=kind, source_name=entry.name, meta=meta, body=body)
        )


def validate_outbox(
    outbox: OutboxPaths,
    *,
    writable_agents: frozenset[str],
) -> OutboxValidation:
    accepted: list[RecordCandidate] = []
    rejected: list[OutboxRejection] = []
    for directory, kind in (
        (outbox.observations, "observation"),
        (outbox.proposals, "proposal"),
    ):
        if not directory.is_dir():
            continue
        _validate_kind(
            directory,
            kind,
            writable_agents=writable_agents,
            accepted=accepted,
            rejected=rejected,
        )
    return OutboxValidation(accepted=tuple(accepted), rejected=tuple(rejected))
```

- [ ] **Step 4: Export from the package**

Append to `agency/records/__init__.py`:

```python
from .validation import (
    MAX_RECORD_BYTES,
    MAX_RECORDS_PER_KIND,
    OutboxRejection,
    OutboxValidation,
    RecordCandidate,
    validate_outbox,
)

__all__ += [
    "MAX_RECORD_BYTES",
    "MAX_RECORDS_PER_KIND",
    "OutboxRejection",
    "OutboxValidation",
    "RecordCandidate",
    "validate_outbox",
]
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_records_validation.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/records/validation.py agency/records/__init__.py tests/test_records_validation.py
git commit -m "feat(records): validate outbox records before ingest"
```

---

### Task 4: Record ingest

**Files:**
- Create: `agency/records/ingest.py`
- Modify: `agency/records/__init__.py`
- Test: `tests/test_records_ingest.py`

**Interfaces:**
- Consumes: `OutboxValidation` and `RecordCandidate` from Task 3; `slugify` and `extract_display_title` from Task 1.
- Produces:
  - `IngestedRecord(kind: str, path: Path)`
  - `ingest_records(validation: OutboxValidation, *, observations_dir: Path, proposals_dir: Path, agent_name: str, now: datetime, job_id: str) -> tuple[IngestedRecord, ...]`

Agency stamps `agent`, `date`, and `status`, and assigns the filename `<YYYY-MM-DD>-<slug>.md`, suffixing `-2`, `-3` on collision.

- [ ] **Step 1: Write the failing test**

Create `tests/test_records_ingest.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agency.records.frontmatter import parse_frontmatter
from agency.records.ingest import ingest_records
from agency.records.validation import OutboxValidation, RecordCandidate

NOW = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def dirs(tmp_path: Path):
    observations = tmp_path / "observations"
    proposals = tmp_path / "proposals"
    observations.mkdir()
    proposals.mkdir()
    return observations, proposals


def candidate(kind="observation", meta=None, body="**Suite is red.** Details."):
    return RecordCandidate(
        kind=kind,
        source_name="a.md",
        meta=dict(meta or {}),
        body=body,
    )


def ingest(dirs, *candidates, agent_name="duncan"):
    observations, proposals = dirs
    return ingest_records(
        OutboxValidation(accepted=tuple(candidates), rejected=()),
        observations_dir=observations,
        proposals_dir=proposals,
        agent_name=agent_name,
        now=NOW,
        job_id="job123",
    )


def test_observation_lands_in_the_observations_directory(dirs):
    observations, _ = dirs

    written = ingest(dirs, candidate())

    assert len(written) == 1
    assert written[0].path.parent == observations
    assert written[0].path.name == "2026-07-31-suite-is-red.md"


def test_agency_stamps_agent_date_and_status(dirs):
    written = ingest(dirs, candidate(meta={"agent": "someone-else", "date": "1999-01-01"}))

    meta, _ = parse_frontmatter(written[0].path.read_text(encoding="utf-8"))
    assert meta["agent"] == "duncan"
    assert meta["date"] == "2026-07-31"
    assert meta["status"] == "open"


def test_author_supplied_fields_other_than_the_stamped_ones_survive(dirs):
    written = ingest(dirs, candidate(meta={"ttl_days": 14, "float": True}))

    meta, _ = parse_frontmatter(written[0].path.read_text(encoding="utf-8"))
    assert meta["ttl_days"] == 14
    assert meta["float"] is True


def test_explicit_slug_is_used_when_valid(dirs):
    written = ingest(dirs, candidate(meta={"slug": "custom-name"}))

    assert written[0].path.name == "2026-07-31-custom-name.md"


def test_invalid_slug_falls_back_to_the_title(dirs):
    written = ingest(dirs, candidate(meta={"slug": "../escape"}))

    assert written[0].path.name == "2026-07-31-suite-is-red.md"


def test_untitled_record_falls_back_to_the_job_id(dirs):
    written = ingest(dirs, candidate(body="plain text with no bold"))

    assert written[0].path.name == "2026-07-31-job123.md"


def test_colliding_names_gain_a_numeric_suffix(dirs):
    written = ingest(dirs, candidate(), candidate(), candidate())

    assert [item.path.name for item in written] == [
        "2026-07-31-suite-is-red.md",
        "2026-07-31-suite-is-red-2.md",
        "2026-07-31-suite-is-red-3.md",
    ]


def test_collision_with_a_preexisting_file_is_avoided(dirs):
    observations, _ = dirs
    (observations / "2026-07-31-suite-is-red.md").write_text("old", encoding="utf-8")

    written = ingest(dirs, candidate())

    assert written[0].path.name == "2026-07-31-suite-is-red-2.md"
    assert (observations / "2026-07-31-suite-is-red.md").read_text(encoding="utf-8") == "old"


def test_proposal_lands_in_the_proposals_directory_with_open_status(dirs):
    _, proposals = dirs

    written = ingest(dirs, candidate(kind="proposal", meta={"execution_agent": "paul"}))

    assert written[0].path.parent == proposals
    meta, _ = parse_frontmatter(written[0].path.read_text(encoding="utf-8"))
    assert meta["status"] == "open"
    assert meta["execution_agent"] == "paul"


def test_ingest_creates_missing_target_directories(tmp_path: Path):
    written = ingest_records(
        OutboxValidation(accepted=(candidate(),), rejected=()),
        observations_dir=tmp_path / "fresh" / "observations",
        proposals_dir=tmp_path / "fresh" / "proposals",
        agent_name="duncan",
        now=NOW,
        job_id="job123",
    )

    assert written[0].path.is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.records.ingest'`

- [ ] **Step 3: Implement the module**

Create `agency/records/ingest.py`:

```python
"""Ingest validated outbox records into the group's pipeline directories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from agency.fs.atomic import atomic_write_text

from .frontmatter import extract_display_title, slugify
from .validation import OutboxValidation, RecordCandidate

_SLUG_PATTERN = re.compile(r"^[a-z0-9-]{1,60}$")

# Agency owns these; an author-supplied value is discarded.
_STAMPED_FIELDS = ("agent", "date", "status")


@dataclass(frozen=True)
class IngestedRecord:
    kind: str
    path: Path


def _record_slug(candidate: RecordCandidate, job_id: str) -> str:
    raw = candidate.meta.get("slug")
    if isinstance(raw, str) and _SLUG_PATTERN.match(raw.strip()):
        return raw.strip()
    title = extract_display_title(candidate.body, "")
    slug = slugify(title)
    return slug or slugify(job_id) or "record"


def _unique_path(directory: Path, date_prefix: str, slug: str) -> Path:
    candidate = directory / f"{date_prefix}-{slug}.md"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = directory / f"{date_prefix}-{slug}-{suffix}.md"
        if not candidate.exists():
            return candidate
        suffix += 1


def _render(meta: dict, body: str) -> str:
    front = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{front}\n---\n\n{body.strip()}\n"


def ingest_records(
    validation: OutboxValidation,
    *,
    observations_dir: Path,
    proposals_dir: Path,
    agent_name: str,
    now: datetime,
    job_id: str,
) -> tuple[IngestedRecord, ...]:
    targets = {
        "observation": Path(observations_dir),
        "proposal": Path(proposals_dir),
    }
    for directory in targets.values():
        directory.mkdir(parents=True, exist_ok=True)

    date_prefix = now.date().isoformat()
    written: list[IngestedRecord] = []

    for candidate in validation.accepted:
        directory = targets[candidate.kind]
        meta = {
            key: value
            for key, value in candidate.meta.items()
            if key not in _STAMPED_FIELDS and key != "slug"
        }
        meta["agent"] = agent_name
        meta["date"] = date_prefix
        meta["status"] = "open"

        path = _unique_path(directory, date_prefix, _record_slug(candidate, job_id))
        atomic_write_text(path, _render(meta, candidate.body))
        written.append(IngestedRecord(kind=candidate.kind, path=path))

    return tuple(written)
```

- [ ] **Step 4: Export from the package**

Append to `agency/records/__init__.py`:

```python
from .ingest import IngestedRecord, ingest_records

__all__ += ["IngestedRecord", "ingest_records"]
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_records_ingest.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/records/ingest.py agency/records/__init__.py tests/test_records_ingest.py
git commit -m "feat(records): ingest validated records into group storage"
```

---

### Task 5: Reporting protocol in the task input

**Files:**
- Create: `agency/records/protocol.py`
- Modify: `agency/records/__init__.py`
- Modify: `agency/jobs/resolution.py` (the `JobSpec(...)` construction, `task_input=task_input`)
- Test: `tests/test_records_protocol.py`

**Interfaces:**
- Consumes: the `OUTBOX_RELATIVE_*` constants from Task 2.
- Produces:
  - `build_reporting_protocol(*, tool_mode: str, tool_names: tuple[str, ...]) -> str`
  - `append_reporting_protocol(task_input: str, *, tool_mode: str, tool_names: tuple[str, ...]) -> str`

The protocol text names the granted tool policy so a blocked agent can report accurately instead of guessing, which is the failure this feature exists to correct.

- [ ] **Step 1: Write the failing test**

Create `tests/test_records_protocol.py`:

```python
from __future__ import annotations

from agency.records.outbox import (
    OUTBOX_RELATIVE_MEMORY,
    OUTBOX_RELATIVE_OBSERVATIONS,
    OUTBOX_RELATIVE_PROPOSALS,
)
from agency.records.protocol import append_reporting_protocol, build_reporting_protocol


def test_protocol_names_every_outbox_directory():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert OUTBOX_RELATIVE_OBSERVATIONS in text
    assert OUTBOX_RELATIVE_PROPOSALS in text
    assert OUTBOX_RELATIVE_MEMORY in text


def test_protocol_states_that_agency_assigns_identity_fields():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "agent" in text
    assert "date" in text
    assert "status" in text


def test_protocol_reports_an_allowlisted_tool_policy():
    text = build_reporting_protocol(tool_mode="allowlist", tool_names=("read", "search"))

    assert "read, search" in text


def test_protocol_reports_an_unrestricted_tool_policy():
    text = build_reporting_protocol(tool_mode="all", tool_names=())

    assert "all tools" in text


def test_append_places_the_protocol_after_the_task():
    combined = append_reporting_protocol(
        "Run the suite.", tool_mode="all", tool_names=()
    )

    assert combined.startswith("Run the suite.")
    assert OUTBOX_RELATIVE_OBSERVATIONS in combined


def test_append_to_blank_task_input_still_yields_the_protocol():
    combined = append_reporting_protocol("", tool_mode="all", tool_names=())

    assert OUTBOX_RELATIVE_OBSERVATIONS in combined


def test_append_is_idempotent():
    once = append_reporting_protocol("Run.", tool_mode="all", tool_names=())
    twice = append_reporting_protocol(once, tool_mode="all", tool_names=())

    assert once == twice
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.records.protocol'`

- [ ] **Step 3: Implement the module**

Create `agency/records/protocol.py`:

```python
"""The reporting contract appended to every job's immutable task input."""

from __future__ import annotations

from .outbox import (
    OUTBOX_RELATIVE_MEMORY,
    OUTBOX_RELATIVE_OBSERVATIONS,
    OUTBOX_RELATIVE_PROPOSALS,
)

_MARKER = "## Agency reporting protocol"


def _tool_sentence(tool_mode: str, tool_names: tuple[str, ...]) -> str:
    if tool_mode == "allowlist":
        granted = ", ".join(tool_names) if tool_names else "no tools"
        return (
            f"Your granted tool policy is an allowlist: {granted}. "
            "If a task needs a capability outside that list, say so explicitly "
            "and name the missing tool; do not attribute the refusal to any "
            "other cause."
        )
    if tool_mode == "none":
        return "You have been granted no tools."
    return "You have been granted all tools."


def build_reporting_protocol(
    *,
    tool_mode: str,
    tool_names: tuple[str, ...],
) -> str:
    return "\n".join(
        [
            _MARKER,
            "",
            "Report findings by writing Markdown files into these directories,",
            "relative to your working directory. Agency validates and files them",
            "after the run; do not write anywhere else to record them.",
            "",
            f"- Observations: `{OUTBOX_RELATIVE_OBSERVATIONS}`",
            f"- Proposals: `{OUTBOX_RELATIVE_PROPOSALS}`",
            f"- Your memory: `{OUTBOX_RELATIVE_MEMORY}`",
            "",
            "Each record is one Markdown file with YAML front matter and a body.",
            "Open the body with a bold summary sentence; it becomes the title.",
            "Agency assigns the `agent`, `date`, and `status` fields and the file",
            "name, so anything you set for those is discarded.",
            "",
            "A proposal additionally requires `execution_agent` naming a",
            "configured agent that is allowed to write, and a non-empty",
            "`questions` list whose entries each have `id`, `prompt`, and `type`.",
            "",
            "The memory directory is seeded with your current memory. Edit those",
            "files to change what you remember; leave them alone to keep it.",
            "",
            _tool_sentence(tool_mode, tool_names),
        ]
    )


def append_reporting_protocol(
    task_input: str,
    *,
    tool_mode: str,
    tool_names: tuple[str, ...],
) -> str:
    if _MARKER in task_input:
        return task_input
    protocol = build_reporting_protocol(tool_mode=tool_mode, tool_names=tool_names)
    if not task_input.strip():
        return protocol
    return f"{task_input.rstrip()}\n\n{protocol}"
```

- [ ] **Step 4: Export from the package**

Append to `agency/records/__init__.py`:

```python
from .protocol import append_reporting_protocol, build_reporting_protocol

__all__ += ["append_reporting_protocol", "build_reporting_protocol"]
```

- [ ] **Step 5: Run the module tests**

Run: `python -m pytest tests/test_records_protocol.py -v`
Expected: PASS

- [ ] **Step 6: Wire it into job resolution**

In `agency/jobs/resolution.py`, add the import beside the other `agency.` imports:

```python
from agency.records.protocol import append_reporting_protocol
```

Then in the `return JobSpec(` block, replace the line `task_input=task_input,` with:

```python
        task_input=append_reporting_protocol(
            task_input,
            tool_mode=runtime_policy.tools.mode,
            tool_names=tuple(runtime_policy.tools.names),
        ),
```

This single site covers every trigger — routine, saved prompt, ad hoc, decision, and decision retry — because they all converge on this `JobSpec` construction.

- [ ] **Step 7: Add the resolution regression test**

This proves the claim that one wiring point covers every trigger, so it exercises
`resolve_job_request` for two different triggers rather than inspecting source text.
Add to `tests/test_job_submission.py`, reusing that module's existing
`_write_config`, `_write_blueprint`, `_projector`, and `FakeIntegration` helpers:

```python
def _resolve(tmp_path, **request_kwargs):
    config = _write_config(tmp_path, command="echo ok")
    _write_blueprint(tmp_path / "agent-library")
    return resolve_job_request(
        JobRequest(
            config_path=config,
            group_key="newsletter",
            agent_name="builder",
            **request_kwargs,
        ),
        config_store=ConfigStore(config),
        library=BlueprintLibrary(tmp_path / "agent-library"),
        cache=CompilationCache(
            tmp_path / "compiled-agents", {"copilot": _projector()}
        ),
        prompt_store=PromptStore(tmp_path / "prompts"),
        integrations={"copilot": FakeIntegration()},
    )


def test_decision_task_input_carries_the_reporting_protocol(tmp_path):
    spec = _resolve(tmp_path, trigger="decision", task_input="Decide what changed.")

    assert spec.task_input.startswith("Decide what changed.")
    assert "## Agency reporting protocol" in spec.task_input
    assert ".agency/outbox/observations" in spec.task_input


def test_ad_hoc_prompt_task_input_carries_the_reporting_protocol(tmp_path):
    spec = _resolve(tmp_path, trigger="manual_prompt", task_input="Run the suite.")

    assert spec.task_input.startswith("Run the suite.")
    assert ".agency/outbox/proposals" in spec.task_input
    assert ".agency/memory" in spec.task_input


def test_reporting_protocol_reports_the_granted_tool_policy(tmp_path):
    """`_write_config` grants `allowlist [shell, write]` to the builder agent."""
    spec = _resolve(tmp_path, trigger="decision", task_input="Decide.")

    assert spec.runtime_policy.tool_mode == "allowlist"
    assert "shell, write" in spec.task_input
```

`_write_config` configures a single agent named `builder` on the `copilot`
integration with `tools: allowlist [shell, write]`, which is why the third test
asserts that exact string.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. If a job-resolution test asserts an exact `task_input` string, update that assertion to check `startswith` on the original text plus presence of the protocol marker.

- [ ] **Step 9: Commit**

```bash
git add agency/records/protocol.py agency/records/__init__.py agency/jobs/resolution.py tests/test_records_protocol.py
git commit -m "feat(records): tell agents how to report in the task input"
```

---

### Task 6: The write-boundary contract

Express the write boundary as paths. `capabilities.write` stops being a label and becomes an input to the effective runtime policy. This task defines the contract only — **no integration is adapted to satisfy it**, and integrations that cannot honour it must reject narrowed policies verbosely.

An earlier draft of this task granted a write *tool* instead. It was wrong: without path-scoped write permissions, granting the tool grants write to every readable root, so a `capabilities.write: false` agent would gain workspace write access. Do not reintroduce a tool grant. `runtime.tools` stays a complete override with no exception.

**Files:**
- Modify: `agency/integrations/models.py` (`EffectiveRuntimePolicy`, `RuntimeCapabilities`)
- Modify: `agency/configuration/effective.py` (`_resolve_sandbox` / `resolve_effective_policy`)
- Modify: `agency/integrations/__init__.py` (`BaseIntegration.validate_runtime_policy`)
- Modify: `agency/jobs/models.py` (`RuntimePolicySnapshot` must round-trip the new field)
- Test: `tests/test_write_boundary_contract.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `EffectiveRuntimePolicy.writable_roots: tuple[Path, ...] = ()`
  - `RuntimeCapabilities.enforces_write_boundary: bool = False`
  - `RuntimePolicySnapshot.writable_roots: tuple[str, ...] = ()`
  - Validation issue code `unsupported-write-boundary`

**Contract semantics (exact):**
- `sandbox_roots` is everything readable. Unchanged.
- `writable_roots` is the subset that may be written.
- `capabilities.write: true` → `writable_roots == sandbox_roots`.
- `capabilities.write: false` → `writable_roots == ()`.
- Under `sandbox_mode == "unrestricted"`, `sandbox_roots` is `()` and means "everything"; `writable_roots` is `()` when `capabilities.write` is false and `()` when true as well — so unrestricted plus `write: false` is a narrowed policy and must be detectable. Use the helper `policy.narrows_writes` defined below rather than comparing tuples at call sites.
- The launch view is **never** placed in `writable_roots`. Its writability is implicit and travels on `IntegrationRunRequest.launch_dir` / `memory_working_dir`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_write_boundary_contract.py`:

```python
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from agency.configuration.effective import resolve_effective_policy
from agency.configuration.issues import ValidationFailed
from agency.configuration.store import ConfigStore
from agency.integrations import BaseIntegration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    ResolvedToolPolicy,
    RuntimeCapabilities,
)
from agency.jobs.models import RuntimePolicySnapshot


class EnforcingIntegration(BaseIntegration):
    name = "enforcing"
    display_name = "Enforcing"
    supports_execution = True
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"restricted", "unrestricted"}),
        tool_modes=frozenset({"all", "allowlist"}),
        enforces_write_boundary=True,
    )

    def identity_filename(self) -> str:
        return "AGENTS.md"

    def parse_identity(self, agent_dir: Path):
        return None

    def write_identity(self, agent_dir: Path, identity):
        raise NotImplementedError

    def run(self, request):
        raise NotImplementedError


class NonEnforcingIntegration(EnforcingIntegration):
    name = "nonenforcing"
    runtime_capabilities = RuntimeCapabilities(
        path_modes=frozenset({"restricted", "unrestricted"}),
        tool_modes=frozenset({"all", "allowlist"}),
    )


def policy(*, writable, roots=(Path("/ws"),), mode="restricted"):
    return EffectiveRuntimePolicy(
        timeout=60,
        sandbox_mode=mode,
        sandbox_roots=roots,
        tools=ResolvedToolPolicy(mode="all", names=()),
        writable_roots=roots if writable else (),
    )


def _config(tmp_path: Path, raw_config, *, write: bool):
    raw = deepcopy(raw_config)
    raw["groups"]["newsletter"]["agents"][0]["capabilities"] = {"write": write}
    raw["groups"]["newsletter"]["runtime"] = {
        "sandbox": {"mode": "restricted", "roots": []}
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return ConfigStore(path).load().config


def test_capabilities_write_true_makes_the_workspace_writable(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, write=True)

    resolved = resolve_effective_policy(
        config, "newsletter", "builder", integration=EnforcingIntegration()
    )

    assert resolved.writable_roots == resolved.sandbox_roots
    assert resolved.writable_roots != ()


def test_capabilities_write_false_yields_no_writable_roots(tmp_path, raw_config):
    config = _config(tmp_path, raw_config, write=False)

    resolved = resolve_effective_policy(
        config, "newsletter", "builder", integration=EnforcingIntegration()
    )

    assert resolved.writable_roots == ()
    assert resolved.sandbox_roots != ()


def test_read_only_policy_narrows_writes():
    assert policy(writable=False).narrows_writes is True


def test_writable_policy_does_not_narrow_writes():
    assert policy(writable=True).narrows_writes is False


def test_unrestricted_read_only_policy_still_narrows_writes():
    unrestricted = EffectiveRuntimePolicy(
        timeout=60,
        sandbox_mode="unrestricted",
        sandbox_roots=(),
        tools=ResolvedToolPolicy(mode="all", names=()),
        writable_roots=(),
        writes_narrowed=True,
    )

    assert unrestricted.narrows_writes is True


def test_non_enforcing_integration_rejects_a_narrowed_policy():
    issues = NonEnforcingIntegration().validate_runtime_policy(policy(writable=False))

    assert [issue.code for issue in issues] == ["unsupported-write-boundary"]
    message = issues[0].message
    assert "nonenforcing" in message
    assert "capabilities.write" in message


def test_non_enforcing_integration_accepts_a_writable_policy():
    assert NonEnforcingIntegration().validate_runtime_policy(policy(writable=True)) == ()


def test_enforcing_integration_accepts_a_narrowed_policy():
    assert EnforcingIntegration().validate_runtime_policy(policy(writable=False)) == ()


def test_resolve_rejects_a_read_only_agent_on_a_non_enforcing_integration(
    tmp_path, raw_config
):
    config = _config(tmp_path, raw_config, write=False)

    with pytest.raises(ValidationFailed) as excinfo:
        resolve_effective_policy(
            config, "newsletter", "builder", integration=NonEnforcingIntegration()
        )

    assert [issue.code for issue in excinfo.value.issues] == [
        "unsupported-write-boundary"
    ]


def test_snapshot_round_trips_writable_roots():
    original = policy(writable=True)

    restored = RuntimePolicySnapshot.from_effective_policy(original).to_effective_policy()

    assert restored.writable_roots == original.writable_roots
    assert restored.narrows_writes is False


def test_snapshot_round_trips_a_narrowed_policy():
    original = policy(writable=False)

    restored = RuntimePolicySnapshot.from_effective_policy(original).to_effective_policy()

    assert restored.writable_roots == ()
    assert restored.narrows_writes is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_write_boundary_contract.py -v`
Expected: FAIL with `TypeError: EffectiveRuntimePolicy.__init__() got an unexpected keyword argument 'writable_roots'`

- [ ] **Step 3: Extend the policy and capability models**

In `agency/integrations/models.py`, replace `EffectiveRuntimePolicy` and `RuntimeCapabilities` with:

```python
@dataclass(frozen=True)
class EffectiveRuntimePolicy:
    timeout: int
    sandbox_mode: PathPolicyMode
    sandbox_roots: tuple[Path, ...]
    tools: ResolvedToolPolicy
    writable_roots: tuple[Path, ...] = ()
    # Set when an unrestricted policy still forbids writes, where empty
    # sandbox_roots means "everything" and cannot be compared to writable_roots.
    writes_narrowed: bool | None = None

    @property
    def narrows_writes(self) -> bool:
        """Whether this policy grants read access it does not grant write access to."""
        if self.writes_narrowed is not None:
            return self.writes_narrowed
        return tuple(self.writable_roots) != tuple(self.sandbox_roots)


@dataclass(frozen=True)
class RuntimeCapabilities:
    path_modes: frozenset[PathPolicyMode] = frozenset()
    tool_modes: frozenset[ToolPolicyMode] = frozenset()
    enforces_write_boundary: bool = False
```

- [ ] **Step 4: Derive the writable set from `capabilities.write`**

In `agency/configuration/effective.py`, change `_resolve_sandbox` to return the writable set as well. Replace its signature and both `return` statements:

```python
def _resolve_sandbox(
    group: GroupConfig,
    agent: AgentInstance,
    *,
    group_id: str,
    agent_id: str,
) -> tuple[str, tuple[Path, ...], tuple[Path, ...], bool]:
    agent_sandbox = agent.runtime.sandbox
    group_sandbox = group.runtime.sandbox
    agent_overrides_mode = "mode" in agent_sandbox.model_fields_set
    mode = agent_sandbox.mode if agent_overrides_mode else group_sandbox.mode
    additional_roots = tuple(agent_sandbox.additional_roots)
    may_write = agent.capabilities.write

    if mode == "unrestricted":
        if additional_roots:
            issue = _build_issue(
                code="sandbox-contradiction",
                scope=f"groups.{group_id}.agents.{agent_id}",
                field="runtime.sandbox.additional_roots",
                message="Unrestricted sandbox cannot add roots.",
                hint="Remove additional roots or switch to restricted mode.",
            )
            raise ValidationFailed((issue,))
        return mode, (), (), not may_write

    paths = resolve_group_paths(group)
    roots = _merge_roots(
        (paths.workspace_root, paths.group_root),
        tuple(group_sandbox.roots),
        additional_roots,
    )
    return mode, roots, (roots if may_write else ()), not may_write
```

Then in `resolve_effective_policy`, replace the unpacking and the policy construction:

```python
    sandbox_mode, sandbox_roots, writable_roots, writes_narrowed = _resolve_sandbox(
        group,
        agent,
        group_id=group_id,
        agent_id=agent_id,
    )
    policy = EffectiveRuntimePolicy(
        timeout=_resolve_timeout(group, agent, timeout_override),
        sandbox_mode=sandbox_mode,
        sandbox_roots=sandbox_roots,
        tools=_resolve_tools(group, agent),
        writable_roots=writable_roots,
        writes_narrowed=writes_narrowed,
    )
```

- [ ] **Step 5: Fail closed in the integration contract**

In `agency/integrations/__init__.py`, inside `BaseIntegration.validate_runtime_policy`, append this check after the existing tool-mode check and before `return tuple(issues)`:

```python
        if policy.narrows_writes and not self.runtime_capabilities.enforces_write_boundary:
            issues.append(
                ValidationIssue(
                    code="unsupported-write-boundary",
                    scope=f"integrations.{self.name}",
                    field="capabilities.write",
                    message=(
                        f"Integration '{self.name}' does not implement the write-"
                        f"boundary contract. This agent has capabilities.write "
                        f"false, so Agency must grant it read access to its "
                        f"workspace while withholding write access, and "
                        f"'{self.name}' cannot enforce that separation. Running "
                        f"the agent anyway would give it write access it is "
                        f"configured not to have."
                    ),
                    corrective_hint=(
                        "Set capabilities.write true for this agent if it is "
                        "genuinely trusted to modify the workspace, or run it on "
                        "an integration that declares "
                        "RuntimeCapabilities.enforces_write_boundary."
                    ),
                )
            )
```

Do **not** set `enforces_write_boundary` on any shipped integration. Every existing integration keeps the default `False`, so read-only agents stop resolving. That is the intended outcome of this task.

- [ ] **Step 6: Round-trip the field through the job spec**

In `agency/jobs/models.py`, add the field to `RuntimePolicySnapshot` and carry it both ways:

```python
@dataclass(frozen=True)
class RuntimePolicySnapshot:
    timeout: int
    sandbox_mode: str
    sandbox_roots: tuple[str, ...]
    tool_mode: str
    tool_names: tuple[str, ...] = ()
    writable_roots: tuple[str, ...] = ()
    writes_narrowed: bool | None = None
```

In `from_effective_policy`, add:

```python
            writable_roots=tuple(
                str(Path(root).resolve(strict=False))
                for root in policy.writable_roots
            ),
            writes_narrowed=policy.writes_narrowed,
```

In `to_effective_policy`, add:

```python
            writable_roots=tuple(Path(root) for root in self.writable_roots),
            writes_narrowed=self.writes_narrowed,
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_write_boundary_contract.py -v`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: failures are likely, and each needs judging rather than silencing. Tests that construct `EffectiveRuntimePolicy` or `RuntimePolicySnapshot` positionally may need keyword arguments. Tests that resolve a policy for an agent with `capabilities.write` false on a stock integration will now raise `ValidationFailed` — that is the contract working, so update those tests to expect it, or give the fixture agent `capabilities.write: true` where the test is not about the boundary. Do not add `enforces_write_boundary=True` to a shipped integration to make a test pass; report it as a concern instead.

- [ ] **Step 9: Commit**

```bash
git add agency/integrations/models.py agency/integrations/__init__.py agency/configuration/effective.py agency/jobs/models.py tests/test_write_boundary_contract.py
git commit -m "feat(integrations): define the write-boundary contract"
```

---

### Task 7: Memory round-trip through the launch view

Today `memory_working_dir` points at the staging directory, no integration reads it, and the stage always returns unchanged. This task builds the helper that mirrors the agent-visible memory directory back onto the stage; Task 8 wires it into the worker.

**Files:**
- Modify: `agency/records/outbox.py`
- Modify: `agency/records/__init__.py`
- Test: `tests/test_memory_round_trip.py`

**Interfaces:**
- Consumes: `create_outbox` and `OutboxPaths` from Task 2.
- Produces: `copy_outbox_memory_to_stage(outbox: OutboxPaths, stage_directory: Path) -> None` in `agency/records/outbox.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_round_trip.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agency.records.outbox import copy_outbox_memory_to_stage, create_outbox


@pytest.fixture
def launch(tmp_path: Path):
    path = tmp_path / "launch"
    path.mkdir()
    return path


def test_edited_memory_replaces_the_stage_contents(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memory.md").write_text("canonical", encoding="utf-8")
    outbox = create_outbox(launch, memory_files={"memory.md": b"canonical"})
    (outbox.memory / "memory.md").write_text("edited", encoding="utf-8")

    copy_outbox_memory_to_stage(outbox, stage)

    assert (stage / "memory.md").read_text(encoding="utf-8") == "edited"


def test_untouched_memory_leaves_the_stage_byte_identical(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memory.md").write_text("canonical", encoding="utf-8")
    outbox = create_outbox(launch, memory_files={"memory.md": b"canonical"})

    copy_outbox_memory_to_stage(outbox, stage)

    assert (stage / "memory.md").read_bytes() == b"canonical"


def test_a_new_memory_file_is_copied(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "decisions.md").write_text("new", encoding="utf-8")

    copy_outbox_memory_to_stage(outbox, stage)

    assert (stage / "decisions.md").read_text(encoding="utf-8") == "new"


def test_a_deleted_memory_file_is_removed_from_the_stage(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "memory.md").write_text("canonical", encoding="utf-8")
    outbox = create_outbox(launch, memory_files={"memory.md": b"canonical"})
    (outbox.memory / "memory.md").unlink()

    copy_outbox_memory_to_stage(outbox, stage)

    assert not (stage / "memory.md").exists()


def test_non_markdown_files_in_the_memory_directory_are_ignored(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "scratch.txt").write_text("junk", encoding="utf-8")

    copy_outbox_memory_to_stage(outbox, stage)

    assert not (stage / "scratch.txt").exists()


def test_subdirectories_in_the_memory_directory_are_rejected(launch, tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outbox = create_outbox(launch, memory_files={})
    (outbox.memory / "nested").mkdir()

    with pytest.raises(ValueError, match="subdirector"):
        copy_outbox_memory_to_stage(outbox, stage)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_memory_round_trip.py -v`
Expected: FAIL with `ImportError: cannot import name 'copy_outbox_memory_to_stage'`

- [ ] **Step 3: Implement the copy-back helper**

Append to `agency/records/outbox.py`:

```python
def copy_outbox_memory_to_stage(outbox: OutboxPaths, stage_directory: Path) -> None:
    """Mirror the agent-visible memory directory onto the publication stage."""
    stage_directory = Path(stage_directory)
    stage_directory.mkdir(parents=True, exist_ok=True)

    produced: dict[str, bytes] = {}
    for entry in sorted(outbox.memory.iterdir(), key=lambda item: item.name.casefold()):
        if entry.is_dir():
            raise ValueError(
                f"memory directory must not contain subdirectories: {entry.name}"
            )
        if entry.suffix.casefold() != ".md":
            continue
        produced[entry.name] = entry.read_bytes()

    for name, payload in produced.items():
        atomic_write_bytes(stage_directory / name, payload)

    for entry in list(stage_directory.iterdir()):
        if entry.is_file() and entry.name not in produced:
            entry.unlink()
```

Export it by appending to `agency/records/__init__.py`:

```python
from .outbox import copy_outbox_memory_to_stage

__all__ += ["copy_outbox_memory_to_stage"]
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_memory_round_trip.py -v`
Expected: PASS

- [ ] **Step 5: Commit the helper**

```bash
git add agency/records/outbox.py agency/records/__init__.py tests/test_memory_round_trip.py
git commit -m "feat(records): mirror launch-view memory onto the stage"
```

---

### Task 8: Worker wiring

Create the outbox before the run, validate and ingest after it, and mirror memory into the stage before publication.

**Files:**
- Modify: `agency/jobs/execution.py` (inside `with _memory_lock(...)`, around lines 351-500)
- Test: `tests/test_records_worker.py`

**Interfaces:**
- Consumes: `create_outbox`, `copy_outbox_memory_to_stage` (Tasks 2, 7); `validate_outbox` (Task 3); `ingest_records` (Task 4).
- Produces: `writable_agent_names(config, group_key) -> frozenset[str]` in `agency/records/validation.py`.

- [ ] **Step 1: Write the failing test for the writable-agent helper**

Create `tests/test_records_worker.py`:

```python
from __future__ import annotations

from agency.configuration.models import AgencyConfig
from agency.records.validation import writable_agent_names


def build_config(raw_config, agents):
    raw_config["groups"]["newsletter"]["agents"] = agents
    return AgencyConfig.model_validate(raw_config)


def test_only_writable_agents_are_returned(raw_config):
    config = build_config(
        raw_config,
        [
            {
                "name": "paul",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "capabilities": {"write": True},
            },
            {
                "name": "gurney",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
                "capabilities": {"write": False},
            },
        ],
    )

    assert writable_agent_names(config, "newsletter") == frozenset({"paul"})


def test_agents_without_a_capabilities_block_are_not_writable(raw_config):
    config = build_config(
        raw_config,
        [
            {
                "name": "gurney",
                "blueprint": "builder-blueprint",
                "integration": "claude-code",
            }
        ],
    )

    assert writable_agent_names(config, "newsletter") == frozenset()


def test_unknown_group_yields_an_empty_set(raw_config):
    config = build_config(raw_config, [])

    assert writable_agent_names(config, "no-such-group") == frozenset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_worker.py -v`
Expected: FAIL with `ImportError: cannot import name 'writable_agent_names'`

- [ ] **Step 3: Implement the helper**

Append to `agency/records/validation.py`:

```python
def writable_agent_names(config, group_key: str) -> frozenset[str]:
    """Configured instances in a group that may be trusted to execute."""
    from agency.integrations import get_integration

    group = config.groups.get(group_key)
    if group is None:
        return frozenset()

    names: set[str] = set()
    for name, agent in group.agents.items():
        if not agent.capabilities.write:
            continue
        try:
            integration = get_integration(agent.integration)
        except KeyError:
            continue
        if integration.supports_execution:
            names.add(name)
    return frozenset(names)
```

- [ ] **Step 4: Run the helper tests**

Run: `python -m pytest tests/test_records_worker.py -v`
Expected: PASS

- [ ] **Step 5: Wire the outbox into execution**

In `agency/jobs/execution.py`, add the imports beside the other `agency.` imports:

```python
from agency.configuration.store import load_config_snapshot
from agency.records.ingest import ingest_records
from agency.records.outbox import copy_outbox_memory_to_stage, create_outbox
from agency.records.validation import validate_outbox, writable_agent_names
```

After the launch view is created (currently `launch_view = create_launch_view(artifact, launch_dir)`), add:

```python
                outbox = create_outbox(launch_view, memory_files=canonical_files)
```

Change the `IntegrationRunRequest(...)` construction to hand the agent the reachable memory directory:

```python
                    memory_working_dir=outbox.memory,
```

- [ ] **Step 6: Wire validation, ingest, and the memory mirror into the success path**

`resolve_job_context` builds a `SimpleNamespace` with no `config` attribute, so the writable-agent set comes from the config the job pinned at submission — `spec.config_path`.

In the `else:` branch that currently begins `try: prepared = prepare_publication(`, insert before that `try:`:

```python
                    snapshot = load_config_snapshot(Path(spec.config_path))
                    validation = validate_outbox(
                        outbox,
                        writable_agents=writable_agent_names(
                            snapshot.config, spec.group_key
                        ),
                    )
                    if not validation.ok:
                        reasons = "; ".join(
                            f"{item.kind} {item.source_name}: {item.reason}"
                            for item in validation.rejected
                        )
                        artifacts = retain_failed_stage(
                            job_store=_jobs_dir(job_path),
                            job_id=spec.job_id,
                            stage_directory=outbox.observations,
                            diff_bytes=None,
                        )
                        return _terminalize_failure(
                            job_path,
                            summary=f"Rejected agent records: {reasons}",
                            started_at=started.isoformat(),
                            stdout_path=str(stdout_path.resolve()),
                            stderr_path=persisted_stderr_path,
                            exit_code=result.exit_code,
                            duration_seconds=result.duration_seconds,
                            changed_files=changes,
                            base_sha=base_sha,
                            memory_publication={
                                "failed_artifacts": [
                                    artifact.to_dict() for artifact in artifacts
                                ]
                            },
                            session_id=result.session_id,
                        )

                    ingested = ingest_records(
                        validation,
                        observations_dir=Path(context.group_root) / "observations",
                        proposals_dir=Path(context.group_root) / "proposals",
                        agent_name=spec.agent_name,
                        now=started,
                        job_id=spec.job_id,
                    )
                    copy_outbox_memory_to_stage(outbox, stage.directory)
```

Retaining the rejected records matters: without it the agent's work is destroyed with the launch view and the operator has only the reason string. `retain_failed_stage` copies the outbox observations into the job's artifact directory, which is where the dashboard already looks.

Then extend the existing success summary so the operator can see what was filed. Replace the `summary = (` expression in that branch with:

```python
                        record_note = (
                            f" Filed {len(ingested)} "
                            f"{'record' if len(ingested) == 1 else 'records'}."
                            if ingested
                            else ""
                        )
                        summary = (
                            f"Agent completed execution; captured "
                            f"{len(changes)} changed "
                            f"{'file' if len(changes) == 1 else 'files'}."
                            f"{record_note}"
                            if changes
                            else (
                                "Agent completed execution "
                                f"(inferred from exit code).{record_note}"
                            )
                        )
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add agency/jobs/execution.py agency/records/validation.py agency/records/__init__.py tests/test_records_worker.py
git commit -m "feat(jobs): validate and ingest agent records after a run"
```

---

### Task 9: Documentation

**Files:**
- Modify: `AGENTS.md` (the authority-boundaries and configuration sections, and the stale Development block)
- Modify: `kb/configuration.md:20`
- Modify: `skills/agency-setup/references/templates.md:86`
- Modify: `.github/skills/agency-setup/references/templates.md:86`

- [ ] **Step 1: Record the write-boundary contract in `AGENTS.md`**

In the configuration section, after the sentence "Agent tools are a complete override, not an addition.", add:

```markdown
Writability is a separate axis from tools. `capabilities.write` decides whether
an agent may write its workspace: true grants write to the sandbox roots, false
grants none of them. The per-job launch view holding the agent's outbox and
memory is implicitly writable for every agent and never appears in
configuration. An integration that cannot enforce that separation must not run
an agent whose writes are narrowed; it declares
`RuntimeCapabilities.enforces_write_boundary` when it can.
```

In the authority-boundaries section, after the sentence about explicit instances, add:

```markdown
Reporting is unconditional. `capabilities.write` governs workspace mutation and
decision execution only; it never prevents an agent from recording an
observation, creating a proposal, or updating its own memory.
```

Then fix the stale Development block at `AGENTS.md:106-109`. There is no `.venv`
in this repository, so every agent told to run `.venv/Scripts/python` reports a
false blocker — one already did. Replace the block with:

```text
python -m pytest tests/ -q
python -m agency.app
```

- [ ] **Step 2: Mirror it in `kb/configuration.md`**

Append to the paragraph at line 20:

```markdown
`capabilities.write` selects the writable subset of those roots: all of them
when true, none of them when false. The tool policy is unaffected by it.
```

- [ ] **Step 3: Replace the vague pipeline sentence in both skill copies**

In `skills/agency-setup/references/templates.md` and `.github/skills/agency-setup/references/templates.md`, replace line 86:

```markdown
3. Record observations or proposals through the project's configured pipeline.
```

with:

```markdown
3. Record observations and proposals by writing Markdown files into
   `.agency/outbox/observations/` and `.agency/outbox/proposals/`, relative to
   the working directory. Agency validates and files them after the run, and
   assigns the `agent`, `date`, and `status` fields and the file name itself.
   Keep durable knowledge by editing the seeded files in `.agency/memory/`.
```

- [ ] **Step 4: Verify the skill test still passes**

Run: `python -m pytest tests/test_agency_setup_skill.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md kb/configuration.md skills/agency-setup/references/templates.md .github/skills/agency-setup/references/templates.md
git commit -m "docs(records): document the agent reporting protocol"
```

---

## Completion

- [ ] Run the complete suite from the worktree root and confirm it is green.
- [ ] Review the whole branch before integrating.
- [ ] Fast-forward `master` to the reviewed tip, re-run the suite, push both branches, and remove the worktree.
