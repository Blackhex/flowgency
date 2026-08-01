# Agent reporting protocol

Date: 2026-07-31
Status: approved

## Problem

Agents configured with `capabilities.write: false` cannot record observations,
cannot create proposals, and cannot update their memory. The pipeline's premise
is that non-writing agents observe and propose while a writable executor acts,
so an advisor that cannot file an observation has no way to participate.

Investigation showed three independent defects behind the one symptom.

### `capabilities.write` is not a runtime restriction

`AgentCapabilities.write` is read only by the decision-executor gate in
`agency/app.py` (`execution_agent_options`, and the decide-form validation),
by the CLI equivalents in `agency/cli.py`, and by display code in
`agency/web/routes/agent_detail.py`. `resolve_effective_policy` in
`agency/configuration/effective.py` never consults it. It reaches neither the
sandbox, nor the tool policy, nor the memory publish path.

### The tool allowlist is the actual blocker

A group whose `runtime.tools` is `allowlist [read, search]` produces
`--allow-tool read --allow-tool search` in
`agency/integrations/agency/copilot.py`. No write tool, no shell. An agent under
that policy cannot run a command and cannot create a file, whatever its
`capabilities.write` value.

This mislabelling has already produced a false diagnosis in production. A
scheduled `suite-health` run reported that "pipeline recording was blocked by
the agent's non-write capability" when it was blocked by the tool allowlist.
The agent guessed at a cause and guessed wrong, and nothing in the system
contradicted it.

### Memory writing is wired but dead

`_stage_memory_locked` in `agency/memory/store.py` copies canonical memory into
`<memory_store>/.staging/<hash>/<job_id>/`, and `agency/jobs/execution.py`
passes that directory as `IntegrationRunRequest.memory_working_dir`. No
integration reads that field. It is never added to the sandbox roots, never
named in the prompt, and never copied into the launch view. The stage therefore
always returns byte-identical, and `prepare_publication` always resolves to
`no_change`. No agent has ever been able to write memory, writable or not, and
no agent can read its memory either.

### Observations and proposals have no agent-facing mechanism

The group root is already in the sandbox roots, so `<group.path>/observations/`
is physically reachable. But nothing tells an agent the path, the filename
convention, or the front-matter schema.
`skills/agency-setup/references/templates.md` instructs agents to "record
observations or proposals through the project's configured pipeline" — a
sentence describing something that does not exist.

## Constraints discovered

The enforcement options were tested against GitHub Copilot CLI 1.0.77 on
Windows rather than assumed from documentation. Findings that bound this
design:

- `--add-dir` is repeatable and each listed root is genuinely writable, but it
  governs read and write together. There is no read-path list and write-path
  list.
- `write(<path>)` is **not** a recognized permission rule. As an allow rule it
  matches nothing, so `write` never enters the allowlist and every edit is
  refused; as a deny rule it matches nothing, so nothing is denied. It fails
  silently in both directions. Only `shell(...)` and MCP `Server(tool)` accept
  arguments.
- `--add-dir` is what switches path verification on at all. With no `--add-dir`,
  `--allow-tool write` is effectively unbounded. Agency takes that branch today
  by emitting `--allow-all-paths` whenever `sandbox_roots` is empty.
- Listing roots does not fence off the working directory's ancestry. With roots
  listed, writes still succeeded to the parent of `cwd`, to a sibling directory,
  and two levels up. Agency runs agents with `cwd` set to the launch view inside
  the job store, so an agent holding a write tool can reach the surrounding job
  store. This is a pre-existing isolation weakness, recorded here because the
  outbox design depends on knowing it, and deliberately not fixed here.
- Copilot local sandboxing **does** express the read/write split, via
  `sandbox.userPolicy.filesystem.readonlyPaths` / `readwritePaths` /
  `deniedPaths` in `settings.json`, and `COPILOT_HOME` relocates the whole
  configuration directory so the policy can be per-job. This was verified
  working: a write to a `readonlyPaths` entry was refused and a write to a
  `readwritePaths` entry succeeded. It is the right primitive, but it is
  experimental, its built-in file-tool checks are in-process and best-effort
  rather than OS-enforced, and it ignores denied paths on Windows. It is
  therefore scheduled as a dependent specification rather than folded into this
  one.

