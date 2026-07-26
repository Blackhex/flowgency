# Optional Blueprint Skills Design

## Context

Agency blueprint source currently uses two interoperable formats:

```text
<blueprint>/
|-- AGENTS.md
`-- .agents/skills/
    `-- <skill-name>/
        `-- SKILL.md
```

`AGENTS.md` is the open Markdown format for agent-facing project instructions.
Each skill directory follows the Agent Skills specification, while
`.agents/skills/` is the Agent Skills implementation guide's cross-client
discovery convention.

The blueprint validator currently rejects this otherwise valid source when it
contains `AGENTS.md` but no skills. This conflicts with the documented contract
that blueprint skills are optional and with the Agent Skills implementation
guidance for the no-skills case.

## Goal

Permit an instruction-only blueprint containing a root `AGENTS.md` and zero
Agent Skills, while preserving strict validation and projection of every skill
that is present.

## Non-Goals

- Do not change the blueprint directory layout.
- Do not introduce `skills/`, prompt files, custom-agent files, or subagent
  definitions into canonical blueprint source.
- Do not change routine, instance, integration, configuration, memory, or job
  schemas.
- Do not synthesize placeholder skills or report missing optional skills as a
  warning.
- Do not add compatibility loaders for other source layouts.

## Standards Boundary

The canonical blueprint remains a standards-based source tree:

- `AGENTS.md` is required at the blueprint root.
- `.agents/skills/<name>/SKILL.md` is optional and may occur zero or more times.
- Every present `SKILL.md` must satisfy the existing Agent Skills validation
  rules for location, directory name, frontmatter, encoding, and field lengths.
- Prompt and custom-agent formats remain integration-specific and outside the
  blueprint contract.

Agency projectors may continue relocating source files into an integration's
native runtime discovery paths without changing source bytes.

## Behavior

`inspect_blueprint()` will continue to capture the complete blueprint tree and
require a valid root `AGENTS.md`. It will collect and validate any standard
skills it finds. When it finds none, inspection will succeed and return
`BlueprintInspection.skills == ()` instead of raising
`missing-blueprint-skills`.

The following behavior remains unchanged:

| Blueprint source | Result |
| --- | --- |
| Missing `AGENTS.md` | Reject |
| Invalid `AGENTS.md` encoding | Reject |
| Valid `AGENTS.md`, no skills | Accept with `skills == ()` |
| Valid standard skills | Accept and list skills |
| A `SKILL.md` outside `.agents/skills/<name>/` | Reject |
| A malformed standard skill | Reject |

An empty `.agents/skills/` directory is neither required nor significant.
Filesystem snapshots and immutable digests are file-based, so an
instruction-only blueprint has `AGENTS.md` as its only projected source file.

## Projection And UI

The existing runtime projector already maps files independently. For an
instruction-only blueprint it will emit the integration's instruction target
and no skill directory. Output validation will compare that smaller expected
file set without special handling.

Existing Agent Library and Agent Detail views already iterate over the skills
tuple and therefore naturally render no skill entries for an empty tuple. The
routines editor will have no selectable skills for that blueprint. No new empty
state or warning is required for this correction.

## Setup Guidance

The Agency setup skill will describe blueprint skills as optional. It will
always create the approved role's `AGENTS.md`, and create
`.agents/skills/<skill>/SKILL.md` only for approved routine capabilities. A role
without a routine does not need a placeholder skill or an empty skills
directory.

## Error Handling

Remove only the `missing-blueprint-skills` validation failure. All failures for
a missing instruction file or an invalid present skill remain fail-closed with
their current structured issues and corrective hints.

No migration or fallback is needed. Existing instruction-only blueprints become
valid the next time they are inspected, while their bytes and digests remain
unchanged.

## Testing

Add focused regression coverage that:

1. Builds a blueprint containing only valid `AGENTS.md`.
2. Confirms inspection succeeds with `skills == ()`.
3. Confirms runtime projection emits only the integration instruction target
   and validates successfully.
4. Confirms a malformed skill is still rejected when a skill is present.
5. Confirms setup guidance explicitly permits zero skills for roles without
   approved routine capabilities.

Run the focused blueprint and projector tests during implementation, followed
by the complete pytest suite.

## Acceptance Criteria

- The existing `builder` blueprint with only `AGENTS.md` no longer produces the
  "Blueprint must contain at least one standard Agent Skill" banner.
- Blueprints retain the current `AGENTS.md` plus optional `.agents/skills/`
  layout.
- Invalid present skills remain validation errors.
- No placeholder content, new source layout, or schema change is introduced.
- Focused tests and the full suite pass.