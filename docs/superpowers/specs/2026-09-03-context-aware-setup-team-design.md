# Context-Aware Setup Team Design

**Date:** 2026-09-03
**Status:** Approved (design)

## Problem

First-run setup inspects the selected project, asks for a group name and ID,
asks how many agents to create, and then proposes agent roles. The canonical
`agency-setup` skill does not require the proposal to synthesize those inputs.
Its current team-planning contract says only to summarize the project, propose
three to five roles, ask for an agent count, and ask which roles to create.

That contract permits generic suggestions which neither reflect the repository
nor adapt to the user's group concept and requested team size. It also omits
instance identity fields and treats permissions, routines, schedules,
workspaces, and memory as later questions disconnected from the role proposal.
Static tests currently prove only that an agent count and role selection are
requested; they do not prove contextual grounding, exact-size synthesis, or
coherent identities.

## Goals

1. Ground the first team draft in inspected project facts and earlier setup
   answers.
2. Approve the group display name, stable ID, and initial positive agent count
   before generating the first team draft.
3. Generate the first full team draft at exactly the requested count.
4. Allow the user to change the count after seeing the draft.
5. Preserve user-selected draft survivors as complete profiles while
   resynthesizing all remaining positions coherently.
6. Propose complete operating profiles, including identity, responsibilities,
   permissions, routines, schedules, workspace use, memory, and rationale.
7. Allow multiple agents to share a broad role or blueprint when their
   responsibilities and operating profiles are materially distinct.
8. Infer project write access from proposed responsibilities and expose every
   grant for approval.
9. Preserve configuration authority, reusable-blueprint boundaries, path
   approval, validation, and one atomic config write.
10. Confine any application changes to launching `agency-setup` and processing
    that setup session's outputs.

## Non-Goals

- Adding configuration fields or changing schema version 5.
- Persisting an inspection digest or conversational setup state.
- Adding a browser form for team design.
- Changing application functionality outside the canonical `agency-setup`
  skill, the way that setup skill is launched, and the way its session outputs
  are processed.
- Changing data-root selection semantics, canonical skill packaging or
  discovery, normal agent execution, or non-setup integration behavior.
- Using external repository analysis services or network research.
- Forcing every agent to have a routine, schedule, memory selector, emoji, or
  write access.
- Updating an existing configured team's authority without explicit approval.
- Creating blueprints, prompts, directories, or config while drafting the team.

## Superseded Team-Planning Contract

This design supersedes the generic role-proposal paragraph introduced by the
Agency data-root setup work. Setup no longer proposes a fixed three-to-five role
slate and then asks which roles to instantiate. It asks for an initial count
first and produces one full draft with exactly that many profiles.

The existing launch-context, project-workspace selection, read-only inspection,
data-root derivation, path safety, grouped override, consolidated path approval,
blueprint creation, registration, validation, and atomic-write contracts remain
in force.

## Context Collection

After the project workspace is selected, setup performs the existing read-only
inspection before asking team-design questions. It retains a concise working
context containing:

- the project's domain and stated purpose;
- languages, frameworks, and major architectural boundaries;
- repository maturity, source and test organization, and documentation quality;
- build, deployment, CI, release, and operational signals;
- apparent work streams, risks, maintenance pressure, and missing capabilities;
- the selected integration and exact project workspace;
- any user-stated near-term priorities.

This context is conversational state only. It is not written to config, memory,
the project, or Agency-owned storage.

Setup summarizes its understanding in user-facing prose before team synthesis.
The summary need not cite file paths, but it must name concrete project
characteristics and distinguish inspected facts from assumptions. It must not
invent technologies, delivery cadence, business goals, or operational needs.

If the workspace cannot be inspected, setup identifies what was inaccessible
and returns to workspace selection. If the workspace is empty, unfamiliar, or
too sparse to justify a grounded proposal, setup asks exactly one focused
question about near-term priorities or current pain points. It incorporates the
answer into the working context before continuing and does not silently fall
back to a stock team.

## Team Synthesis Flow

After inspection and any sparse-evidence clarification, setup follows this
order:

1. Ask for and approve the group display name and stable group ID. The ID must
   remain a valid lowercase hyphenated identifier.
2. Ask for the initial positive agent count.
3. Generate the first complete team draft with exactly that many agent
   profiles.
