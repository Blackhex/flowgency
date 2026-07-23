# Agency Data Root Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make guided setup ask for one well-explained Agency data root first, derive the canonical storage tree, and keep individual path overrides behind one optional grouped review.

**Architecture:** Keep schema version 3 and all runtime storage APIs unchanged. Strengthen the first-run launch prompt and canonical `agency-setup` skill as the behavioral contract, then align the setup template and user documentation with that contract. Pytest text-contract tests prevent the launcher, skill, and documentation from drifting back to path-by-path setup.

**Tech Stack:** Python 3.14, FastAPI setup handoff, Markdown Agent Skills, pytest

## Global Constraints

- After read-only project inspection, the Agency data root is the first user-facing setup question.
- Explain that the root owns reusable agent blueprints, disposable compiled projections, semantic memory and durable jobs, and per-group records; it does not own project source or the authoritative `config.yaml`.
- Accept an existing directory or a new absolute path when its nearest existing parent is a writable real directory that can safely create it; expand user-home syntax.
- Derive exactly `agent-library/`, `compiled-agents/`, `memory/`, and `groups/<group-id>/` beneath the selected root by default.
- Keep individual path changes behind one `Customize the derived storage paths?` opt-in and one grouped review.
- Add no `agency.data_root` field, browser setup field, migration, cleanup, or runtime storage behavior.
- Keep `groups.<group-id>.workspace_path` at the project workspace and all Agency-owned paths outside it; never create or reference `<workspace_path>/shared`.
- Create no directory or blueprint before the consolidated path summary is approved.
- Preserve the one revision-checked `ConfigStore.replace(expected_revision, complete_candidate)` write and existing path validation.
- Baseline recorded on 2026-07-23: `1225 passed, 3 skipped, 2 failed`; the approved out-of-scope failures are `test_library_source_write_keeps_infra_outside_source_root` and `test_library_source_write_serializes_concurrent_saves` in `tests/test_agent_library_routes.py`.

---

## File Structure

- `agency/web/setup_flow.py`: supplies the root-first storage instructions to every interactive setup integration without changing the request model.
- `tests/test_setup_flow.py`: locks the launcher handoff to the root-first explanation, canonical derivations, grouped override, and approval order.
- `skills/agency-setup/SKILL.md`: owns question ordering, path derivation, opt-in overrides, validation, creation timing, and final reporting.
- `tests/test_agency_setup_skill.py`: locks the canonical skill and supporting documentation to the approved setup contract.
- `skills/agency-setup/references/templates.md`: connects the friendly root choice to the unchanged schema version 3 fields.
- `kb/setup-skill.md`: documents the complete user-facing root-first setup flow.
- `README.md`: gives first-run users the short form of the one-root convention.

The `.github/skills/agency-setup` path remains a discovery junction to `skills/agency-setup`; do not edit or replace it separately.

---

### Task 1: Enforce The Root-First Launcher Handoff

**Files:**
- Modify: `tests/test_setup_flow.py:19-40`
- Modify: `agency/web/setup_flow.py:22-44`

**Interfaces:**
- Consumes: `build_setup_prompt(project_dir: Path, config_path: Path, *, selected_integration: str) -> str` and the existing project/config/integration launch context.
- Produces: the same `build_setup_prompt(...) -> str` signature with explicit root-first, canonical derivation, grouped override, and pre-creation approval instructions.

- [ ] **Step 1: Write the failing launcher-contract test**

Add this test after `test_build_setup_prompt_names_project_and_config`:

```python
def test_build_setup_prompt_requires_root_first_storage_flow(tmp_path: Path) -> None:
    prompt = build_setup_prompt(
        tmp_path,
        tmp_path / "config.yaml",
        selected_integration="copilot",
    )

    for phrase in (
        "Agency data root the first user-facing question",
        "separate home for reusable agent blueprints",
        "project workspace remains source",
        "authoritative config remains at the supplied path",
        "existing directory or a new absolute path",
        "nearest existing parent is a writable real directory that can safely create it",
        r"C:\Agency",
        "~/Agency",
        "agency.agent_library as <root>/agent-library",
        "agency.compilation_cache as <root>/compiled-agents",
        "agency.memory_store as <root>/memory",
        "groups.<group-id>.path as <root>/groups/<group-id>",
        "Customize the derived storage paths?",
        "review all four derived paths together",
        "do not ask about individual storage paths",
        "before creating any directory or blueprint",
    ):
        assert phrase in prompt
```

