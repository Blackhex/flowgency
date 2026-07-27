# Prompt Frontmatter Diagnostics And Setup Prevention

Date: 2026-07-27

## Problem

A clean Agency setup produces blueprint task prompts that fail validation, reports them with the wrong corrective hint, and drops the entire dashboard into the first-run setup page with no reachable remedy.

Observed on the setup page after a clean install:

```
prompts
Prompt markdown frontmatter is incomplete: .agents/prompts/diff-review.prompt.md.
Use unique prompt names across blueprint and instance scopes.
```

Three independent defects produce this single symptom.

### Defect 1: invalid prompt data

The blueprint task prompts written into the agent library carry no YAML frontmatter. `parse_prompt_document` in `agency/prompts/assets.py` requires a terminated `---` frontmatter block containing `name` and `description`, so every one of these files fails to parse.

### Defect 2: the setup skill never specified the prompt contract

`skills/agency-setup/references/templates.md` provides a complete frontmatter template for `SKILL.md`, but reduces task prompts to a single sentence naming the file location. It states no frontmatter requirement. `SKILL.md` Phase 5 lists blueprint and Agent Skill validation but omits prompt documents, and still says "routine skill" where Phase 4 selects a scoped prompt. A setup session therefore writes plain markdown prompt files and never checks them. The defect reproduces on every clean setup.

### Defect 3: the validator masks the real issue

`AssetValidationError` derives from `ValidationFailed`, which derives from `ValueError`. `validate_prompt_catalogs` in `agency/prompts/catalog.py` catches bare `ValueError`, discards the precise `ValidationIssue` the parser already constructed, and re-labels it `invalid-prompt-catalog` with the corrective hint for name collisions. The operator receives an accurate message paired with an unrelated remedy.

### Consequence: unreachable dashboard

`build_services` raises `ValidationFailed(catalog_issues)`, which sets `startup_error` and leaves `blueprint_library`, `prompt_store`, and `instances` as `None`. Every route degrades to the setup page. One malformed file in one blueprint blocks unrelated groups, and the only remedy the setup page offers is choosing a project folder and integration, neither of which repairs a prompt file.

## Goals

- A clean setup produces valid prompt documents, verified before the config write.
- A malformed prompt document reports its own precise message and corrective hint.
- A malformed prompt document degrades to the agents that reference it instead of blocking the dashboard.

## Non-goals

- Auto-repairing files in the agent library.
- Adding a blueprint-scoped prompt writer API, route, or generator. Blueprint source stays hand-authored under Agent Library authorship.
- Changing instance-scoped `PromptStore` behavior.
- Any startup conversion or directory-shape loading. `config.yaml` remains the sole control-plane authority.

## Design

### 1. Prevention in the setup skill

Both skill copies change: `skills/agency-setup/` and its mirror `.github/skills/agency-setup/`.

**Task Prompt template.** `references/templates.md` gains a "Standard Task Prompt" section peer to the existing "Standard Agent Skill" section:

```markdown
---
name: {prompt}
description: {ONE_LINE_PURPOSE}
argument-hint: {OPTIONAL_ARGUMENT_SUMMARY}
---

# {Prompt Title}

{TASK_INSTRUCTIONS}
```

The section states the contract enforced by `agency/prompts/assets.py`:

- The file lives at `{agent_library}/{blueprint}/.agents/prompts/{prompt}.prompt.md`.
- `name` exactly equals the file slug.
- The slug is lowercase letters, digits, and single hyphen separators, 1 to 64 characters.
- `description` is a non-empty string of at most 1024 characters.
- `argument-hint` is optional and, when present, a string. Omit it when the prompt takes no arguments.
- No keys other than `name`, `description`, and `argument-hint` are permitted.
- The markdown body after the closing fence is non-empty.

**Phase 5 validation.** `SKILL.md` Phase 5 adds prompt-document validation to its validation list and corrects the stale "routine skill" wording to "routine prompt". Validation runs before the atomic config write, and a failure stops the write.

**Mechanical verification.** A new CLI command performs the check so the setup session proves validity instead of asserting it:

```text
christag-agency validate --config "{config_path}"
```

The command loads services and prints every collected `ValidationIssue` grouped by scope, showing code, field, message, and corrective hint. It exits non-zero when any issue exists and zero otherwise. Phase 5 runs it before the config write and stops on a non-zero exit. The same command is the operator's recovery check for an already-broken library.

### 2. Failure model

`validate_prompt_catalogs` stops being a startup gate and becomes a startup reporter.

`AgencyServices` gains `prompt_issues: tuple[ValidationIssue, ...] = ()`. `build_services` calls `validate_prompt_catalogs` as it does today, stores the result on that field, and does not raise. Services are fully constructed and `startup_error` stays `None`. Genuinely fatal conditions, including a missing or invalid config and unresolvable storage paths, keep their existing fatal behavior.

Enforcement moves to the call sites that already resolve prompts:

- `agency/jobs/resolution.py` `_resolve_saved_prompt` catches only `KeyError` and `PromptNotFoundError`, so an `AssetValidationError` propagates intact rather than being flattened into a missing-prompt error. Job submission for a broken prompt fails with the precise issue. No change required.
- `PromptService.catalog` keeps raising. Its contract is unchanged; presentation is the caller's decision.
- The two route-level adapters degrade per agent instead of failing the whole page. `_available_prompts` in `agency/web/routes/agent_detail.py` and `_launcher_prompts` in `agency/web/routes/agents.py` catch `ValidationFailed`, return an empty prompt list for that agent, and pass the carried issues into the template context. `_launcher_prompts` currently raises HTTP 409 for the entire roster when any one agent's catalog fails to resolve; that becomes a per-agent degradation so a single broken blueprint no longer hides every other agent in the group. Other exception types keep their existing 409 behavior.

Net effect: the dashboard boots, agents with valid blueprints work normally, and agents with a broken catalog render their diagnostic and refuse to submit the affected prompt.

### 3. Diagnostic correctness

`_validate_effective_catalog` raises `ValidationFailed` carrying a structured issue instead of a stringly-typed `ValueError`. It already receives `group_id` and `agent_id`:

```text
code            invalid-prompt-catalog
scope           groups.{group_id}.agents.{agent_id}
field           prompts
message         Prompt '{name}' exists in both blueprint and instance scopes for {group_id}/{agent_id}.
corrective_hint Use unique prompt names across blueprint and instance scopes.
```

`validate_prompt_catalogs` drops its `except ValueError` branch entirely and collects `.issues` from any `ValidationFailed`. The `PromptNotFoundError` branch is unchanged and ordered first.

Issues carried up from `agency/prompts/assets.py` are re-emitted with `code`, `message`, `corrective_hint`, and `field` untouched. Only `scope` is re-attributed from `prompt` to `groups.{group_id}.agents.{agent_id}` so the operator knows which agent is affected; the source file path remains visible in `field`.

Results are deduplicated on `(code, field, message)`, preserving first-seen order, so one broken blueprint shared by several agents reports once.

Removing the bare `except ValueError` is the substance of this change rather than a workaround. Every structured error in this domain derives from `ValueError`, so that clause silently relabels any structured error and would do so again for the next error type added upstream.

After this change the reported diagnostic becomes:

```
Prompt markdown frontmatter is incomplete: .agents/prompts/diff-review.prompt.md.
Terminate the YAML frontmatter before the prompt body.
```

### 4. Surfacing

Prompt issues appear where they are actionable:

- The agent detail Prompts section shows the issues scoped to that agent with message and corrective hint, and omits the unreadable catalog entries from the prompt selector.
- `christag-agency validate` prints all issues and exits non-zero.
- The setup page keeps its Startup diagnostics block for fatal startup errors only. Prompt issues no longer appear there because they no longer force the setup fallback.

### 5. Repair of existing data

The four affected files live in the operator's agent library outside this repository, at `{agent_library}/{blueprint}/.agents/prompts/`. Repair is a data fix, not a code change.

