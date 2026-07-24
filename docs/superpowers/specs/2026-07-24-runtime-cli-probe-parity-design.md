# Runtime CLI Probe Parity

**Date:** 2026-07-24
**Status:** Approved (design)

## Problem

Agency has eight built-in executable AI CLI integrations, but its live runtime
coverage is a single opt-in Copilot test. The test keeps a hand-written
Copilot-only tuple and selects cases by selected-skill capability, which
conflates two separate questions:

1. is a supported CLI installed and operational on this machine;
2. which runtime, instruction, skill, and confinement capabilities does that
   integration declare and enforce?

This means an installed supported CLI can avoid every real execution test, and
the normal suite skips the only live probe even when `copilot.exe` is available.
It also provides no parity across basic execution, native root instructions,
selected skills, and write confinement.

## Goals

1. Run live runtime probes automatically for supported AI CLIs installed on the
   current machine.
2. Give every supported AI CLI the same four named scenario contracts.
3. Derive expected success or fail-closed behavior from declared capabilities.
4. Fail when an installed CLI is unauthenticated, offline, times out, exits
   unsuccessfully, ignores instructions, violates confinement, or mutates
   protected state.
5. Keep unavailable CLIs out of the generated live cases rather than producing
   one skip per CLI or scenario.
6. Make executable discovery production-owned rather than duplicating command
   knowledge in tests.
7. Preserve fail-closed selected-skill and write-policy behavior where an
   integration cannot enforce a capability.

## Non-Goals

- Including the configurable Script integration in the AI CLI matrix.
- Including the non-executable SDK integration.
- Claiming selected-skill activation for integrations that have not earned it.
- Claiming restricted paths or tool allowlists for integrations that cannot
  enforce them.
- Exercising durable jobs, dispatch scheduling, or worker reconciliation in
  these probes.
- Adding timeout behavior as a fifth live scenario.
- Hiding authentication, network, quota, or runtime errors behind skips or
  warnings.

## Supported AI CLI Set

The parity contract covers exactly these built-in integrations:

| Integration | Command |
| --- | --- |
| GitHub Copilot | `copilot` |
| Claude Code | `claude` |
| Google Gemini CLI | `gemini` |
| OpenAI Codex | `codex` |
| Aider | `aider` |
| Goose | `goose` |
| OpenCode | `opencode` |
| Pi | `pi` |

An integration participates by declaring an external CLI command in its
production adapter. A registry contract pins the expected eight names so a new
or removed built-in integration requires an explicit parity decision.

`script` has no intrinsic external AI executable and remains outside the set.
`sdk` remains outside because `supports_execution` is false.

## Executable Discovery

`BaseIntegration` owns a shared external CLI contract:

- an optional canonical CLI command declaration;
- shared `_find_cmd` behavior for adapters that declare a command;
- a public, side-effect-free resolver that returns the launchable command path
  or `None` when unavailable.

The resolver follows the same command path production execution uses. It does
not launch the CLI, authenticate, or perform a network request. On Windows,
Copilot retains its adapter-specific wrapper-to-real-`copilot.exe` resolution.
Other adapters may resolve a native binary or their production-launchable
command wrapper; a global Copilot-specific `.exe` rule must not incorrectly
hide them.

Tests consume this public resolver instead of reaching into `_find_cmd` and
`_resolve_real_cmd`. Production and tests therefore cannot disagree about which
entrypoint represents an installed CLI.

## Runtime Capability Model

Runtime declarations must describe what each adapter can actually enforce.

Copilot keeps its existing capabilities:

```text
path modes: restricted, unrestricted
tool modes: all, allowlist
```

The other seven AI CLIs gain only the common unconfined production contract:

```text
path modes: unrestricted
tool modes: all
```

This makes their existing `supports_execution = true` declaration usable
through normal validation without pretending they support confinement.

`ProjectorCapabilities` gains an explicit root-instruction discovery flag.
All eight AI CLI projectors declare root-instruction discovery at their native
target. Script and SDK do not gain that capability implicitly. Selected-skill
capabilities remain unchanged: only integrations whose projector declares both
skill discovery and selected-skill activation may run that scenario
successfully. At design time that is Copilot only.

