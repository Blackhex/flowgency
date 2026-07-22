# Task 3 Report: Mandatory Roots in Effective Runtime Policy

## RED/GREEN evidence
- RED: `\.venv\Scripts\python -m pytest tests\test_effective_policy.py tests\test_job_submission.py -q` -> `5 failed, 31 passed in 1.21s`
  - Failing coverage included missing mandatory workspace/group roots in effective policy and serialized job runtime policy.
- GREEN: `\.venv\Scripts\python -m pytest tests\test_effective_policy.py tests\test_job_submission.py -q` -> `36 passed in 1.29s`
- Selected verification: `\.venv\Scripts\python -m pytest tests\test_effective_policy.py tests\test_job_submission.py tests\test_agent_detail.py tests\test_cli.py -q` -> `91 passed in 41.77s`
- Full suite: `\.venv\Scripts\python -m pytest tests\ -q` -> `1217 passed, 3 skipped in 149.58s (0:02:29)`

## Exact commands/results
1. RED: `Set-Location 'C:\Projekty\christag-agency\.worktrees\group-storage-redesign'; .\.venv\Scripts\python -m pytest tests\test_effective_policy.py tests\test_job_submission.py -q`
   - Result: `5 failed, 31 passed`
2. GREEN: `Set-Location 'C:\Projekty\christag-agency\.worktrees\group-storage-redesign'; .\.venv\Scripts\python -m pytest tests\test_effective_policy.py tests\test_job_submission.py -q`
   - Result: `36 passed`
3. Verification: `Set-Location 'C:\Projekty\christag-agency\.worktrees\group-storage-redesign'; .\.venv\Scripts\python -m pytest tests\test_effective_policy.py tests\test_job_submission.py tests\test_agent_detail.py tests\test_cli.py -q`
   - Result: `91 passed`
4. Full suite: `Set-Location 'C:\Projekty\christag-agency\.worktrees\group-storage-redesign'; .\.venv\Scripts\python -m pytest tests\ -q`
   - Result: `1217 passed, 3 skipped`
5. Diff check: `Set-Location 'C:\Projekty\christag-agency\.worktrees\group-storage-redesign'; git --no-pager diff --check`
   - Result: clean

## Changed files
- `agency/configuration/effective.py`
- `agency/jobs/resolution.py`
- `tests/test_effective_policy.py`
- `tests/test_job_submission.py`

## Self-review
- Restricted sandbox resolution now always prefixes `workspace_root` then `group_root`, then merges configured group roots and agent additional roots with de-duplication.
- Unrestricted policies remain rootless.
- Job resolution now uses the shared `resolve_effective_policy(...)` path with the already bound integration for final runtime validation.
- No compatibility aliases or inferred roots were added.

## Concerns
- None.
