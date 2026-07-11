# Copilot Agent Detection Marker Design

## Problem

Agency uses `AGENTS.md` for both OpenAI Codex and GitHub Copilot identities. An
`AGENTS.md` file without a tool-specific marker intentionally detects as Codex. The
Copilot/Windows setup profile currently creates `AGENTS.md` and `memory.md`, but no
`.copilot/` directory, so filesystem-first integration resolution overrides the
configured `integration: copilot` and displays the generated agents as OpenAI Codex.

Dashboard-created Copilot agents have the same risk because the admin create route
writes the identity file directly without asking the integration to prepare its native
directory structure.

Copilot detection also accepts `.github/` for compatibility with pre-existing
repository-root configurations. That signal is separate from the managed-agent
scaffolding problem addressed here.

## Decision

Require `.copilot/` as an idempotent filesystem marker for setup-generated and
dashboard-managed Copilot agent directories. This is a creation and management
contract, not an exclusive detection rule. Preserve the existing filesystem-first
resolution order, the rule that an `AGENTS.md` directory without any accepted
tool-specific signal belongs to Codex, and compatibility for pre-existing repository
roots detected as Copilot through `.github/`.

## Components

### Integration Preparation Hook

Add `BaseIntegration.prepare_agent_dir(agent_dir)`, a no-op hook for integrations that
need no extra filesystem structure. `CopilotIntegration` overrides it to create
`agent_dir/.copilot/` with `parents=True` and `exist_ok=True`.

The admin agent-create route calls the selected integration's preparation hook before
writing its identity file. `CopilotIntegration.write_identity()` also calls the hook so
direct API/helper writes cannot create an unmarked Copilot identity.

### Setup Skill

For the Copilot/Windows profile, Agency Setup creates `.copilot/` inside every generated
agent directory alongside `AGENTS.md` and `memory.md`. The canonical skill explicitly
states that this marker disambiguates setup-generated agent directories from Codex and
must not be omitted. This managed-directory requirement does not replace or narrow
`.github/` detection for pre-existing repository roots.

Generation verification checks that every Copilot agent has all three artifacts and,
when Agency's Python package is available, that `detect_integration(agent_dir).name`
equals `copilot`.

### Existing Team Migration

Create `.copilot/` in each of the ten existing generated agent directories. Do not
rewrite identities, sidecar metadata, memories, prompts, or configuration. Detection
will change on the next request because the running app resolves integrations from the
filesystem for each agent collection.

## Data Flow

1. Setup or dashboard creation selects the Copilot integration.
2. The integration-specific preparation step creates `.copilot/`.
3. The identity writer creates or updates `AGENTS.md` and optional sidecar metadata.
4. `detect_integration()` evaluates Copilot at priority 7 and sees `.copilot/`.
5. Agency renders and executes the agent through `CopilotIntegration` rather than the
   generic Codex `AGENTS.md` fallback at priority 10.

Pre-existing repository roots that use `.github/` continue through the existing
Copilot detection path; they are not required to add `.copilot/` under this design.

## Error Handling

Marker creation is idempotent and propagates filesystem errors to the caller. Agency
must not silently create only `AGENTS.md` after marker creation fails, because that
would produce a valid-looking agent with the wrong runtime integration.

No config-priority fallback is added. Missing markers remain visible as Codex detection,
which keeps ambiguous or externally authored `AGENTS.md` directories deterministic.
Here, "missing markers" means that none of the accepted tool-specific signals are
present. A pre-existing repository root containing `.github/` remains an accepted
Copilot detection and is not treated as an unmarked Codex directory.

## Tests

1. A failing integration test proves that writing a Copilot identity currently leaves
   the directory detected as Codex; after the fix it creates `.copilot/` and detects as
   Copilot.
2. A failing admin-route test proves that creating an agent in a Copilot-default group
   currently omits `.copilot/`; after the fix the marker exists.
3. A failing setup-skill contract test proves that the Copilot/Windows generation
   instructions currently omit the marker; after the fix it requires `.copilot/` and
   post-generation detection verification.
4. Existing integration, admin, and full pytest suites remain green, including the rule
   that `AGENTS.md` without an accepted marker detects as Codex and the compatibility
   rule that a pre-existing repository root with `.github/` detects as Copilot.
5. A live check confirms all ten current dashboard cards display GitHub Copilot after
   migration.

## Non-Goals

- Changing integration resolution from filesystem-first to config-first.
- Changing Codex detection or identity filenames.
- Removing or narrowing existing repository-root `.github/` Copilot detection.
- Adding integration metadata to `.agency-meta.yaml`.
- Altering Copilot CLI invocation, dispatch scheduling, or launcher behavior.
