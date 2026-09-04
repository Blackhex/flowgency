# Integrations

An integration adapts an explicit configured instance to one LLM runtime. Each instance pins one integration; filesystem contents do not override it.

Integrations declare executable support, enforceable sandbox/tool modes, a versioned runtime projector, native instruction and skill targets, and whether a selected skill can be activated non-interactively. Unsupported policy or activation fails before launch.

Runtime projectors consume standards-based Agent Library source plus prompt snapshots. They may relocate root `AGENTS.md`, whole `.agents/skills` directories, and saved prompts into native discovery paths, but must preserve canonical instruction, `SKILL.md`, and prompt bytes. Compiled artifacts are immutable and keyed by integration, projector version, and source digest.

Team sandbox roots form the baseline; instance `additional_roots` are additive. A present instance tool policy is a complete override. Integrations reject modes or names they cannot enforce rather than widening access.

## What is actually enforced

`copilot` is the only integration that enforces path rules. It writes a per-job `COPILOT_HOME` holding a sandbox filesystem policy, so a rule granting `read` on the workspace and the generated zone grants together mean a read-only agent can read its workspace and write nothing but its own outbox and memory. Enforcement is claimed only against a CLI version it has been measured against; where the version cannot be read the claim is withheld and the rules are rendered as a narrower global tool grant instead.

The policy is an allowlist, so it is switched on only when something is actually confined. An `unrestricted` policy naming no path is not sandboxed, because an empty allowlist would deny everything.

The boundary has real limits, and the job record names them rather than implying they are covered:

- Built-in file edits are policed in-process and cooperatively. Only shell commands are contained by the operating system, and the shell backend is unavailable on this platform, so `shell` is never claimed as path-scopable.
- Credentials carried in the environment are outside a path-based boundary entirely. The agent is given an explicit allowlist of environment variables rather than Agency's own environment, but anything on that list is visible to it.
- `gitAuth` and `ghAuth` are granted only to an agent whose policy grants `write` on the workspace root itself, since a filesystem policy cannot stop a push: the write lands on the remote.
- Where a rule cannot be expressed at all, the run says so on the job record instead of reporting the policy as applied.

The other integrations do not enforce path rules. They declare `unrestricted` only, so `mode: restricted` is rejected before launch; narrow rules written under `unrestricted` are not enforced by them. `claude-code` and `codex` previously disabled their own CLI permission models on every run, which is the only enforcement those integrations have. They now do so only when the policy grants write somewhere. Where the flag is withheld those CLIs may prompt, and such a run may not complete unattended -- the alternative was to keep over-granting silently.

`agency/integrations/integrations.yaml` controls which Python plugins are loadable. It is plugin discovery metadata, not team, instance, routine, identity, or memory configuration.

## Superseded layouts

Integration auto-detection, sidecar parsing, and directory-coupled runtime hints are not part of the current runtime. Native files under projected runtime layouts are generated output and never become control-plane authority.


## Runtime verification

Normal pytest runs automatically execute five live scenarios for each installed built-in AI CLI: basic execution, native root instructions, selected skill, write boundary, and launch zones. The launch-zone scenario skips for a runtime that cannot scope a write to a path. Missing CLIs do not create per-scenario skips; the installed-only collection omits absent executables instead of producing unavailable-case markers.

Installed runtimes are expected to be authenticated and operational. Live scenarios can consume configured CLI credentials, network access, model quota, and time. Authentication failures, network failures, quota limits, timeouts, and runtime process errors fail the test run; they are not skipped or xfailed.

To run only the live probes:

```text
python -m pytest -m real_runtime -v
```

To run the normal deterministic suite excluding live probes:

```text
python -m pytest -m "not real_runtime" -q
```

Deterministic integration and projector contracts continue to cover all eight built-in AI CLIs even when their executables are absent on the machine.
