# Task 2 Report: Native Prompt Rendering And Shared Runtime Projection

Date: 2026-07-26
Worktree: C:/Users/black/Projects/christag-agency/.worktrees/portable-agent-prompts
Task brief: .superpowers/sdd/2026-07-26-portable-agent-prompts/task-2-brief.md

## Scope Implemented
Implemented only Task 2 requirements:
- Structured native prompt rendering module.
- Explicit projector prompt capabilities.
- Named runtime projector version bump from version 1 to version 2 semantics.
- Prompt projection included in shared runtime inventories/validation.
- Capability constructor updates in required tests and defaults.
- Dependency update for structured TOML writer.

No private prompt storage, schema v4, jobs/web feature work was introduced.

## Files Changed
- agency/prompts/projection.py (new)
- agency/prompts/__init__.py
- agency/projector_capabilities.py
- agency/blueprints/projectors.py
- agency/integrations/__init__.py
- pyproject.toml
- tests/test_runtime_projectors.py
- tests/test_compilation_cache.py
- tests/test_cache_locking.py
- tests/test_job_submission.py

## Implementation Details
1. Added prompt renderer in agency/prompts/projection.py:
- PromptProjectionFormat: "prompt-markdown" | "markdown-command" | "gemini-toml"
- render_prompt(document, target, format) -> (path, bytes)
- Uses tomli_w.dumps for Gemini TOML payload:
  {"description": document.description, "prompt": document.body}

2. Exported PromptProjectionFormat and render_prompt from agency/prompts/__init__.py.

3. Extended ProjectorCapabilities in agency/projector_capabilities.py:
- prompts_target: PurePosixPath | None
- prompt_format: PromptProjectionFormat | None
- discovers_prompts: bool

4. Updated static projector behavior in agency/blueprints/projectors.py:
- Added project_prompt_documents(documents, destination) -> tuple[PurePosixPath, ...].
- _mapped_paths now parses canonical .agents/prompts/*.prompt.md files via parse_prompt_document and merges render_prompt outputs.
- validate_output now inventories prompt target roots when configured.
- Named projector capabilities set explicitly:
  - copilot: .github/prompts + prompt-markdown + discovers_prompts=True
  - claude-code: .claude/commands + markdown-command + discovers_prompts=True
  - gemini: .gemini/commands + gemini-toml + discovers_prompts=True
- Named projector version strings bumped to version-2 semantics ("2").

5. Updated integration default projector in agency/integrations/__init__.py:
- prompts_target=None
- prompt_format=None
- discovers_prompts=False

6. Added dependency in pyproject.toml:
- tomli-w>=1,<2

7. Updated required tests for explicit constructor fields:
- tests/test_compilation_cache.py
- tests/test_cache_locking.py
- tests/test_job_submission.py

8. Added renderer tests in tests/test_runtime_projectors.py:
- Markdown formats preserve PromptDocument.source bytes for Copilot and Claude projections.
- Gemini renderer produces structured TOML decoded by tomllib with expected keys/values.

## TDD Evidence
### RED
Command:
- python -m pytest tests/test_runtime_projectors.py -k "prompt" -v

Observed failure:
- ModuleNotFoundError: No module named 'agency.prompts.projection'

### GREEN (focused)
Command:
- python -m pip install -e .
- python -c "import tomli_w; print('tomli_w ok')"
- python -m pytest tests/test_runtime_projectors.py -k "prompt" -v
- python -m pytest tests/test_runtime_projectors.py tests/test_compilation_cache.py tests/test_cache_locking.py -v

Observed:
- Editable install first hit WinError 32 lock, then succeeded with:
  python -m pip install --user -e .
- tomli_w import succeeded: tomli_w ok
- Prompt-focused runtime projector run: 2 passed, 15 deselected
- Projector/cache suite: 47 passed

## Full Suite Evidence
Command:
- python -m pytest tests/ -q

First run:
- 1 failure from repository boundary policy: prohibited term "v2" in agency/blueprints/projectors.py

Fix applied:
- Replaced projector version string literals from "v2" to "2" while preserving version-2 bump semantics.

Final run:
- 1277 passed, 2 skipped in 190.67s (0:03:10)

## Self-Review
- Confirmed prompt markdown projections preserve source bytes exactly for Copilot and Claude targets.
- Confirmed Gemini prompt projection uses deterministic structured TOML from parsed prompt fields.
- Confirmed prompt paths are included in runtime projector expected map and output validation root inventory.
- Confirmed default projector behavior is explicit about no prompt support.
- Confirmed constructor updates are explicit (no hidden defaults) in required tests.
- Confirmed full suite passes after resolving prohibited-term boundary test conflict.

## Concerns
- Requested interpreter path C:/Users/black/Projects/christag-agency/.venv/Scripts/python.exe does not exist in this environment; active interpreter used instead.
- Editable install initially failed with WinError 32 due file lock and required retry with --user.

## Fix round 1
Important finding addressed (verbatim in substance):
- tests/test_runtime_projectors.py unit-tests render_prompt() but never puts a real .agents/prompts/<slug>.prompt.md into a projector source snapshot and runs _mapped_paths -> project -> validate_output. Therefore the actual shared-runtime projection branch and prompt inventory validation are untested.

Changes made:
- Added `PROMPT_SOURCE` fixture data in `tests/test_runtime_projectors.py` for a canonical `.agents/prompts/pr-review.prompt.md` document.
- Added parameterized integration-level test `test_projector_projects_prompt_assets_and_validates_output` covering `copilot`, `claude-code`, and `gemini`.
- For each integration, the test creates a real source snapshot with `.agents/prompts/pr-review.prompt.md`, runs `projector.project(...)`, asserts exact projected native path and exact bytes/content, and asserts `projector.validate_output(...) == ()`.
- Renamed `test_gemini_renderer_uses_structured_toml` to `test_prompt_gemini_renderer_uses_structured_toml` so the Gemini case is included by the focused `-k "prompt"` command.

Covering test commands and exact output:
- `python -m pytest tests/test_runtime_projectors.py -k "prompt" -v`
  - Collected: 20 items / 14 deselected / 6 selected
  - Passed nodes:
    - tests/test_runtime_projectors.py::test_projector_projects_prompt_assets_and_validates_output[prompt-copilot]
    - tests/test_runtime_projectors.py::test_projector_projects_prompt_assets_and_validates_output[prompt-claude-code]
    - tests/test_runtime_projectors.py::test_projector_projects_prompt_assets_and_validates_output[prompt-gemini]
    - tests/test_runtime_projectors.py::test_markdown_prompt_renderers_preserve_source_bytes[target0-prompt-markdown-expected_path0]
    - tests/test_runtime_projectors.py::test_markdown_prompt_renderers_preserve_source_bytes[target1-markdown-command-expected_path1]
    - tests/test_runtime_projectors.py::test_prompt_gemini_renderer_uses_structured_toml
  - Final: 6 passed, 14 deselected in 1.68s
- `python -m pytest tests/test_runtime_projectors.py tests/test_compilation_cache.py tests/test_cache_locking.py -v`
  - Collected: 50 items
  - Final: 50 passed in 2.98s

Files in this fix round:
- tests/test_runtime_projectors.py
- .superpowers/sdd/2026-07-26-portable-agent-prompts/task-2-report.md

Commit:
- Pending (recorded after commit)

Self-review:
- Confirms previously untested runtime branch now executes through projection + inventory validation for all required integrations.
- Confirms Gemini prompt projection coverage is selected by the prompt-focused test filter.
- No production behavior change was required; fix is test-only.