4. Present the draft as one consolidated team review, including project
   coverage, overlaps, handoffs, permissions, and assumptions.
5. Allow targeted profile edits and allow the user to replace the agent count.
6. When the count changes, ask which existing profiles must survive unchanged.
7. Reject a survivor set larger than the new count and ask the user to reduce
   the set or increase the count.
8. Preserve selected survivor profiles verbatim and synthesize every remaining
   slot from the complete working context and team coverage gaps.
9. Re-run the team-level coverage, overlap, handoff, stable-name, and permission
   checks after every count or profile change.
10. Present the resulting exact-size team for one consolidated approval.

The skill does not mechanically truncate the previous draft or append generic
roles. It treats survivors as fixed constraints and synthesizes the remaining
team as a whole. If survivors occupy all slots while leaving an important
project need uncovered, setup shows that gap rather than silently changing a
survivor. The user may edit or release a survivor, change the count, or approve
the documented gap.

If the user accepts the first draft without changing its count or profiles, it
becomes the final team without a redundant second proposal.

## Operating Profile Contract

Every draft and final agent entry is a complete operating profile containing:

- a unique stable instance `name` using a valid lowercase hyphenated slug;
- a reusable blueprint slug and broad role;
- instance `identity.display_name`, `identity.title`, and an optional
  `identity.emoji`;
- a concise mission;
- materially distinct responsibilities and ownership boundaries;
- explicit handoffs to other proposed agents;
- a concise rationale tying the profile to inspected project characteristics,
  the group concept, requested count, priority answer, and retained profiles;
- the selected integration and intended workspace use;
- proposed runtime permissions on exact paths;
- project-grounded routine tasks and prompt purposes;
- schedules only where a recurring cadence is supported;
- default or routine memory scopes and shared channels only where continuity or
  collaboration requires them.

`None proposed` is a valid result for routines, schedules, memory, shared
channels, or emoji. Completeness means every category was considered and the
result is explicit, not that every optional field is populated.

The profile communicates these semantic categories in any clear layout; literal
label names and fixed Markdown structure are not required. Closely related
optional categories may be combined (for example, routines and schedules may be
addressed together). Assumptions that apply to the entire team may appear once
in the consolidated team summary rather than repeat under every individual
profile.

The profile rationale summarizes evidence without requiring a file-path list.
It names which project needs and prior answers shaped the proposal and labels
unsupported assumptions. A generic description such as "helps with the
project" is not sufficient grounding.

The operating profile is a conversational review model, not a new config
object. After approval, setup maps it only onto existing authority surfaces:

- stable name, identity, integration, runtime permissions, routines, default
  memory, and prompt registrations become existing instance config fields;
- approved workspace and shared-channel choices become existing group and
  memory config fields;
- reusable mission, responsibilities, boundaries, and working method become
  blueprint instructions where they apply across projects;
- project-specific task instructions become scoped prompt documents and routine
  assignments;
- proposal rationale, coverage analysis, and handoff explanation remain
  conversational unless an approved detail belongs in one of those existing
  surfaces.

Setup never persists new `mission`, `rationale`, `ownership`, `handoffs`, or
`coverage` keys merely because they appeared in the team review.

## Identity And Blueprint Rules

Identity style is adaptive:

- A group name or user explanation that clearly establishes a naming theme may
  shape a coherent set of display names, titles, and optional emoji.
- An ordinary or ambiguous group concept produces domain-specific functional
  identities.
- Setup does not force a theme, invent an unexplained persona, or sacrifice
  clarity to wordplay.
- Stable instance and blueprint slugs remain valid and unique regardless of
  display style.

A user-selected survivor retains its full profile verbatim, including identity,
mission, responsibilities, permissions, routines, schedules, workspace use,
and memory. The user may still request a targeted edit during consolidated
review, but setup does not re-theme or silently rewrite a survivor.

Agents may share a broad role when their responsibilities, ownership, or
routines differ materially. They may share a blueprint only when their reusable
behavior and working method are genuinely the same. Project-specific identity,
ownership, permissions, routines, schedules, workspace use, and memory remain
instance or group configuration rather than blueprint source. Different
reusable behavior requires distinct blueprints even if both agents carry the
same broad role label.