## Design

### Capability model

`capabilities.write` and the ability to report become independent axes.

- **`capabilities.write`** decides one thing: whether the agent may write its
  **workspace**. It continues to gate decision-executor selection, and it now
  also determines the writable path set in the effective runtime policy.
- **Reporting** — writing observations, proposals, and one's own memory — is an
  unconditional capability of every agent. It is not configurable, because an
  agent that cannot report cannot participate in the pipeline at all.

A read-only agent therefore gains an outbox it may write, a memory stage it may
write, and read access to existing group records so it can avoid filing
duplicates. It gains nothing in the workspace.

### The write-boundary contract

The boundary is expressed as **paths**, not tools. `EffectiveRuntimePolicy`
gains a `writable_roots` field alongside its existing `sandbox_roots`:

- `sandbox_roots` stays what it is — everything the agent may **read**.
- `writable_roots` is the subset it may **write**. With
  `capabilities.write: true` that is the workspace and group roots; with
  `capabilities.write: false` it is **empty**.

The reporting paths are deliberately absent from that field. The launch view —
which holds `.agency/outbox/` and `.agency/memory/` — is **implicitly writable
for every agent**, always, and it never appears in configuration. It is per-job,
so it reaches integrations through `IntegrationRunRequest.launch_dir` and
`memory_working_dir`. "Read-only" therefore means read-only to the workspace,
never mute.

`runtime.tools` gains **no exception**. Tools remain a complete override, as
`AGENTS.md` already states. Writability is a separate axis, which is why an
earlier draft of this design — granting a write *tool* to satisfy the protocol —
was wrong: on an integration without path-scoped write permissions, granting the
tool grants write to every readable root, including the workspace. That would
give a `capabilities.write: false` agent more workspace access than it has
today, which is the opposite of the contract.

`RuntimeCapabilities` gains `enforces_write_boundary: bool = False`. An
integration must declare whether it can actually honour the split.
`validate_runtime_policy` **fails closed**: a policy whose writable set is
narrower than its readable set is rejected, verbosely, by any integration that
has not declared the capability. The rejection names the integration, the
contract it fails, and the fact that the agent cannot run until an
implementation satisfies it.

Failing closed blocks currently configured read-only agents from running on
integrations that do not yet implement the contract. That is intended and
accepted: a contract that is declared but not enforced is the failure mode this
whole design exists to remove. No integration is adapted here — this
specification defines the contract only.

**Failure reporting stops lying.** Agency records the effective runtime policy
alongside the run, so a blocked agent's self-diagnosis can be checked against
what was actually granted.

### Outbox

Location: `<launch_view>/.agency/outbox/{observations,proposals}/`.

The outbox lives inside the launch view because the launch view is already the
process working directory, so it is writable without granting any additional
root, and because `create_launch_view` recreates that directory each run, so the
outbox is inherently per-job and disposable.

### Memory

`memory_working_dir` is repointed to `<launch_view>/.agency/memory/`, seeded
with the canonical memory files so the agent can read its memory as well as
write it. After a successful run the worker copies that directory's contents
into the existing `stage.directory` and hands off to `prepare_publication`
unchanged.

This is the smallest change that makes memory work. The staging, locking,
journalling, diffing, and rollback machinery stay exactly as they are. The only
thing ever missing was that nothing wrote into the stage.

### Ingest

After a zero-exit run the worker validates the outbox, then writes into
`<group.path>/observations/` and `<group.path>/proposals/` using
`atomic_write_text`.

Validation rules:

- Only `*.md` files directly in each directory. No subdirectories, no other
  extensions, no symlinks or reparse points, reusing the checks already present
  in `create_launch_view`.
