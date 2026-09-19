# Local Assets and Copilot Isolation

## Approval and Context

On 2026-09-19 the user selected remediation options 2 and 3: package Tailwind
locally and isolate live Copilot launches. These are follow-up fixes on
`feature/workflow-transition-outputs`, not a new UI design or a change to the
locally verified Git-evidence contract.

The current feature baseline is `78db860b952252dc0ceba9c72f85ecec9e836ed2`.
Its recorded feature gates passed: 3039 Python tests, 598 browser tests, and
155 Linux Git/process tests. The subsequent main-checkout retry failed with
three live Copilot failures and 42 browser failures. Preserve those failures
and the original passing evidence; do not relabel either run.

The browser trace proves a DNS failure for `https://cdn.tailwindcss.com/`.
The page then lacks utility styles and its inline Tailwind configuration
raises an error. One Copilot run failed to retrieve its model catalog.
Another connected to Flowgency immediately but loaded unrelated MCP servers;
an inherited plugin hit a 60-second startup timeout. That run completed 11
ticket calls before the overall 300-second timeout. The remaining timed-out
run reported unavailable ticket tools and needs a focused discovery check.

## Local Tailwind Assets

- Compile Tailwind CSS ahead of time using an exact version of Tailwind 3,
  preserving the current configuration, theme extensions, font stacks,
  breakpoints, preflight behavior, and utility semantics.
- Scan the application's templates and other utility-class-producing source
  files. Cover any dynamically assembled classes explicitly rather than
  relying on runtime generation.
- Check the generated stylesheet into `flowgency/static` and include it in
  wheels. Normal application startup and packaged installations must not
  require Node.js, an asset build, or network access for Tailwind.
- Replace the CDN script and inline runtime configuration with a local
  stylesheet reference. Preserve cascade ordering against existing styles.
- Add an explicit, repeatable development build command. Keep unrelated
  package versions unchanged. Document when developers must rebuild CSS.
- Prove that existing screens and responsive layouts render with Tailwind's
  CDN blocked. Existing screenshots remain the visual contract; do not update
  baselines or increase tolerance to conceal a styling regression.

Google Fonts and other unrelated external services are outside this change.
The browser fixtures already provide deterministic local font responses.
No new layout sketches are needed because the approved appearance is unchanged.

## Isolated Copilot Launches

- Isolation applies to Flowgency-owned Copilot launches, including the live
  acceptance tests. Do not alter the user's global Copilot configuration,
  plugins, credentials, or editor session.
- Prevent unrelated user/plugin MCP servers and built-in MCP servers from
  loading into these launches. Ticket-enabled launches must retain exactly
  the Flowgency-supplied ticket transport and its trusted lifecycle.
- Use per-job configuration and supported, measured CLI controls. Do not
  hard-code the name of one user's plugin as the general isolation policy.
  A copied home alone is insufficient evidence of isolation.
- Preserve authentication through the established credential handling and
  environment allowlist. Preserve sandbox rules, local-network consent,
  executor eligibility, and credential-withholding behavior. Do not widen
  permissions or fall back to an unisolated launch while claiming isolation.
- Make live acceptance runs select their CLI/model explicitly, and report
  that selection. Do not change the user's global default model. Keep runtime
  preferences in canonical integration configuration, not inferred directory
  contents or global preference files.
- Test both launches without ticket tools and supervised ticket launches.
  Verify missing-credential and unsupported-capability paths explicitly.
  A minimal live probe must show the expected server set and discover and
  invoke Flowgency ticket tools before retrying longer acceptance scenarios.

Isolation removes unrelated startup work; it does not make model inference
offline or guarantee that an external model service is available. Diagnose
remaining service failures honestly. Do not increase the 300-second timeout
or weaken ticket assertions as part of this change.

## Implementation and Verification

Use the existing isolated feature worktree and its recorded baseline. Add a
failing regression first for each change, run it, implement the smallest
corresponding fix, and rerun that check before continuing. Keep local-asset
and Copilot-isolation changes independently reviewable.

Required checks cover:

1. Local stylesheet delivery, deterministic rebuild, wheel inclusion, and
   rendering without the Tailwind CDN across the four existing UI projects.
2. Copilot launch arguments, per-job configuration, credential and permission
   preservation, capability failure paths, and absence of unrelated servers.
3. Focused live ticket discovery and the three previously failing acceptance
   scenarios, retaining startup and execution evidence.
4. Complete Python and browser suites at the reviewed feature tip, followed
   by the repository-required fast-forward and main-checkout verification.

All existing live gates remain required. Neither separate test reporting nor
an isolated passing retry replaces a required complete-suite result. Do not
alter snapshot tolerances, skip policies, authentication, machine DNS, or
network security settings to obtain a pass.

Commit this specification and the implementation plan separately before code
changes. After review and passing gates, follow the repository's authorized
fast-forward, push, archive, and worktree-cleanup process. Preserve runtime
configuration, unrelated files, previous failure artifacts, and the discarded
local changes' recovery archive. Do not restore the discarded local changes.

## Rejected Alternatives

- Repeated full retries without addressing either dependency.
- Tailwind interception only in tests, which leaves application users exposed
  to the same runtime CDN failure.
- Globally disabling the user's plugins or changing their default model.
- Broadly increasing timeouts, skipping live tests, changing screenshots, or
  treating an infrastructure failure as a successful acceptance result.