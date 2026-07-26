# Optional Blueprint Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a blueprint containing only `AGENTS.md` while preserving strict validation and projection for every Agent Skill that is present.

**Architecture:** Keep the existing blueprint source layout and remove only the validator's minimum-skill-count rule. Exercise the unchanged projector through a real instruction-only `BlueprintInspection`, then align the canonical setup skill and public setup guide so they create skill directories only for approved routine capabilities.

**Tech Stack:** Python 3.14, pytest, Markdown, AGENTS.md, Agent Skills

**Spec:** `docs/superpowers/specs/2026-07-26-optional-blueprint-skills-design.md`

## Global Constraints

- `AGENTS.md` remains required at the blueprint root.
- `.agents/skills/<name>/SKILL.md` remains the only accepted skill source layout and may occur zero or more times.
- Every present skill remains subject to the existing location, directory-name, frontmatter, encoding, and field-length validation.
- Do not add `skills/`, prompt files, custom-agent files, or subagent definitions to canonical blueprint source.
- Do not change routine, instance, integration, configuration, memory, or job schemas.
- Do not synthesize placeholder skills, emit warnings for absent optional skills, or add compatibility loaders.
- Runtime projectors must preserve source bytes and emit no skill directory for an instruction-only blueprint.
- Run tests from `C:/Users/black/Projects/christag-agency/.worktrees/optional-blueprint-skills`; do not edit that worktree while the full suite or live runtime projector tests are running.

---

## File Structure

- Modify `agency/blueprints/library.py`: accept an empty collected skill set while retaining all validation for present skills.
- Modify `tests/test_blueprint_library.py`: prove inspection returns an empty skill tuple and rename the misleading existing test.
- Modify `tests/test_runtime_projectors.py`: prove all built-in static projectors emit only their instruction target for an instruction-only blueprint.
- Modify `skills/agency-setup/SKILL.md`: make `AGENTS.md` the only unconditional blueprint file and create skills only for approved routine capabilities.
- Modify `kb/setup-skill.md`: describe the same optional-skill contract in the public setup guide.
- Modify `tests/test_agency_setup_skill.py`: lock the optional-skill wording across the canonical skill and public guide.

No production projector, UI, configuration, job, or schema file changes are required.

---

### Task 1: Accept And Project Instruction-Only Blueprints

**Files:**
- Modify: `tests/test_blueprint_library.py:10-38`
- Modify: `tests/test_runtime_projectors.py:1-125`
- Modify: `agency/blueprints/library.py:113-153`
- Commit existing plan: `docs/superpowers/plans/2026-07-26-optional-blueprint-skills.md`

**Interfaces:**
- Consumes: `inspect_blueprint(root: Path, key: str) -> BlueprintInspection`, `StaticRuntimeProjector.project(source: TreeSnapshot, destination: Path) -> None`, and `StaticRuntimeProjector.validate_output(source: TreeSnapshot, destination: Path) -> tuple[ValidationIssue, ...]`.
- Produces: a valid `BlueprintInspection` with `skills == ()` when the captured source contains a valid root `AGENTS.md` and no `SKILL.md`; projector behavior remains unchanged.

- [ ] **Step 1: Commit the approved implementation plan before changing code**

```powershell
git add docs/superpowers/plans/2026-07-26-optional-blueprint-skills.md
git commit -m "docs: plan optional blueprint skills"
```

Expected: one documentation-only commit; `git status --short` returns no output.

- [ ] **Step 2: Write the failing inspection regression test**

Add this test after `_write_blueprint()` in `tests/test_blueprint_library.py`:

```python
def test_inspect_blueprint_accepts_agents_md_without_skills(tmp_path):
    root = tmp_path / "library"
    blueprint = root / "advisor"
    blueprint.mkdir(parents=True)
    (blueprint / "AGENTS.md").write_bytes(b"# Advisor\n")

    inspection = inspect_blueprint(root, "advisor")

    assert inspection.key == "advisor"
    assert inspection.skills == ()
    assert tuple(item.path.as_posix() for item in inspection.snapshot.files) == (
        "AGENTS.md",
    )
```

