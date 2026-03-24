# Proposal Questions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single approve/defer/reject decision model with typed questions (boolean, choice, free-response) embedded in proposals, allowing richer human-agent interaction.

**Architecture:** Questions are a YAML list in proposal frontmatter. Answers live in decision frontmatter as a dict keyed by question ID. The proposal detail template renders type-specific form controls. Execution always dispatches the origin agent after any decision.

**Tech Stack:** Python/FastAPI, Jinja2 templates, Tailwind CSS, PyYAML

**Spec:** `docs/superpowers/specs/2026-03-24-proposal-questions-design.md`

---

### Task 1: Update status_badge and pipeline constants

Update the status badge color mapping and any pipeline status references to support `decided` (green) and remove old terminal statuses.

**Files:**
- Modify: `agency/app.py:698-710` (status_badge function)
- Test: `tests/test_needs_action.py`

- [ ] **Step 1: Write failing test for `decided` badge**

In `tests/test_needs_action.py`, add a test that the `decided` status is treated as a terminal (non-actionable) status:

```python
def test_decided_is_not_actionable():
    proposals = [
        {"status": "proposed"},
        {"status": "decided"},
    ]
    actionable = [c for c in proposals if c.get("status") in ("proposed", "investigating")]
    assert len(actionable) == 1
    assert actionable[0]["status"] == "proposed"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_needs_action.py -v`

This test should pass already since `decided` is not in the actionable set. Confirms the filter logic is correct.

- [ ] **Step 3: Update status_badge colors in app.py**

In `agency/app.py` at line 700, update the `colors` dict:

```python
colors = {
    "open": "bg-amber-100 text-amber-800",
    "connected": "bg-blue-100 text-blue-800",
    "investigating": "bg-purple-100 text-purple-800",
    "proposed": "bg-green-100 text-green-800",
    "decided": "bg-emerald-100 text-emerald-800",
    "dismissed": "bg-gray-100 text-gray-500",
    "archived": "bg-gray-100 text-gray-400",
}
```

Remove `approved` from the color mapping. `decided` takes over as the green terminal status.

- [ ] **Step 4: Update `enforce_ttl()` terminal statuses**

In `agency/app.py` at line 396, update the terminal status list:

```python
    if status in ("archived", "dismissed", "decided"):
        return False
```

Replace `"approved", "rejected", "deferred"` with `"decided"`. This prevents the TTL system from auto-archiving decided proposals.

- [ ] **Step 5: Update test_needs_action.py to use `decided` instead of `approved`/`deferred`**

Replace the test data in `test_needs_action_counts_actionable_proposals_and_floated_observations`:

```python
proposals = [
    {"status": "proposed"},
    {"status": "investigating"},
    {"status": "decided"},
    {"status": "decided"},
]
```

The assertion `assert needs_action == 3` stays the same (2 actionable proposals + 1 floated observation).

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add agency/app.py tests/test_needs_action.py
git commit -m "feat: add 'decided' status, update enforce_ttl and pipeline constants"
```

---

### Task 2: Rewrite proposal_decide() route handler

Replace the old approve/defer/reject handler with one that reads typed questions from the proposal, extracts answers from the form, builds a decision file with an `answers` dict, and always triggers execution.

**Files:**
- Modify: `agency/app.py:2279-2339` (proposal_decide route)
- Modify: `agency/app.py:256-265` (update_decision_execution — simplify to flat field)
- Modify: `agency/app.py:268-354` (execute_approved_decision — rename, update prompt)
- Test: `tests/test_proposal_questions.py` (new)

- [ ] **Step 1: Write tests for the new decide logic**

Create `tests/test_proposal_questions.py`:

```python
"""Tests for proposal questions and decision answers."""
import yaml


def _make_proposal_frontmatter(questions):
    """Build proposal frontmatter YAML string with questions."""
    meta = {
        "origin_agent": "product",
        "date": "2026-03-24",
        "status": "proposed",
        "observations": [],
        "feedback_requested": [],
        "feedback_received": [],
        "ttl_days": 14,
        "questions": questions,
    }
    body = "## Proposal: Test\n\nSome context."
    fm = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body}\n"


def _parse_decision_answers(decision_text):
    """Parse answers from a decision file."""
    meta, _ = _parse_frontmatter(decision_text)
    return meta.get("answers", {})


