# Context-Aware Setup Team Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make first-run Agency setup synthesize an exact-size, project-grounded team whose identities and operating profiles incorporate the user's approved group choices.

**Architecture:** Strengthen the guided launch prompt, then make the package-owned `agency-setup` skill retain inspected project facts and prior answers as conversational context for team synthesis. The skill generates a complete exact-size draft, supports count changes with verbatim survivor preservation, and maps approved profiles only onto existing blueprint, prompt, group, and instance authority. The interactive session remains the context owner; Agency continues processing only the canonical config readiness signal and does not gain a second output model.

**Tech Stack:** Python 3.13, Markdown Agent Skills, FastAPI setup launch prompt, pytest text-contract tests, GitHub Copilot CLI live acceptance. No new dependency.

## Global Constraints

- Approved specification: `docs/superpowers/specs/2026-09-03-context-aware-setup-team-design.md`, committed in `deb8f0d` and clarified in `76f4751`.
- Work in `C:/Projekty/christag-agency/.worktrees/context-aware-setup-team` on `fix/context-aware-setup-team` until the integration task.
- Baseline: **1983 passed, 5 skipped, 0 failed** from the isolated worktree.
- Keep `schema_version: 5`; do not add configuration fields or a second team/output model.
- The browser and launcher still select the Agency data root and integration. The guided session still asks for the project workspace first.
- Before the first team draft: inspect the workspace read-only, summarize concrete project facts, approve group display name and stable ID, then approve an initial positive agent count.
- The first draft contains exactly the initial count. The count may change afterward.
- A selected survivor keeps its entire profile verbatim. Survivors may not outnumber the revised count.
- Multiple agents may share a broad role or blueprint only when the relevant responsibilities or reusable behavior justify it.
- Infer new-agent write access from approved implementation responsibilities; expose the exact workspace path and rationale. Never widen an existing agent's authority implicitly.
- Optional emoji, routines, schedules, memory, and channels may be explicitly absent when evidence does not support them.
- Team drafts, rationales, coverage maps, and survivor choices remain conversational state. Persist only existing schema, blueprint, and prompt fields after approval.
- No blueprint, prompt, derived storage, or config write occurs while drafting or revising the team.
- Preserve the data-root, grouped path approval, path safety, skill packaging/discovery, config validation, revision checking, atomic write, scheduler, and polling contracts.
- Application changes are limited to `agency/web/setup_flow.py`. Do not modify setup routes, templates, integration models, integration command builders, jobs, dashboard behavior, group administration, or runtime execution.
- `InteractiveSetupRequest` remains `(data_root, config_path, prompt)`. `InteractiveSetupResult` remains `(fallback_command)`. The canonical config remains the only setup completion output.
- Edit the canonical skill at `agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md`; do not create a mirrored copy or replace either repository symlink.
- Do not modify or stage `config.yaml`, `config.yaml.lock`, group state, logs, build output, or other runtime-local files.
- Do not run an individual `real_runtime` test. The complete suite owns its configured environment-dependent coverage.
- Use Conventional Commits with lowercase imperative subjects no longer than 72 characters.
- After implementation, invoke `superpowers:requesting-code-review`. When review and verification are green, follow `AGENTS.md`: fast-forward `master`, re-run the suite, push `master` and the feature branch, remove the worktree, and retain the branch.

## File Structure

### Production And Guidance

- `agency/web/setup_flow.py` - reinforce the context-aware team handoff in the existing guided launch prompt; no signature or return-type change.
- `agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md` - own project context, exact-size team synthesis, operating profiles, revision/survivor rules, authority mapping, and consolidated approval.
- `kb/setup-skill.md` - explain the user-facing context-aware team workflow without duplicating the complete skill.

### Focused Tests

- `tests/test_setup_flow.py` - pin the concise guided launch handoff and canonical-output boundary.
- `tests/test_server.py` - prove the actual setup launch request carries the strengthened prompt and still writes no config before the session completes.
- `tests/test_agency_setup_skill.py` - pin context ordering, sparse-evidence behavior, exact-size profiles, identity/permission rules, count revision, survivor semantics, review mapping, and guide parity.

### Explicitly Unchanged Application Boundaries

- `agency/integrations/models.py` - no interactive request/result payload changes.
- `agency/integrations/agency/copilot.py` - no command, skill-discovery, or terminal behavior changes.
- `agency/web/routes/admin_groups.py` - no launch result or config-readiness processing changes.
- `agency/templates/setup.html` - no browser workflow changes.
- `agency/configuration/`, `agency/jobs/`, and other runtime modules - no changes.

## Shared Contracts

No new Python type or persisted data shape is introduced.

```text
# agency/web/setup_flow.py - signature remains unchanged
def build_setup_prompt(
    data_root: Path,
    config_path: Path,
    *,
    selected_integration: str,
) -> str
```

The skill uses this conversational profile shape for both draft and final
review; it is not YAML and is never persisted wholesale:

```markdown
### {identity.display_name} (`{name}`)

- Blueprint / broad role: {blueprint} / {role}
- Title / emoji: {identity.title} / {identity.emoji or "None proposed"}
- Mission: {mission}
- Responsibilities and ownership: {distinct responsibilities}
- Handoffs: {other proposed agents and exchange points}
- Rationale: {project facts and prior answers; labeled assumptions}
- Integration / workspace: {integration} / {exact workspace path and use}
- Permissions: {exact path and tools, with write rationale when present}
- Routines and prompts: {grounded assignments or "None proposed"}
- Schedules: {supported cadence or "None proposed"}
- Memory and channels: {grounded selectors/channels or "None proposed"}
- Assumptions: {remaining assumptions or "None"}
```

After approval, the skill maps this review shape only to existing surfaces:

```text
instance config: name, blueprint, integration, identity, runtime permissions,
                 prompts, routines, default memory
group config:    workspace, shared channels, group defaults
blueprint:       reusable mission, responsibilities, boundaries, working method
task prompts:    project-specific task instructions
conversation:    rationale, coverage analysis, handoff explanation
```

---

### Task 1: Strengthen The Guided Setup Handoff

**Files:**
- Modify: `agency/web/setup_flow.py`
- Modify: `tests/test_setup_flow.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: existing `build_setup_prompt(data_root, config_path, *, selected_integration) -> str` and `InteractiveSetupRequest.prompt`.
- Produces: the same prompt API with an explicit context-aware team contract; no request/result/model/output-processing changes.

- [ ] **Step 1: Write the failing prompt contract test**

Add to `tests/test_setup_flow.py` after the guided data-root test:

```python
def test_build_setup_prompt_hands_context_aware_team_synthesis_to_skill(
    tmp_path: Path,
):
    data_root = tmp_path / "Agency"
    data_root.mkdir()

    prompt = build_setup_prompt(
        data_root,
        tmp_path / "config.yaml",
        selected_integration="copilot",
    )

    for phrase in (
        "carry inspected project facts and every approved setup answer forward",
        "Approve the group display name and stable ID, then an initial positive agent count",
        "first complete team draft with exactly that many profiles",
        "Do not use a fixed role slate",
        "Keep team drafts and revisions inside this interactive conversation",
        "The canonical config remains the only setup completion output",
        "do not emit or request a second application-side team payload",
    ):
        assert phrase in prompt

    workspace = prompt.index(
        "Ask for the first group project workspace as the first user-facing question."
    )
    inspection = prompt.index("inspect that project read-only")
    synthesis = prompt.index(
        "carry inspected project facts and every approved setup answer forward"
    )
    assert workspace < inspection < synthesis
```

- [ ] **Step 2: Pin the handoff at the actual route boundary**

In `tests/test_server.py`, extend
`test_setup_launch_does_not_write_config` after the existing prompt assertions:

```python
    assert (
        "carry inspected project facts and every approved setup answer forward"
        in integration.requests[0].prompt
    )
    assert (
        "The canonical config remains the only setup completion output"
        in integration.requests[0].prompt
    )
```

Keep the existing assertions that `config_path` does not exist, the page enters
the waiting state, and no fallback request is constructed. Those assertions are
the setup-output regression boundary.

- [ ] **Step 3: Run the focused tests to observe RED**

Run:

```powershell
python -m pytest tests/test_setup_flow.py::test_build_setup_prompt_hands_context_aware_team_synthesis_to_skill tests/test_server.py::test_setup_launch_does_not_write_config -q
```

Expected: both tests fail because the existing prompt names project inspection
but does not carry the context-aware team, exact-size draft, or canonical-output
instructions.

- [ ] **Step 4: Add the minimal launch-prompt handoff**

In `build_setup_prompt()`, immediately after the sentence ending
`before discussing the team.`, add these exact string fragments before the
existing project/data-root authority sentence:

```python
        "While planning the initial team, carry inspected project facts and every "
        "approved setup answer forward. Approve the group display name and stable "
        "ID, then an initial positive agent count, before generating the first "
        "complete team draft with exactly that many profiles. Do not use a fixed "
        "role slate. Keep team drafts and revisions inside this interactive "
        "conversation. The canonical config remains the only setup completion "
        "output; do not emit or request a second application-side team payload. "