## Installed-Only Collection

At pytest collection, the live probe module asks the registry for supported AI
CLI integrations whose public resolver returns an available command. It
parametrizes live cases only for those integrations.

Unavailable supported CLIs do not produce per-CLI or per-scenario skips. If no
supported AI CLI is installed, the module reports one actionable availability
skip rather than silently passing an empty matrix.

A separate deterministic registry test always verifies that all eight supported
AI CLI integrations declare executable metadata and all four scenario
contracts. Therefore absent executables cannot remove an integration from the
static parity obligation.

On the current Windows machine only the real `copilot.exe` is available. The
normal suite therefore collects four Copilot live cases and no unavailable-CLI
cases.

## Automatic Execution

The `AGENCY_REAL_RUNTIME_PROBES` opt-in gate is removed. Installed CLI probes run
as part of a normal `pytest` invocation.

The `real_runtime` marker remains useful for selection:

```text
python -m pytest -m real_runtime -v
python -m pytest -m "not real_runtime" -q
```

The marker means "uses an installed external AI runtime," not "disabled unless
an environment variable is set." Documentation must state that normal test runs
can use authenticated CLI credentials, network access, model quota, and time.

An installed command is considered expected to be operational. Authentication,
network, quota, timeout, malformed output, or nonzero-exit failures fail the
case and include the integration name plus captured stderr. They do not become
skips.

## Scenario Matrix

Each installed supported CLI receives four separate pytest scenarios.

### 1. Basic Execution

The request uses `unrestricted` paths, `all` tools, no selected skill, and the
production validation path. The projected source contains only neutral
instructions; the task contains the unique response token and tells the CLI not
to use tools or modify files.

Expected result for every installed CLI:

- validation succeeds;
- the real CLI launches non-interactively;
- exit code is zero;
- stdout contains the unique task token;
- no protected filesystem state changes.

This scenario proves the adapter command, request construction, authentication,
and response capture work through the production path.

### 2. Root Instructions

The unique token exists only in the source `AGENTS.md`, which is projected to
the integration's native instruction target. The task requests compliance but
does not contain or derive the token.

Expected result for every installed CLI:

- projection validates;
- production validation succeeds;
- the real CLI launches;
- stdout contains the instruction-only token;
- the projected files remain byte-identical.

An installed CLI that does not discover its declared native instruction target
fails. It is not reclassified as unsupported or skipped.

### 3. Selected Skill

The unique skill token exists only in a standard Agent Skill. The request names
that selected skill, while the root instruction and task contain no copy or
derivation of the skill token.

If the projector declares both skill discovery and selected-skill activation:

- the real CLI launches;
- stdout contains the skill-only token;
- exit code is zero.

Otherwise:

- validation raises `unsupported-skill-activation`;
- after collection-time availability discovery, rejection occurs before any
  per-scenario command re-resolution, task reading, or subprocess launch;
- the filesystem remains unchanged.

This is capability-aware parity: every CLI receives the same scenario, while
the declared contract determines whether success or fail-closed rejection is
correct.

### 4. Write Boundary

The request asks the runtime to create `write-probe.txt` with unique content in
the otherwise-empty workspace. Its runtime policy is exact:

```text
sandbox mode: restricted
sandbox roots: [workspace_root]
tools mode: allowlist
tools: [read, search]
```

This is the exact configured runtime policy, not a claim that the CLI cannot
read its immutable launch bundle. The launch directory is the CLI working
directory containing the projected instructions and is not added as a
configured sandbox root. Agency reads the task file before subprocess launch
and passes its text through the adapter's native prompt mechanism; the CLI does
not receive the task-file path. Both inputs are snapshotted and must remain
unchanged.

If the integration declares both requested policy modes:

- the real CLI launches under that policy;
- the attempted file is not created;
- the result reports no changed files;
- workspace, projection, task, and repository snapshots remain unchanged.

Otherwise:

- validation raises exactly the unsupported path/tool policy issues implied by
  the adapter's declarations;
