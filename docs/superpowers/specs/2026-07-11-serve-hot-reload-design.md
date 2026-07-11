# Hot Reload for `christag-agency serve` Design

## Problem

The public `christag-agency serve` command starts Uvicorn with an in-memory ASGI
application and has no reload option. Development hot reload is available only through
a VS Code task that invokes Uvicorn directly, bypassing Agency's first-run setup and
public CLI startup path.

Developers need one supported command that restarts the server when application code,
UI assets, themes, or `config.yaml` change. Reload must remain opt-in so production and
service startup behavior does not change.

## Goals

- Support `christag-agency serve --reload` on every platform supported by Uvicorn.
- Keep `christag-agency serve` behavior, host, and port defaults unchanged.
- Use the current working directory as the reload boundary.
- Reload for Python code, UI assets, theme files, and `config.yaml`.
- Avoid reloads caused by durable Agency records under configured groups' `shared/`
  directories.
- Give the CLI entry point and `python -m agency.app` one server startup path.

## Non-Goals

- Browser live refresh or state-preserving hot module replacement.
- Enabling reload in service or production examples.
- Exposing every Uvicorn reload setting as an Agency CLI option.
- Applying malformed configuration without a process restart.
- Watching source installed outside the current working directory.

## Decision

Add an opt-in `--reload` flag and introduce a shared server launcher in
`agency.app`. Both the console CLI and the module entry point call this launcher.

The launcher owns first-run config creation, group initialization, watch policy, and
the final `uvicorn.run()` call. This removes the console CLI's current `sys.argv`
rewrite and prevents the two entry points from drifting.

## Components

### Shared Server Launcher

Add `run_server(host, port, reload=False)` in `agency.app`.

The launcher performs the existing startup sequence:

1. Create the default `config.yaml` when it does not exist and print the existing
   first-run guidance.
2. Call `reload_groups()` so the parent process has current configuration.
3. Start Uvicorn using the selected launch mode.

Normal mode passes the in-memory `app` object to Uvicorn and does not supply reload
settings. Reload mode passes the import string `agency.app:app`, because Uvicorn must
re-import the application in each replacement worker.

`agency.app.main()` remains the parser for `python -m agency.app`, adds `--reload`,
and delegates to `run_server()`.

### Console CLI

The `serve` parser in `agency.cli` adds a boolean `--reload` option. `cmd_serve()`
calls `run_server()` with the parsed host, port, and reload values instead of rewriting
global process arguments and invoking the second parser.

No `--no-reload`, custom watch directory, include pattern, or exclude pattern option is
added. Reload is a development convenience with one predictable project-level policy.

### Watch Policy

Reload mode passes the following policy to Uvicorn:

- Reload root: the resolved current working directory.
- Included file patterns: `*.py`, `*.html`, `*.css`, `*.js`, `*.json`, `*.yaml`,
  and `*.yml`.
- Excluded development artifacts: VCS metadata, virtual environments, Python caches,
  test/tool caches, and package metadata directories within the reload root.
- Excluded runtime data: every `shared/` subtree beneath the reload root, using
  recursive patterns for each included file type.

The root `config.yaml` is deliberately included. A manual edit or an admin UI save
therefore restarts the development server. This is an accepted trade-off of reload
mode; normal mode continues to apply admin changes through the existing
`reload_groups()` calls without restarting.

Agency reserves `shared/` beneath a group root for durable runtime data, so the static
pattern covers existing groups, groups added after the supervisor starts, and groups
configured at the project root. It does not exclude the group or reload root itself.
Group directories outside the reload root require no exclusion because Uvicorn is not
watching them. Markdown records are also outside the include patterns.

### VS Code And Documentation

Change the existing hot-reload VS Code task to run:

```text
christag-agency serve --reload --host 127.0.0.1
```

This keeps local task behavior behind the supported public command. Preserve the
existing uncommitted changes in `.vscode/tasks.json` while changing only the direct
Uvicorn hot-reload invocation.

Document the opt-in development command near Quick Start and in the getting-started
guide. Production deployment and service examples continue to use `serve` without
`--reload`.

## Data Flow

1. The user runs `christag-agency serve`, optionally with `--reload`.
2. `agency.cli` parses the command and calls the shared launcher directly.
3. The launcher creates first-run config if needed and refreshes global group state.
4. In normal mode, Uvicorn serves the existing in-memory application.
5. In reload mode, Uvicorn supervises the current working directory and imports
   `agency.app:app` in a child worker.
6. A matching, non-excluded file event stops the current worker and imports a new one.
7. The new worker reads `config.yaml` during module initialization, so config edits are
   reflected after restart.

`python -m agency.app --reload` enters the same flow after its local argument parser.

## Error Handling

- Config creation and network bind errors propagate and retain Uvicorn's nonzero exit
  behavior.
- A Python syntax/import error is printed by the replacement worker. The Uvicorn
  supervisor remains available so a later valid save can recover the server.
- Malformed `config.yaml` is not ignored or replaced. The worker reports the YAML
  failure and can recover after the file is corrected and saved again.
- Reload exclusions are static patterns, so missing optional artifact or group data
  directories do not affect startup.
- A config save from the admin UI may complete immediately before the development
  worker restarts. That brief interruption is expected only when `--reload` is active.

## Tests

1. Extend CLI tests to prove `serve --help` exposes `--reload` and that `cmd_serve()`
   forwards host, port, and reload values to the shared launcher without mutating
   `sys.argv`.
2. Unit-test normal launcher mode by mocking `uvicorn.run()` and asserting it receives
   the in-memory ASGI app with no reload configuration.
3. Unit-test reload launcher mode by asserting it receives `agency.app:app`,
   `reload=True`, the resolved current working directory, and the approved include and
   exclude policy.
4. Prove `config.yaml` matches the reload policy while YAML job records under both an
  existing and a not-yet-created configured `shared/` directory do not.
5. Preserve first-run coverage by proving the default config is created before either
   Uvicorn mode starts.
6. Run the focused CLI/server tests, then the full pytest suite.

No timing-based test will start a persistent watcher. Unit tests will verify the
deterministic Uvicorn configuration, while a manual smoke check will confirm that one
source edit causes a worker restart on Windows.

## Acceptance Criteria

- `christag-agency serve --help` documents `--reload`.
- `christag-agency serve` starts exactly as before without a watcher.
- `christag-agency serve --reload` watches the current working directory and restarts
  for code, template, static, theme, and `config.yaml` changes.
- Writes under configured groups' `shared/` directories do not trigger restarts.
- The hot-reload VS Code task uses the public CLI command.
- Focused tests and the complete test suite pass.