def _parse_frontmatter(text):
    """Minimal frontmatter parser for tests."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return meta, body


class TestQuestionTypes:
    def test_boolean_question_structure(self):
        q = {"id": "approve", "type": "boolean", "prompt": "Proceed?"}
        assert q["type"] == "boolean"
        assert q["id"] == "approve"

    def test_choice_question_structure(self):
        q = {
            "id": "color",
            "type": "choice",
            "prompt": "Pick a color",
            "options": [{"label": "Red"}, {"label": "Blue"}],
            "multi": False,
        }
        assert q["type"] == "choice"
        assert len(q["options"]) == 2
        assert q["multi"] is False

    def test_choice_multi_select(self):
        q = {
            "id": "features",
            "type": "choice",
            "prompt": "Pick features",
            "options": [{"label": "Auth"}, {"label": "Search"}, {"label": "Chat"}],
            "multi": True,
        }
        assert q["multi"] is True

    def test_free_response_question_structure(self):
        q = {"id": "description", "type": "free-response", "prompt": "Describe it"}
        assert q["type"] == "free-response"


class TestBuildAnswers:
    def test_builds_answers_from_form_data(self):
        """Simulate building answers dict from form data."""
        questions = [
            {"id": "approve", "type": "boolean", "prompt": "Proceed?"},
            {"id": "color", "type": "choice", "prompt": "Pick", "options": [{"label": "Red"}, {"label": "Blue"}], "multi": False},
            {"id": "desc", "type": "free-response", "prompt": "Describe"},
        ]
        # Simulate form data (answer_{id} keys)
        form_data = {
            "answer_approve": "approved",
            "answer_color": "Red",
            "answer_desc": "A sunset scene",
        }
        answers = {}
        for q in questions:
            key = f"answer_{q['id']}"
            answers[q["id"]] = form_data.get(key, "")

        assert answers == {
            "approve": "approved",
            "color": "Red",
            "desc": "A sunset scene",
        }

    def test_multi_select_answers(self):
        """Multi-select choice answers should be a list."""
        questions = [
            {"id": "features", "type": "choice", "prompt": "Pick", "options": [{"label": "Auth"}, {"label": "Search"}], "multi": True},
        ]
        # Multi-select sends multiple values — getlist() in form
        form_values = ["Auth", "Search"]
        answers = {"features": form_values}
        assert answers["features"] == ["Auth", "Search"]


class TestProposalFrontmatter:
    def test_proposal_with_questions_parses(self):
        questions = [
            {"id": "approve", "type": "boolean", "prompt": "Proceed?"},
        ]
        text = _make_proposal_frontmatter(questions)
        meta, body = _parse_frontmatter(text)
        assert "questions" in meta
        assert len(meta["questions"]) == 1
        assert meta["questions"][0]["id"] == "approve"

    def test_decision_with_answers_parses(self):
        answers = {"approve": "approved", "color": "Red"}
        meta = {
            "proposal": "test.md",
            "decided_by": "admin",
            "date": "2026-03-24",
            "answers": answers,
        }
        fm = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
        text = f"---\n{fm}\n---\n\n"
        parsed_meta, _ = _parse_frontmatter(text)
        assert parsed_meta["answers"]["approve"] == "approved"
        assert parsed_meta["answers"]["color"] == "Red"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_proposal_questions.py -v`
Expected: All tests PASS (these are pure data structure tests).

- [ ] **Step 3: Rewrite `proposal_decide()` route**

Replace `agency/app.py` lines 2279-2339 with:

```python
@app.post("/{group}/proposals/{slug}/decide", response_class=HTMLResponse)
async def proposal_decide(request: Request, group: str, slug: str,
                           background_tasks: BackgroundTasks):
    """Create a decision by answering a proposal's questions."""
    g = get_group(group)
    decisions_dir = g["shared"] / "decisions"
    proposals_dir = g["shared"] / "proposals"

    # Read proposal to get questions and origin_agent
    cpath = proposals_dir / f"{slug}.md"
    if not cpath.exists():
        raise HTTPException(404, "Proposal not found")
    cmeta, _ = parse_frontmatter(cpath.read_text())
    origin_agent = cmeta.get("origin_agent", "")
    questions = cmeta.get("questions", [])

    form = await request.form()

    # Build answers from form data
    answers = {}
    for q in questions:
        key = f"answer_{q['id']}"
        if q.get("type") == "choice" and q.get("multi"):
            # Multi-select: collect all values for this key
            answers[q["id"]] = form.getlist(key)
        else:
            answers[q["id"]] = form.get(key, "")

    agency_cfg = get_agency_config()
    decided_by = agency_cfg.get("decided_by", "admin")
    today = datetime.now().strftime("%Y-%m-%d")

    # Build decision frontmatter
    meta = {
        "proposal": f"{slug}.md",
        "decided_by": decided_by,
        "date": today,
        "answers": answers,
        "execution_status": "pending",
    }

    frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    decision_content = f"---\n{frontmatter}\n---\n"

    decisions_dir.mkdir(exist_ok=True)
    decision_path = decisions_dir / f"{slug}.md"
    decision_path.write_text(decision_content)

    # Update proposal status to decided
    update_frontmatter_field(cpath, "status", "decided")

    # Always dispatch agent to act on the decision
    if origin_agent:
        background_tasks.add_task(
            execute_decision,
            decision_path, Path(g["path"]), origin_agent, slug,
            group_key=group,
        )

    return RedirectResponse(f"/{group}/decisions/{slug}", status_code=303)
```

- [ ] **Step 4: Update `update_decision_execution()` to use flat `execution_status` field**

Replace `agency/app.py` lines 256-265:

```python
def update_decision_execution(decision_path: Path, field: str, value) -> None:
    """Update execution_status (or other top-level field) in a decision file."""
    raw = decision_path.read_text()
    meta, body = parse_frontmatter(raw)
    meta[field] = value
    frontmatter = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    decision_path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n")
