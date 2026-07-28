# Agency Repository Guide

Agency is a FastAPI and Jinja2 application with filesystem-backed canonical configuration, standards-based agent blueprints, immutable runtime projections, semantic Markdown memory, durable jobs, and observation/proposal/decision records.

## Authority boundaries

- `config.yaml` with `schema_version: 4` is the sole control-plane authority.
- `agency.agent_library` contains reusable blueprint source: `AGENTS.md` and `.agents/skills/<name>/SKILL.md`.
- `agency.compilation_cache` contains disposable immutable integration projections.
- `agency.memory_store` contains hash-addressed mutable Markdown selected by semantic scope.
- `agency.prompt_store` contains canonical saved prompt files referenced by configured instances.
- A group `workspace_path` is the execution workspace and source repository.
- A group `path` is the Agency-owned group root for pipeline records, locks, and logs.
- Every group agent entry is an explicit instance with `name`, `blueprint`, and `integration`.

Do not add runtime directory-shape loaders, native-file integration detection for configured instances, physical instance identity writers, prompt-file schedules, arbitrary-path memory editors, or startup conversion. Native integration files are generated runtime output only and never become authority.

## Configuration

```yaml
schema_version: 4
agency:
  title: Agency
  default_group: newsletter
  ai_backend: copilot
  agent_library: C:/Agency/agent-library
  compilation_cache: C:/Agency/compiled-agents
  memory_store: C:/Agency/memory
  prompt_store: C:/Agency/prompts
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
            prompt:
              scope: blueprint
              name: daily-review
            arguments: [--brief]
            schedule:
              at: "09:00"
            memory:
              scope: routine
          - id: brand-audit
            prompt:
              scope: instance
              name: brand-audit
            schedule:
              every: 7d
            memory:
              scope: channel
              channel: brand-strategy
```

Relative global and group paths resolve against the config directory. Relative sandbox roots resolve against the group workspace. Agent roots are additive. Agent tools are a complete override, not an addition. Omitted runtime defaults are timeout 1800, unrestricted sandbox, tools `all`, dispatch disabled, and daily limit 20.

The group root is automatically available to restricted agents. Agency never loads or creates `<workspace_path>/shared`. Durable jobs live in `agency.memory_store/.jobs`; operation locks live in `<group.path>/locks`.

## Execution

The Agents page lists group-owned instances. Agent Detail owns the `Profile/Blueprint/Runtime/Routines/Prompts/Memory/Activity` surfaces; Group Settings owns defaults only. Agent Library owns standard `AGENTS.md`, Agent Skills, and shared blueprint prompts, while Memory Channels and semantic memory selectors own mutable memory.

Configured instance integration is authoritative. Job submission resolves the blueprint digest, projector, effective runtime policy, selected prompt source, immutable task input, and semantic memory before launch. Manual launches may run a saved prompt from the effective catalog or a one-off task. The worker runs from a private launch view and publishes memory only after successful execution and validation.

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
- Commit each approved design specification and implementation plan in its own documentation-only commit before implementation. Keep the specification and plan in separate commits, and do not combine either with implementation changes.
- Prefer test-driven changes and add regression coverage for every corrected failure path, especially validation, concurrency, path traversal, and filesystem-safety behavior.
- Review each implementation task before starting dependent work, and perform a whole-branch review before integrating a feature.
- Integration is pre-authorized. Once implementation and review are complete and tests pass, do not present completion options or ask whether to merge, open a pull request, or preserve the branch. Automatically perform the fast-forward, verification, push, and cleanup sequence below. This rule overrides generic branch-finishing workflows that require an integration choice.
- After implementation, move `master` to the reviewed feature tip with a fast-forward only. Do not create a merge commit, squash, or rebase unless explicitly requested.
- If `master` gained commits while the feature was in progress, rebase the feature branch onto `master` first, re-run the suite in the worktree, and then fast-forward. The fast-forward requirement is what forces the rebase; it is not a request for arbitrary history rewriting.
- If `master` has uncommitted changes, stash them before the fast-forward and restore them afterwards. Never discard them and never fold them into the feature.
- Re-run the complete suite on the fast-forwarded `master`.
- Push both `master` and the feature branch to `origin` once the suite is green. Do not leave integrated work unpublished.
- Once `master` carries the feature and the suite is green, remove the feature worktree with `git worktree remove .worktrees/<feature>` and prune stale entries. Do not leave the worktree behind for later cleanup. Keep the feature branch unless explicitly asked to delete it.
- Preserve unrelated and runtime-local files. In particular, do not stage, delete, or rewrite `config.yaml`, `config.yaml.lock`, group-state directories, logs, or other untracked runtime data unless the task explicitly requires it.