Rename the existing test so its name does not imply that every blueprint requires a skill:

```python
def test_blueprint_inspection_includes_standard_skill(tmp_path):
```

- [ ] **Step 3: Write the failing instruction-only projection regression test**

Add the inspection import near the top of `tests/test_runtime_projectors.py`:

```python
from agency.blueprints.library import inspect_blueprint
```

Add this test after the `blueprint_snapshot` fixture:

```python
@pytest.mark.parametrize(
    ("integration", "instruction_target"),
    [
        ("copilot", "AGENTS.md"),
        ("claude-code", "CLAUDE.md"),
        ("gemini", "GEMINI.md"),
    ],
)
def test_projector_emits_only_instruction_for_blueprint_without_skills(
    tmp_path, integration: str, instruction_target: str
):
    library_root = tmp_path / "library"
    blueprint_root = library_root / "advisor"
    blueprint_root.mkdir(parents=True)
    instruction = b"# Advisor\n"
    (blueprint_root / "AGENTS.md").write_bytes(instruction)
    inspection = inspect_blueprint(library_root, "advisor")
    destination = tmp_path / f"runtime-{integration}"
    projector = get_projector(integration)

    projector.project(inspection.snapshot, destination)

    assert (destination / instruction_target).read_bytes() == instruction
    skills_target = destination / Path(*projector.capabilities.skills_target.parts)
    assert not skills_target.exists()
    assert projector.validate_output(inspection.snapshot, destination) == ()
```

- [ ] **Step 4: Run the new tests to verify they fail for the intended reason**

Run:

```powershell
python -m pytest tests/test_blueprint_library.py tests/test_runtime_projectors.py -k "accepts_agents_md_without_skills or emits_only_instruction_for_blueprint_without_skills" -v
```

Expected: four failed cases. Each reaches `inspect_blueprint()` and raises `AssetValidationError` with issue code `missing-blueprint-skills`; there must be no import, fixture, or path-construction error.

- [ ] **Step 5: Remove only the mandatory-skill validation branch**

Delete this block from `inspect_blueprint()` in `agency/blueprints/library.py`:

```diff
-    if not skills:
-        _raise("skills", f"Blueprint must contain at least one standard Agent Skill: {key}.", "Add .agents/skills/<name>/SKILL.md to the blueprint.", code="missing-blueprint-skills")
-
```

Do not change skill discovery, `_parse_skill()`, invalid-location checks, directory-name checks, title extraction, or snapshot construction.

- [ ] **Step 6: Run the new tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_blueprint_library.py tests/test_runtime_projectors.py -k "accepts_agents_md_without_skills or emits_only_instruction_for_blueprint_without_skills" -v
```

Expected: four passed cases. Each projector writes its instruction target, creates no skills target, and returns no output-validation issues.

- [ ] **Step 7: Run the complete touched behavior slice**

Run:

```powershell
python -m pytest tests/test_blueprint_library.py tests/test_runtime_projectors.py -q
```

Expected: all selected tests pass. In particular, the existing malformed-frontmatter, missing-description, invalid-location, encoding, length, and name-mismatch tests remain green.

- [ ] **Step 8: Commit the behavior change**

```powershell
git add agency/blueprints/library.py tests/test_blueprint_library.py tests/test_runtime_projectors.py
git commit -m "fix(blueprints): allow instruction-only blueprints"
```

Expected: the commit contains only the validator and its focused regression tests.

- [ ] **Step 9: Review Task 1 before starting Task 2**

Review the Task 1 commit against the spec and Global Constraints. Verify that no new layout, warning, placeholder skill, projector branch, or relaxed validation for present skills was introduced. If review changes are required, commit them separately and rerun Step 7 before proceeding.

---

### Task 2: Align Setup Guidance With Optional Skills

**Files:**
- Modify: `tests/test_agency_setup_skill.py:17-29`
- Modify: `skills/agency-setup/SKILL.md:47-62`
- Modify: `kb/setup-skill.md:45-58`

**Interfaces:**
- Consumes: canonical setup source at `skills/agency-setup/SKILL.md` and its discovery symlink at `.github/skills/agency-setup`.
- Produces: setup guidance that always creates `AGENTS.md`, creates `.agents/skills/<skill>/SKILL.md` only for approved routine capabilities, and explicitly forbids placeholder skills and empty skills directories.

- [ ] **Step 1: Write the failing setup-guidance contract test**

Add this test after `test_setup_creates_standard_global_agent_library_blueprints()` in `tests/test_agency_setup_skill.py`:

```python
def test_setup_guidance_keeps_blueprint_skills_optional():
    documents = {
        "skill": SKILL_PATH.read_text(encoding="utf-8"),
        "guide": SETUP_KB_PATH.read_text(encoding="utf-8"),
    }
    required = (
        "Blueprints may contain zero or more standard Agent Skills.",
        "Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities.",
    )

    for document_name, text in documents.items():
        for phrase in required:
            assert phrase in text, document_name