- after collection-time availability discovery, rejection occurs before any
  per-scenario command re-resolution, task reading, or subprocess launch;
- no file is created.

At design time only Copilot executes this scenario. The other seven integrations
must fail closed until they declare and implement confinement.

## Isolation And Data Flow

Every successful live scenario creates independent temporary roots:

```text
source snapshot -> projector -> private launch directory
task text -------------------> immutable task file
runtime request -------------> installed CLI adapter
CLI output ------------------> RunResult assertions
```

Before launch, the test records:

- every projected file and its bytes;
- the workspace tree;
- the task bytes;
- repository status and binary diff.

After launch, it verifies all protected state is unchanged unless a scenario
explicitly permits a change. Temporary resources are removed in `finally`.
Tests do not expose secrets, print credentials, or modify repository files.

Shared helpers own request construction, unique tokens, projection snapshots,
state capture, and diagnostic assertion messages. Scenario tests remain
separate so pytest reports which contract failed for which CLI.

## Failure Reporting

Live failures name both integration and scenario. A failed real launch reports:

- resolved command path;
- exit code;
- stderr;
- timeout status when applicable;
- missing expected token or unexpected changed path.

Fail-closed scenarios report the actual validation issue codes. If execution
reaches command resolution or subprocess launch when rejection was expected,
the test fails explicitly.

An unavailable CLI is not an error. It is absent from live parametrization but
remains covered by deterministic registry, capability, projector, and invocation
contract tests.

## Documentation

Update integration documentation to distinguish:

- executable support;
- unconfined runtime support;
- root-instruction discovery;
- selected-skill activation;
- restricted path and tool enforcement;
- automatic installed-runtime probes.

Update the pytest marker description so it no longer calls live probes opt-in.
Document targeted inclusion/exclusion commands and the credential/network/quota
impact of normal test execution.

## Testing

### Deterministic Contracts

- Exactly the eight supported AI CLI adapters declare canonical commands.
- Script and SDK remain excluded.
- Public executable resolution returns the production-launchable command and
  preserves Copilot's Windows real-executable behavior.
- The seven non-Copilot AI CLIs accept only `unrestricted` + `all` runtime
  policy; Copilot retains its stronger capability set.
- All eight projectors declare root-instruction discovery and deterministically
  project source instructions to their declared native targets.
- Selected-skill capability declarations remain fail closed except where
  verified.
- Every supported CLI maps to all four scenario definitions.
- Installed-only case generation does not create unavailable-CLI cases.
- No-installed-CLI collection produces one actionable skip.
- Installed CLI runtime failures are failures, not skips.

### Live Matrix

For each installed CLI:

- basic execution returns its unique token;
- the real CLI proves native root-instruction discovery by returning an
  instruction-only token;
- selected skill succeeds or rejects according to projector capabilities;
- write attempt is blocked by enforced policy or rejected before launch;
- protected state remains unchanged.

### Regression Boundary

- Existing adapter invocation tests continue verifying exact command arguments.
- Projector relocation and validation tests cover every native target.
- Integration contract tests cover every registered plugin.
- The full Python suite runs with automatic live cases for installed CLIs.

## Acceptance Criteria

1. Normal pytest runs live probes automatically for installed supported AI CLIs.
2. A machine on which Copilot is the only resolved supported CLI collects
  exactly four real Copilot scenarios without an environment opt-in flag; each
  additional resolved supported CLI contributes the same four scenarios.
3. All eight built-in AI CLIs have the same four declared scenario contracts.
4. Unavailable CLIs do not create per-scenario skips.
5. Installed but broken CLIs fail with actionable diagnostics.
6. Basic validated execution is enabled for all eight AI CLI adapters using
   truthful runtime capabilities.
7. Root-instruction discovery is explicitly declared and projector-tested for
  all eight AI CLIs, then verified against the real runtime for every installed
  CLI.
8. Selected-skill and write-boundary scenarios succeed or fail closed according
   to declared capabilities.
9. Live runs leave projection, workspace, task, and repository state unchanged.
10. Script and SDK remain outside the AI CLI parity matrix.