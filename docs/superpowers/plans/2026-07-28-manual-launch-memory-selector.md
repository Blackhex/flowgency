# Manual Launch Memory Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow saved-prompt and one-off web launches with an explicit memory target to submit a typed `MemorySelector` and return HTTP 202.

**Architecture:** Keep form parsing and HTTP validation in the manual launch route, but convert each validated memory override into the domain model before constructing `JobRequest`. Declare that model on the request dataclass so every producer sees the same contract already used by job resolution and the CLI.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, pytest

## Global Constraints

- Cover both saved-prompt and one-off manual launcher modes.
- Preserve all current HTTP 400 validation messages and valid HTTP 202 responses.
- Do not change the launcher UI, memory precedence, durable job schema, configuration schema, CLI behavior, or worker behavior.
- Run every command from `.worktrees/manual-launch-memory-selector`.
- Use `python -m pytest`; this repository does not have a `.venv` interpreter.

---

## File Map

- `tests/test_agent_run.py`: Prove both web launch modes emit typed memory overrides and retain routine-memory coverage.
- `agency/app.py`: Convert validated form values into `MemorySelector` instances at the web boundary.
- `agency/jobs/models.py`: Declare `JobRequest.memory_override` as `MemorySelector | None`.

### Task 1: Type Manual Launch Memory Overrides

**Files:**
- Modify: `tests/test_agent_run.py`
- Modify: `agency/app.py`
- Modify: `agency/jobs/models.py`

**Interfaces:**
- Consumes: POST form fields `mode`, `memory_scope`, and `memory_channel`; existing `MemorySelector(scope: MemoryScope, channel: str | None = None)`.
- Produces: `JobRequest.memory_override: MemorySelector | None`, consumed unchanged by `select_effective_memory()` and `resolve_memory_selector()`.

- [ ] **Step 1: Establish the clean full-suite baseline**

Run:

```powershell
python -m pytest tests/ -q
```

Expected: PASS. If it fails, record the pre-existing failure and stop before changing implementation files.

- [ ] **Step 2: Write the failing web-boundary regression tests**

Add the model import in `tests/test_agent_run.py`:

```python
from agency.configuration.models import MemorySelector
```

Add this parameterized test near the other manual-run request tests:

```python
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {
                "mode": "saved",
                "prompt_scope": "blueprint",
                "prompt_name": "daily-review",
                "memory_scope": "agent",
            },
            id="saved",
        ),
        pytest.param(
            {
                "mode": "one-off",
                "task_input": "Inspect the current suite.",
                "memory_scope": "agent",
            },
            id="one-off",
        ),
    ],
)
def test_run_submits_typed_memory_override_for_manual_modes(
    tmp_path,
    monkeypatch,
    payload,
):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr(
        "agency.app.submit_job_request",
        lambda request: calls.append(request) or SimpleNamespace(job_id="job-1"),
    )
    client = TestClient(app)

    response = client.post("/test/agents/product/run", data=payload)

    assert response.status_code == 202
    assert isinstance(calls[0].memory_override, MemorySelector)
    assert calls[0].memory_override == MemorySelector(scope="agent")
```

Update the existing routine-memory assertion:

```python
assert calls[0].memory_override == MemorySelector(scope="routine")
```

- [ ] **Step 3: Run the new regression test and verify it fails for the reported reason**

Run:

```powershell
python -m pytest tests/test_agent_run.py::test_run_submits_typed_memory_override_for_manual_modes -q
```

Expected: FAIL for both parameter cases because `memory_override` is a `dict`, not a `MemorySelector`.

- [ ] **Step 4: Implement the typed producer contract**

Import the selector in `agency/app.py`:

```python
from agency.configuration.models import MemorySelector
```

Replace only the two successful override assignments in the manual launch route:

```python
memory_override = MemorySelector(
    scope="channel",
    channel=memory_channel,
)
```

```python
memory_override = MemorySelector(scope=memory_scope)
```

Import and declare the selector type in `agency/jobs/models.py`:

```python
from agency.configuration.models import MemorySelector, PromptSelector
```

```python
memory_override: MemorySelector | None = None
```

Do not add resolver coercion or change the existing route validation branches.

- [ ] **Step 5: Run the regression test and verify it passes**

Run:

```powershell
python -m pytest tests/test_agent_run.py::test_run_submits_typed_memory_override_for_manual_modes -q
```

Expected: PASS for `saved` and `one-off`.

- [ ] **Step 6: Run all manual launcher tests**

Run:

```powershell
python -m pytest tests/test_agent_run.py -q
```

Expected: PASS, including the typed routine-memory assertion and existing invalid-selector responses.

- [ ] **Step 7: Run the complete suite**

Run:

```powershell
python -m pytest tests/ -q
```

Expected: PASS with no repository-boundary or behavior regressions.

- [ ] **Step 8: Commit the implementation**

```powershell
git add agency/app.py agency/jobs/models.py tests/test_agent_run.py
git commit -m "fix(agents): type manual memory overrides"
```

The commit must contain only the three implementation files listed above. Request a whole-branch code review after this task and address any findings before integration.