- [ ] **Step 2: Run the new test to verify it fails**

Run:

```powershell
python -m pytest tests/test_setup_flow.py::test_build_setup_prompt_requires_root_first_storage_flow -q
```

Expected: FAIL on the first missing root-first phrase.

- [ ] **Step 3: Implement the minimal launcher prompt**

Replace the `build_setup_prompt` return value with:

```python
    return (
        "Use the agency-setup skill to configure Agency for this project. "
        f"Project workspace: {project_dir.resolve()}. "
        f"Authoritative config: {config_path.resolve()}. "
        f"Selected integration: {selected_integration}. "
        "Use it for group.default_integration and the initial agent instances unless "
        "the user explicitly approves a different registered integration. "
        "After read-only project inspection, make the Agency data root the first user-facing question. "
        "Explain that it is a separate home for reusable agent blueprints, disposable compiled "
        "projections, semantic memory and durable jobs, and per-group records; the project "
        "workspace remains source and the authoritative config remains at the supplied path. "
        "Accept an existing directory or a new absolute path when its nearest existing parent "
        "is a writable real directory that can safely create it, and expand user-home syntax. Use C:\\Agency and ~/Agency as examples. "
        "By default derive agency.agent_library as <root>/agent-library, "
        "agency.compilation_cache as <root>/compiled-agents, agency.memory_store as "
        "<root>/memory, and groups.<group-id>.path as <root>/groups/<group-id>. "
        "Configure schema_version: 3. For every group, set workspace_path to the project "
        "execution workspace and path to a disjoint Agency-owned group root. "
        "Never create or reference a project-local shared directory. "
        "After the group ID is approved, ask `Customize the derived storage paths?` once. "
        "Only if accepted, review all four derived paths together; otherwise do not ask about "
        "individual storage paths. Show one consolidated path summary and obtain approval "
        "before creating any directory or blueprint. Discuss and obtain approval for the group "
        "name, storage paths, agent team, integrations, routines, runtime policy, workspaces, "
        "and memory. Perform validation on the final config and make one atomic write for one "
        "complete configuration. Do not write a partial configuration."
    )
```

- [ ] **Step 4: Run the launcher tests to verify the contract passes**

Run:

```powershell
python -m pytest tests/test_setup_flow.py -q
```

Expected: PASS, including the new root-first contract and all existing setup status and integration tests.

- [ ] **Step 5: Commit the launcher contract**

```powershell
git add tests/test_setup_flow.py agency/web/setup_flow.py
git commit -m "feat(setup): require Agency data root first"
```

---

### Task 2: Make The Canonical Setup Skill Derive Storage Paths

**Files:**
- Modify: `tests/test_agency_setup_skill.py:39-84`
- Modify: `skills/agency-setup/SKILL.md:7-54`
- Modify: `skills/agency-setup/SKILL.md:135-164`

**Interfaces:**
- Consumes: launch tokens `Project workspace:`, `Authoritative config:`, and `Selected integration:` plus the root-first instructions from Task 1.
- Produces: a deterministic conversational contract that stores effective paths in the existing `agency.agent_library`, `agency.compilation_cache`, `agency.memory_store`, `groups.<group-id>.path`, and `groups.<group-id>.workspace_path` fields.

- [ ] **Step 1: Write the failing skill-contract tests**

Add these tests after `test_setup_requires_user_selected_agent_count_and_roles`:

```python
def test_setup_asks_for_data_root_before_team_questions():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(skill.split()).lower()

    root_question = normalized.index(
        "ask for the agency data root as the first user-facing question"
    )
    team_question = normalized.index("how many agents to create")

    assert root_question < team_question


def test_setup_derives_canonical_paths_from_one_data_root():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    for phrase in (
        "separate home for Agency-owned data",
        "existing directory or a new absolute path",
        "nearest existing parent is a writable real directory that can safely create it",
        r"C:\Agency",
        "~/Agency",
        "agency.agent_library = <root>/agent-library",
        "agency.compilation_cache = <root>/compiled-agents",
        "agency.memory_store = <root>/memory",
        "groups.<group-id>.path = <root>/groups/<group-id>",
        "groups.<group-id>.workspace_path = <project workspace>",
    ):
        assert phrase in skill


def test_setup_keeps_path_overrides_behind_one_grouped_review():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "Ask exactly once: `Customize the derived storage paths?`" in skill
    assert "one grouped review" in skill
    assert "Do not ask about individual storage paths in the default flow." in skill
    assert "one consolidated path summary" in skill
    assert "No directory or blueprint may be created before" in skill
```

