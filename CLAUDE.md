# Agency Repository Guide

Agency is a FastAPI and Jinja2 application with filesystem-backed canonical configuration, standards-based agent blueprints, immutable runtime projections, semantic Markdown memory, durable jobs, and observation/proposal/decision records.

## Authority boundaries

- `config.yaml` with `schema_version: 3` is the sole control-plane authority.
- `agency.agent_library` contains reusable blueprint source: `AGENTS.md` and `.agents/skills/<name>/SKILL.md`.
- `agency.compilation_cache` contains disposable immutable integration projections.
- `agency.memory_store` contains hash-addressed mutable Markdown selected by semantic scope.
- A group `workspace_path` is the execution workspace and source repository.
- A group `path` is the Agency-owned group root for pipeline records, locks, and logs.
- Every group agent entry is an explicit instance with `name`, `blueprint`, and `integration`.

Do not add runtime directory-shape loaders, native-file integration detection for configured instances, physical instance identity writers, prompt-file schedules, arbitrary-path memory editors, or startup conversion.

## Configuration

```yaml
schema_version: 3
agency:
  title: Agency
  default_group: newsletter
  ai_backend: copilot
  agent_library: C:/Agency/agent-library
  compilation_cache: C:/Agency/compiled-agents
  memory_store: C:/Agency/memory
memory:
  channels:
    brand-strategy:
      display_name: Brand Strategy
groups:
  newsletter:
    name: Newsletter
    workspace_path: C:/Projects/newsletter
    path: C:/Agency/groups/newsletter
    default_integration: copilot
    runtime:
      timeout: 1800
      sandbox:
        mode: restricted
        roots: [C:/Projects/newsletter]
      tools:
        mode: allowlist
        names: [read, search]
    dispatch:
      enabled: true
      daily_limit: 20
    agents:
      - name: advisor
        blueprint: advisor
        integration: copilot
        identity:
          display_name: Advisor
          title: Editorial Advisor
        capabilities:
          write: false
        runtime:
          sandbox:
            additional_roots: [C:/Research/editorial]
          tools:
            mode: allowlist
            names: [read, search, write]
        default_memory:
          scope: agent
        routines:
          - id: daily-review
            skill: daily-review
            schedule:
              at: "09:00"
            memory:
              scope: routine
          - id: brand-audit
            skill: strategic-review
            schedule:
              every: 7d
            memory:
              scope: channel
              channel: brand-strategy
```

Relative global and group paths resolve against the config directory. Relative sandbox roots resolve against the group workspace. Agent roots are additive. Agent tools are a complete override, not an addition. Omitted runtime defaults are timeout 1800, unrestricted sandbox, tools `all`, dispatch disabled, and daily limit 20.

The group root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`. Durable jobs live in `agency.memory_store/.jobs`; operation locks live in `<group.path>/locks`.

## Execution

The Agents page lists group-owned instances. Agent Detail owns the `Profile/Blueprint/Runtime/Routines/Memory/Activity` surfaces; Group Settings owns defaults only. Agent Library owns standard `AGENTS.md` and Agent Skills, while Memory Channels and semantic memory selectors own mutable memory.

Configured instance integration is authoritative. Job submission resolves the blueprint digest, projector, effective runtime policy, selected routine/skill, immutable task input, and semantic memory before launch. The worker runs from a private launch view and publishes memory only after successful execution and validation.

Decision execution requires an explicit configured `execution_agent` whose integration is executable and whose `capabilities.write` is true. A missing, invalid, non-executable, or non-writable executor blocks the decide form and POST until corrected. It does not silently skip execution.

Preserve observation, proposal, decision, log, job, dashboard, and workspace behavior when changing configuration surfaces.

## Development

```text
.venv/Scripts/python -m pytest tests/ -q
.venv/Scripts/python -m agency.app
```

Routes use async FastAPI handlers, POST plus 303 redirects, shared domain validators, revision-checked config patches, and path validation. Config writes must lock, compare the expected revision, preserve unrelated data, validate the current config, and replace atomically.

### Development workflow

- Develop every new feature on a named feature branch in an ignored project-local `.worktrees/<feature>/` worktree. Do not implement features directly on `master`.
- Run commands and tests from the active worktree root. Running tests from another checkout can resolve the wrong local `tests` package.
- Establish a clean full-suite baseline in the worktree before implementation. Use focused tests while iterating, then run the complete suite before review and completion.
- Prefer test-driven changes and add regression coverage for every corrected failure path, especially validation, concurrency, path traversal, and filesystem-safety behavior.
- Review each implementation task before starting dependent work, and perform a whole-branch review before integrating a feature.
- After implementation, move `master` to the reviewed feature tip with a fast-forward only. Do not create a merge commit, squash, or rebase unless explicitly requested.
- Re-run the complete suite on the fast-forwarded `master`, remove the completed worktree, and keep the feature branch unless explicitly asked to delete it.
- Preserve unrelated and runtime-local files. In particular, do not stage, delete, or rewrite `config.yaml`, `config.yaml.lock`, group-state directories, logs, or other untracked runtime data unless the task explicitly requires it.

## Superseded layout handling

Only the current control-plane shape is accepted at runtime. Directory-coupled agent folders, native identity sidecars, prompt schedules, per-agent memory files, or `tmux_config` must not be loaded by the application.
