# Task 2 Report: Align Setup Guidance With Optional Skills

Working tree: C:/Users/black/Projects/christag-agency/.worktrees/optional-blueprint-skills

## Summary
Implemented Task 2 per the brief: added a failing contract test, observed the RED failure, updated the canonical skill and public guide to require optional-skill language, re-ran the test to confirm GREEN, and ran the full setup-skill contract suite to confirm all tests pass. Committed the three files with the exact commit subject required by the brief.

## Files changed
- tests/test_agency_setup_skill.py — added `test_setup_guidance_keeps_blueprint_skills_optional` immediately after the standard-blueprint test.
- skills/agency-setup/SKILL.md — replaced Section 3 role-tree and creation paragraph to make `AGENTS.md` unconditional and to state that skills are optional and only created for approved routine capabilities; forbids placeholder skills and empty `.agents/skills` directories.
- kb/setup-skill.md — replaced item 7 to match the canonical skill wording about optional Agent Skills and prohibition on placeholder/empty skill directories.

(Notes: `.github/skills/agency-setup` is a discovery symlink and was not staged separately.)

## Commands run (exact)
- RED (failing test before edits):

```
python -m pytest tests/test_agency_setup_skill.py::test_setup_guidance_keeps_blueprint_skills_optional -v
```

- GREEN (after edits):

```
python -m pytest tests/test_agency_setup_skill.py::test_setup_guidance_keeps_blueprint_skills_optional -v
```

- Full setup-skill contract suite:

```
python -m pytest tests/test_agency_setup_skill.py -q
```

- Commit:

```
git add skills/agency-setup/SKILL.md kb/setup-skill.md tests/test_agency_setup_skill.py
git commit -m "docs(setup): make blueprint skills optional"
```

## Captured outputs (relevant excerpts)
- RED output (expected failure):

"The test failed with `AssertionError: skill` because the required phrases ("Blueprints may contain zero or more standard Agent Skills." and "Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities.") were present in the public guide but missing from the canonical `skills/agency-setup/SKILL.md`.

- GREEN output (single test):

```
============================== 1 passed in 0.03s ==============================
```

- Full contract suite output:

```
22 tests passed in 0.04s
```

- Commit:

Committed files:
- kb/setup-skill.md
- skills/agency-setup/SKILL.md
- tests/test_agency_setup_skill.py

HEAD: 9872362530076b2cbf51ae99b86e2df82b3ae2c2

## Self-review
- Implemented exactly the three changes requested by the brief.
- The new test is placed precisely after `test_setup_creates_standard_global_agent_library_blueprints()` as requested.
- The `SKILL.md` Section 3 replacement uses the brief-specified language and makes `AGENTS.md` unconditional while clearly stating that skills are optional and must not be provided as placeholders or empty directories.
- The `kb/setup-skill.md` item 7 now matches canonical wording.
- Ran the focused RED test, observed the expected failure mode (the failure identified the `skill` document first), implemented the minimal canonical and guide changes, re-ran the focused test (GREEN), and ran the full file-level contract suite (22 tests passing).
- Did not stage or modify `.github/skills/agency-setup` (symlink) in the commit.

## Concerns
- None functional. The changes are documentation-only and test expectations; no runtime code, templates, schemas, prompts, subagents, or projector behavior were altered.
- Ensure downstream consumers of `skills/agency-setup/SKILL.md` are aware that skills directories may now be absent for roles without routines; any automation that previously assumed a `.agents/skills/` directory must handle its absence.

## Relevant hashes and paths
- Commit: 9872362530076b2cbf51ae99b86e2df82b3ae2c2 — "docs(setup): make blueprint skills optional"
- Report path: C:/Users/black/Projects/christag-agency/.worktrees/optional-blueprint-skills/.superpowers/sdd/2026-07-26-optional-blueprint-skills/task-2-report.md

## Round 1 Fix Evidence (this change)

Finding addressed: `skills/agency-setup/SKILL.md` contained a duplicated opening paragraph and nested markdown fence markers around the role tree replacement (lines 49-60), which risked rendering guidance as an example block rather than authoritative instructions.

Files changed for this fix:
- skills/agency-setup/SKILL.md — removed duplicated paragraph and nested fences; inserted one opening paragraph, a single `text` fence with the two-line `AGENTS.md` tree, and the authoritative creation paragraph per the brief.
- tests/test_agency_setup_skill.py — strengthened `test_setup_guidance_keeps_blueprint_skills_optional` to require the exact `text` fence, ensure the opening paragraph appears only once, and forbid nested `markdown` fences.
- .superpowers/sdd/2026-07-26-optional-blueprint-skills/task-2-report.md — appended this evidence section.

Commands run (exact) and outputs:

- Focused covering test (verbose):

```
python -m pytest tests/test_agency_setup_skill.py::test_setup_guidance_keeps_blueprint_skills_optional -v
```

Observed excerpted output:

```
tests/test_agency_setup_skill.py::test_setup_guidance_keeps_blueprint_skills_optional PASSED

1 passed in 0.03s
```

- Full setup-skill contract suite (verbose):

```
python -m pytest tests/test_agency_setup_skill.py -v
```

Observed excerpted output:

```
... [tests output truncated] ...
22 passed in 0.04s
```

Git commit for this fix:

```
git add skills/agency-setup/SKILL.md tests/test_agency_setup_skill.py .superpowers/sdd/2026-07-26-optional-blueprint-skills/task-2-report.md
git commit -m "docs(setup): fix duplicated paragraph and nested fence in Section 3"
```

Notes:
- The strengthened test would have failed the malformed duplicate/fence shape because it verifies the exact `text` fence and forbids nested `markdown` fences and duplicated opening paragraph.
- No other files were changed. The `.github/skills/agency-setup` discovery symlink was not modified or staged.