- [ ] **Step 2: Run the new skill tests to verify they fail**

Run:

```powershell
python -m pytest `
  tests/test_agency_setup_skill.py::test_setup_asks_for_data_root_before_team_questions `
  tests/test_agency_setup_skill.py::test_setup_derives_canonical_paths_from_one_data_root `
  tests/test_agency_setup_skill.py::test_setup_keeps_path_overrides_behind_one_grouped_review `
  -q
```

Expected: FAIL because the current skill asks about team composition before defining the root-first flow.

- [ ] **Step 3: Replace the skill opening and Sections 1-2**

Replace the opening paragraph through the end of Section 2 with:

````markdown
The `agency-setup` skill owns the one authoritative canonical Agency config. After the user chooses a project folder and supported AI integration, the skill first asks for one Agency data root, derives the canonical storage paths, and then takes over group naming, blueprint source, explicit instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write. It accepts only the canonical config shape, creates it when absent, and reports validation errors directly. It does not create runtime-native identities, physical per-agent runtime directories, memory files, prompt schedules, or conversion surfaces.

## 1. Inspect And Choose The Data Root

Consume the launch context before asking questions. Read project instructions, README, dependency manifests, source layout, tests, deployment files, and recent git history. Detect the host OS and available agent CLI. Keep this inspection read-only and do not ask about the group, agents, roles, routines, workspaces, memory channels, or individual storage paths yet.

After inspection, ask for the Agency data root as the first user-facing question. Explain that it is a separate home for Agency-owned data: reusable agent blueprints, disposable compiled projections, semantic memory and durable jobs, and per-group records. The project workspace remains the source and execution location, and `config.yaml` remains at the authoritative path supplied by the launcher.

Accept an existing directory or a new absolute path. Expand user-home syntax. A missing root is valid when its nearest existing parent is a writable real directory that can safely create it. Give `C:\Agency` and `~/Agency` as examples. Derive these paths in memory without creating them:

```text
agency.agent_library = <root>/agent-library
agency.compilation_cache = <root>/compiled-agents
agency.memory_store = <root>/memory
groups.<group-id>.path = <root>/groups/<group-id>
groups.<group-id>.workspace_path = <project workspace>
```

The group path remains pending until the group ID is approved. Do not create any derived directory during this section.

## 2. Plan The Team And Resolve Agency

After the root is selected, summarize the project and propose three to five distinct roles. Exactly one builder normally receives write capability; observational roles remain fail-closed.

Before registration, ask the user how many agents to create for the first team and which proposed roles to create now. Do not infer extra instances beyond the approved count and selected roles. Ask the user to approve the group name and ID, team, each role's routine tasks, schedules, workspace definitions, and any shared memory channels. When the launch prompt contains `Selected integration:`, use that registered integration for `group.default_integration` and the initial agent instances unless the user explicitly approves a different registered integration.

After the group ID is approved, derive `groups.<group-id>.path`. Ask exactly once: `Customize the derived storage paths?` If declined, keep every derived path and do not ask about individual storage paths in the default flow. If accepted, present all four storage paths in one grouped review and allow any of them to be replaced.

Resolve every effective path before creation. Require that each missing effective path's nearest existing parent is a writable real directory that can safely create it; reject files, symlinks, and unsafe Windows reparse points, keep the global stores mutually disjoint, and keep every Agency-owned path disjoint from the project workspace. If validation fails, name the conflicting fields and resolved paths and return to the root choice or grouped review. Never choose a fallback location or project-local storage.

Show one consolidated path summary containing the project workspace, authoritative config path, Agency data root, and four effective storage paths. No directory or blueprint may be created before the user approves this summary.

When the launch prompt contains `Authoritative config:`, use that exact path and do not search for or choose another config. When the skill is invoked manually without an explicit authoritative path, find one config in this order: a valid `AGENCY_CONFIG`, the current project's config, then common user-level Agency locations. Parse YAML and accept only a mapping where the required `agency.agent_library`, `agency.compilation_cache`, and `agency.memory_store` paths are present.

If no config exists, record the absent revision and defer creation and replacement until Section 5. Do not write a placeholder or partial config. If an existing candidate is invalid or superseded, report validation errors and stop; never invoke another skill, never scan or convert superseded authority, and never convert old layouts. During manual invocation, if multiple canonical configs remain, ask the user which is authoritative; never choose implicitly.

