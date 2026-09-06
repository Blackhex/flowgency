# Setup Schedule Proposals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make setup propose useful routines and schedules, require an explicit manual-only opt-out, and obtain separate approval before enabling automatic execution.

**Architecture:** Change the package-owned setup skill's conversational contract, not the runtime scheduler. Repository skill paths resolve to that same source, and existing wheel tests verify packaged bytes. Extend neighboring instruction-contract tests and update setup documentation alongside each change.

**Tech Stack:** Markdown agent instructions, Python 3.11+, pytest, existing YAML configuration and scoped prompt contracts, existing setuptools wheel packaging.

## Global Constraints

- Use the existing configuration schema, scoped prompts, routine schedules, and singleton scheduler. No new runtime behavior or configuration fields are needed.
- Do not redesign the dashboard, require a routine for every agent, invent a business cadence as fact, install another scheduler, or expand agent permissions to accommodate a suggested routine.
- Do not modify the user's existing team, runtime config, prompts, or scheduler as part of this change.
- Preserve the existing project inspection, team naming, agent count, and first complete team draft ordering.
- Schedule approval alone does not authorize activation.
- Resolve this activation choice before the existing single atomic config write.
- All existing path approval, filesystem safety, configuration revision checks, cross-reference validation, and atomic replacement requirements remain in force.
- Static instruction tests demonstrate the required contract and packaging parity, not guaranteed agent compliance.
- Any exercise uses disposable data and must not activate real recurring work.

---

## Approved Inputs And Working Directory

- Specification: `docs/superpowers/specs/2026-09-06-setup-schedule-proposals-design.md`, committed in `47bc284`. The user's invocation of writing-plans follows the written-spec review gate.
- Feature branch: `feat/setup-schedule-proposals`.
- Existing isolated worktree: `C:/Projekty/Flowgency/.worktrees/setup-schedule-proposals`.
- Run all commands below from that worktree root, unless an integration step explicitly changes directories. Do not create a second worktree or implement on `master`.
- No approved visual assets exist or are required for this instruction-only change.
- Baseline: `python -m pytest tests/ -q` returned 2,035 passed, 6 skipped, 4 failed. All failures were live Copilot probes in `tests/test_runtime_projectors_live.py` with network, DNS, or model-catalog errors. The user permitted documentation to proceed; that permission does not waive implementation or integration gates.
- Before implementation, rerun the full baseline. If it still fails, report the actual failures and obtain permission to proceed with implementation against that baseline. Do not troubleshoot connectivity or relax tests without approval.

## File Ownership

| File | Responsibility | Planned Action |
| --- | --- | --- |
| `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md` | Canonical shipped setup instructions | Modify Sections 2, 4, and 5 |
| `tests/test_flowgency_setup_skill.py` | Instruction-contract and repository alias checks | Add section-scoped tests; update the scheduler ordering assertion |
| `kb/setup-skill.md` | User-facing setup workflow | Document proposal choice, activation, verification, and result states |
| `README.md` | Quick-start and scheduling expectations | Update only setup and scheduling paragraphs |
| `tests/test_setup_assets.py` | Package discovery and wheel byte parity | Run unchanged |
| `tests/test_setup_skill_e2e.py` | Template materialization and validation | Run unchanged; this is not a live conversation test |
| `tests/test_setup_flow.py`, `tests/test_interactive_setup.py` | Setup launch and guided flow regression coverage | Run unchanged |

`skills/flowgency-setup` and `.github/skills/flowgency-setup` resolve to the package-owned source. Edit only the canonical file. Do not copy it manually, alter junctions, or edit `build/lib` or installed caches. Confirm these assumptions with the existing alias and packaging tests.

No new Python abstraction or runtime API is needed. Keep the two implementation tasks sequential because both edit the same instruction document and its tests. Each task includes its own documentation, red/green cycle, commit, and review gate.

## Task 1: Require Grounded Routine Proposals And Explicit Choice