| Blueprint | Prompt |
| --- | --- |
| `reviewer` | `diff-review` |
| `architect` | `authority-audit` |
| `test-engineer` | `suite-health` |
| `docs-writer` | `docs-audit` |

Each file gains frontmatter with `name` equal to its slug and a one-line `description` derived from its existing body. None of the four takes arguments, so `argument-hint` is omitted. Bodies are unchanged.

## Setup skill end-to-end test

No existing test exercises the artifacts a setup run produces. `tests/test_agency_setup_skill.py` asserts prose in `SKILL.md`, and `tests/test_interactive_setup.py` covers launcher mechanics. Neither feeds a produced agent library to the real loaders, which is why a prompt template that never existed went unnoticed.

A new `tests/test_setup_skill_e2e.py` closes that gap by treating the skill's own templates as executable input and the application's validators as the oracle.

**Materialize.** The test extracts the fenced `markdown` blocks for Blueprint AGENTS.md, Standard Agent Skill, and Standard Task Prompt from `references/templates.md`, substitutes every `{PLACEHOLDER}` with a conforming concrete value, and writes a temporary agent library:

```text
{tmp}/agent-library/reviewer/AGENTS.md
{tmp}/agent-library/reviewer/.agents/skills/diff-review/SKILL.md
{tmp}/agent-library/reviewer/.agents/prompts/diff-review.prompt.md
```

It then writes a `config.yaml` built from the canonical group registration YAML template in the same file, repointed at temporary storage roots, declaring one group, one agent instance bound to the `reviewer` blueprint, and one routine selecting the blueprint-scoped `diff-review` prompt.

**Assert.** Against that library and config:

- `build_services` returns fully constructed services with `startup_error is None` and `prompt_issues == ()`.
- `BlueprintLibrary.inspect("reviewer")` yields the skill and a `PromptDocument` whose `name` equals the file slug and whose `description` is the substituted value.
- `christag-agency validate --config {path}` exits zero and reports no issues.

**Negative control.** The same fixture with the prompt frontmatter stripped must make `validate` exit non-zero and report `invalid-prompt-frontmatter` with the hint about terminating the frontmatter. This proves the test would have caught the reported defect rather than merely passing alongside it.

**Template parity.** The extracted templates from `skills/agency-setup/references/templates.md` and `.github/skills/agency-setup/references/templates.md` must be identical, so the two skill copies cannot drift apart silently.

This test fails today for two independent reasons: no Task Prompt template exists to extract, and the resulting library would not validate. Both are fixed by this design.

## Testing

- `validate_prompt_catalogs` returns the parser's own issue verbatim for a blueprint prompt with no frontmatter: code `invalid-prompt-frontmatter` and the hint about terminating the frontmatter. This is the regression test for the reported defect.
- A prompt name present in both blueprint and instance scope still yields `invalid-prompt-catalog` with the unique-names hint, now raised as `ValidationFailed`.
- A missing instance prompt still yields `missing-instance-prompt`.
- One broken blueprint shared by two agents collapses to a single issue.
- `build_services` returns usable services with populated `prompt_issues` and `startup_error is None` when a blueprint prompt is malformed, and a group with clean blueprints continues to function.
- Agent detail renders rather than returning a 500 for an agent with a broken catalog, and shows the diagnostic.
- The agents roster renders for a group in which one agent has a broken catalog, listing the remaining agents with their prompts intact.
- Job submission for a broken prompt fails with the structured issue rather than a missing-prompt error.
- `christag-agency validate` exits non-zero and prints the issue for a broken library, and exits zero for a clean config.
- `tests/test_agency_setup_skill.py` asserts the prompt template exists in both skill copies and that Phase 5 references prompt validation.
- `tests/test_setup_skill_e2e.py` covers the end-to-end template conformance described above, including the negative control and template parity between the two skill copies.

## Risks

- Making catalog issues non-fatal widens the set of states in which the dashboard runs. The point-of-use guards and the agent detail surface keep a broken catalog visible rather than silent.
- Two skill copies must stay identical. The skill test covers both.