Load the current revision before editing, or use the absent revision when the file does not exist. Preserve unrelated keys and groups while building the complete candidate in memory. Do not replace the authoritative config during inspection, blueprint creation, or instance registration.
````

- [ ] **Step 4: Gate blueprint creation on path approval**

Replace the first paragraph under `## 3. Build The Agent Library` with:

```markdown
After the consolidated path summary is approved, create the approved `agency.agent_library` through safe directory operations; do not place blueprints under the project workspace. For each approved role, create:
```

- [ ] **Step 5: Make final validation and reporting explicit**

Replace the first paragraph and final report sentence under `## 5. Verify And Schedule` with:

```markdown
Validate every blueprint and Agent Skill, config cross-reference, registered explicit integration, effective root union, complete tool override, routine skill, channel, workspace, group naming, and storage path. Re-read the authoritative config revision and stop on drift. Write one complete configuration atomically. Use Agency's revision-checked `ConfigStore.replace(expected_revision, complete_candidate)` for that single write; it initializes the approved cache, memory, durable-job, group, record, lock, and log directories. On revision drift, validation failure, or filesystem failure, stop without replacing the previous config and do not automatically remove approved directories or blueprint source. Then parse the final config from disk and confirm it is still the revision just written. Then offer the singleton scheduler setup:
```

```markdown
Report the Agency data root, effective storage paths, blueprint keys, instance IDs, routines, semantic memory scopes/channels, authoritative config path, and scheduler status.
```

- [ ] **Step 6: Run the skill contract tests**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py -q
```

Expected: PASS with the three new root-flow tests and all existing setup authority, scheduler, capability, and launcher assertions.

- [ ] **Step 7: Commit the canonical skill behavior**

```powershell
git add tests/test_agency_setup_skill.py skills/agency-setup/SKILL.md
git commit -m "feat(setup): derive storage from Agency data root"
```

---

### Task 3: Align Templates And User Documentation

**Files:**
- Modify: `tests/test_agency_setup_skill.py:4-10`
- Modify: `tests/test_agency_setup_skill.py:86-105`
- Modify: `skills/agency-setup/references/templates.md:35-55`
- Modify: `kb/setup-skill.md:1-45`
- Modify: `README.md:54-58`

**Interfaces:**
- Consumes: the canonical root-first phrases and folder names established by Task 2.
- Produces: one consistent user-facing explanation that maps `C:/Agency` to the unchanged schema version 3 fields.

- [ ] **Step 1: Write the failing documentation contract test**

Add these constants beside the existing setup-test paths:

```python
TEMPLATES_PATH = CANONICAL_SKILL_DIR / "references" / "templates.md"
README_PATH = REPO_ROOT / "README.md"
```

Add this test after the root-flow tests from Task 2:

```python
def test_setup_docs_present_one_data_root_default():
    templates = TEMPLATES_PATH.read_text(encoding="utf-8")
    guide = SETUP_KB_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")

    for document_name, text in {
        "templates": templates,
        "guide": guide,
        "readme": readme,
    }.items():
        assert "Agency data root" in text, document_name

    for path in (
        "C:/Agency/agent-library",
        "C:/Agency/compiled-agents",
        "C:/Agency/memory",
        "C:/Agency/groups/example",
    ):
        assert path in templates
        assert path in guide

    assert "first question" in guide.lower()
    assert "first question" in readme.lower()
    assert "`Customize the derived storage paths?`" in guide
```

- [ ] **Step 2: Run the documentation test to verify it fails**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py::test_setup_docs_present_one_data_root_default -q
```

Expected: FAIL because the current template, guide, and README describe separate storage paths rather than an Agency data root.

- [ ] **Step 3: Add the root-derived tree to the canonical template**

Replace the introduction under `## Canonical Group Registration` with:

````markdown
Default setup starts from one user-selected Agency data root. For an Agency data root at `C:/Agency` and a group ID of `example`, derive:

```text
C:/Agency/
|-- agent-library/
|-- compiled-agents/
|-- memory/
`-- groups/
    `-- example/
```

Map those derived paths to the unchanged schema version 3 fields and keep the execution workspace separate from Agency-owned state:
````

Keep the existing YAML example immediately after this text. It already contains the exact four expected `C:/Agency/...` values.

- [ ] **Step 4: Document the root-first flow in the setup guide**

Insert this section before `## Install`:

````markdown
## Agency Data Root