```

- [ ] **Step 2: Run the new contract test to verify it fails**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py::test_setup_guidance_keeps_blueprint_skills_optional -v
```

Expected: FAIL because neither required sentence exists yet. The failure must identify the `skill` document first, not a missing file or symlink problem.

- [ ] **Step 3: Make only `AGENTS.md` unconditional in the canonical setup skill**

Replace the role tree and its following creation paragraph in Section 3 of `skills/agency-setup/SKILL.md` with:

````markdown
After the consolidated path summary is approved, create the approved `agency.agent_library` through safe directory operations; do not place blueprints under the project workspace. For each approved role, always create:

```text
{agent_library}/{blueprint}/
`-- AGENTS.md
```

Write `{agent_library}/{blueprint}/AGENTS.md` from `references/templates.md`. Blueprints may contain zero or more standard Agent Skills. For each approved routine capability, create `{agent_library}/{blueprint}/.agents/skills/{skill}/SKILL.md`. Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities. Skill frontmatter must contain a directory-matching `name` and a trigger-only `description`. Put supporting scripts, references, and assets inside the skill directory.
````

Keep the following paragraph about reusable blueprint content and runtime-native projections unchanged.

- [ ] **Step 4: Align the public setup guide**

Replace item 7 in the `kb/setup-skill.md` run sequence with this single paragraph item:

```markdown
7. Writes each approved blueprint with global `AGENTS.md` source. Blueprints may contain zero or more standard Agent Skills. For each approved routine capability, writes `.agents/skills/<skill>/SKILL.md`. Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities.
```

- [ ] **Step 5: Run the new contract test to verify it passes**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py::test_setup_guidance_keeps_blueprint_skills_optional -v
```

Expected: one passed test. The canonical skill and public guide contain both exact optional-skill contract sentences.

