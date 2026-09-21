# Git Ref Link Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retain the existing ref-alias hardening and reject linked refs roots with a controlled domain error.

**Architecture:** Walk the lexical ref path, including its root, for symlinks/reparse points before resolution. Keep the resolved containment guard and bounded loose/packed reads. Reuse the current Git-evidence test fixtures and public interfaces.

**Tech Stack:** Python, pathlib, Git, pytest, existing Windows reparse-point helper.

## Global Constraints

- Approved specification: `docs/superpowers/specs/2026-09-21-git-ref-link-hardening-design.md`, commit 34fe5d1, explicitly approved by the user.
- Work only in `C:/Projekty/Flowgency/.worktrees/git-ref-link-hardening`, branch `fix/git-ref-link-hardening`.
- Original master base: 05d389e85456d41a1d8a965f4f860ab08ce00375.
- Original two-file patch is preserved under main `.superpowers/integration/git-ref-link-hardening` as raw backups, original.patch and preserved-edits.json; stash b4cd81f2d51c34c3425a9513e18108657448a845.
- Preserve those edits and their original two tests. Do not drop the recovery stash or reapply it to master after integrating the repaired result.
- Exact ref-link rejection: `git-evidence-publication-ref-denied`.
- Preserve ordinary/missing/packed refs, symbolic-ref handling, bounded reads, object/ancestor verification and local-only Git semantics.
- No public API/config/schema/dependency/UI/transport or permission-policy changes. No source-repository mutation or new remote/credential operation.
- No atomic filesystem-race guarantee is added. Do not expand this into a generic filesystem framework.
- No weakened assertions, added broad skips, retry loops, timeout increases or unrelated fixes.
- Tests run from the active worktree root with its own interpreter and verified editable source path.
- No visual assets or browser layout changes belong to this task.

## Controller Preparation

- [ ] Commit this plan separately from implementation.
- [ ] Prepare a private worktree Python environment using the same installed dependency versions as master; register this worktree's editable source and verify isolated imports. Do not modify master's environment or personal settings.
- [ ] Run the complete Python baseline on the clean worktree and retain log/JUnit/exit records. On failure, diagnose without altering the baseline or waiving required tests.
- [ ] Once baseline is green, apply the preserved stash to this worktree only and verify both files against their raw backup hashes. Keep the stash as a recovery record.

### Task 1: Reject Linked Ref Roots Before Resolution

**Files:**
- Modify: `flowgency/git_evidence/publication.py`.
- Test: `tests/test_git_evidence.py`.

**Interfaces:**
- Consumes: `GitRepository.common_dir`, validated `refs/...` names, existing `is_symlink_or_reparse`, `read_bounded_metadata`, and `_publication_context`/`_verify` fixtures.
- Produces: unchanged signatures and successful normal ref observations; linked roots/components return the existing `GitEvidenceError` code.

- [ ] Read the reapplied two-file patch. The local hypothesis is that resolving refs_root before lexical containment both loses the root link and causes `relative_to` to raise. The cheapest discriminating test is a real repository with `.git/refs` renamed and replaced by a directory symlink to that same data.
- [ ] Add this regression next to the existing alias/ref tests, reusing their imports/fixtures:

```python
@requires_git
@pytest.mark.parametrize("directory", ("refs", "refs/heads"))
def test_a_linked_ref_directory_is_refused(tmp_path, directory):
    fixture = create_git_repository(tmp_path / "repo")
    linked = fixture.root / ".git" / directory
    target = linked.with_name(f"{linked.name}-real")
    linked.rename(target)
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with _publication_context(
        fixture.root, fixture.base_commit, fixture.end_commit, tmp_path / "scratch"
    ) as context:
        with pytest.raises(GitEvidenceError) as failure:
            _verify(
                context, _restricted("refs/heads/main"),
                publication_ref="refs/heads/main",
            )
    assert failure.value.code == "git-evidence-publication-ref-denied"
```

- [ ] Immediately run `.venv/Scripts/python.exe -m pytest tests/test_git_evidence.py -q -rs -k linked_ref_directory`. The root case must fail with the reproduced raw `ValueError` against the preserved patch; the intermediate case may already pass. If link creation is skipped, report the platform limitation and use an available host with real symlink support, not a mocked substitute for the only behavioral proof.
- [ ] Make the smallest root-cause repair. At helper entry, check the lexical refs root before visiting descendant parts. In `_read_source_ref`, construct both paths lexically, invoke the helper, then resolve for the existing containment check:

```python
refs_root = repository.common_dir / "refs"
lexical_candidate = repository.common_dir / ref
_reject_linked_ref_path(lexical_candidate, refs_root)
resolved_root = refs_root.resolve(strict=False)
candidate = lexical_candidate.resolve(strict=False)
if not candidate.is_relative_to(resolved_root):
    raise GitEvidenceError("git-evidence-publication-ref-denied")
```

```python
current = refs_root
if is_symlink_or_reparse(current):
    raise GitEvidenceError("git-evidence-publication-ref-denied")
```

Leave the helper's existing descendant walk and packed-file guard intact. Do not broadly catch `ValueError` to hide invalid path construction. Preserve source style without new inline narration comments.

- [ ] Immediately rerun the same regression and retain GREEN evidence. Then run the neighboring aliases, symbolic refs, packed refs and bounds selection:

```text
.venv/Scripts/python.exe -m pytest tests/test_git_evidence.py -q -rs -k "linked_ref_directory or ref_aliased or linked_packed_refs or ref_entry_leaving or symbolic_ref or packed_refs_that_grow or oversized_loose_ref"
```

- [ ] Run the complete `tests/test_git_evidence.py` module and repository-boundary tests, retaining counts/skips and checking editor diagnostics. The original two tests remain present; ordinary allowed refs must still pass the existing tests.
- [ ] Self-review and commit the combined original hardening plus root repair and regressions as `fix(git-evidence): reject linked ref paths`. Stage only these two files. Do not stage runtime config, locks, environment, ignored reports or backups.
- [ ] Write the task report in this plan's ignored SDD directory, with original-patch preservation, exact RED/GREEN commands/results, module results, commit and caveats. No push or merge by the implementer.

## Controller Completion

- [ ] Review Task 1 against specification and code quality, including the inherited edits. Address concrete blocking findings with focused checks.
- [ ] Run the complete Python suite at the feature tip before final review; retain failures separately and never combine subset passes into a full-suite claim.
- [ ] Obtain the required whole-branch review of base 05d389e through the final tip, including the separate documentation commits. No broad review of unchanged earlier features.
- [ ] Archive this task's evidence, inspect local files, then fast-forward master only. Preserve any newly arrived unrelated edits; never restore the original stale patch on top of its integrated fix.
- [ ] Run the complete suite from master. When green, push master and fix/git-ref-link-hardening without force, verify both remote tips, archive final reports, remove the worktree normally and prune. Keep the fix branch and recovery stash/backup unless the user requests removal.