After read-only project inspection, the first question asks for one Agency data root. This is a separate home for reusable agent blueprints, disposable compiled projections, semantic memory and durable jobs, and per-group records. The project workspace remains source code and execution context, while `config.yaml` remains at its authoritative path.

The root may be an existing directory or a new absolute path whose nearest existing parent is a writable real directory that can safely create it. For a root at `C:/Agency` and group ID `example`, setup derives:

```text
C:/Agency/
|-- agent-library/       -> C:/Agency/agent-library
|-- compiled-agents/     -> C:/Agency/compiled-agents
|-- memory/              -> C:/Agency/memory
`-- groups/example/      -> C:/Agency/groups/example
```

Setup then asks `Customize the derived storage paths?` once. Declining keeps the complete derived layout without individual path questions. Accepting opens one grouped review of all four paths. Nothing is created until the consolidated path summary is approved.
````

Replace the numbered list under `## Run` with:

```markdown
1. Inspects project instructions, source, tests, deployment, and available integrations without asking setup questions.
2. Asks for the Agency data root as the first question and derives the canonical global paths.
3. Proposes reusable roles and asks how many agents to create plus which roles to create for the first team.
4. Approves the group ID, derives `groups/<group-id>`, and offers one optional grouped path override.
5. Plans Agent Skills, schedules, runtime policy, workspaces, and semantic memory for approval.
6. Resolves exactly one canonical config with only the supported root sections (`agency`, `memory`, and `groups`) and requires `agency.agent_library`, `agency.compilation_cache`, and `agency.memory_store`.
7. Writes each approved blueprint as global `AGENTS.md` source plus standard Agent Skills under `.agents/skills/<skill>/SKILL.md`.
8. Registers explicit group-owned instances and every approved group workspace. Every instance pins a blueprint and integration; routines select skills and semantic memory selectors.
9. Validates group naming, storage paths, integrations, cross-references, and revision safety, performs one atomic config write, reparses from disk, and optionally verifies the singleton dispatcher.
```

Replace the guide's final report sentence with:

```markdown
The skill reports the Agency data root, effective storage paths, blueprint keys, instance names, routines, memory scopes and channels, the authoritative config path, and scheduler status.
```

- [ ] **Step 5: Update the README quick start**

Replace the two paragraphs under `## Quick start` with:

```markdown
Start Agency, choose the project folder and supported AI integration, complete the agency-setup conversation, and return to the dashboard automatically. The first question chooses an existing or new Agency data root for all Agency-owned runtime data. The [Agency Setup Skill](kb/setup-skill.md) then owns group naming, blueprint source, instances, routines, runtime policy, workspaces, memory, validation, and the one atomic config write.

On first run, open `/setup` and hand off the project folder and supported integration to `agency-setup`. Setup derives `agent-library`, `compiled-agents`, `memory`, and `groups/<group-id>` beneath the approved root. Advanced users can opt into one grouped path review; the default flow asks no individual storage-path questions.
```

- [ ] **Step 6: Run the documentation and focused setup tests**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py::test_setup_docs_present_one_data_root_default -q
python -m pytest tests/test_agency_setup_skill.py tests/test_setup_flow.py -q
```

Expected: `1 passed` for the documentation contract, then `34 passed` for the complete focused setup suite.

- [ ] **Step 7: Commit the aligned documentation**

```powershell
git add tests/test_agency_setup_skill.py skills/agency-setup/references/templates.md kb/setup-skill.md README.md
git commit -m "docs(setup): explain Agency data root layout"
```

---

## Final Verification

- [ ] Run the complete focused regression suite:

```powershell
python -m pytest tests/test_agency_setup_skill.py tests/test_setup_flow.py -q
```

Expected: `34 passed`.

- [ ] Run the full Python suite from the feature worktree root:

```powershell
python -m pytest tests/ -q
```

Expected: no failures outside the two approved baseline tests named in Global Constraints. Every new setup test must pass; any new failure blocks completion. If the baseline failures recur unchanged, record the exact final counts rather than modifying unrelated Agent Library code.

- [ ] Check whitespace and the committed feature diff:

```powershell
git diff --check
git diff --check master...HEAD
git diff --stat master...HEAD
```

Expected: both diff checks produce no output; the stat contains only the launcher, setup skill, focused tests, template, guide, and README changes described above.

- [ ] Confirm no implementation work remains unstaged:

```powershell
git status --short
```

Expected: either no output, or only `?? docs/superpowers/plans/2026-07-23-agency-data-root-setup.md` when the plan document was intentionally left outside the implementation commits.