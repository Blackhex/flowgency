# Local Assets and Copilot Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Tailwind runtime network dependency and unrelated Copilot MCP/plugin startup from Flowgency-owned launches.

**Architecture:** Package a precompiled Tailwind 3 stylesheet alongside existing static assets. Prepare authentication-only private Copilot configuration, retain the existing sandbox and environment boundaries, and disable built-in MCP servers using measured CLI controls. Live probes select and report their executable and model explicitly.

**Tech Stack:** FastAPI, Jinja2, Python/pytest, Tailwind CSS 3.4.17, Node.js, Playwright, GitHub Copilot CLI.

## Global Constraints

- Authority: `docs/superpowers/specs/2026-09-19-local-assets-and-copilot-isolation-design.md`, approved and committed separately as `bfb79ab`.
- Work only in `C:/Projekty/Flowgency/.worktrees/workflow-transition-outputs`, branch `feature/workflow-transition-outputs`.
- Use its recorded baseline at `78db860b952252dc0ceba9c72f85ecec9e836ed2`: 3039 Python, 598 browser and 155 Linux tests passed. Preserve subsequent failed main runs as failed evidence.
- No appearance change, snapshot update, tolerance increase, new skip, timeout increase, or removal of a required live gate.
- No DNS, network-security, global plugin, global credential, or global model-setting changes.
- Keep unrelated package versions unchanged. Normal application startup and wheel installation must not require Node.js or a CSS build.
- Preserve credential withholding, the environment allowlist, sandbox/path/tool policy, executor eligibility, local-network consent, and ticket lifecycle containment.
- Do not contact Git remotes as part of application Git-evidence validation.
- No approved visual sketches exist for this follow-up: existing screenshot baselines are normative.
- Retain reports in this plan's ignored SDD workspace; never stage runtime configuration, ignored reports, prior recovery files, or unrelated state.

## Local Evidence

`base.html` currently executes `https://cdn.tailwindcss.com` and then assigns its runtime configuration. The retained browser trace proves DNS failure for that script. Font responses are already deterministic in `tests/ui/layout.ts` and are outside this change.

`CopilotIntegration._prepare_copilot_home` currently copies the complete personal `config.json`. Its top-level keys include `installedPlugins`, `lastLoggedInUser`, and `loggedInUsers`. A failed live run loaded the unrelated `metavr` plugin and a built-in GitHub MCP server, although Flowgency's server connected immediately. CLI 1.0.87-0 help documents `--disable-builtin-mcps`, `--no-auto-update`, `--additional-mcp-config`, and `--model`. An empty-home plugin inventory returned empty output, which is not by itself accepted as proof of isolation; a real launch must verify the server set.

### Task 1: Package Local Tailwind Assets

**Files:**
- Create: `tailwind.config.cjs`, `tools/tailwind.css`, `flowgency/static/tailwind.css` (generated).
- Modify: `package.json`, the existing lockfile only if the repository tracks one, `flowgency/templates/base.html`, `README.md`.
- Test: `tests/test_setup_assets.py`, `tests/ui/layout.ts`, an existing UI smoke/layout spec such as `tests/ui/layout.spec.ts` if present, otherwise the nearest existing spec using `installBasePageSetup`.

**Interfaces:**
- Consumes: current inline Tailwind configuration and all application source that produces utility classes.
- Produces: `npm run build:css` and packaged `/static/tailwind.css`; no runtime JavaScript Tailwind dependency.

- [ ] Add a regression to the existing asset tests for local CSS and no runtime Tailwind script. Reuse the existing app/client fixture rather than introducing a second application harness. The core assertions are:

```python
assert "https://cdn.tailwindcss.com" not in response.text
assert "tailwind.config" not in response.text
assert '/static/tailwind.css' in response.text
stylesheet = client.get('/static/tailwind.css')
assert stylesheet.status_code == 200
assert 'text/css' in stylesheet.headers['content-type']
assert '.hidden' in stylesheet.text
```

- [ ] Run the new test first and retain its expected RED result. Immediately validate any substantive test edit before more implementation.
- [ ] Install exactly `tailwindcss@3.4.17` as a development dependency without upgrading existing dependencies. Respect the repository's existing lockfile policy. Add:

```json
"build:css": "tailwindcss --config tailwind.config.cjs --input tools/tailwind.css --output flowgency/static/tailwind.css --minify"
```

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] Move the existing runtime configuration unchanged into CommonJS configuration, adding source discovery:

```javascript
module.exports = {
  content: ['./flowgency/templates/**/*.html', './flowgency/static/**/*.js', './flowgency/**/*.py'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      screens: { '3xl': '1920px' },
      colors: {
        flowgency: {
          50: '#f0f4ff', 100: '#dbe4ff', 200: '#bac8ff',
          500: '#5c7cfa', 600: '#4263eb', 700: '#3b5bdb',
          800: '#364fc7', 900: '#1e2a5e', 950: '#141b3d',
        },
      },
    },
  },
};
```

Inspect dynamically composed utility names and add a bounded safelist or expose their complete names to scanning. Do not assume interpolation is discovered. Preserve the effective cascade against inline theme styles; investigate screenshot discrepancies rather than updating baselines.

- [ ] Build CSS and replace only the CDN script/runtime configuration with the local link. Rerun the same regression immediately.
- [ ] Extend existing wheel assertions to require the generated CSS bytes in the wheel. Document the rebuild command and when templates/utility classes require it.
- [ ] Block `https://cdn.tailwindcss.com/**` in the shared browser setup so existing browser assertions exercise the offline-Tailwind path. Add a focused assertion that no Tailwind CDN request is attempted, and check visible desktop/mobile utility-driven layout, not just a successful HTTP response.
- [ ] Run the existing affected browser smoke tests across all four projects with current snapshots. Run `tests/test_setup_assets.py` and build twice, comparing the generated file's SHA-256 before/after to prove repeatability. Store commands/results in the report.
- [ ] Commit with a scoped Conventional Commit, then obtain task-scoped spec and code-quality review before Task 2. Do not push or integrate.

### Task 2: Isolate Copilot Launches and Verify Live Discovery

**Files:**
- Modify: `flowgency/integrations/flowgency/copilot.py` and its existing configuration descriptor/validation surface.
- Test: `tests/test_copilot_home.py`, `tests/test_copilot_launch_arguments.py`, `tests/test_copilot_credentials.py`, `tests/test_copilot_capability_detection.py`, related existing ticket transport tests.
- Modify/test: `tests/_runtime_probe_helpers.py`, `tests/test_runtime_projectors_live.py`, `tests/test_ticket_runtime_live.py`, relevant existing deterministic helper tests, `tests/conftest.py` only if shared fixture wiring needs it.
- Document: `kb/integrations.md` and/or the existing Copilot runtime documentation.

**Interfaces:**
- Consumes: `IntegrationRunRequest`, existing `CopilotIntegration` configuration, `InstalledRuntime(name, command)`, supervised ticket launch lifecycle, and canonical `integration_config`.
- Produces: private authentication-only Copilot configuration for every job, required isolation argv controls, optional canonical `model` configuration, explicit live executable/model diagnostics, and server-discovery assertions.

- [ ] Add a RED home regression using a source configuration containing fake authentication identity fields, `installedPlugins`, an unrelated `model`, and `trustedFolders`. Assert the job config preserves authentication values only, has no installed plugin or inherited model/trust preferences, and leaves the source bytes unchanged. Use synthetic values, never real tokens in tests or logs:

```python
source = {
    'lastLoggedInUser': {'login': 'fixture-user', 'host': 'https://github.com'},
    'loggedInUsers': [{'login': 'fixture-user', 'host': 'https://github.com'}],
    'installedPlugins': [{'name': 'unrelated-fixture', 'enabled': True}],
    'model': 'personal-default',
    'trustedFolders': ['C:/personal'],
}
expected = {key: source[key] for key in ('lastLoggedInUser', 'loggedInUsers')}
assert job_config == expected
assert source_path.read_bytes() == original_bytes
```

