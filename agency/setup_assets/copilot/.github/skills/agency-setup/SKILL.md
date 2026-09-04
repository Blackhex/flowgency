---
name: agency-setup
description: Use when creating or registering a new Agency agent team for a codebase.
user_invocable: true
---

# Agency Setup

The `agency-setup` skill owns the one authoritative canonical Agency config.
Guided first-run setup supplies an approved Agency data root, authoritative
config path, and supported AI integration; manual invocation collects missing
context explicitly. The skill selects and inspects the first team project
workspace, derives canonical storage paths, and then owns team naming,
blueprint source, explicit instances, routines, runtime policy, workspaces,
memory, validation, and the one atomic config write.

## 1. Resolve Launch Context And Project Workspace

Consume the launch context before asking questions. Guided mode requires both
the exact `Setup mode: guided-first-run.` marker and an `Agency data root:`
line. In guided mode, use that root as already selected and do not ask for the
data root again; ask for the first team project workspace as the first
user-facing question.

In manual mode, without that complete guided context, ask for the Agency data
root first. Explain that it is a separate home for Agency-owned data: reusable
agent blueprints, disposable compiled projections, semantic memory and durable
jobs, and per-team records. Accept an existing directory or a new absolute path,
expand user-home syntax, and require a writable real nearest parent for a
missing root. Give `C:\Agency` and `~/Agency` as examples, then ask for the first
team project workspace. No environment variable or hidden process state selects a mode.

After the project workspace is selected, inspect that workspace read-only.
Read project instructions, README, dependency manifests, source layout, tests,
deployment files, and recent git history. Detect the host OS and available
agent CLI. Do not ask about the team, agents, roles, routines, workspaces,
memory channels, or individual storage paths before this inspection completes.

Build and retain a working context from inspected facts: the project's domain
and stated purpose; languages, frameworks, and architectural boundaries;
repository maturity, source and test organization, and documentation quality;
build, deployment, CI, release, and operational signals; apparent work streams,
risks, maintenance pressure, and missing capabilities; the selected integration;
and the exact project workspace. Include any user-stated near-term priorities.
Keep this context in the conversation only; do not write a digest or scratch
file.

Before team design, summarize this working context in user-facing prose. Name
concrete project characteristics and distinguish inspected facts from
assumptions; file-path citations are not required. Do not invent technologies,
delivery cadence, business goals, or operational needs. If the workspace cannot
be inspected, identify what was inaccessible and return to project workspace
selection before proposing a team. If the evidence is too sparse to support a
grounded proposal, ask exactly one focused question about near-term priorities
or current pain points, incorporate that answer into the working context, and
then continue. Do not fall back to a stock team.

Derive these paths in memory without creating them:

```text
agency.agent_library = <root>/agent-library
agency.compilation_cache = <root>/compiled-agents
agency.memory_store = <root>/memory
agency.prompt_store = <root>/prompts
teams.<team-id>.path = <root>/teams/<team-id>
teams.<team-id>.workspace_path = <project workspace>
```

The team path remains pending until the team ID is approved.

The guided launcher may already have created the selected data root so it can
serve as the session working directory. Derive every child path in memory only.
No derived directory or blueprint may be created before the user approves the
consolidated path summary.

If validation fails in guided mode, return to the project-workspace choice or
the grouped path review; do not ask for the supplied data root again. In manual
mode, validation may return to the root choice or grouped path review.

## 2. Synthesize And Approve The Team

After inspection and any sparse-evidence clarification, ask the user to approve
the team display name and stable team ID. Require a lowercase stable ID using
only letters, digits, and single hyphen separators. Then ask for an initial
positive integer agent count. Do not generate a team draft until the team name,
ID, and count are approved. Derive `teams.<team-id>.path` after the ID is
approved.

Do not ask the user to select candidate roles or profiles during team and count
collection. Once the team name, ID, and count are approved, present the first
complete team draft before asking any other question. Do not ask about storage
paths, routines, schedules, memory, or channels until after one consolidated
team approval. The first draft contains complete operating profiles, not a
role-selection form.