**Files:**
- Modify: `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`, Section 2, especially the pre-draft barrier, optional categories, and consolidated review.
- Modify: `kb/setup-skill.md`, Context-Aware Team Design and Run steps 3-4.
- Modify: `README.md`, final Quick start paragraph.
- Test: `tests/test_flowgency_setup_skill.py`.

**Interfaces:**
- Consumes: existing `SKILL_PATH`, `SETUP_KB_PATH`, and `README_PATH` constants in the test module; their `Path.read_text(encoding="utf-8")` results are strings.
- Produces: Section 2's explicit team approval choice and an approved in-session routine list. These remain conversational state, not new config keys or Python types. Task 2 maps them to existing `routines` entries and activation settings.

- [ ] **Step 1: Add failing proposal-contract tests.** Append these tests to `tests/test_flowgency_setup_skill.py`. Existing imports and constants suffice.

```python
def test_setup_proposes_routines_with_recommended_cadences():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    team = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]
    normalized = " ".join(team.split())
    for phrase in (
        "Propose useful recurring work in the first complete team draft",
        "task, prompt purpose, recommended schedule, and rationale",
        "Label each suggested cadence as a recommendation, not an existing project practice",
        "An agent with no useful recurring role may remain manual-only with a short explanation",
        "Do not add filler routines or expand permissions to accommodate a routine",
    ):
        assert phrase in normalized
    assert "`None proposed` is valid for optional emoji, routines" not in normalized


def test_setup_allows_schedule_clarification_between_draft_and_approval():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    team = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]
    normalized = " ".join(team.split())
    draft = normalized.index("Generate the first complete team draft")
    clarify = normalized.index(
        "ask one focused question about desired recurring checks or operating cadence"
    )
    choice = normalized.index("Approve the proposed team, including its listed routines and schedules")
    assert draft < clarify < choice
    assert "after the first complete draft and before consolidated team approval" in normalized
    assert "Do not ask about storage paths, routines, schedules" not in normalized
    assert "Do not ask about storage paths, memory, or channels until after one consolidated team approval" in normalized


def test_setup_requires_explicit_manual_only_choice():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    team = skill.split("## 2. Synthesize And Approve The Team", 1)[1].split(
        "\n## 3.", 1
    )[0]
    normalized = " ".join(team.split())
    for phrase in (
        "Approve the proposed team, including its listed routines and schedules",
        "Request targeted changes to profiles, routines, or schedules",
        "Choose manual-only operation for the team",
        "Manual-only operation must be an explicit user choice",
        "Mixed teams with scheduled and manual-only agents are valid",
        "A generic team approval without a visible scheduling decision is insufficient",
    ):
        assert phrase in normalized


def test_setup_docs_explain_routine_proposals_and_manual_only_choice():
    for path in (SETUP_KB_PATH, README_PATH):
        normalized = " ".join(path.read_text(encoding="utf-8").split())
        assert "proposes useful routines and recommended schedules" in normalized, path
        assert "explicitly choose manual-only operation" in normalized, path
```

- [ ] **Step 2: Run the new tests before changing instructions.**

```text
python -m pytest tests/test_flowgency_setup_skill.py -q -k "proposes_routines_with_recommended_cadences or allows_schedule_clarification or requires_explicit_manual_only_choice or docs_explain_routine_proposals"
```

Expected: four failures for absent contract text. An import or filesystem error is a test setup problem, not the intended red result.

- [ ] **Step 3: Edit Section 2 and its matching docs.** Use `apply_patch`. Replace the existing paragraph beginning `Do not ask the user to select candidate roles` with:

```text
Do not ask the user to select candidate roles or profiles during team and count
collection. Once the team name, ID, and count are approved, present the first
complete team draft before asking any other question. Do not ask about storage
paths, memory, or channels until after one consolidated team approval. The first
draft contains complete operating profiles, not a role-selection form. Routine
and schedule clarification belongs after the first complete draft and before
consolidated team approval.
```