```

- [ ] **Step 5: Rename and update `execute_approved_decision()` to `execute_decision()`**

Replace `agency/app.py` lines 268-354 with:

```python
def execute_decision(decision_path: Path, group_path: Path, agent: str,
                     proposal_slug: str, group_key: str = "") -> None:
    """Background task: dispatch agent to act on a decision's answers."""
    now = datetime.now(timezone.utc).isoformat()
    update_decision_execution(decision_path, "execution_status", "running")

    # Build the execution prompt
    prompt = (
        f"A decision has been made on your proposal.\n\n"
        f"Read the decision file at agents/shared/decisions/{decision_path.name}\n"
        f"Read the linked proposal at agents/shared/proposals/{proposal_slug}.md\n\n"
        f"The decision file contains an 'answers' section in the frontmatter with "
        f"the human's responses to each question you asked in the proposal.\n\n"
        f"Act on these answers:\n"
        f"- If approved/accepted: execute the proposed action\n"
        f"- If deferred: acknowledge and schedule for later\n"
        f"- If rejected: close the loop gracefully, do not proceed\n"
        f"- For choice/free-response answers: use the human's input to guide your work\n\n"
        f"When done, update the decision file's 'execution_status' field in the "
        f"YAML frontmatter to one of: complete, failed\n"
        f"Also set 'execution_summary' to a brief description of what you did."
    )

    # Resolve integration
    g = GROUPS.get(group_key, {})
    grp = {"path": group_path, "agents_full": g.get("_agents_normalized", [])}

    agent_dir = get_agent_dir(grp, agent)
    if not agent_dir.exists():
        agent_dir = group_path

    log_dir = group_path / "shared" / "logs" / datetime.now().strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    out_path = log_dir / f"{agent}-exec-{ts}.out"
    err_path = log_dir / f"{agent}-exec-{ts}.err"

    prompt_file = log_dir / f"{agent}-exec-{ts}.prompt"
    prompt_file.write_text(prompt)

    try:
        agent_integration = get_agent_integration(grp, agent)

        if not agent_integration.supports_execution:
            update_decision_execution(decision_path, "execution_status", "failed")
            update_decision_execution(decision_path, "execution_summary",
                                      f"Integration '{agent_integration.name}' does not support execution.")
            return

        if hasattr(agent_integration, 'with_config'):
            for a in g.get("_agents_normalized", []):
                if a["name"] == agent and "integration_config" in a:
                    agent_integration = agent_integration.with_config(a["integration_config"])
                    break

        result = agent_integration.run(agent_dir, prompt_file, timeout=300)
        out_path.write_text(result.stdout)
        err_path.write_text(result.stderr)

        # Check if agent updated status itself
        updated_meta, _ = parse_frontmatter(decision_path.read_text())
        exec_status = updated_meta.get("execution_status", "")
        if exec_status not in ("complete", "failed"):
            if result.exit_code == 0:
                update_decision_execution(decision_path, "execution_status", "complete")
                update_decision_execution(decision_path, "execution_summary",
                                          "Agent completed execution (inferred from exit code).")
            else:
                update_decision_execution(decision_path, "execution_status", "failed")
                update_decision_execution(decision_path, "execution_summary",
                                          f"Agent exited with code {result.exit_code}.")
    except Exception as e:
        update_decision_execution(decision_path, "execution_status", "failed")
        update_decision_execution(decision_path, "execution_summary", f"Execution error: {e}")
    finally:
        if prompt_file.exists():
            prompt_file.unlink()
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add agency/app.py tests/test_proposal_questions.py
git commit -m "feat: rewrite proposal_decide for typed questions, simplify execution model"
```

---

### Task 3: Update proposal_detail() route and template

Update the route handler to pass questions and answers to the template. Rewrite the template to render type-specific form controls (stacked cards) and the read-only decided state.

**Files:**
- Modify: `agency/app.py:2228-2276` (proposal_detail route)
- Modify: `agency/templates/proposal_detail.html`

- [ ] **Step 1: Update proposal_detail() route handler**

Replace `agency/app.py` lines 2259-2275 (the status sync block and template return):

```python
    # Sync proposal status if a decision exists but status is stale
    if decision and meta.get("status") != "decided":
        update_frontmatter_field(path, "status", "decided")
        meta["status"] = "decided"

    # Parse questions for template
    questions = meta.get("questions", [])
    decision_answers = decision["meta"].get("answers", {}) if decision else {}

    return templates.TemplateResponse("proposal_detail.html", {
        "request": request,
        **group_context(g),
        "meta": meta,
        "body_html": render_md(body),
        "body_raw": body,
        "slug": slug,
        "title": extract_display_title(body, slug),
        "linked_observations": linked,
        "decision": decision,
        "questions": questions,
        "answers": decision_answers,
    })
```

- [ ] **Step 2: Rewrite proposal_detail.html template**

Replace `agency/templates/proposal_detail.html` entirely:

```html
{% extends "base.html" %}
{% set active = "proposals" %}

{% block title %}{{ slug }} - Proposals{% endblock %}

{% block content %}
<div class="mb-4">
  <a href="/{{ group }}/proposals" class="text-sm text-indigo-600 hover:text-indigo-800">&larr; Back to proposals</a>
</div>