Generate the first complete team draft with exactly that many profiles. The
draft must contain exactly the approved initial count. Synthesize it from inspected project facts,
the approved group concept, the requested count, any priority answer, the
selected integration, and the exact workspace. Do not offer a fixed candidate
slate or ask which generic roles to instantiate. Label unsupported assumptions.
When the launch prompt contains `Selected integration:`, use that registered
integration for `team.default_integration` and the initial agent instances
unless the user explicitly approves a different registered integration.

For every proposed agent, communicate all of the following semantic categories
in any clear layout that remains unambiguous and reviewable. Closely related
optional categories may be combined (for example, routines and schedules may be
addressed together); team-wide assumptions may appear once in the consolidated
coverage summary:

```text
- stable name (`name`) using a valid lowercase hyphenated slug;
  reusable blueprint slug and broad role
- display identity: display_name, title, and optional emoji
- mission
- distinct responsibilities and ownership boundaries
- explicit handoffs to other proposed agents
- rationale citing inspected project characteristics and prior answers,
  with labeled assumptions
- selected integration and intended workspace use
- proposed runtime permissions on exact paths;
  write rationale where write access is present
- routine tasks and prompt purposes, or explicit absence
- schedules where a recurring cadence is supported, or explicit absence
- memory scopes and shared channels where continuity requires them,
  or explicit absence
- remaining assumptions or none
```

The rationale names which inspected project characteristics and prior answers
justify the profile; a generic statement that an agent helps with the project is
not sufficient. `None proposed` is valid for optional emoji, routines,
schedules, memory, and channels. Do not invent recurring work, cadence, or
shared memory merely to populate the profile.

Before presenting the team-decision form, verify that the number of complete
profiles equals the approved count and every required semantic category is
identifiable in each profile. Put the complete profiles in the message that
precedes the team-decision form; do not replace them with compact prose or a
summary.

When the approved group concept clearly establishes a naming theme, adapt
display names, titles, and optional emoji coherently without obscuring each
agent's function; every `identity.display_name` and `identity.title` must
visibly acknowledge it. Stable slugs and broad role labels alone do not satisfy
themed identity. When the theme is ambiguous, use domain-specific functional
identities; do not force a theme or invent unexplained personas. Use purely
functional identities only when the theme is ambiguous or the user explicitly
declines themed identities. Stable instance and blueprint slugs remain valid
and unique.

Agents may share a broad role when their responsibilities, ownership boundaries,
or routines differ materially. They may share a blueprint only when their
reusable behavior and working method are genuinely the same. Different reusable
behavior requires distinct blueprints even when agents share a broad role.

For proposed new agents, read and search are the baseline. Derive write access
from approved implementation responsibilities. Multiple new agents may receive
write when their distinct responsibilities require it. For every write-enabled
profile, show the exact project workspace path and explain why write is required.
Team approval includes approval of every displayed permission grant. Never infer
write authority for an existing agent; keep an ambiguous new-agent grant visible
as an assumption for targeted review.

Present all profiles together for one consolidated team review. Follow them with
a coverage summary naming major project needs and their owning agents;
intentional shared roles and how they differ; handoffs and collaboration paths;
uncovered needs and explicit assumptions; every write-enabled agent and exact
writable path; routine cadence and memory or channel relationships; and the
current exact agent count and preserved survivors. Allow targeted edits without
forcing agent-by-agent approval.

The user may accept the first draft, edit profiles, or replace the agent count.
When the count changes, ask which existing profiles must survive unchanged. If
the selected survivors outnumber the revised count, require the user to reduce
the survivor set or increase the count. Preserve every selected survivor profile
verbatim, including identity, mission, responsibilities, permissions, routines,
schedules, workspace use, and memory. Synthesize every remaining slot from the
complete working context and current team coverage gaps. Do not mechanically
truncate the previous draft or append generic roles.

If survivors consume all slots while leaving an important need uncovered, show
the uncovered need instead of rewriting a survivor. Let the user edit or release
a survivor, change the count, or approve the documented gap. Re-run the
team-level consistency check after every count or profile change: exact count,
stable names, coverage, overlaps, handoffs, permissions, cadence, memory, and
assumptions. If the user accepts the first draft without changing count or
profiles, it becomes the final team without a redundant second proposal. Obtain
one consolidated team approval before continuing to storage-path approval.