### Commit messages

Every commit message follows [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/). The style of the description, body, and footers follows the seven rules of a great Git commit message, as defined by Chris Beams in [How to Write a Git Commit Message](https://cbea.ms/git-commit/).

```text
<type>[optional scope][optional !]: <description>

[optional body]

[optional footer(s)]
```

- **Type is required** and is a lowercase noun terminated by a colon and a space. `feat` introduces a feature, `fix` patches a bug. This repository also uses `docs`, `test`, `refactor`, `style`, and `chore`.
- **Scope is optional**, lowercase, and parenthesized: a noun naming the section of the codebase, as in `feat(prompts):`, `fix(library):`, or `test(runtime):`.
- **Breaking changes** are marked with a `!` before the colon, a `BREAKING CHANGE: <description>` footer, or both. `BREAKING CHANGE` is the one token that must be uppercase, and it may appear on any type.
- **Write the description in the imperative mood.** It must complete the sentence "If applied, this commit will _____". Write `fix: remove deprecated methods`, not `fix: removed deprecated methods`.
- **Keep the description lowercase and free of a trailing period.** The type prefix already opens the subject, so the description continues it: `feat(lang): add Polish language`, not `feat(lang): Add Polish language.`
- **Aim for a subject line of 50 characters and treat 72 as the hard limit**, prefix included. If summarizing is hard, the commit is doing too much — make it atomic instead. A commit that fits more than one type is more than one commit.
- **Separate the body from the description with a blank line.** Not every commit needs a body; a single subject line is fine when the change is self-explanatory. When a body exists the blank line is critical, because `log`, `shortlog`, `format-patch`, and `rebase` all treat the text up to the first blank line as the title.
- **Wrap the body at 72 characters.** Git never wraps text automatically, so wrap it manually. 72 leaves room for Git's indentation while staying under 80 columns overall. The imperative-mood restriction applies only to the description; the body may relax it.
- **Use the body to explain what and why, not how.** The diff already shows how. Explain the way things worked before and what was wrong with that, the way they work now, and why the change was made this way. Note side effects and non-obvious consequences.
- **Put footers one blank line after the body** in git trailer form, with `-` in place of whitespace in the token: `Refs: #123`, `Reviewed-by: Z`, `BREAKING CHANGE: ...`.

Template:

```text
fix(prompts): prevent racing of catalog requests

More detailed explanatory text, if necessary. Wrap it to about 72
characters or so. The blank line separating the description from the
body is critical (unless you omit the body entirely); various tools
like `log`, `shortlog` and `rebase` can get confused if you run the
two together.

Explain the problem that this commit is solving. Focus on why you
are making this change as opposed to how (the code explains that).
Are there side effects or other unintuitive consequences of this
change? Here's the place to explain them.

Further paragraphs come after blank lines.

 - Bullet points are okay, too

 - Typically a hyphen or asterisk is used for the bullet, preceded
   by a single space, with blank lines in between, but conventions
   vary here

Refs: #123
See-also: #456, #789
```

## Superseded layout handling

Only the current control-plane shape is accepted at runtime. Directory-coupled agent folders, native identity sidecars, prompt schedules, per-agent memory files, or `tmux_config` must not be loaded by the application.