Replace the two routine/schedule category lines in the profile block with:

```text
- routine tasks and prompt purposes, or a justified manual-only role
- recommended schedules and their rationale, or a justified manual-only role
```

Replace the paragraph beginning `The rationale names which inspected project characteristics` with:

```text
The rationale names which inspected project characteristics and prior answers
justify the profile; a generic statement that an agent helps with the project is
not sufficient. `None proposed` is valid for optional emoji, memory, and channels.
Do not invent shared memory merely to populate the profile.

Propose useful recurring work in the first complete team draft from inspected
project facts and the profile's responsibilities. Each proposed routine names
its task, prompt purpose, recommended schedule, and rationale. Label each
suggested cadence as a recommendation, not an existing project practice. An
agent with no useful recurring role may remain manual-only with a short
explanation. Keep every task within the proposed permissions and ownership
boundaries. Do not add filler routines or expand permissions to accommodate a
routine.

If the inspected evidence is insufficient to propose useful recurring work,
ask one focused question about desired recurring checks or operating cadence
after the first complete draft and before consolidated team approval.
Incorporate the answer into the draft instead of silently omitting scheduling.
```

After the coverage-summary paragraph ending `forcing agent-by-agent approval.`, insert:

```text
The consolidated team review must explicitly offer these choices:

- Approve the proposed team, including its listed routines and schedules.
- Request targeted changes to profiles, routines, or schedules.
- Choose manual-only operation for the team.

Manual-only operation must be an explicit user choice, not an inference from
missing proposals. Mixed teams with scheduled and manual-only agents are valid.
A generic team approval without a visible scheduling decision is insufficient.
Preserve the existing survivor rules for approved routines and schedules. If a
manual-only choice would change a protected survivor, obtain explicit approval
to release or edit that survivor rather than silently removing its routines.
```

In `kb/setup-skill.md`, replace this sentence:

```text
Optional operating choices may be `None proposed`.
```

With this sentence:

```text
Optional emoji, memory, and channels may be `None proposed`.
```

Insert this paragraph immediately before `## Install`:

```text
Setup proposes useful routines and recommended schedules in the first complete
team draft, grounded in the inspected project and each agent's responsibilities.
Cadences are recommendations, not assumed project practices. Agents without
useful recurring work may remain manual-only with an explanation. When evidence
is insufficient, setup asks one focused cadence question after the first draft
and before team approval. Approve the listed routines and schedules, request
targeted changes, or explicitly choose manual-only operation. Mixed teams are
valid; setup does not invent filler routines or widen permissions for them.
```

Replace Run steps 3-4 in that guide with:

```text
3. Approves the team name and stable ID, asks for the initial count, and drafts
   exactly that many complete operating profiles with routine proposals and
   recommended schedules for consolidated review.
4. Accepts targeted edits or a revised count, preserves selected full survivor
   profiles, resynthesizes remaining slots, and obtains an explicit choice to
   approve the listed routines and schedules or operate manually, together with
   approval of team coverage, permissions, memory, and assumptions.
```

In `README.md`, replace only the Quick start paragraph beginning `for the project workspace as its first question` (continue the preceding sentence) with:

```text
for the project workspace as its first question, then names your team and
proposes agent blueprints and instances. Setup proposes useful routines and
recommended schedules; approve them, request changes, or explicitly choose
manual-only operation. It writes one validated `config.yaml` with no individual
storage-path questions.
```

- [ ] **Step 4: Run the same four-test command immediately, then the full instruction-contract file.**

```text
python -m pytest tests/test_flowgency_setup_skill.py -q
```

Expected: all contract tests pass, including existing phase barriers, exact-count profiles, survivors, permissions, and aliases. Do not weaken those checks to make the new wording pass. Repair any contradiction in the touched Section 2 instead.

- [ ] **Step 5: Commit this deliverable and review it before Task 2.** Check editor diagnostics and `git diff --check`, then stage only these four files:

```text
git add flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md tests/test_flowgency_setup_skill.py kb/setup-skill.md README.md
git commit -m "fix(setup): require routine and schedule proposals"
```

Review against the spec's Proposal And Approval Flow section, especially the pre-draft barrier, justified individual exceptions, and explicit whole-team opt-out. Resolve findings before beginning activation work.

## Task 2: Separate Activation And Verify Saved Scheduling Choices

**Files:**
- Modify: `flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md`, Sections 2, 4, and 5.
- Modify: `kb/setup-skill.md`, Run and Result sections.
- Modify: `README.md`, Scheduling section.
- Test: `tests/test_flowgency_setup_skill.py`.
- Validate unchanged: `tests/test_setup_assets.py`, `tests/test_setup_skill_e2e.py`, `tests/test_setup_flow.py`, `tests/test_interactive_setup.py`.

**Interfaces:**
- Consumes: Task 1's approved routines and explicit manual-only decision in conversation.
- Produces: instructions mapping those choices into existing `teams.<team-id>.agents[].routines` and `teams.<team-id>.dispatch.enabled`; no API additions.
- Preserves: `ConfigStore.replace(expected_revision, complete_candidate)` as the single revision-checked write and the existing `flowgency validate`, `flowgency dispatch install`, and `flowgency dispatch status` commands.

- [ ] **Step 1: Add the failing activation and persistence tests.** Append to `tests/test_flowgency_setup_skill.py`:

```python
def test_setup_separates_activation_from_schedule_approval_before_write():
    normalized = " ".join(SKILL_PATH.read_text(encoding="utf-8").split())
    approval = normalized.index("Obtain one consolidated team approval")
    activation = normalized.index("Separately ask whether to enable automatic execution")
    write = normalized.index("Write one complete configuration atomically.")
    assert approval < activation < write
    for phrase in (
        "Schedule approval alone does not authorize activation",
        "an already-running singleton scheduler can pick up enabled routines once configuration is saved",
        "Manual-only: omit routines for the new team and set `dispatch.enabled: false`",
        "Scheduled but inactive: save approved routines and set `dispatch.enabled: false`",
        "Scheduled with dispatch enabled: save approved routines and set `dispatch.enabled: true`",
        "Do not perform a second config write to activate initial schedules",
    ):
        assert phrase in normalized


def test_setup_verifies_saved_routines_against_approved_choices():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = " ".join(skill.split("## 5. Verify And Schedule", 1)[1].split())
    revision = section.index("Then parse the final config from disk")
    compare = section.index("Compare the saved configuration with the approved in-session choices")
    validate = section.index("flowgency validate --config")
    install = section.index("flowgency dispatch install --config")
    assert revision < compare < validate < install
    for phrase in (
        "owning instance, ID, scoped prompt reference, schedule, arguments, and memory selection",
        "prompt documents exist and meet the Standard Task Prompt contract",
        "dispatch enablement matches the activation decision",
        "Stop on missing or mismatched approved data",
        "Do not perform an unapproved repair write or delete approved source files",
    ):
        assert phrase in section


def test_setup_gates_scheduler_installation_and_reports_status_separately():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    section = " ".join(skill.split("## 5. Verify And Schedule", 1)[1].split())
    for phrase in (
        "Only when activation was approved, offer the singleton scheduler setup:",
        "Never install the scheduler solely because schedules were approved",
        "If installation is declined, fails, or its status cannot be verified",
        "do not silently change the saved config",
        "Manual-only: no routines approved; dispatch disabled",
        "Scheduled but inactive: routines saved; dispatch disabled",
        "Scheduled with dispatch enabled: routines saved; activation approved",
        "Report singleton scheduler status separately",
        "not installed, confirmed status, installation failure, or unknown status",
        "Dispatch enabled is not proof that the platform scheduler is installed or running",
        "list the saved routine schedules",
    ):
        assert phrase in section


def test_setup_docs_distinguish_schedules_activation_and_scheduler():
    guide = " ".join(SETUP_KB_PATH.read_text(encoding="utf-8").split())
    readme = " ".join(README_PATH.read_text(encoding="utf-8").split())
    for phrase in (
        "Manual-only", "Scheduled but inactive", "Scheduled with dispatch enabled",
        "compares saved routines and dispatch enablement with the approved choices",
        "scheduler status is reported separately",
    ):
        assert phrase in guide
    assert "Schedule approval does not enable automatic execution" in readme
    assert "Installing the scheduler does not create routines" in readme
```