- [ ] Run that regression and preserve the expected RED result before implementation.
- [ ] Replace whole-file copying with structured JSON and an explicit authentication-key allowlist. Preserve the existing supported authentication fields `lastLoggedInUser`, `loggedInUsers`, and `authProvider`; verify real authentication in the live probe. Keep the per-job settings generated by `build_sandbox_settings` unchanged. Always prepare a private home even when the source config is absent: write an empty config and retain genuine CLI authentication failures, never fall back to the shared home. Malformed/non-object source config must fail clearly before agent launch. Do not log credential values.
- [ ] Rerun the home regression immediately, then add/run missing-source, malformed-source, absent-plugin, nested-auth preservation, source-nonmutation, and environment-boundary tests.
- [ ] Add RED argv/capability tests covering both ordinary and supervised ticket launches:

```python
assert '--disable-builtin-mcps' in argv
assert '--no-auto-update' in argv
assert argv[argv.index('--model') + 1] == 'gpt-5.4'
```

For a launch without configured `model`, assert no model argument is invented or inherited from personal config. For a ticket launch, assert the sole extra MCP configuration is the trusted Flowgency-generated configuration and the lifecycle supervisor is still used.

- [ ] Add optional nonblank string `model` to canonical Copilot integration configuration and render it as its own argv element. Add required isolation flags to all job launches. Verify flag support with the existing bounded/cached CLI capability mechanism and fail closed on unsupported isolation, including when ordinary permission validation is bypassed. Keep sandbox-version rules and permission grants unchanged. Update synthetic help fixtures only to model the flags under test, not to bypass the guard.
- [ ] Rerun argv/capability tests immediately. Run the existing home, credentials, sandbox, launch arguments, capability, output, and ticket transport suites before live checks. Any failures in those surfaces must be repaired locally without weakening their permission assertions.
- [ ] In shared live-test wiring, resolve the executable once, bind launches to that exact path, report its version, and disable automatic updates for that launch. Select model `gpt-5.4` explicitly for Copilot live probes via canonical integration configuration, allowing an explicit `FLOWGENCY_TEST_COPILOT_MODEL` override. Do not modify global defaults or persist test preferences into runtime `config.yaml`. Preserve non-Copilot runtime behavior and all existing skip/failure policies.
- [ ] Extend real session-event parsing in the existing helpers to verify that no unexpected MCP server is present, including failed servers. A ticket session must report `flowgency-tickets` connected and execute a ticket read; an ordinary session must not load unrelated MCP servers. Assert on the event data and actual tool call, not assistant prose. Preserve expected/actual server names in failures without credentials.
- [ ] Run a minimal real ticket discovery/read probe first, and then the three failures by their existing names: `test_agent_verifies_presatisfied_project_without_rewriting_it`, `test_agent_in_restricted_workspace_keeps_ticket_flow_and_records_denied_write`, and `test_one_run_creates_updates_and_transitions_multiple_tickets`. Retain startup evidence and verify personal configuration hashes are unchanged. Keep the 300-second deadlines. If services fail, report that as failure with the exact stage; do not widen scope into network settings or authentication changes.
- [ ] Commit the isolated implementation and tests with Conventional Commits; obtain task-scoped spec and code-quality review. Do not push or integrate.

## Controller Verification and Integration

- [ ] Record each task's base/tip, RED/GREEN checks, review findings and disposition in this plan's SDD ledger.
- [ ] At the reviewed feature tip run the complete Python suite and complete browser suite, with fresh logs and explicit exit records. Preserve failed attempts separately. All four browser projects must retain existing screenshot tolerances and baselines.
- [ ] Obtain one final review of `78db860..HEAD`; the earlier Git-evidence range already has its whole-branch and repair reviews. Address concrete final findings once and obtain a scoped re-review.
- [ ] Once all required gates pass, inspect both checkouts and fast-forward main according to `AGENTS.md`, preserving any unrelated changes without restoring previously discarded edits.
- [ ] Run the complete Python and browser gates on main. Only after genuine green verification push main and the feature branch without force, verify remote tips, archive ignored reports, inspect the worktree for local/runtime data, remove it normally, and prune stale worktree entries. Keep the feature branch.