The operating profile is a conversational review model. After approval, map
stable name, identity, integration, runtime permissions, routines, default
memory, and prompt registrations to existing instance config fields; map
workspace and shared-channel choices to existing team and memory config fields;
reusable behavior becomes blueprint instructions; and project-specific task
instructions become scoped prompt documents. Proposal rationale, coverage
analysis, and handoff explanation remain conversational unless an approved
detail belongs in one of those existing surfaces. Do not persist new `mission`,
`rationale`, `ownership`, `handoffs`, or `coverage` keys. Keep team drafts,
survivor choices, and the working context in this conversation only.

Ask exactly once: `Customize the derived storage paths?` If declined, keep every derived path. Do not ask about individual storage paths in the default flow. If accepted, present all five storage paths in one grouped review and allow any of them to be replaced.

Resolve every effective path before creation. Require that each missing effective path's nearest existing parent is a writable real directory that can safely create it, reject files, symlinks, and unsafe Windows reparse points, keep the global stores mutually disjoint, and keep every Agency-owned path disjoint from the project workspace. If validation fails, name the conflicting fields and resolved paths and return to the root choice or grouped review. Never choose a fallback location or project-local storage.

Show one consolidated path summary containing the project workspace, authoritative config path, Agency data root, and five effective storage paths. No derived directory or blueprint may be created before the user approves this summary.

When the launch prompt contains `Authoritative config:`, use that exact path and do not search for or choose another config. When the skill is invoked manually without an explicit authoritative path, find one config in this order: a valid `AGENCY_CONFIG`, the current project's config, then common user-level Agency locations. Parse YAML and accept only a mapping where the required `agency.agent_library`, `agency.compilation_cache`, `agency.memory_store`, and `agency.prompt_store` paths are present.

If no config exists, record the absent revision and defer creation and replacement until Section 5. Do not write a placeholder or partial config. If an existing candidate is invalid or superseded, report validation errors and stop; never invoke another skill, never scan or convert superseded authority, and never convert old layouts. During manual invocation, if multiple canonical configs remain, ask the user which is authoritative; never choose implicitly.

Load the current revision before editing, or use the absent revision when the file does not exist. Preserve unrelated keys and teams while building the complete candidate in memory. Do not replace the authoritative config during inspection, blueprint creation, or instance registration.

## 3. Build The Agent Library

After the consolidated path summary is approved, create the approved `agency.agent_library` through safe directory operations; do not place blueprints under the project workspace. For each approved role, always create:

```text
{agent_library}/{blueprint}/
`-- AGENTS.md
```

Write `{agent_library}/{blueprint}/AGENTS.md` from `references/templates.md`. Blueprints may contain zero or more standard Agent Skills. For each approved routine capability, create `{agent_library}/{blueprint}/.agents/skills/{skill}/SKILL.md`. Do not create a placeholder skill or an empty `.agents/skills` directory for a role without approved routine capabilities. Skill frontmatter must contain a directory-matching `name` and a trigger-only `description`. Put supporting scripts, references, and assets inside the skill directory.

Blueprint files contain reusable instructions only. Translate only behavior
that remains reusable across projects into the blueprint mission,
responsibilities, boundaries, and working method. Put project-specific task
instructions in scoped prompt documents. Do not copy the conversational
rationale, coverage map, or project-specific ownership text into blueprint
source merely because it appeared in the approved profile. They do not contain
identity, integration, schedules, runtime policy, or mutable memory. Do not
create runtime-native `CLAUDE.md` or `GEMINI.md`; Agency's projector creates
disposable native layouts in `agency.compilation_cache`.

## 4. Register Instances

Upsert one team whose `workspace_path` points to the project workspace and whose `path` points to the Agency-owned team-state root. `workspace_path` is the execution workspace and source repository; `path` is the Agency-owned team root. The team root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`. Durable jobs live in `agency.memory_store/.jobs`, and operation locks live in `<team.path>/locks`. Preserve existing team workspaces and unrelated settings. Every instance explicitly pins a blueprint and integration. Runtime defaults belong to the team; instance roots are additive and an instance tool policy is a complete override.

