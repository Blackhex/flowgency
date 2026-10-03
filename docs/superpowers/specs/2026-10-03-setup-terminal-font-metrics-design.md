# Setup Terminal Font Metrics

Date: 2026-10-03
Branch: `feature/setup-terminal-font-metrics`
Worktree: `.worktrees/setup-terminal-font-metrics/`

## Failure And Evidence

The live embedded Copilot terminal renders ordinary text with excessive spacing.
The requested font remains JetBrains Mono at 13px and its face is loaded, but
xterm retains approximately 12.27px cells and adds approximately 4.46px spacing
to ordinary characters. Loaded JetBrains Mono measures approximately 7.8px per
ordinary character. A 13px serif fallback W measures 12.2700px, matching the
cached cell width. Dispatching an ordinary resize event did not repair it.

The terminal currently opens before webfont loading settles. Its
`document.fonts.ready` callback invokes fitting, which does not refresh the
initial cached character measurement. Existing browser tests supply fast,
deterministic fonts and do not cover delayed font delivery.

The terminal bundle and configured font did not change in the preceding
workflow-completeness fix. This is a cold/delayed-font initialization problem,
not an intentional typography or renderer change.

## Approved Scope

Preserve JetBrains Mono at 13px, the existing xterm renderer, page layout, and
terminal controls. Fix font readiness and character measurement only. Do not
bundle font assets, change font licensing/build inputs, replace the renderer,
or redesign setup.

Do not stop, restart, answer, or otherwise alter the user's live Copilot process.
Do not modify setup configuration, agent data, or runtime permissions. Browser
diagnosis may inspect only font/layout metrics, not credentials or raw terminal
output.

## Initialization

Use an explicit preferred font stack including a monospace fallback. Request
the existing terminal font faces through the browser font-loading API before
opening and measuring the terminal. Merely fitting after `document.fonts.ready`
does not satisfy this requirement.

The initial font wait must be bounded at 2 seconds. A failed font request,
missing font-loading API, empty face result, or timeout must leave a usable
13px monospace terminal rather than a blocked or blank session. Do not swallow
unrelated terminal or transport errors as font-loading failures.

On successful initial loading, open the terminal with the preferred font and
fit before sending its geometry. On timeout, open with the stable monospace
fallback. If the requested face subsequently loads, change the public font
option from the fallback to the preferred stack so xterm remeasures characters,
then refit and send the actual updated rows and columns.

Use only xterm's public APIs, not private measurement services or DOM character
styles. Keep the initialization and late-load continuation race-safe: a
late-loaded face must not update a disposed page or reconnect a terminal that
has stopped retrying. Avoid unnecessary new abstractions or files.

## Session Compatibility

Font readiness changes display initialization, not ownership. Preserve keyboard
input, replay, truncated-output handling, resize propagation, bounded reconnect,
failed-session diagnostics, Stop, and completion navigation. A font arriving
late must not reset the screen, discard terminal history, stop the server-owned
CLI, or send user input. Preserve existing attach/control security.

Existing resize observers and window resize handling continue to refit after
initialization. A font change and a container resize must converge on the same
current geometry without duplicating sockets or terminal instances.

## Verification

Reuse existing setup Playwright fixtures and font-response helpers. Delay the
actual JetBrains font response so the regression exercises real xterm metrics,
not a mocked renderer or assertions on font-option source text.

Cover cold immediate load, load delayed beyond the initial wait, failed font
load, and unavailable font-loading API. Assert a usable terminal, ordinary
monospace glyph/cell agreement, no multi-pixel padding of ordinary characters,
and correct resize messages. Check that late delivery preserves keyboard input
and the running session. Keep assertions robust to device-pixel rounding rather
than requiring one exact platform-specific pixel width.

Use desktop and mobile default-headless projects, including the delayed-load
regression and the existing setup session/input/resize/reconnect tests. Inspect
screenshots of the rendered terminal for readable spacing. Do not update
unrelated snapshots or relax tolerances.

Rebuild the committed terminal bundle and its generated CSS/legal asset through
the existing build command. Follow repository requirements: establish the full
worktree baseline before implementation, use focused checks during iteration,
review, run complete Python and browser gates sequentially, then perform the
pre-authorized fast-forward, master verification, publication, and owned-worktree
cleanup. Preserve the feature branch and unrelated/runtime-local files.

## Alternatives

- Approved: explicit bounded font readiness, stable monospace fallback, and
  public-option remeasurement for late arrivals. This keeps the current visual
  design and avoids new assets.
- Not selected: bundle fonts locally. It would improve offline reliability but
  adds font assets, licensing, and build changes beyond this narrow correction.
- Rejected: change font size, manually override xterm character spacing, or
  switch renderers. Those would conceal the stale-metric problem.

## Acceptance Criteria

1. Immediate and delayed font delivery no longer leaves serif-sized cells with
   extra spacing around JetBrains Mono text.
2. Font failure or timeout produces a usable, bounded-time monospace fallback.
3. A late successful load remeasures and refits using public APIs without
   resetting history, duplicating sockets, or interrupting the CLI.
4. Existing session controls, security, and geometry behavior remain intact.
5. No runtime data, font assets, dependency manifests, or unrelated snapshots
   are changed.