```

Do not modify the function signature, launch mode/root/config/integration lines,
path derivation instructions, validation instructions, or atomic-write contract.

- [ ] **Step 5: Run launch-focused regressions**

Run:

```powershell
python -m pytest tests/test_setup_flow.py tests/test_server.py -q
```

Expected: all tests pass. Existing setup-status, path preparation, fallback,
polling, and no-write behavior remains green.

- [ ] **Step 6: Verify the output-processing model stayed unchanged**

Run:

```powershell
python -c "from dataclasses import fields; from agency.integrations.models import InteractiveSetupRequest, InteractiveSetupResult; assert tuple(f.name for f in fields(InteractiveSetupRequest)) == ('data_root', 'config_path', 'prompt'); assert tuple(f.name for f in fields(InteractiveSetupResult)) == ('fallback_command',); print('interactive setup boundary unchanged')"
```

Expected: `interactive setup boundary unchanged`. Do not add this as a new
application API test; the feature intentionally does not alter those models.

- [ ] **Step 7: Commit the launch handoff**

Run:

```powershell
git add agency/web/setup_flow.py tests/test_setup_flow.py tests/test_server.py
git commit -m "fix(setup): hand off contextual team synthesis"
```

---

### Task 2: Implement Context-Aware Team Synthesis In The Skill

**Files:**
- Modify: `agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md`
- Modify: `tests/test_agency_setup_skill.py`

**Interfaces:**
- Consumes: guided prompt markers and the selected integration/workspace already supplied to the skill; existing schema-version-5 config, blueprint, prompt, permission, routine, workspace, and memory contracts.
- Produces: a conversational working context and operating-profile review flow; no Python API, config field, scratch file, or second output payload.

- [ ] **Step 1: Replace the obsolete count/role test with ordering tests**

Delete `test_setup_requires_user_selected_agent_count_and_roles()`. In
`test_guided_setup_asks_for_workspace_before_inspection_and_team_questions()`,
replace:

```python
    team = normalized.index("how many agents to create")
```

with:

```python
    team = normalized.index(
        "ask the user to approve the group display name and stable group ID"
    )
```

Then add:

```python
def test_setup_summarizes_project_before_group_count_and_first_draft():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    inspection = normalized.index(
        "After the project workspace is selected, inspect that workspace read-only."
    )
    summary = normalized.index(
        "Before team design, summarize this working context in user-facing prose."
    )
    group = normalized.index(
        "ask the user to approve the group display name and stable group ID"
    )
    count = normalized.index("ask for an initial positive integer agent count")
    draft = normalized.index(
        "Generate the first complete team draft with exactly that many profiles."
    )

    assert inspection < summary < group < count < draft
    assert "propose three to five distinct roles" not in normalized.lower()
    assert "which proposed roles to create now" not in normalized.lower()


def test_setup_uses_one_priority_question_only_when_project_evidence_is_sparse():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "If the workspace cannot be inspected" in normalized
    assert "return to project workspace selection before proposing a team" in normalized
    assert (
        "ask exactly one focused question about near-term priorities or current pain points"
        in normalized
    )
    assert "incorporate that answer into the working context" in normalized
    assert "Do not fall back to a stock team" in normalized
```

- [ ] **Step 2: Add failing complete-profile and grounding tests**

Add:

```python
def test_setup_drafts_exact_count_complete_grounded_operating_profiles():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    team = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]

    for marker in (
        "### {identity.display_name} (`{name}`)",
        "Blueprint / broad role",
        "Title / emoji",
        "Mission",
        "Responsibilities and ownership",
        "Handoffs",
        "Rationale",
        "Integration / workspace",
        "Permissions",
        "Routines and prompts",
        "Schedules",
        "Memory and channels",
        "Assumptions",
    ):
        assert marker in team

    normalized = " ".join(team.split())
    assert "exactly the approved initial count" in normalized
    assert "inspected project facts" in normalized
    assert "approved group concept" in normalized
    assert "selected integration" in normalized
    assert "Label unsupported assumptions" in normalized
    assert "None proposed" in team


def test_setup_adapts_identity_and_distinguishes_shared_roles():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "When the approved group concept clearly establishes a naming theme",
        "use domain-specific functional identities",
        "do not force a theme",
        "Agents may share a broad role",
        "responsibilities, ownership boundaries, or routines differ materially",
        "share a blueprint only when their reusable behavior and working method are genuinely the same",
    ):
        assert phrase in normalized
```

- [ ] **Step 3: Add failing count-revision and survivor tests**

Add:

```python
def test_setup_revises_count_with_verbatim_survivors_and_coherent_resynthesis():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "The user may accept the first draft, edit profiles, or replace the agent count",
        "ask which existing profiles must survive unchanged",
        "survivors outnumber the revised count",
        "reduce the survivor set or increase the count",
        "Preserve every selected survivor profile verbatim",
        "Synthesize every remaining slot from the complete working context",
        "Do not mechanically truncate the previous draft or append generic roles",
        "show the uncovered need instead of rewriting a survivor",
        "becomes the final team without a redundant second proposal",
    ):
        assert phrase in normalized


def test_setup_requires_one_consolidated_team_review_and_consistency_pass():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "Present all profiles together for one consolidated team review",
        "major project needs and their owning agents",
        "intentional shared roles",
        "handoffs and collaboration paths",
        "uncovered needs and explicit assumptions",
        "every write-enabled agent and exact writable path",
        "routine cadence and memory or channel relationships",
        "current exact agent count and preserved survivors",
        "Re-run the team-level consistency check after every count or profile change",
    ):
        assert phrase in normalized