- [ ] **Step 6: Run the complete setup-skill contract suite**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py -q
```

Expected: all setup contract tests pass, including canonical symlink discovery, standard skill paths, root-first storage selection, and templates.

- [ ] **Step 7: Commit the setup guidance**

```powershell
git add skills/agency-setup/SKILL.md kb/setup-skill.md tests/test_agency_setup_skill.py
git commit -m "docs(setup): make blueprint skills optional"
```

Expected: `.github/skills/agency-setup` resolves the canonical change through its existing symlink and is not staged as a separate file.

- [ ] **Step 8: Review Task 2 before whole-branch verification**

Review the Task 2 commit against the spec and Global Constraints. Verify that roles with routines still receive standard `.agents/skills/<name>/SKILL.md` files, roles without routines receive only `AGENTS.md`, and no prompt, custom-agent, subagent, schema, or compatibility behavior was added. If review changes are required, commit them separately and rerun Step 6 before proceeding.

---

### Task 3: Verify The Complete Feature Branch

**Files:**
- Verify: `agency/blueprints/library.py`
- Verify: `tests/test_blueprint_library.py`
- Verify: `tests/test_runtime_projectors.py`
- Verify: `skills/agency-setup/SKILL.md`
- Verify: `kb/setup-skill.md`
- Verify: `tests/test_agency_setup_skill.py`

**Interfaces:**
- Consumes: reviewed Task 1 and Task 2 commits.
- Produces: a clean, fully tested feature tip suitable for fast-forward integration.

- [ ] **Step 1: Run whitespace and focused regression checks**

Run:

```powershell
git diff --check master...HEAD
python -m pytest tests/test_blueprint_library.py tests/test_runtime_projectors.py tests/test_agency_setup_skill.py -q
```

Expected: `git diff --check` emits nothing and every focused test passes.

- [ ] **Step 2: Run the complete suite without concurrent file edits**

Run:

```powershell
python -m pytest tests/ -q
```

Expected in the current environment: `1265 passed, 2 skipped`, no failures, and no warnings. The five additional passing cases are one inspection test, three projector parameters, and one setup-guidance test. Do not edit, format, stage, or commit files while this command runs because the live projector tests assert that repository state remains unchanged.

- [ ] **Step 3: Perform the whole-branch review**

Review:

```powershell
git diff --stat master...HEAD
git diff master...HEAD -- agency/blueprints/library.py tests/test_blueprint_library.py tests/test_runtime_projectors.py skills/agency-setup/SKILL.md kb/setup-skill.md tests/test_agency_setup_skill.py
```

Expected: no high-confidence correctness, standards-compliance, regression, or missing-test findings. Confirm specifically that `missing-blueprint-skills` is gone, every other skill validation remains, projector production code is unchanged, and setup text is consistent in both documents.

- [ ] **Step 4: Resolve any review findings and repeat verification**

If review finds an issue, write the smallest failing regression test, verify it fails for that issue, apply one focused correction, commit it separately, then repeat Steps 1-3. If review has no findings, make no review-only commit.

- [ ] **Step 5: Confirm the feature worktree is clean**

Run:

```powershell
git status --short
```

Expected: no output.

---

### Task 4: Fast-Forward Master And Clean Up

**Files:**
- Integrate: reviewed `feature/optional-blueprint-skills` tip into `master`
- Preserve: feature branch `feature/optional-blueprint-skills`
- Remove after verification: `.worktrees/optional-blueprint-skills`

**Interfaces:**
- Consumes: clean reviewed feature tip with a passing full suite.
- Produces: fast-forwarded and independently verified `master`, no completed worktree, and the retained feature branch.

- [ ] **Step 1: Verify fast-forward preconditions from the main checkout**

Run:

```powershell
Set-Location C:/Users/black/Projects/christag-agency
git status --short
git merge-base --is-ancestor master feature/optional-blueprint-skills
```

Expected: the main checkout has no tracked changes and the ancestry command exits 0. If either check fails, stop without merging, rebasing, resetting, or removing either checkout.

- [ ] **Step 2: Fast-forward master without a merge commit**

Run:

```powershell
git merge --ff-only feature/optional-blueprint-skills
```

Expected: `master` moves directly to the reviewed feature tip; Git creates no merge commit.

- [ ] **Step 3: Re-run the complete suite on fast-forwarded master**

Run from `C:/Users/black/Projects/christag-agency` with no concurrent edits:

```powershell
python -m pytest tests/ -q
```

Expected in the current environment: `1265 passed, 2 skipped`, no failures, and no warnings.

- [ ] **Step 4: Remove the completed worktree and retain the branch**

Run:

```powershell
git worktree remove .worktrees/optional-blueprint-skills
git branch --list feature/optional-blueprint-skills
git worktree list
```

Expected: the optional-blueprint-skills worktree is absent, `feature/optional-blueprint-skills` is still listed, and the main checkout remains on `master`.

- [ ] **Step 5: Report final evidence**

Report the focused-test result, both full-suite results, whole-branch review outcome, fast-forwarded commit hash, removed worktree path, and retained feature branch. Do not push or delete the feature branch unless the user explicitly requests it.