Use this canonical shape:

```yaml
schema_version: 6
agency:
  title: Agency
  default_team: example
  ai_backend: copilot
  jobs:
    pool: 4
  agent_library: C:/Agency/agent-library
  compilation_cache: C:/Agency/compiled-agents
  memory_store: C:/Agency/memory
  prompt_store: C:/Agency/prompts
memory:
  channels:
    project-strategy:
      display_name: Project Strategy
teams:
  example:
    name: Example
    workspace_path: C:/Projects/example
    path: C:/Agency/teams/example
    default_integration: copilot
    runtime:
      timeout: 1800
      permissions:
        mode: restricted
        rules:
          - path: C:/Projects/example
            tools: [read, search]
    dispatch:
      enabled: true
    agents:
      - name: builder
        blueprint: builder
        integration: copilot
        identity:
          display_name: Builder
          title: Implementation Lead
        runtime:
          permissions:
            rules:
              - path: C:/Projects/example
                tools: [read, search, write]
        default_memory:
          scope: agent
        routines:
          - id: morning-review
            prompt:
              scope: blueprint
              name: morning-review
            schedule:
              at: "07:00"
            memory:
              scope: routine
          - id: strategy-review
            prompt:
              scope: blueprint
              name: strategy-review
            schedule:
              at: "21:00"
            memory:
              scope: channel
              channel: project-strategy
      - name: advisor
        blueprint: advisor
        integration: copilot
    workspaces:
      - name: Main workspace
        type: ide
        config:
          ide_name: VS Code
          project_path: C:/Projects/example
```

Record each approved Phase 2 routine assignment under that instance's `routines`. A routine selects one scoped prompt, one schedule (`at`, `every`, or supported condition), optional arguments, and optional semantic memory. Keep optional cross-task Agent Skills separate from routine prompt selection. Never write prompt filenames or per-agent dispatch maps.

Write authority is expressed through the workspace path rule. For each new
agent whose approved implementation responsibilities require write access,
include `write` in the `tools` list on a rule whose `path` is the team's exact
`workspace_path`; multiple new agents may receive write when their distinct
responsibilities require it. An agent may execute decisions only when its
effective policy grants `write` on that exact path, not a subdirectory. There is
no `capabilities` key. Never infer write authority for an existing agent. If a
new agent's approved responsibilities do not clearly require write, leave it
read/search-only or return the grant to targeted team review.

Write every approved workspace under the team's `workspaces` list. For a new team, do not omit the list after the user approves a workspace. Keep workspace configuration team-owned and non-authoritative.

## 5. Verify And Schedule

Validate every blueprint, Agent Skill, and prompt document, plus config cross-reference, registered explicit integration, effective root union, complete tool override, routine prompt selection, channel, workspace, team naming, and storage path. Confirm every prompt document against the Standard Task Prompt contract in `references/templates.md` before writing the config.

Re-read the authoritative config revision and stop on drift. Write one complete configuration atomically. Use Agency's revision-checked `ConfigStore.replace(expected_revision, complete_candidate)` for that single write; it initializes the approved cache, memory, durable-job, team, record, lock, and log directories. On revision drift, validation failure, or filesystem failure, stop without replacing the previous config and do not automatically remove approved directories or blueprint source. Then parse the final config from disk and confirm it is still the revision just written.

Then run the mechanical check and stop on a non-zero exit:

```text
christag-agency validate --config "{config_path}"
```

A non-zero exit means the created blueprint source is invalid. Report the printed issues and correct them; do not present setup as complete.

Then offer the singleton scheduler setup:

```text
christag-agency dispatch install --config "{config_path}"
christag-agency dispatch status --config "{config_path}"
```

There must be exactly one Agency dashboard and one singleton scheduler; do not create a fallback project scheduler.

Report the Agency data root, effective storage paths, blueprint keys, instance IDs, routines, semantic memory scopes/channels, authoritative config path, and scheduler status.