```

- [ ] **Step 4: Replace the old permission test and add authority mapping**

Replace
`test_registration_derives_write_eligibility_from_workspace_path_rule()` with:

```python
def test_setup_derives_new_agent_write_access_from_approved_responsibilities():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "Write authority is expressed through the workspace path rule" in normalized
    assert "read and search are the baseline" in normalized
    assert "Derive write access from approved implementation responsibilities" in normalized
    assert "Multiple new agents may receive write" in normalized
    assert "exact project workspace path and explain why write is required" in normalized
    assert "Team approval includes approval of every displayed permission grant" in normalized
    assert "Never infer write authority for an existing agent" in normalized
    assert "Exactly one builder normally receives write capability" not in skill
```

Then add:

```python
def test_setup_maps_review_profiles_only_to_existing_authority_surfaces():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    for phrase in (
        "The operating profile is a conversational review model",
        "existing instance config fields",
        "existing group and memory config fields",
        "reusable behavior becomes blueprint instructions",
        "project-specific task instructions become scoped prompt documents",
        "rationale, coverage analysis, and handoff explanation remain conversational",
        "Do not persist new `mission`, `rationale`, `ownership`, `handoffs`, or `coverage` keys",
        "Keep team drafts, survivor choices, and the working context in this conversation only",
    ):
        assert phrase in normalized
```

- [ ] **Step 5: Run the new tests to observe RED**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py -q
```

Expected: the new tests fail because Section 2 still proposes a fixed
three-to-five role slate before collecting the count and does not define
complete profiles, survivor semantics, or contextual authority mapping.

- [ ] **Step 6: Add the working-context contract after project inspection**

In Section 1 of the canonical `SKILL.md`, insert the following immediately after
the paragraph ending `before this inspection completes.` and before path
derivation:

```markdown
Build and retain a working context from inspected facts: the project's domain
and stated purpose; languages, frameworks, and architectural boundaries;
repository maturity, source and test organization, and documentation quality;
build, deployment, CI, release, and operational signals; apparent work streams,
risks, maintenance pressure, and missing capabilities; the selected integration;
and the exact project workspace. Include any user-stated near-term priorities.
Keep this context in the conversation only; do not write a digest or scratch
file.

Before team design, summarize this working context in user-facing prose. Name
concrete project characteristics and distinguish inspected facts from
assumptions; file-path citations are not required. Do not invent technologies,
delivery cadence, business goals, or operational needs. If the workspace cannot
be inspected, identify what was inaccessible and return to project workspace
selection before proposing a team. If the evidence is too sparse to support a
grounded proposal, ask exactly one focused question about near-term priorities
or current pain points, incorporate that answer into the working context, and
then continue. Do not fall back to a stock team.
```

- [ ] **Step 7: Replace Section 2's generic team paragraphs**

Rename `## 2. Plan The Team And Resolve Agency` to
`## 2. Synthesize And Approve The Team`.

Replace the first two paragraphs of that section, through the sentence ending
`different registered integration.`, with:

````markdown
After inspection and any sparse-evidence clarification, ask the user to approve
the group display name and stable group ID. Require a lowercase stable ID using
only letters, digits, and single hyphen separators. Then ask for an initial
positive integer agent count. Do not generate a team draft until the group name,
ID, and count are approved. Derive `groups.<group-id>.path` after the ID is
approved.

Generate the first complete team draft with exactly that many profiles. The
draft must contain exactly the approved initial count. Synthesize it from inspected project facts,
the approved group concept, the requested count, any priority answer, the
selected integration, and the exact workspace. Do not offer a fixed candidate
slate or ask which generic roles to instantiate. Label unsupported assumptions.
When the launch prompt contains `Selected integration:`, use that registered
integration for `group.default_integration` and the initial agent instances
unless the user explicitly approves a different registered integration.

Use this complete operating-profile format for every proposed agent:

```text
### {identity.display_name} (`{name}`)

- Blueprint / broad role: {blueprint} / {role}
- Title / emoji: {identity.title} / {identity.emoji or "None proposed"}
- Mission: {mission}
- Responsibilities and ownership: {distinct responsibilities}
- Handoffs: {other proposed agents and exchange points}
- Rationale: {project facts and prior answers; labeled assumptions}
- Integration / workspace: {integration} / {exact workspace path and use}
- Permissions: {exact path and tools; explain write when present}
- Routines and prompts: {grounded assignments or "None proposed"}
- Schedules: {supported cadence or "None proposed"}
- Memory and channels: {grounded selectors/channels or "None proposed"}
- Assumptions: {remaining assumptions or "None"}
```

The rationale names which inspected project characteristics and prior answers
justify the profile; a generic statement that an agent helps with the project is
not sufficient. `None proposed` is valid for optional emoji, routines,
schedules, memory, and channels. Do not invent recurring work, cadence, or
shared memory merely to populate the profile.

When the approved group concept clearly establishes a naming theme, adapt
display names, titles, and optional emoji coherently without obscuring each
agent's function. When the theme is ambiguous, use domain-specific functional
identities; do not force a theme or invent unexplained personas. Stable instance
and blueprint slugs remain valid and unique.

Agents may share a broad role when their responsibilities, ownership boundaries,
or routines differ materially. They may share a blueprint only when their
reusable behavior and working method are genuinely the same. Different reusable
behavior requires distinct blueprints even when agents share a broad role.