<div class="bg-white rounded-lg border border-gray-200 p-4 md:p-6">
  <div class="flex flex-wrap items-center gap-2 mb-3">
    <a href="/{{ group }}/agents/{{ meta.get('origin_agent', '') }}">{{ meta.get("origin_agent", "") | agent_badge }}</a>
    {{ meta.get("status", "") | status_badge }}
  </div>
  <h1 class="text-lg md:text-xl font-bold text-gray-900 mb-4">{{ title }}</h1>

  <!-- Metadata -->
  <div class="flex flex-wrap gap-x-4 gap-y-1 text-sm text-gray-500 mb-4 pb-4 border-b border-gray-100">
    <span>Date: <strong>{{ meta.get("date", "\u2014") }}</strong></span>
    <span>TTL: <strong>{{ meta.get("ttl_days", "\u2014") }} days</strong></span>
    {% if meta.get("feedback_requested") %}
    <span>Feedback: <strong>{{ meta.get("feedback_received", [])|length }}/{{ meta.feedback_requested|length }}</strong>
      ({{ meta.feedback_requested | join(", ") }})
    </span>
    {% endif %}
  </div>

  <!-- Pipeline chain -->
  {% if linked_observations or decision %}
  <div class="mb-4 p-4 bg-indigo-50 rounded-lg border border-indigo-200">
    <div class="text-xs font-semibold text-indigo-500 uppercase tracking-wide mb-2">Pipeline</div>
    <div class="flex flex-wrap items-center gap-2 text-sm">
      {% if linked_observations %}
      <div class="flex flex-wrap items-center gap-1">
        {% for lc in linked_observations %}
        <a href="/{{ group }}/observations/{{ lc.slug }}" class="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-amber-100 text-amber-800 font-medium hover:bg-amber-200 transition-colors text-xs">
          {{ lc.slug | replace("-", " ") }}
        </a>
        {% endfor %}
      </div>
      <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
      {% endif %}
      <span class="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-purple-100 text-purple-800 font-medium">
        This proposal
      </span>
      {% if decision %}
      <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
      <a href="/{{ group }}/decisions/{{ decision.slug }}" class="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-emerald-100 text-emerald-800 font-medium hover:bg-emerald-200 transition-colors">
        Decided
      </a>
      {% endif %}
    </div>
  </div>
  {% endif %}

  <!-- Body -->
  <div class="prose max-w-none">
    {{ body_html }}
  </div>

  <!-- Questions section -->
  {% if questions %}
  <div class="mt-6 pt-4 border-t border-gray-100">
    <h3 class="text-sm font-semibold text-purple-700 mb-3">
      {% if decision %}Answers{% else %}Questions{% endif %}
    </h3>

    {% if not decision %}
    {# ── Unanswered: render form ── #}
    <form method="POST" action="/{{ group }}/proposals/{{ slug }}/decide">
      {% for q in questions %}
      <div class="border border-gray-200 rounded-lg p-4 mb-3">
        <div class="font-medium text-gray-800 mb-2">{{ loop.index }}. {{ q.prompt }}</div>

        {% if q.type == "boolean" %}
        <div class="flex gap-2">
          <label class="cursor-pointer">
            <input type="radio" name="answer_{{ q.id }}" value="approved" class="hidden peer" required>
            <span class="inline-block px-4 py-1.5 rounded-md text-sm font-medium bg-gray-100 text-gray-600 peer-checked:bg-emerald-100 peer-checked:text-emerald-700 transition-colors">Approve</span>
          </label>
          <label class="cursor-pointer">
            <input type="radio" name="answer_{{ q.id }}" value="deferred" class="hidden peer">
            <span class="inline-block px-4 py-1.5 rounded-md text-sm font-medium bg-gray-100 text-gray-600 peer-checked:bg-amber-100 peer-checked:text-amber-700 transition-colors">Defer</span>
          </label>
          <label class="cursor-pointer">
            <input type="radio" name="answer_{{ q.id }}" value="rejected" class="hidden peer">
            <span class="inline-block px-4 py-1.5 rounded-md text-sm font-medium bg-gray-100 text-gray-600 peer-checked:bg-red-100 peer-checked:text-red-700 transition-colors">Reject</span>
          </label>
        </div>

        {% elif q.type == "choice" %}
        <div class="flex flex-col gap-1.5">
          {% for opt in q.options %}
          <label class="flex items-center gap-2 px-3 py-2 border border-gray-200 rounded-md cursor-pointer hover:border-purple-300 has-[:checked]:border-purple-500 has-[:checked]:bg-purple-50 transition-colors">
            {% if q.multi %}
            <input type="checkbox" name="answer_{{ q.id }}" value="{{ opt.label }}" class="text-purple-600 rounded">
            {% else %}
            <input type="radio" name="answer_{{ q.id }}" value="{{ opt.label }}" class="text-purple-600" {% if loop.first %}required{% endif %}>
            {% endif %}
            <span class="text-sm">{{ opt.label }}</span>
          </label>
          {% endfor %}
        </div>

        {% elif q.type == "free-response" %}
        <textarea name="answer_{{ q.id }}" rows="3" required
                  class="w-full border border-gray-300 rounded-lg p-3 text-sm focus:border-purple-400 focus:ring-1 focus:ring-purple-400"
                  placeholder="Type your answer..."></textarea>
        {% endif %}
      </div>
      {% endfor %}

      <div class="text-right mt-4">
        <button type="submit" class="px-6 py-2 bg-purple-600 text-white text-sm font-medium rounded-lg hover:bg-purple-700 transition-colors">
          Submit All Answers
        </button>
      </div>
    </form>

    {% else %}
    {# ── Decided: show read-only answers ── #}
    <div class="mb-3 flex items-center gap-2">
      <span class="text-sm text-gray-500">by {{ decision.meta.get("decided_by", "\u2014") }} on {{ decision.meta.get("date", "\u2014") }}</span>
    </div>

    {% for q in questions %}
    <div class="border border-gray-200 rounded-lg p-4 mb-3 bg-gray-50">
      <div class="font-medium text-gray-500 mb-2">{{ loop.index }}. {{ q.prompt }}</div>

      {% set answer = answers.get(q.id, "") %}

      {% if q.type == "boolean" %}
      <div class="inline-block px-3 py-1 rounded-md text-sm font-medium
        {% if answer == 'approved' %}bg-emerald-100 text-emerald-700
        {% elif answer == 'deferred' %}bg-amber-100 text-amber-700
        {% elif answer == 'rejected' %}bg-red-100 text-red-700
        {% else %}bg-gray-100 text-gray-600{% endif %}">
        {{ answer | title }}
      </div>

      {% elif q.type == "choice" %}
      {% if answer is string %}
      <div class="inline-flex items-center gap-2 px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-md">
        <span class="text-sm font-medium text-emerald-700">{{ answer }}</span>
      </div>
      {% else %}
      <div class="flex flex-col gap-1">
        {% for a in answer %}
        <div class="inline-flex items-center gap-2 px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-md">
          <span class="text-sm font-medium text-emerald-700">{{ a }}</span>
        </div>
        {% endfor %}
      </div>
      {% endif %}

      {% elif q.type == "free-response" %}
      <div class="px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-md text-sm text-gray-700">
        {{ answer }}
      </div>
      {% endif %}
    </div>
    {% endfor %}

    <a href="/{{ group }}/decisions/{{ decision.slug }}" class="text-sm text-emerald-700 underline mt-2 inline-block">View decision</a>
    {% endif %}
  </div>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add agency/app.py agency/templates/proposal_detail.html
git commit -m "feat: render typed question forms and read-only answers on proposal detail"
```

---

### Task 4: Update decision detail page

Update the decision detail template and route to show answers (rendered as read-only cards) instead of the old execution block format.

**Files:**
- Modify: `agency/app.py:2354-2389` (decision_detail route)
- Modify: `agency/app.py:2392-2423` (decision_retry route)
- Modify: `agency/templates/decision_detail.html`
- Modify: `agency/templates/decisions.html` (list page badge)

- [ ] **Step 1: Update decision_detail() route handler**

In `agency/app.py`, update the `decision_detail()` function to also load the proposal's questions and pass them alongside answers:

Replace `execution = meta.get("execution", {})` (line 2377) and the template return (lines 2379-2389) with:

```python
    execution_status = meta.get("execution_status", "")
    execution_summary = meta.get("execution_summary", "")

    # Load proposal questions for rendering answers
    questions = []
    if proposal_slug:
        proposal_path = g["shared"] / "proposals" / f"{proposal_slug}.md"
        if proposal_path.exists():
            pmeta, _ = parse_frontmatter(proposal_path.read_text())
            questions = pmeta.get("questions", [])

    return templates.TemplateResponse("decision_detail.html", {
        "request": request,
        **group_context(g),
        "meta": meta,
        "body_html": render_md(body),
        "slug": slug,
        "title": extract_display_title(body, slug),
        "pipeline_observations": pipeline_observations,
        "proposal_slug": proposal_slug,
        "execution_status": execution_status,
        "execution_summary": execution_summary,
        "questions": questions,
        "answers": meta.get("answers", {}),
    })
```

- [ ] **Step 2: Update decision_retry() route**

In `agency/app.py`, update `decision_retry()` (lines 2392-2423) to use the new flat fields and `execute_decision`:

```python
@app.post("/{group}/decisions/{slug}/retry", response_class=HTMLResponse)
async def decision_retry(request: Request, group: str, slug: str,
                         background_tasks: BackgroundTasks):
    """Retry execution of a decision."""
    g = get_group(group)
    decision_path = g["shared"] / "decisions" / f"{slug}.md"
    if not decision_path.exists():
        raise HTTPException(404, "Decision not found")

    meta, _ = parse_frontmatter(decision_path.read_text())
    proposal_slug = (meta.get("proposal", "") or "").replace(".md", "")

    # Find origin agent from proposal
    origin_agent = ""
    if proposal_slug:
        proposal_path = g["shared"] / "proposals" / f"{proposal_slug}.md"
        if proposal_path.exists():
            pmeta, _ = parse_frontmatter(proposal_path.read_text())
            origin_agent = pmeta.get("origin_agent", "")

    if not origin_agent or not proposal_slug:
        raise HTTPException(400, "Decision has no linked proposal or origin agent")

    # Reset execution status
    update_decision_execution(decision_path, "execution_status", "pending")
    update_decision_execution(decision_path, "execution_summary", "")

    background_tasks.add_task(
        execute_decision,
        decision_path, Path(g["path"]), origin_agent, proposal_slug,
        group_key=group,
    )

    return RedirectResponse(f"/{group}/decisions/{slug}", status_code=303)
```

- [ ] **Step 3: Rewrite decision_detail.html template**

Replace `agency/templates/decision_detail.html` entirely:

```html
{% extends "base.html" %}
{% set active = "decisions" %}

{% block title %}{{ slug }} - Decisions{% endblock %}

{% block content %}
<div class="mb-4">
  <a href="/{{ group }}/decisions" class="text-sm text-indigo-600 hover:text-indigo-800">&larr; Back to decisions</a>
</div>

<div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
  <div class="flex items-start justify-between mb-4">
    <h1 class="text-xl font-bold text-gray-900 dark:text-gray-100">{{ title }}</h1>
    {{ "decided" | status_badge }}
  </div>

  <div class="flex flex-wrap gap-x-6 gap-y-1 text-sm text-gray-500 dark:text-gray-400 mb-6 pb-4 border-b border-gray-100 dark:border-gray-700">
    <span>Decided by: <strong>{{ meta.get("decided_by", "\u2014") }}</strong></span>
    <span>Date: <strong>{{ meta.get("date", "\u2014") }}</strong></span>
  </div>

  {% if proposal_slug or pipeline_observations %}
  <div class="mb-6 p-4 bg-indigo-50 dark:bg-indigo-900/20 rounded-lg border border-indigo-200 dark:border-indigo-800">
    <div class="text-xs font-semibold text-indigo-500 uppercase tracking-wide mb-2">Pipeline</div>
    <div class="flex flex-wrap items-center gap-2 text-sm">
      {% if pipeline_observations %}
      <div class="flex flex-wrap items-center gap-1">
        {% for lc in pipeline_observations %}
        <a href="/{{ group }}/observations/{{ lc.slug }}" class="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-amber-100 text-amber-800 font-medium hover:bg-amber-200 transition-colors text-xs">
          {{ lc.slug | replace("-", " ") }}
        </a>
        {% endfor %}
      </div>
      <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
      {% endif %}
      {% if proposal_slug %}
      <a href="/{{ group }}/proposals/{{ proposal_slug }}" class="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-purple-100 text-purple-800 font-medium hover:bg-purple-200 transition-colors">
        {{ proposal_slug | replace("-", " ") | title }}
      </a>
      <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
      {% endif %}
      <span class="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-emerald-100 text-emerald-800 font-medium">
        This decision
      </span>
    </div>
  </div>
  {% endif %}

  <!-- Answers -->
  {% if questions and answers %}
  <div class="mb-6">
    <h3 class="text-sm font-semibold text-purple-700 dark:text-purple-400 mb-3">Answers</h3>
    {% for q in questions %}
    <div class="border border-gray-200 dark:border-gray-600 rounded-lg p-4 mb-3 bg-gray-50 dark:bg-gray-700/50">
      <div class="font-medium text-gray-500 dark:text-gray-400 mb-2">{{ loop.index }}. {{ q.prompt }}</div>

      {% set answer = answers.get(q.id, "") %}

      {% if q.type == "boolean" %}
      <div class="inline-block px-3 py-1 rounded-md text-sm font-medium
        {% if answer == 'approved' %}bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300
        {% elif answer == 'deferred' %}bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300
        {% elif answer == 'rejected' %}bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300
        {% else %}bg-gray-100 text-gray-600{% endif %}">
        {{ answer | title }}
      </div>

      {% elif q.type == "choice" %}
      {% if answer is string %}
      <div class="inline-flex items-center px-3 py-2 bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700 rounded-md">
        <span class="text-sm font-medium text-emerald-700 dark:text-emerald-300">{{ answer }}</span>
      </div>
      {% else %}
      <div class="flex flex-col gap-1">
        {% for a in answer %}
        <div class="inline-flex items-center px-3 py-2 bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700 rounded-md">
          <span class="text-sm font-medium text-emerald-700 dark:text-emerald-300">{{ a }}</span>
        </div>
        {% endfor %}
      </div>
      {% endif %}

      {% elif q.type == "free-response" %}
      <div class="px-3 py-2 bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700 rounded-md text-sm text-gray-700 dark:text-gray-300">
        {{ answer }}
      </div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if body_html and body_html != '' %}
  <div class="prose dark:prose-invert max-w-none mb-6">
    {{ body_html | safe }}
  </div>
  {% endif %}

  <!-- Execution Status -->
  {% if execution_status %}
  <div class="mt-6 p-4 rounded-lg border
    {% if execution_status == 'complete' %}bg-emerald-50 border-emerald-200 dark:bg-emerald-900/20 dark:border-emerald-800
    {% elif execution_status == 'failed' %}bg-red-50 border-red-200 dark:bg-red-900/20 dark:border-red-800
    {% elif execution_status == 'running' %}bg-blue-50 border-blue-200 dark:bg-blue-900/20 dark:border-blue-800
    {% else %}bg-gray-50 border-gray-200 dark:bg-gray-700/50 dark:border-gray-600{% endif %}">

    <div class="flex items-center justify-between mb-2">
      <div class="flex items-center gap-2">
        <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300">Execution</h3>
        {% if execution_status == 'pending' %}
        <span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-gray-200 text-gray-700 dark:bg-gray-600 dark:text-gray-300">Pending</span>
        {% elif execution_status == 'running' %}
        <span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-blue-200 text-blue-800 dark:bg-blue-800 dark:text-blue-200">Running</span>
        {% elif execution_status == 'complete' %}
        <span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-200 text-emerald-800 dark:bg-emerald-800 dark:text-emerald-200">Complete</span>
        {% elif execution_status == 'failed' %}
        <span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-200 text-red-800 dark:bg-red-800 dark:text-red-200">Failed</span>
        {% endif %}
      </div>

      {% if execution_status in ('failed',) %}
      <form method="POST" action="/{{ group }}/decisions/{{ slug }}/retry" class="inline">
        <button type="submit" class="px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors"
                onclick="return confirm('Re-execute this decision?')">
          Retry
        </button>
      </form>
      {% endif %}
    </div>

    {% if execution_summary %}
    <div class="prose dark:prose-invert prose-sm max-w-none">
      {{ execution_summary | render_md }}
    </div>
    {% endif %}

    {% if execution_status == 'running' %}
    <div class="mt-2 text-xs text-blue-600 dark:text-blue-400">
      Agent is working on this now. Refresh to check progress.
    </div>
    {% endif %}
  </div>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 4: Update decisions.html list page**

In `agency/templates/decisions.html`, line 22, replace:

```html
<span class="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800">{{ d.get("decision", "") }}</span>
```

with:

```html
{{ "decided" | status_badge }}
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add agency/app.py agency/templates/decision_detail.html agency/templates/decisions.html
git commit -m "feat: show answers on decision detail/list, simplify execution status display"
```

---

### Task 5: Update dashboard attention queue

Remove inline approve/defer/reject buttons, replace with "N questions" badge linking to proposal detail.

**Files:**
- Modify: `agency/templates/home.html:132-161`

- [ ] **Step 1: Replace inline action buttons with questions badge**

In `agency/templates/home.html`, replace lines 132-161 (the `{% for c in actionable_proposals %}` block) with:

```html
    {# ─ Proposals (high priority) ─ #}
    {% for c in actionable_proposals %}
    <a href="/{{ group }}/proposals/{{ c._slug }}" class="block border border-purple-200 dark:border-purple-800 rounded-md px-3 py-2.5 mb-2 bg-white dark:bg-gray-800 hover:border-purple-400 dark:hover:border-purple-600 transition-colors">
      <div class="flex items-start justify-between gap-2">
        <div class="min-w-0 flex-1">
          <span class="text-base font-medium text-gray-900 dark:text-gray-100 leading-tight">{{ c._title }}</span>
          <div class="flex flex-wrap items-center gap-1.5 mt-1">
            {{ c.get("origin_agent", "") | agent_badge }}
            {{ c.get("status", "") | status_badge }}
            {% if c.get("feedback_requested") %}
            <span class="text-sm text-gray-400 dark:text-gray-500 font-mono">{{ c.get("feedback_received", [])|length }}/{{ c.get("feedback_requested", [])|length }} feedback</span>
            {% endif %}
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          {% set q_count = c.get("questions", [])|length %}
          {% if q_count %}
          <span class="inline-block px-2.5 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-300">{{ q_count }} question{{ 's' if q_count != 1 else '' }}</span>
          {% endif %}
        </div>
      </div>
    </a>
    {% endfor %}
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add agency/templates/home.html
git commit -m "feat: replace inline approve/defer/reject with questions badge on dashboard"
```

---

### Task 6: Update CLI — replace approve/defer/reject with `decide`

Remove the three separate CLI commands and add a single interactive `decide` command that prompts for each question.

**Files:**
- Modify: `agency/cli.py:270-314` (remove _decide_proposal, cmd_approve, cmd_defer, cmd_reject)
- Modify: `agency/cli.py:358-362` (remove approve/defer/reject parser registration)
- Modify: `agency/cli.py` (add cmd_decide and its parser)
- Test: `tests/test_cli.py` (update if it tests approve/defer/reject)

- [ ] **Step 1: Check existing CLI tests**

Read `tests/test_cli.py` to see if approve/defer/reject commands are tested.

- [ ] **Step 2: Replace `_decide_proposal` and the three commands with `cmd_decide`**

In `agency/cli.py`, replace lines 270-314 with:

```python
def cmd_decide(args):
    """Interactively answer a proposal's questions."""
    import yaml

    g = _resolve_group(args)
    slug = args.slug
    proposals_dir = g["shared"] / "proposals"
    decisions_dir = g["shared"] / "decisions"

    path = proposals_dir / f"{slug}.md"
    if not path.exists():
        print(f"Error: Proposal '{slug}' not found.", file=sys.stderr)
        sys.exit(1)

    meta, body = parse_frontmatter(path.read_text())
    questions = meta.get("questions", [])
    if not questions:
        print("Error: Proposal has no questions.", file=sys.stderr)
        sys.exit(1)

    origin_agent = meta.get("origin_agent", "")
    print(f"\n{bold(slug)}\n")

    answers = {}
    for i, q in enumerate(questions, 1):
        print(f"  {cyan(str(i))}. {q['prompt']}")

        if q["type"] == "boolean":
            print(f"     {green('[a]')}pprove  {yellow('[d]')}efer  {red('[r]')}eject")
            while True:
                choice = input("     > ").strip().lower()
                if choice in ("a", "approve"):
                    answers[q["id"]] = "approved"
                    break
                elif choice in ("d", "defer"):
                    answers[q["id"]] = "deferred"
                    break
                elif choice in ("r", "reject"):
                    answers[q["id"]] = "rejected"
                    break
                print("     Invalid choice. Enter a/d/r.")

        elif q["type"] == "choice":
            options = q.get("options", [])
            for j, opt in enumerate(options, 1):
                print(f"     [{j}] {opt['label']}")
            if q.get("multi"):
                print("     (comma-separated numbers for multiple)")
                raw = input("     > ").strip()
                indices = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
                answers[q["id"]] = [options[idx - 1]["label"] for idx in indices if 1 <= idx <= len(options)]
            else:
                while True:
                    raw = input("     > ").strip()
                    if raw.isdigit() and 1 <= int(raw) <= len(options):
                        answers[q["id"]] = options[int(raw) - 1]["label"]
                        break
                    print(f"     Enter a number 1-{len(options)}.")

        elif q["type"] == "free-response":
            answers[q["id"]] = input("     > ").strip()

        print()

    # Create decision file
    today = datetime.now().strftime("%Y-%m-%d")
    dec_meta = {
        "proposal": f"{slug}.md",
        "decided_by": "cli",
        "date": today,
        "answers": answers,
        "execution_status": "pending",
    }
    frontmatter = yaml.dump(dec_meta, default_flow_style=False, sort_keys=False).strip()
    content = f"---\n{frontmatter}\n---\n"

    decisions_dir.mkdir(exist_ok=True)
    decision_path = decisions_dir / f"{slug}.md"
    decision_path.write_text(content)

    # Update proposal status
    update_frontmatter_field(path, "status", "decided")

    print(f"{green('✓')} Decision saved: shared/decisions/{slug}.md")
    for qid, ans in answers.items():
        if isinstance(ans, list):
            print(f"  {qid}: {', '.join(ans)}")
        else:
            print(f"  {qid}: {ans}")
```

- [ ] **Step 3: Update CLI parser registration**

In `agency/cli.py`, replace lines 358-362 (the approve/defer/reject for loop) with:

```python
    # decide
    p = sub.add_parser("decide", help="Answer a proposal's questions")
    p.add_argument("slug", help="Proposal slug")
    p.add_argument("--group", "-g")
```

- [ ] **Step 4: Update the command dispatch**

In the `main()` function, find where commands are dispatched and replace the `approve`/`defer`/`reject` entries with `decide`:

```python
    # Replace these three:
    # "approve": cmd_approve,
    # "defer": cmd_defer,
    # "reject": cmd_reject,
    # With:
    "decide": cmd_decide,
```

- [ ] **Step 5: Update `cmd_decisions` to show answers instead of old decision field**

In `agency/cli.py`, replace lines 228-244 (`cmd_decisions` function) with:

```python
def cmd_decisions(args):
    """List decisions."""
    g = _resolve_group(args)
    items = list_decisions(g)

    if getattr(args, "json", False):
        print(json.dumps([{"slug": i["_slug"], "title": i.get("_title", i["_slug"]), "answers": i.get("answers", {}), "date": str(i.get("date", ""))} for i in items], indent=2))
        return

    print(f"\n{bold('Decisions')} — {g['name']} ({len(items)} total)\n")
    for i in items:
        title_text = i.get("_title", i["_slug"])
        answers = i.get("answers", {})
        answer_count = len(answers)
        print(f"  {green('decided')}  {title_text[:60]}  {dim(str(i.get('date', '')))}  {dim(f'{answer_count} answer(s)')}")
    print()
```

- [ ] **Step 6: Update CLI tests if needed**

If `tests/test_cli.py` tests approve/defer/reject, update those tests to test `decide` instead.

- [ ] **Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add agency/cli.py tests/test_cli.py
git commit -m "feat: replace approve/defer/reject CLI commands with interactive 'decide'"
```

---

### Task 7: Update agent instructions

Update `_observation-system-steps.md` to teach agents the new proposal format with questions.

**Files:**
- Modify: `agents/shared/prompts/_observation-system-steps.md:52-100`

- [ ] **Step 1: Replace Step 5 and Step 6 in observation system steps**

In `agents/shared/prompts/_observation-system-steps.md`, replace lines 52-100 with:

```markdown
### 5. Promote to proposal (if warranted)
If you have 2+ connected observations (yours or linked from others) that converge on an
actionable issue, create a proposal file.

**Check first:** You may have at most 3 proposals in non-terminal status
(`investigating` or `feedback`). If you already have 3, complete or abandon one first.

Filename: `agents/shared/proposals/{YYYY-MM-DD}-{slug}.md`

Use this template:
```
---
origin_agent: {your-agent-name}
date: {YYYY-MM-DD}
status: investigating
observations:
  - {observation-filename-1}
  - {observation-filename-2}
feedback_requested: []
feedback_received: []
ttl_days: 14
questions:
  - id: {short_snake_case_id}
    type: {boolean|choice|free-response}
    prompt: "{question text}"
---

## Proposal: {title}

### Evidence
{Summarize the connected observations and why they converge}

### Investigation
{Your deeper analysis}

### Proposed Action
{What you recommend doing, with specifics}

### Agent Feedback
{Leave headings for each agent you'll request feedback from}
```

**Question types:**
- `boolean` — go/no-go decision. Answer will be `approved`, `deferred`, or `rejected`.
  Only needs `id`, `type`, and `prompt`.
- `choice` — discrete options you've identified. Add `options` (list of `{label: "..."}`)
  and `multi: false` (or `true` for multi-select).
- `free-response` — open-ended human input. Only needs `id`, `type`, and `prompt`.
  Include hints in the prompt text itself.

**Guidelines:**
- Every proposal must have at least one question.
- Use `boolean` for simple go/no-go. Use `choice` when you have specific options.
  Use `free-response` when you need open-ended input.
- Batch independent questions into one proposal. Don't create separate proposals for
  questions that can be answered together.
- Question `id` should be short, descriptive, snake_case (e.g., `approach`, `color_scheme`).

After writing the Investigation and Proposed Action, update `status` to `feedback`
and list the agents whose input you need in `feedback_requested`.

Update the linked observations' `status` to `connected` and set their `linked_proposal`.

### 6. Finalize proposed proposals
If a proposal you originated has `feedback_received` matching `feedback_requested`,
ensure your questions are well-formed and complete. Set `status: proposed`.
The human will then see your questions in the Agency dashboard and answer them.
```

- [ ] **Step 2: Commit**

```bash
git add agents/shared/prompts/_observation-system-steps.md
git commit -m "docs: update agent instructions for typed proposal questions"
```

---

### Task 8: Migrate existing proposals and decisions

Convert the 2 existing proposals and 2 existing decisions to the new format.

**Files:**
- Modify: `agents/shared/proposals/2026-03-22-pipeline-item-display-titles.md`
- Modify: `agents/shared/proposals/2026-03-22-ux-overhaul-strategy.md`
- Modify: `agents/shared/decisions/2026-03-22-pipeline-item-display-titles.md`
- Modify: `agents/shared/decisions/2026-03-22-ux-overhaul-strategy.md`

- [ ] **Step 1: Migrate pipeline-item-display-titles proposal**

In `agents/shared/proposals/2026-03-22-pipeline-item-display-titles.md`, change status from `approved` to `decided` and add a `questions` field:

```yaml
status: decided
questions:
  - id: approve
    type: boolean
    prompt: "Approve implementing display titles for pipeline items?"
```

- [ ] **Step 2: Migrate ux-overhaul-strategy proposal**

In `agents/shared/proposals/2026-03-22-ux-overhaul-strategy.md`, change status from `approved` to `decided` and add a `questions` field:

```yaml
status: decided
questions:
  - id: approve
    type: boolean
    prompt: "Approve the UX overhaul — wizard finale, CLI interface, and mission control dashboard?"
```

- [ ] **Step 3: Migrate pipeline-item-display-titles decision**

In `agents/shared/decisions/2026-03-22-pipeline-item-display-titles.md`, replace `decision: approved` with:

```yaml
answers:
  approve: approved
execution_status: complete
```

- [ ] **Step 4: Migrate ux-overhaul-strategy decision**

In `agents/shared/decisions/2026-03-22-ux-overhaul-strategy.md`, replace `decision: approved` with:

```yaml
answers:
  approve: approved
execution_status: complete
```

- [ ] **Step 5: Commit**

```bash
git add agents/shared/proposals/ agents/shared/decisions/
git commit -m "chore: migrate existing proposals and decisions to questions format"
```

---

### Task 9: Update dashboard test data and run full test suite

Update test files that reference old statuses, run full suite, and verify the app starts.

**Files:**
- Modify: `tests/test_dashboard.py:10` (decision test data)
- Modify: `tests/test_dashboard.py:39` (healthy flow test data)

- [ ] **Step 1: Update test_dashboard.py decision references**

In `tests/test_dashboard.py`, line 10, change:
```python
decisions = [{"decision": "approved"}, {"decision": "deferred"}]
```
to:
```python
decisions = [{"answers": {"approve": "approved"}}, {"answers": {"approach": "Option A"}}]
```

Line 39, change:
```python
decisions = [{"decision": "approved"}] * 2
```
to:
```python
decisions = [{"answers": {"approve": "approved"}}] * 2
```

(These fields aren't actually read by `build_pipeline_stats()` — it only counts totals — so any dict works. But keeping them realistic avoids confusion.)

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 3: Start the app and manually verify**

Run: `.venv/bin/python3 -m agency.app`

Verify:
1. Dashboard loads, proposals show question count badges
2. Clicking a proposal shows the questions form
3. The existing decided proposals show read-only answers
4. Decision detail pages show answers and execution status

- [ ] **Step 4: Restart the service**

Run: `systemctl --user restart agency.service`

- [ ] **Step 5: Commit any remaining fixes**

```bash
git add tests/
git commit -m "test: update dashboard tests for new decision format"
```

---

### Task 10: Update CLAUDE.md documentation

Update the root CLAUDE.md to reflect the new proposal/decision data model.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update Proposal Frontmatter section**

In `CLAUDE.md`, find the "Observation Frontmatter" / "Proposal Frontmatter" section and update the proposal example to include `questions` and change the status values. Update the Decision Frontmatter to show `answers` instead of `decision`.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for proposal questions data model"
```