- Agency assigns the final filename as `<job date>-<slug>.md`. The slug is taken
  from the front-matter `slug` field when it is present and matches
  `[a-z0-9-]{1,60}`, otherwise it is derived from the record's display title by
  the same rule `extract_display_title` already uses, otherwise it falls back to
  the job id. A collision with an existing file gains a `-2`, `-3` suffix. The
  agent never controls the destination path, which removes traversal and
  collision as a class of bug.
- Front matter must parse via `parse_frontmatter`. Proposals must additionally
  pass `validate_proposal_schema`, including that `execution_agent` names a
  configured, executable, `write: true` instance — the same rule the decide form
  enforces, applied at authorship time instead of at decision time.
- `agent`, `date`, and initial `status` are set by Agency, not read from the
  file. An agent cannot file an observation under another agent's name and
  cannot backdate one.
- At most 20 records per directory per run, and at most 64 KiB per record, so a
  looping agent cannot flood the pipeline. Exceeding either cap fails the run
  rather than silently truncating.

### Failure handling

Validation failures neither discard the agent's work nor silently succeed.

Valid records are ingested even when the same run also produced invalid ones.
Discarding five good observations because a sixth file was malformed throws away
work the agent did and that Agency already judged sound. The run still **fails**
and the summary still names every rejection, so nothing is hidden — but the good
records land.

Invalid records are retained as job artifacts, from **both** the `observations`
and `proposals` directories; a rejected proposal's body is as valuable to an
operator as a rejected observation's.

Retention must survive the very inputs that caused the rejection. The existing
`retain_failed_stage` refuses non-markdown files, subdirectories, symlinks and
reserved names — exactly what a rejected outbox contains. Retention therefore
copies only retainable entries, and a retention failure must never replace the
per-record reasons with a generic error. Losing those reasons reintroduces the
"failure reporting lies" defect this design exists to remove.

A run that produced no valid records is a failed run; completing quietly is how
the current system produces a green job with nothing recorded.

### Bounds

Records are capped at 20 per directory per run and 64 KiB each. The memory
directory carries the same exposure — agent-written, destined for canonical
storage, read by the worker — so it is bounded the same way, by entry count and
per-file size, and streamed per file rather than accumulated whole. An unbounded
memory outbox lets a single run exhaust the worker.

### Where the writable-agent set comes from

Proposal validation must know which agents may be named as `execution_agent`.
That set is resolved at **submission**, from the configuration snapshot the job
already pins, and carried on the job spec.

Resolving it at execution instead would read a mutable file after the agent had
already done its work, so an unrelated configuration edit between submission and
execution could fail the run — and would report it as though the agent had named
a bad executor. The job spec already snapshots the task input, the runtime
policy, the blueprint digest and the memory binding for exactly this reason; the
writable-agent set is the same kind of fact.

### Ordering

Record ingest and memory publication are separate transactions. Records ingest
first, because they are append-only and independently valid. Memory publishes
second, because it is the one with revision conflicts and rollback. A memory
conflict therefore cannot lose an observation.

### Projection

The outbox contract is appended to the **task input** at job resolution time,
alongside `build_routine_task_input` and `build_decision_prompt` in
`agency/jobs/prompts.py`. It is not carried in the blueprint.

Blueprints are user-editable library content, so a protocol that depends on them
is a protocol that disappears when someone edits `AGENTS.md`. Putting it in the
task input makes it integration-agnostic, makes it part of the immutable prompt
snapshot the worker already builds, and writes it verbatim to the `.prompt` log
file. When an agent fails to report, the operator can read exactly what it was
told instead of inferring it.

### Module boundaries

A new `agency/records/` package owns outbox layout, validation, and ingest —
deliberately not `agency/app.py`, which is already past two thousand lines and
mixes routing, parsing, and pipeline logic.

The package exposes a narrow surface: build the outbox for a job, validate a
populated outbox, ingest a validated one. The worker calls those three functions
and knows nothing about front matter.

Existing helpers are reused rather than reimplemented: `validate_proposal_schema`
from `agency/proposals.py`, `atomic_write_text` from `agency/fs/atomic.py`, and
`resolve_group_paths` from `agency/configuration/group_paths.py`.