For proposed new agents, read and search are the baseline. Derive write access
from approved implementation responsibilities. Multiple new agents may receive
write when their distinct responsibilities require it. For every write-enabled
profile, show the exact project workspace path and explain why write is required.
Team approval includes approval of every displayed permission grant. Never infer
write authority for an existing agent; keep an ambiguous new-agent grant visible
as an assumption for targeted review.

Present all profiles together for one consolidated team review. Follow them with
a coverage summary naming major project needs and their owning agents;
intentional shared roles and how they differ; handoffs and collaboration paths;
uncovered needs and explicit assumptions; every write-enabled agent and exact
writable path; routine cadence and memory or channel relationships; and the
current exact agent count and preserved survivors. Allow targeted edits without
forcing agent-by-agent approval.

The user may accept the first draft, edit profiles, or replace the agent count.
When the count changes, ask which existing profiles must survive unchanged. If
the selected survivors outnumber the revised count, require the user to reduce
the survivor set or increase the count. Preserve every selected survivor profile
verbatim, including identity, mission, responsibilities, permissions, routines,
schedules, workspace use, and memory. Synthesize every remaining slot from the
complete working context and current team coverage gaps. Do not mechanically
truncate the previous draft or append generic roles.

If survivors consume all slots while leaving an important need uncovered, show
the uncovered need instead of rewriting a survivor. Let the user edit or release
a survivor, change the count, or approve the documented gap. Re-run the
team-level consistency check after every count or profile change: exact count,
stable names, coverage, overlaps, handoffs, permissions, cadence, memory, and
assumptions. If the user accepts the first draft without changing count or
profiles, it becomes the final team without a redundant second proposal. Obtain
one consolidated team approval before continuing to storage-path approval.

The operating profile is a conversational review model. After approval, map
stable name, identity, integration, runtime permissions, routines, default
memory, and prompt registrations to existing instance config fields; map
workspace and shared-channel choices to existing group and memory config fields;
reusable behavior becomes blueprint instructions; and project-specific task
instructions become scoped prompt documents. Proposal rationale, coverage
analysis, and handoff explanation remain conversational unless an approved
detail belongs in one of those existing surfaces. Do not persist new `mission`,
`rationale`, `ownership`, `handoffs`, or `coverage` keys. Keep team drafts,
survivor choices, and the working context in this conversation only.
````

In the following storage paragraph, remove the now-duplicate sentence
`After the group ID is approved, derive groups.<group-id>.path.` and retain the
single `Customize the derived storage paths?` question unchanged.

- [ ] **Step 8: Align Section 3 and Section 4 with the approved profile**

In Section 3, after `Blueprint files contain reusable instructions only.`, add:

```markdown
Translate only behavior that remains reusable across projects into the
blueprint mission, responsibilities, boundaries, and working method. Put
project-specific task instructions in scoped prompt documents. Do not copy the
conversational rationale, coverage map, or project-specific ownership text into
blueprint source merely because it appeared in the approved profile.
```

Replace Section 4's write-authority paragraph with:

```markdown
Write authority is expressed through the workspace path rule. For each new
agent whose approved implementation responsibilities require write access,
include `write` in the `tools` list on a rule whose `path` is the group's exact
`workspace_path`; multiple new agents may receive write when their distinct
responsibilities require it. An agent may execute decisions only when its
effective policy grants `write` on that exact path, not a subdirectory. There is
no `capabilities` key. Never infer write authority for an existing agent. If a
new agent's approved responsibilities do not clearly require write, leave it
read/search-only or return the grant to targeted team review.
```

Retain the existing schema migration explanation immediately after this
paragraph.

- [ ] **Step 9: Run skill and package regressions**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py tests/test_setup_skill_e2e.py tests/test_setup_assets.py tests/test_surface_contracts.py -q
```

Expected: all tests pass. The wheel test proves the package-owned skill bytes
are shipped, and existing path, prompt, scheduler, schema, and atomic-write
contracts remain green.

- [ ] **Step 10: Commit the canonical skill behavior**

Run:

```powershell
git add agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md tests/test_agency_setup_skill.py
git commit -m "fix(agency-setup): synthesize contextual teams"
```

---

### Task 3: Align The Setup Guide With Context-Aware Team Review

**Files:**
- Modify: `kb/setup-skill.md`
- Modify: `tests/test_agency_setup_skill.py`

**Interfaces:**
- Consumes: the canonical skill behavior from Task 2.
- Produces: concise active guidance for initial count, exact-size draft, count revision, survivor preservation, complete operating profiles, and contextual permissions.

- [ ] **Step 1: Write the failing guide contract test**

Add to `tests/test_agency_setup_skill.py`:

```python
def test_setup_guide_describes_context_aware_team_synthesis():
    guide = SETUP_KB_PATH.read_text(encoding="utf-8")
    normalized = " ".join(guide.split())

    for phrase in (
        "summarizes concrete project facts",
        "approves the group display name and stable ID",
        "initial positive agent count",
        "exactly that many complete operating profiles",
        "may change the count after reviewing the draft",
        "selected survivor profiles remain unchanged",
        "responsibilities and operating profiles remain materially distinct",
        "Write access follows approved implementation responsibilities",
        "one consolidated team review",
        "rationale and coverage summary remain conversational",
    ):
        assert phrase in normalized

    assert "Proposes reusable roles and asks how many agents" not in guide
