# Task 8 Report

## Status

Implemented and self-reviewed.

## RED/GREEN evidence

- RED: `.venv\Scripts\python -m pytest tests\test_agency_setup_skill.py tests\test_repository_boundaries.py tests\test_server.py -q`
  - `43 passed, 1 failed`; the failure was the pre-existing tracked `task-7-report.md` `legacy` reference.
- GREEN: `.venv\Scripts\python -m pytest tests\test_agency_setup_skill.py tests\test_repository_boundaries.py tests\test_server.py tests\test_cli_contract.py tests\test_setup_flow.py -q`
  - `99 passed`.
- Final full suite: `.venv\Scripts\python -m pytest tests\ -q`
  - `1220 passed, 3 skipped`.

## Search and fixture validation

- `rg -n 'schema_version:\s*2|group\.path.*/.*shared|\["shared"\]|shared/(observations|proposals|decisions|jobs|logs)|workspace_dir|group_path=' agency tests CLAUDE.md README.md kb skills examples`
  - No matches.
- `.venv\Scripts\python -c "from pathlib import Path; from agency.configuration import ConfigStore; ConfigStore(Path('tests/ui/fixtures/config.yaml')).load(); print('valid')"`
  - `valid`.
- `git diff --check`
  - Passed.
- `git status --short` and `Get-ChildItem -Force .\shared`
  - No repository-local runtime `shared` directory or generated lock file was staged or changed.

## Changes

- Updated schema 3 guidance, storage tree, setup skill, templates, examples, and `config.yaml.example`.
- Migrated UI fixtures/server and test-only group fixtures to separate workspace and Agency group roots.
- Removed reload filtering for `shared`; added repository-boundary and external-root reload coverage.
- Removed obsolete job/script compatibility tokens and migrated strict job/config builders.
- Removed obsolete example `shared` prompt/memory fixtures; preserved historical design/plan documents.
- Added the required EOF newline and fixed contract-test formatting/path fixtures.

## Self-review

- Repository-boundary tests cover application path construction and reload behavior.
- Durable jobs remain under `memory_store/.jobs`; group logs and locks remain under the configured group root.
- Restricted policies retain workspace plus group-root access.
- No parent-checkout runtime data or lock files were staged.

## Concerns

- Three full-suite skips remain platform/environment-gated existing tests.
- Example per-agent `CLAUDE.md` files remain as explanatory templates only; runtime does not load them.

## Commit

- Final Task 8 commit: `docs(storage): adopt external group roots` (hash reported in completion status).
