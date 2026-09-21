# Git Ref Link Hardening

## Approved Intent

The user approved retaining the two existing uncommitted hardening edits,
fixing the linked-refs-directory edge case identified in review, and
committing the result separately. Base: 05d389e. Work in the ignored
`.worktrees/git-ref-link-hardening` checkout on `fix/git-ref-link-hardening`.
Preserve the original edits before transferring them out of master.

## Behavior

Publication ref observation must reject symlinks and Windows reparse points
in the requested loose ref path, including the refs root itself and any
intermediate directory. It must also retain the existing uncommitted guard
against a linked packed-refs file. Rejection uses
`git-evidence-publication-ref-denied`, not an uncaught path exception.

The reproduced defect compares a lexical candidate with a resolved root.
When the refs root is linked, `relative_to` raises `ValueError`. The repair
will keep both operands lexical while checking the root and each descendant
for links, before resolving the candidate or reading the selected ref.
The current resolved containment check remains as a separate safeguard.

Ordinary loose refs, packed refs, missing refs, symbolic-ref rejection,
object validation, bounded metadata reads, and local ancestor verification
keep their existing behavior. Public interfaces and configuration do not
change. No remote contact, publication enforcement, credential access, or
source-repository mutation is added.

## Scope and Alternatives

- Owning implementation: `flowgency/git_evidence/publication.py`.
- Reuse `tests/test_git_evidence.py` and its real Git fixtures.
- Keep the original two alias/packed-ref regressions. Add real linked-root
  and intermediate-directory cases asserting the exact domain error.
- Do not discard the useful original guards, swallow arbitrary exceptions,
  or follow an alias merely because its final target is inside the repository.
- No UI, dependency, unrelated cleanup, transport, or test-policy changes.
- This path validation is not a new claim of atomic protection against a
  filesystem mutation between inspection and open; no race-safe I/O redesign
  is part of this bounded fix.

## Verification and Integration

Establish a clean complete Python-suite baseline in the new worktree before
applying the preserved patch. Add the linked-root regression first, observe
its failure against the preserved patch, repair the root cause, and rerun
the same check immediately. Run the full Git-evidence test module, then the
complete Python suite before review and integration. Do not skip or weaken
unrelated live checks to manufacture a passing suite. No browser layout
changes are made, so the existing browser gate is not repeated for this fix.

Commit the specification and implementation plan separately from code.
Review the complete hardening change including the inherited edits, then
follow the repository's fast-forward, complete-master-suite, push-both-refs,
archive, and normal worktree-cleanup procedure. Preserve unrelated edits,
runtime config, locks, logs, prior archives, and the original patch backup.