```

- [ ] **Step 2: Run the guide test to observe RED**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py::test_setup_guide_describes_context_aware_team_synthesis -q
```

Expected: FAIL because the guide still describes proposing reusable roles
before asking the count and does not explain survivor or complete-profile review.

- [ ] **Step 3: Add a concise context-aware team section**

Insert this section in `kb/setup-skill.md` after `## Agency Data Root` and before
`## Install`:

```markdown
## Context-Aware Team Design

After workspace selection, setup inspects the project read-only and summarizes
concrete project facts before team design. If those facts are too sparse for a
grounded proposal, it asks one focused priorities question. It then approves the
group display name and stable ID, asks for an initial positive agent count, and
drafts exactly that many complete operating profiles.

Each profile combines identity, mission, distinct responsibilities, handoffs,
project and prior-answer rationale, integration and workspace use, permissions,
routines and prompts, supported schedules, memory or channels, and explicit
assumptions. Optional operating choices may be `None proposed`. A clear group
theme may shape display identities; otherwise setup uses functional names.
Agents may share a broad role or blueprint only when their responsibilities and
operating profiles remain materially distinct. Write access follows approved
implementation responsibilities and is shown on the exact project workspace for
approval.

The user reviews the draft in one consolidated team review and may change the
count after reviewing the draft. When the count changes, selected survivor
profiles remain unchanged and every other slot is synthesized again from the
project, group, priorities, and current coverage gaps. Setup shows coverage,
overlaps, handoffs, permissions, cadence, memory, and assumptions before final
team approval. The rationale and coverage summary remain conversational; only
existing config, blueprint, and prompt fields are written after approval.
```

- [ ] **Step 4: Replace the stale Run sequence**

Replace steps 2-5 in the guide's `## Run` numbered list with:

```markdown
2. Inspects project instructions, architecture, source, tests, deployment, and
   available integrations read-only, then summarizes project facts and asks one
   priorities question only when evidence is sparse.
3. Approves the group name and stable ID, asks for the initial count, and drafts
   exactly that many context-aware operating profiles for consolidated review.
4. Accepts targeted edits or a revised count, preserves selected full survivor
   profiles, resynthesizes remaining slots, and approves team coverage,
   permissions, routines, cadence, memory, and assumptions.
5. Derives `groups/<group-id>`, offers one optional grouped path override, and
   obtains the consolidated storage-path approval.
```

Leave the remaining config resolution, blueprint writing, registration,
validation, and result sections unchanged.