## Permissions And Operating Choices

The current heuristic that exactly one builder normally receives write access
is removed for new team synthesis. Permissions follow responsibilities:

- `read` and `search` are the baseline for observational and advisory work.
- An agent whose approved responsibilities include project implementation may
  receive `write` on the group's exact `workspace_path`.
- Multiple agents may receive write when their distinct approved
  responsibilities require it.
- Every write-enabled profile states why write is necessary and identifies the
  exact workspace path.
- Consolidated team approval includes approval of the displayed permission
  grants.
- Ambiguous write needs remain visible assumptions or targeted review edits;
  setup does not hide the grant inside generated config.
- Existing agents never gain write through this first-team synthesis rule.

Routines, prompts, schedules, and memory follow the same evidence standard.
Setup must not manufacture daily reviews, recurring schedules, shared channels,
or semantic-memory bindings merely to make a profile look complete. It proposes
them only when the repository, user priorities, collaboration model, or
operational cadence supports them.

## Consolidated Team Review

The draft and final review present all profiles together, followed by a team
coverage summary containing:

- major project needs and their owning agents;
- intentional shared roles and how their responsibilities differ;
- handoffs and collaboration paths;
- uncovered needs and explicit assumptions;
- every write-enabled agent and exact writable path;
- routine cadence and memory/channel relationships;
- the current exact agent count and preserved survivors.

The review accepts targeted edits without forcing an agent-by-agent approval
sequence. After an edit, setup rechecks the team as a whole so a local profile
change cannot silently create duplicate ownership, an uncovered responsibility,
an invalid slug, or an unjustified permission grant.

Team approval precedes the existing consolidated storage-path approval and all
filesystem creation. The skill continues to create blueprints and config only
after the applicable approvals.

## Components

### Canonical Setup Skill

`agency/setup_assets/copilot/.github/skills/agency-setup/SKILL.md` owns the
context digest, sparse-evidence question, question order, exact-size synthesis,
count revision, survivor semantics, operating-profile format, identity style,
permission inference, team review, and transition into path approval.

The repository discovery paths continue resolving to this package-owned
canonical source. No mirrored skill copy is introduced.

### Setup Guide

`kb/setup-skill.md` describes the context-aware question sequence and complete
team review without duplicating the full implementation instructions. It makes
clear that the initial count precedes the draft and remains editable.

### Setup Session Boundary

The existing first-run launch path may change only where necessary to supply the
canonical skill with the context needed for this workflow. Processing of the
setup session's outputs may change only where necessary to preserve and validate
the context-aware team result. These changes remain inside the setup-session
boundary: they do not alter normal agent launches, job execution, configuration
authority, dashboard behavior after setup, group administration, or unrelated
integration behavior.

The design does not require an application-layer change when the canonical
skill can carry the context and approval flow itself. Any launch or output
processing change must have a specific setup-workflow requirement and focused
regression coverage; it is not a license to add a second team model or persist
conversational scratch state.

### Contract Tests

`tests/test_agency_setup_skill.py` pins the skill behavior and ordering. Existing
skill, packaging, surface, schema, path, and atomic-write tests continue to
protect unchanged authority boundaries.

No application functionality outside the canonical skill and this setup-session
launch/output boundary changes.

## Failure Behavior

- Inaccessible workspace: report the inaccessible surface and return to project
  workspace selection before any team proposal.
- Sparse evidence: ask one focused priorities question, then synthesize from
  the combined evidence and answer.
- Missing or invalid count: require a positive integer before drafting.
- Revised count smaller than selected survivors: require a smaller survivor set
  or a larger count.
- Duplicate or invalid stable slugs: resolve before final approval.
- Ambiguous group theme: use functional identities.
- Conflicting survivors or uncovered needs: preserve survivors, show the
  conflict or gap, and ask for a targeted decision.
- Unjustified schedule, memory binding, or permission: omit it or label the
  underlying assumption for review.
- Any team revision: re-run the complete team consistency check before
  approval.

Failures never trigger generic role substitution, hidden permission widening,
partial config writes, or pre-approval filesystem creation.

## Testing

### Context And Ordering Contract

- Require read-only project inspection and a factual project summary before
  group and team synthesis.