One targeted move is required. `parse_frontmatter` and `extract_display_title`
currently live in `agency/app.py`, which constructs the FastAPI application;
importing them from a worker-side package would drag the whole web layer into
the job process. Both functions move to a small shared module, and `app.py`
imports them from there. Nothing else in `app.py` is touched.

## Verification

Development is test-driven. The cases that matter are the ones that currently
fail silently:

- A `write: false` agent files an observation and it lands, with `agent` and
  `date` stamped by Agency rather than taken from the file.
- An outbox file claiming `agent: someone-else` has that field overwritten.
- Path traversal, nested directories, non-`.md` files, symlinks, oversized
  records, and too many records are each rejected.
- A proposal naming a non-existent, non-executable, or `write: false`
  `execution_agent` is rejected at ingest, mirroring the existing decide-form
  rule covered by `tests/test_execute_decision.py`.
- Memory written into the launch view reaches canonical storage, and memory left
  untouched still resolves to `no_change`. This is the regression that proves the
  dead `memory_working_dir` wire is live.
- A run whose records are all invalid fails, retains artifacts, and names the
  reason.
- `capabilities.write: false` yields an empty `writable_roots` while
  `sandbox_roots` still contains the workspace; `capabilities.write: true`
  yields a writable workspace.
- An integration that has not declared `enforces_write_boundary` rejects a
  narrowed policy, and the rejection names the integration and the contract.
- The launch view is never listed in `writable_roots`; its writability is
  implicit and carried on the run request.

## Documentation

- `AGENTS.md` gains the write-boundary contract and the unconditional reporting
  capability. Its statement that agent tools are a complete override stands
  unchanged — there is no tools exception.
- `kb/configuration.md` gains the same.
- `skills/agency-setup/references/templates.md` replaces "record observations or
  proposals through the project's configured pipeline" with the concrete
  protocol.

## Out of scope

Recorded so they are not relitigated:

- **Any integration's implementation of the write-boundary contract.** This
  specification defines the contract; no integration is adapted to satisfy it.
  The Copilot implementation is the dependent specification: local sandboxing
  via `sandbox.userPolicy.filesystem.readonlyPaths` / `readwritePaths`, a
  per-job `COPILOT_HOME`, authentication seeding, and the relocation of
  `session-state/` and `logs/` that the existing `--resume` and usage-summary
  code reads. Until it lands, read-only agents on Copilot do not run at all.
- The working-directory ancestry isolation weakness.
- An Agency MCP server as an alternative transport. It is the only mechanism
  that removes the filesystem write primitive entirely, and it remains the
  natural third step, but it is integration-specific and would sit on top of the
  same validated ingest path defined here.
- Any change to decision execution.

## Rejected alternatives

- **Making reporting a graded capability** (`capabilities: {workspace, records}`)
  was rejected as a knob for a case this design does not have: an agent that runs
  but must produce nothing.
- **Granting a write tool to satisfy the protocol.** An earlier draft had Agency
  always append a write tool to the configured allowlist. Rejected after a live
  runtime test caught it: without path-scoped write permissions, granting the
  tool grants write to every readable root, so a `capabilities.write: false`
  agent would gain workspace write access it does not have today. The boundary
  is a path contract, not a tool grant.
- **Structured stdout** — the agent emits fenced YAML that the worker parses out
  of the transcript — was rejected despite needing no tools at all. It is
  unvalidated free-form text, it competes with the agent's prose, and it scales
  badly to multi-file memory.
- **Writing directly into `<group.path>/observations/`** was rejected because it
  would let any agent overwrite another agent's observations or edit a decision
  file, which the decision prompt currently prevents only by asking politely.
  The outbox is the only supported way for an agent to add a record, and Agency
  holds the pen. Note that this specification makes that a protocol rule, not a
  filesystem-enforced one: until the dependent sandbox specification lands, an
  agent that holds a write tool and reaches the group root can still edit records
  directly. Ingest validation constrains what Agency will accept, not what the
  filesystem will permit.
- **Path-scoped `write(<path>)` permissions**, proposed during design, were
  falsified by testing. See the constraints section.