In the existing `test_setup_verification_protocol_orders_atomic_write_before_revision_check`, replace only the scheduler lookup with:

```python
    scheduler = section.index(
        "Only when activation was approved, offer the singleton scheduler setup:"
    )
```

Keep its `atomic < revision < scheduler` assertion. Keep `test_phase_five_orders_validate_after_config_write` unchanged.

- [ ] **Step 2: Run the failing activation slice.**

```text
python -m pytest tests/test_flowgency_setup_skill.py -q -k "separates_activation or verifies_saved_routines or gates_scheduler_installation or docs_distinguish_schedules or verification_protocol_orders"
```

Expected: five failures from missing activation and saved-result contracts, including the changed scheduler lookup.

- [ ] **Step 3: Implement the activation and verification instructions with matching docs.**

In Section 2, insert the following after the paragraph ending `Keep team drafts, survivor choices, and the working context in this conversation only.` and before the storage override question:

```text
After the team and its routines are approved, resolve activation before the
single atomic config write. Schedule approval alone does not authorize
activation. Separately ask whether to enable automatic execution for approved
routines. Explain that an already-running singleton scheduler can pick up
enabled routines once configuration is saved. For a manual-only team, leave
dispatch disabled without asking to activate nonexistent routines.

- Manual-only: omit routines for the new team and set `dispatch.enabled: false`.
- Scheduled but inactive: save approved routines and set `dispatch.enabled: false`.
- Scheduled with dispatch enabled: save approved routines and set `dispatch.enabled: true`.

Retain this choice in conversation until building the complete candidate.
Do not perform a second config write to activate initial schedules. Preserve
unrelated teams and settings; these defaults do not authorize modifying an
existing team's routines or activation state without explicit approval.
```

In Section 4, immediately after the routine assignment paragraph, insert:

```text
For every approved routine, create its selected scoped prompt document using
the Standard Task Prompt contract. Preserve its approved owning instance, ID,
prompt scope and name, schedule values, optional arguments, and semantic memory
selection. Apply the approved activation choice to the new team's dispatch
setting. Do not encode schedules in blueprint instructions or native runtime
files. Keep the candidate in memory until the single Section 5 config write.
```

In Section 5, after the atomic-write paragraph and before `Then run the mechanical check`, insert:

```text
Compare the saved configuration with the approved in-session choices. For each
approved routine, verify its owning instance, ID, scoped prompt reference,
schedule, arguments, and memory selection. Confirm that its selected prompt
documents exist and meet the Standard Task Prompt contract and that dispatch
enablement matches the activation decision. For manual-only operation, confirm
that the new team has no routines and dispatch is disabled. Stop on missing or
mismatched approved data and report the discrepancy without declaring setup
complete. Do not perform an unapproved repair write or delete approved source
files. Existing revision-drift, validation, and filesystem failure rules remain
in force.
```

Replace the sentence `Then offer the singleton scheduler setup:` with `Only when activation was approved, offer the singleton scheduler setup:`. Preserve both existing command examples. Immediately after their fence, insert:

```text
Never install the scheduler solely because schedules were approved. Obtain
consent for installation before running the install command. If installation is
declined, fails, or its status cannot be verified, report that separately and
do not silently change the saved config or claim automatic execution is
operational. Use the status command to report observed scheduler state; if it
cannot be checked, report unknown status rather than assuming success.
```

Preserve the singleton prohibition and existing final storage/instance summary. Append:

```text
State which scheduling result applies and list the saved routine schedules:

- Manual-only: no routines approved; dispatch disabled.
- Scheduled but inactive: routines saved; dispatch disabled.
- Scheduled with dispatch enabled: routines saved; activation approved.

Report singleton scheduler status separately: not installed, confirmed status,
installation failure, or unknown status, according to observed command results.
Dispatch enabled is not proof that the platform scheduler is installed or
running.
```

In `kb/setup-skill.md`, insert this paragraph just before `## Install`, following Task 1's proposal paragraph:

```text
After routine approval, setup separately confirms activation before its single
config write. An existing singleton scheduler can pick up enabled routines as
soon as that write completes. Declining activation keeps the approved routines
but leaves dispatch disabled. A manual-only choice saves no routines for the
new team and leaves dispatch disabled. Existing teams are not changed without
explicit approval. Scheduler installation is offered only after activation
approval and configuration verification, and requires installation consent.
```

Replace Run step 9 with:

```text
9. Validates team naming, storage paths, integrations, cross-references, and
   revision safety, performs one atomic config write, reparses from disk, and
   compares saved routines and dispatch enablement with the approved choices.
   Missing or mismatched data blocks completion without an unapproved repair
   write. After validation, offers singleton scheduler installation only when
   activation was approved and reports observed scheduler status independently.
```

Append to Result:

```text
The result distinguishes Manual-only (no routines, dispatch disabled), Scheduled
but inactive (routines saved, dispatch disabled), and Scheduled with dispatch
enabled (routines saved, activation approved). Saved routine schedules are
listed, and scheduler status is reported separately. Installation declined,
failed, or unverified does not justify claiming automatic execution is ready;
unknown status is reported as unknown without changing the saved config.
```

Replace the introductory sentence in README's Scheduling section with the following, retaining both CLI command examples:

```text
Setup proposes routines and schedules for approval. Schedule approval does not
enable automatic execution: setup separately asks whether to enable dispatch
before saving the configuration. You can keep approved routines inactive or
choose manual-only operation.

After approving activation, install the singleton dispatcher to run configured
routines on a platform timer. Installing the scheduler does not create routines.
Dispatch enablement and the scheduler's installed or running status are separate:
```

- [ ] **Step 4: Rerun the same five-test slice immediately, then the full setup regression group.**

```text
python -m pytest tests/test_flowgency_setup_skill.py tests/test_setup_assets.py tests/test_setup_skill_e2e.py tests/test_setup_flow.py tests/test_interactive_setup.py -q
```

Expected: all pass. Wheel construction may need build dependencies from the network; report a packaging-environment failure rather than weakening the byte-parity test. Existing template validation proves scoped prompt compatibility, not conversation compliance.

- [ ] **Step 5: Commit and review Task 2 before whole-branch verification.** Check diagnostics and `git diff --check` first.

```text
git add flowgency/setup_assets/copilot/.github/skills/flowgency-setup/SKILL.md tests/test_flowgency_setup_skill.py kb/setup-skill.md README.md
git commit -m "fix(setup): gate activation and verify saved routines"
```

Review the exact order: routine approval, activation decision, one write, reparse and comparison, mechanical validation, optional installation, independent status report. Reject changes that add a second config write, equate enabled dispatch with a running scheduler, or alter runtime implementation unnecessarily.

## Acceptance And Whole-Branch Verification

- [ ] **Run the complete suite from the feature worktree.**

```text
python -m pytest tests/ -q
```

Record counts and failure causes. Baseline network failures are not a green suite. Stop integration if required tests fail; seek direction without silently exempting live probes.

- [ ] **Exercise guided setup when a functioning runtime and disposable environment are available.** Use the installed `flowgency-setup` skill and a disposable project outside all real workspaces, with fresh config and data roots per scenario. Inspect the project read-only and use a Python service with tests as the project context. Record these inputs and expected outcomes in the review report:

| Scenario | User Inputs | Required Observation |
| --- | --- | --- |
| Default proposal | Approve name and two-agent count; do not volunteer a cadence | First complete draft visibly proposes useful tasks, prompt purposes, recommended schedules, and rationale, with no writes before path approval |
| Manual-only | Choose manual-only at team review | No routines in the new team's config, dispatch false, manual-only result, no scheduler installation |
| Deferred activation | Approve a proposed routine with an edited cadence, then decline activation | Saved routine and prompt match the edited choice, dispatch false, scheduled-but-inactive result, no scheduler installation |
| Sparse evidence | Give a minimal project and no operating cadence | One focused routine/cadence clarification after the first draft and before team approval, not silent omission |
| Mixed team and survivor | Preserve one complete profile while changing count; retain one manual-only agent | Surviving schedule is unchanged and the team review still offers an explicit scheduling decision |

Do not install a real scheduler or approve activation in this exercise. The enabled-state path and installation failures are covered by instruction-contract checks and review only unless an isolated, non-executing scheduler environment is explicitly arranged. If the live runtime is blocked, mark acceptance as unverified. Do not rename template-materialization tests as evidence of a live setup conversation.

- [ ] **Perform a whole-branch review.** Compare the diff against the approved specification, recheck the canonical source and packaged byte-parity result, and verify only scoped docs, instruction text, and tests changed. Review each possible completion state and failure path. Resolve findings with focused red/green tests, then rerun the full suite when changes warrant it.

```text
git diff --check
git diff master...HEAD --stat
git status --short
```

The review report must distinguish passing static contracts, template validation, package parity, live acceptance results, and any unverified requirements. Do not claim end-to-end compliance from prose assertions alone.

## Integration Gates And Sequence

Integration is pre-authorized by the repository, not a user execution-choice question. Only proceed after implementation review and required green suites. Use a separate command for each step and inspect its result before continuing.

- [ ] Check whether `master` advanced. If it has, rebase `feat/setup-schedule-proposals` onto local `master` from the feature worktree, resolve only feature conflicts, then rerun the complete suite and review the result before proceeding.
- [ ] In `C:/Projekty/Flowgency`, inspect `git status --short` and verify the branch is `master`. If there are unrelated tracked changes, stash them with a descriptive message and record the exact stash entry. Preserve untracked runtime files in place; if they block integration, stop rather than deleting or sweeping them into the feature. Restore the recorded stash after fast-forwarding.
- [ ] Fast-forward `master` only:

```text
git merge --ff-only feat/setup-schedule-proposals
```

- [ ] Restore any recorded main-checkout stash without discarding it on conflicts. Rerun `python -m pytest tests/ -q` from the fast-forwarded main checkout. Do not push or clean up on a failing suite.
- [ ] Once the main-checkout suite is green, publish both branches:

```text
git push origin master feat/setup-schedule-proposals
```

- [ ] Verify the feature worktree has no uncommitted work, then from the main checkout remove only this worktree and prune:

```text
git worktree remove .worktrees/setup-schedule-proposals
git worktree prune
```

Keep the feature branch. Do not force-remove a dirty worktree or delete runtime-local files. Report the integrated commit, verification results, and any remaining live-acceptance limitation.

## Plan Self-Review

- Proposal grounding, recommended cadences, explicit opt-out, sparse-evidence ordering, mixed teams, and survivors: Task 1 plus acceptance scenarios.
- Activation consent before one atomic write, existing-team preservation, prompt persistence, mismatch handling, and independent scheduler status: Task 2.
- Canonical and shipped parity, prior setup behavior, prompt contracts, and full-suite gates: Task 2 checks and whole-branch verification.
- No new config fields, Python interfaces, runtime behavior, UI assets, or project scheduler: global constraints and review gates.
- Test constants and section headings match the existing test module and canonical skill. All new test assertions are backed by exact proposed text; existing ordering assertions are preserved or explicitly updated.
- Live acceptance and full-suite failures are reported honestly and do not become implicit completion exemptions.