- Require one priorities question only when evidence is too sparse.
- Require group display name and stable ID before the initial count.
- Require the initial count before the first team draft.
- Reject the old fixed three-to-five-role proposal flow.

### Team Draft Contract

- Require the first draft to contain exactly the initial count.
- Require every semantic category from the operating-profile contract to be identifiable; literal label names and fixed Markdown layout are not required.
- Require rationale based on project characteristics and prior answers, with
  assumptions labeled.
- Require adaptive group-aware identity with functional fallback.
- Require materially distinct responsibilities for shared roles or blueprints.
- Require responsibility-driven write access and explicit writable paths.
- Permit explicit absence of unsupported routines, schedules, memory, channels,
  and emoji.

### Revision And Approval Contract

- Permit the agent count to change after the first draft.
- Require survivor selection after a count change.
- Reject survivor counts greater than the revised count.
- Preserve every selected survivor's complete profile verbatim.
- Require coherent resynthesis of remaining slots rather than truncation or
  generic append behavior.
- Require a consolidated coverage, overlap, handoff, permission, cadence,
  memory, count, and assumption summary.
- Require a team-level consistency pass after targeted edits.
- Preserve no-creation-before-approval, schema-version-5, path validation, and
  one atomic config write assertions.

### Verification

Run focused setup-skill and surface-contract tests while iterating, then the
complete Python suite. Perform one disposable live guided setup against a
project with recognizable architecture and test/deployment signals. Supply a
thematic group name and an initial count, then stop at the first team draft.
Verify that:

- the draft has exactly the requested number of agents;
- identities reflect the approved group concept without obscuring function;
- roles, responsibilities, and operating choices reflect the inspected project;
- each profile's semantic categories — rationale, operating choices, permissions,
  and assumptions — are identifiable and grounded in project facts;
- permission grants follow responsibilities;
- the consolidated coverage summary is present; and
- no config, blueprint, prompt, or derived storage is created.

Because live AI output is nondeterministic, this is a human-reviewed acceptance
check rather than a brittle exact-text automated test.

## Acceptance Criteria

1. Setup inspects and accurately summarizes the selected project before team
   design.
2. Group name/ID and an initial positive agent count precede the first team
   draft.
3. The first draft contains exactly the requested number of complete operating
   profiles.
4. Every proposal visibly incorporates project characteristics and prior setup
   answers rather than using a stock role slate.
5. Identities adapt to a clear group theme and otherwise remain functional.
6. Multiple agents may share a broad role when their responsibilities remain
   materially distinct.
7. Write access is inferred from responsibilities and displayed on exact paths
   for consolidated approval.
8. The user can change the count, preserve selected full profiles, and receive a
   coherently resynthesized exact-size team.
9. The final consolidated review exposes coverage, overlap, handoffs,
   permissions, routines, cadence, memory, survivors, and assumptions.
10. No team drafting or revision creates files or mutates configuration.
11. Existing schema-version-5, path-safety, blueprint-authority, validation,
    and atomic-write behavior remains unchanged.

## Approved Format Amendment

On 2026-09-03 the user ruled that the operating profile is a semantic contract,
not a layout contract. Every category must be identifiable and reviewable but
need not use literal label names or a fixed Markdown structure. Related optional
categories may be combined; team-wide assumptions may appear once in the
consolidated team summary. All other requirements — project grounding, group
theming, exact count, survivor preservation, responsibility-derived permissions,
phase ordering, authority mapping, and no pre-approval writes — remain
unchanged.

## Rejected Alternatives

### Single-Pass Final Proposal

Collecting every answer before showing a team would be simpler, but it gives the
user no concrete team to react to before final approval. The selected flow shows
an exact-size first draft and permits count/profile revision.

### Fixed Candidate Slate

Offering three to five generic roles and asking the user to choose the requested
number reproduces the current failure: the slate is weakly coupled to count,
group identity, and team-level coverage.

### Incremental Truncate-And-Fill

Keeping a draft and mechanically deleting or appending entries preserves visual
continuity but produces incoherent coverage when the count changes. Selected
survivors remain fixed, while every other slot is resynthesized from the whole
context.

### File-Path Evidence In Every Profile

Requiring citations would make grounding auditable but turn setup into a noisy
code-review report. The selected design requires concise factual rationale and
explicit assumptions without displaying a path inventory.