- [ ] **Step 5: Run guide and active-documentation regressions**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py tests/test_surface_contracts.py tests/test_repository_boundaries.py -q
```

Expected: all tests pass; no stale role-before-count wording remains in the
active setup guide and no repository prose boundary is violated.

- [ ] **Step 6: Commit the guide alignment**

Run:

```powershell
git add kb/setup-skill.md tests/test_agency_setup_skill.py
git commit -m "docs(setup): explain contextual team synthesis"
```

---

### Task 4: Verify And Review The Complete Feature

**Files:**
- Verify: every file changed in Tasks 1-3
- Preserve unchanged: all application files outside `agency/web/setup_flow.py`
- Do not create tracked live-acceptance artifacts

**Interfaces:**
- Consumes: the approved design, plan, and all implementation commits.
- Produces: a clean, reviewed feature tip with focused, full-suite, wheel, launch-boundary, and live-session evidence.

- [ ] **Step 1: Enforce the changed-file boundary**

Run from the feature worktree:

```powershell
$allowed = @('agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md','agency/web/setup_flow.py','docs/superpowers/plans/2026-09-03-context-aware-setup-team.md','docs/superpowers/specs/2026-09-03-context-aware-setup-team-design.md','kb/setup-skill.md','tests/test_agency_setup_skill.py','tests/test_server.py','tests/test_setup_flow.py'); $changed = @(git diff --name-only master...HEAD); $unexpected = @($changed | Where-Object { $_ -notin $allowed }); if ($unexpected) { throw "Unexpected changed files: $($unexpected -join ', ')" }; $changed
```

Expected: only the eight listed paths are printed. In particular, no route,
template, integration, configuration, job, or runtime file appears.

- [ ] **Step 2: Check formatting and stale team language**

Run:

```powershell
git diff --check master...HEAD
git grep -n -i -E "propose three to five distinct roles|which proposed roles to create now|exactly one builder normally receives write" -- agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md kb/setup-skill.md
```

Expected: both commands emit no findings; the second exits 1 because all three
superseded instructions are absent.

- [ ] **Step 3: Run the complete focused regression slice**

Run:

```powershell
python -m pytest tests/test_setup_flow.py tests/test_server.py tests/test_agency_setup_skill.py tests/test_setup_skill_e2e.py tests/test_setup_assets.py tests/test_surface_contracts.py tests/test_repository_boundaries.py -q
```

Expected: every focused test passes with only pre-existing environment-dependent
skips, if any.

- [ ] **Step 4: Verify unchanged launch/output data models**

Run:

```powershell
python -c "from dataclasses import fields; from agency.integrations.models import InteractiveSetupRequest, InteractiveSetupResult; assert tuple(f.name for f in fields(InteractiveSetupRequest)) == ('data_root', 'config_path', 'prompt'); assert tuple(f.name for f in fields(InteractiveSetupResult)) == ('fallback_command',); print('interactive setup boundary unchanged')"
git diff --quiet master...HEAD -- agency/integrations/models.py agency/integrations/agency/copilot.py agency/web/routes/admin_groups.py agency/templates/setup.html agency/configuration agency/jobs
```

Expected: the Python command prints `interactive setup boundary unchanged`; Git
exits 0 with no output.

- [ ] **Step 5: Build and inspect the package-owned skill**

Run:

```powershell
$wheelDir = Join-Path $env:TEMP 'agency-context-team-wheel'; Remove-Item -Recurse -Force $wheelDir -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Path $wheelDir | Out-Null; python -m pip wheel --disable-pip-version-check --no-deps --wheel-dir $wheelDir .; python -c "from pathlib import Path; from zipfile import ZipFile; wheel=next(Path(r'$wheelDir').glob('christag_agency-*.whl')); archive=ZipFile(wheel); skill='agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md'; assert skill in archive.namelist(); text=archive.read(skill).decode('utf-8'); assert 'Generate the first complete team draft with exactly that many profiles.' in text; assert 'Preserve every selected survivor profile verbatim' in text; print(skill)"; Remove-Item -Recurse -Force $wheelDir
```

Expected: wheel build succeeds and prints the canonical packaged `SKILL.md`
path. The temporary wheel directory is removed.

- [ ] **Step 6: Run the complete Python suite**

Run with no concurrent source edits:

```powershell
python -m pytest tests/ -q
```

Expected: all tests pass. Record exact pass and skip counts.

- [ ] **Step 7: Perform the live context-aware team acceptance**

Use a disposable root outside the repository and launch one real Copilot setup
session through the production command builder. Run the command with
`run_in_terminal` in async mode so prompts can be answered one at a time:

```powershell
$liveRoot = Join-Path $env:TEMP 'agency-context-team-live'; $dataRoot = Join-Path $liveRoot 'Agency'; $configPath = Join-Path $liveRoot 'config.yaml'; Remove-Item -Recurse -Force $liveRoot -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null; $projectRoot = (Get-Location).Path; python -c "import subprocess; from pathlib import Path; from agency.integrations.agency.copilot import CopilotIntegration; from agency.integrations.models import InteractiveSetupRequest; from agency.web.setup_flow import build_setup_prompt; root=Path(r'$dataRoot').resolve(strict=True); config=Path(r'$configPath').resolve(); prompt=build_setup_prompt(root, config, selected_integration='copilot'); request=InteractiveSetupRequest(data_root=root, config_path=config, prompt=prompt); command=tuple(CopilotIntegration()._interactive_setup_command(request)); raise SystemExit(subprocess.call(command, cwd=root))"
```

Answer the setup questions one prompt at a time with these exact inputs:

```text
Project workspace: <the feature worktree's absolute path from $projectRoot>
Group display name: Mentat Council
Stable group ID: mentat-council
Initial agent count: 3
```

This repository has sufficient architecture, tests, deployment, and project
instructions, so an unsolicited sparse-evidence priorities question is a failed
acceptance signal. Stop the Copilot session immediately after it presents the
first team draft. Do not approve the draft, paths, files, config, dispatcher, or
any filesystem creation.

Review the transcript and record evidence that:

```text
- project summary names concrete Agency characteristics such as FastAPI/Jinja2,
  pytest coverage, filesystem-backed authority, or durable jobs;
- group name/ID and count were collected before the draft;
- exactly 3 complete profiles are present;
- identities coherently acknowledge "Mentat Council" without hiding function;
- each profile has mission, responsibilities, handoffs, rationale,
  integration/workspace, permissions, routines/prompts, schedules,
  memory/channels, and assumptions (including explicit None proposed values);
- rationale uses project facts and prior answers rather than stock prose;
- write grants, if any, follow implementation responsibilities and name the
  exact workspace path;
- one team coverage summary names ownership, overlaps, handoffs, permissions,
  cadence/memory, count, and assumptions;
- no generic fixed candidate slate is offered.
```

After killing the async terminal, verify no pre-approval output was written and
clean up:

```powershell
$liveRoot = Join-Path $env:TEMP 'agency-context-team-live'; $dataRoot = Join-Path $liveRoot 'Agency'; $configPath = Join-Path $liveRoot 'config.yaml'; $forbidden = 'agent-library','compiled-agents','memory','prompts','groups'; $present = @($forbidden | Where-Object { Test-Path (Join-Path $dataRoot $_) }); if (Test-Path $configPath) { $present += 'config.yaml' }; if ($present) { throw "Pre-approval output exists: $($present -join ', ')" }; Remove-Item -Recurse -Force $liveRoot
```

Expected: no forbidden path exists. This one human-reviewed live check may
consume a Copilot request. If Copilot is unavailable or the transcript fails an
acceptance point, stop before integration and report the blocker rather than
substituting static prompt inspection.

- [ ] **Step 8: Perform the required whole-branch review**

Invoke `superpowers:requesting-code-review` with:

```text
Base: 76f4751 (approved specification revision)
Head: current feature tip
Spec: docs/superpowers/specs/2026-09-03-context-aware-setup-team-design.md
```

Require review of context ordering, sparse-evidence branching, exact-size
profiles, adaptive identity, shared-role distinction, responsibility-derived
permissions, optional operating choices, survivor preservation, count revision,
team coverage, authority mapping, launch/output scope, stale guide text, and
test strength.

- [ ] **Step 9: Resolve review findings and repeat evidence**

For each Critical or Important finding, write the smallest failing regression
test, observe RED, apply the focused correction, rerun the covering tests, and
commit separately. Repeat Steps 1-8 after corrections. If review is clean, do
not create a review-only commit.

- [ ] **Step 10: Confirm the feature worktree is clean**

Run:

```powershell
git status --short --branch
git log --oneline --decorate -8
```

Expected: no working-tree changes; the specification and implementation plan
commits precede the three focused implementation commits.

---

### Task 5: Fast-Forward, Verify, Push, And Clean Up

**Files:**
- Integrate: reviewed `fix/context-aware-setup-team` into `master`
- Preserve: feature branch `fix/context-aware-setup-team`
- Remove after verification: `.worktrees/context-aware-setup-team`

**Interfaces:**
- Consumes: a clean reviewed feature tip with focused, wheel, full-suite, and live-session evidence.
- Produces: fast-forwarded and pushed `master`, pushed retained feature branch, restored unrelated main-checkout changes, and no completed worktree.

- [ ] **Step 1: Preserve the review record and inspect integration preconditions**

Before removing the worktree, copy the plan-scoped SDD ledger/report archive to
a temporary path if subagent-driven execution created one. Stop every process
whose cwd is inside the feature worktree.

From `C:/Projekty/christag-agency`, run:

```powershell
git status --short --branch
git fetch origin
git rev-parse master
git rev-parse origin/master
git rev-parse fix/context-aware-setup-team
git merge-base --is-ancestor master fix/context-aware-setup-team
git merge-base --is-ancestor origin/master fix/context-aware-setup-team
```

If the main checkout has user changes, record and stash them with untracked files
before integration; restore them afterward. If remote `master` advanced,
fast-forward local `master`. If current `master` is not an ancestor of the
feature tip, rebase the feature branch onto `master` in its worktree and repeat
Task 4's focused and full verification. Never discard user changes or force a
push.

Use these exact commands only when their corresponding condition applies:

```powershell
# Main checkout with user changes:
git stash push --include-untracked -m "pre-context-aware-setup-team-integration"

# Main checkout when origin/master is strictly ahead:
git merge --ff-only origin/master

# Feature worktree when master is not its ancestor:
git rebase master
```

- [ ] **Step 2: Fast-forward master without a merge commit**

Run from the main checkout:

```powershell
git merge --ff-only fix/context-aware-setup-team
```

Expected: `master` moves directly to the reviewed feature tip.

- [ ] **Step 3: Re-run the complete suite on master**

Run:

```powershell
python -m pytest tests/ -q
```

Expected: all tests pass with the same environment-dependent skip count as the
reviewed feature tip.

- [ ] **Step 4: Push both required refs**

Run only after the master suite is green:

```powershell
git push origin master fix/context-aware-setup-team
```

Expected: both remote refs point to the reviewed feature tip. Do not force-push.

- [ ] **Step 5: Restore user changes and remove the completed worktree**

If Step 1 created a stash, restore it with `git stash pop` and report any
conflict without discarding content. Reinstall the editable package from the
surviving main checkout before removing a worktree-backed editable install.
Then run:

```powershell
Set-Location C:/Projekty/christag-agency
python -m pip install -e .
git worktree remove .worktrees/context-aware-setup-team
git worktree prune
git branch --list fix/context-aware-setup-team
git worktree list
git status --short --branch
```

Expected: the feature worktree is absent, the feature branch remains, `master`
matches `origin/master`, and any pre-existing user changes are restored.

- [ ] **Step 6: Verify the surviving installation and report evidence**

From outside the repository, verify `christag-agency serve --help` succeeds and
`python -m pip show christag-agency` reports the main checkout as its editable
location. Report:

```text
- focused test result
- feature and master full-suite results
- packaged skill verification
- unchanged launch/output model verification
- live team-draft acceptance evidence
- whole-branch review outcome
- fast-forwarded commit
- pushed refs
- removed worktree
- retained feature branch
- restored user changes, if any
```