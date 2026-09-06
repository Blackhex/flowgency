# Restore Readable Agent Logs

**Date:** 2026-09-06
**Status:** Approach approved; written specification pending user review

## Problem And Evidence

The last-run link correctly opens the existing stdout log viewer, but one
Experience Engineer run displays raw Copilot JSONL instead of readable messages.
Tracing that stored log through `_parse_jsonl_output_details` produced an
`AttributeError` at the tool-arguments lookup: an event supplies `arguments` as
a string, while the parser assumes a mapping and calls `.get("path")` on it.
The parser's outer exception handler then returns the entire raw stream,
discarding already-collected messages, file changes, and write attempts.

The affected file is 2,754,157 bytes across 2,219 lines. The log route treats
every `.out` file as Markdown and performs conversion synchronously inside an
async handler. An isolated reproduction exceeded 15 seconds in Markdown's
inline parser. Concurrent requests to the dashboard received no response until
the log request finished. HTML in captured tool output was also interpreted as
page markup, producing malformed asset requests.

These observations explain both the raw JSON regression and the expensive
rendering path. They do not establish that the scheduler failed or that clicking
the last-run link started an agent.

## Goals

- Restore human-readable assistant output for new Copilot jobs.
- Recover readable messages from already-stored Copilot JSONL logs at view time.
- Preserve ordinary Markdown log formatting and the existing same-tab log route.
- Keep malformed events from invalidating unrelated valid messages or metadata.
- Prevent log content from introducing executable HTML, styles, or resources.
- Bound log-view work and keep synchronous processing off the async event loop.

## Scope And Non-Goals

Limit changes to Copilot event normalization, log presentation, the existing
log-view route/template, and their regression tests. Use existing project
utilities where suitable; do not change the global Markdown renderer's behavior
for unrelated pages. Use an established HTML sanitizer for log Markdown if the
repository does not already provide one.

Do not introduce a conversation timeline, streaming output, a new job state,
automatic retries, or heuristic job-success detection. Do not change scheduling,
permissions, config authority, or session-resume semantics. Do not rewrite stored
logs, migrate jobs, restart the user's dashboard, or launch configured agents
as part of diagnosing or verifying this fix.

The original log file remains the authoritative artifact. Viewing it is a
read-only operation and does not submit or resume jobs.

## Copilot Event Normalization

Retain the parser's public return contracts for text, file changes, and write
attempts. Both normal completion and timeout handling use the corrected parser.

For a tool event's arguments, accept a mapping directly or decode a JSON-encoded
string using the JSON parser. Only a decoded mapping can supply named arguments.
Invalid JSON, arrays, scalars, or absent arguments contribute no named argument
metadata. Do not evaluate strings or infer filesystem paths from free text.

Validate the shape of an event and each nested field before using it. Invalid
JSON lines, non-object events, unexpected `data` values, non-string assistant
content, and malformed telemetry must not discard valid information from other
events. A malformed field affects only the information depending on that field;
for example, unusable line-count metrics must not remove a known write attempt.

Preserve the established ordering of complete assistant messages, file-change
aggregation, and deduplicated normalized write attempts. Do not expose reasoning
events, tool payloads, or token deltas as assistant output, and do not duplicate
messages by combining deltas with complete-message events.

Plain text remains supported. If the existing integration contract requires raw
output when no readable message can be recovered, retain that fallback for
diagnostic fidelity. The viewer must recognize and safely display such raw data
rather than sending it through Markdown as ordinary prose.

## Existing Log Recovery

The log presentation boundary distinguishes recognizable Copilot event streams
from ordinary Markdown using parsed event envelopes, not merely the `.out`
suffix or the presence of braces. A normal Markdown document containing a JSON
code example must remain Markdown.

For a recognizable stream, reuse the corrected message-extraction behavior and
render the recovered assistant text. Malformed lines or unrelated event types
do not prevent recovery of later valid messages. If no messages are recoverable,
show escaped raw text within the display limit. Never render the raw stream as
Markdown. Recovery must not modify the underlying file or job record.

For ordinary `.out` text, retain Markdown headings, lists, tables, fenced code,
and safe links. Stderr and other supported text logs remain escaped plain text.

## Rendering, Safety, And Responsiveness

Keep the existing filename, back navigation, route shape, and log-root access
validation. No user-controlled path may bypass the existing traversal checks.
Missing files retain the normal 404 behavior; invalid UTF-8 bytes are displayed
with replacement characters rather than crashing the viewer.

Perform file reading, event decoding, Markdown conversion, and sanitization
outside the async event loop. Use a per-render Markdown converter or an existing
thread-safe equivalent; do not concurrently reuse the mutable global converter.

Bound the preview to the first 4 MiB of source bytes and 64 KiB of UTF-8 display
text. Read at most the source limit plus one byte to detect truncation, rather
than reading the whole file and slicing afterwards. Decode partial Unicode
safely and ignore an incomplete trailing JSONL record. Show a concise truncation
status when either bound is reached. The original full artifact remains intact;
this change does not add a raw-download endpoint or a pagination interface.

Sanitize Markdown-generated HTML with an allowlist suitable for prose and code.
Log-origin scripts, event-handler attributes, styles, embedded frames, forms,
active URLs, and resource-loading tags must not become active page content.
Allow safe ordinary links, but reject unsafe URL schemes. Escape plain-text
fallbacks through normal template escaping; do not mark unsanitized logs safe.

The limits and worker execution complement each other: offloading alone is not
a resource bound, and a byte limit alone does not justify parsing raw JSON as
Markdown. Tests must check both boundaries and the representative regression.

## Verification

Add focused tests before implementation for:

- Mapping arguments and JSON-string arguments, including a write tool.
- Invalid arguments, event/data shapes, message types, and telemetry between
  valid events, preserving readable messages and valid change/attempt metadata.
- Normal and timeout integration paths using the corrected parser.
- Historical JSONL rendered as readable output without rewriting its file.
- Raw JSONL with no assistant messages rendered as inert text, not Markdown.
- Ordinary Markdown formatting and JSON code examples preserved.
- Script tags, event handlers, unsafe links, styles, and embedded resources
  remaining inert in stdout and stderr views.
- Existing missing-file and log-root traversal behavior preserved.
- Preview bounds, truncation status, partial UTF-8, and partial final JSONL line.
- A paused log-processing worker not blocking a separate lightweight request.

Use small synthetic fixtures representing the offending event shapes; do not
commit real job transcripts, credentials, tool payloads, or private paths.
Use a generated roughly 3 MiB JSONL fixture for the representative performance
check. Record end-to-end view latency and compare it with the observed
greater-than-15-second Markdown conversion, without relying on a fragile
machine-specific timing assertion as the sole regression test.

Verify desktop and mobile rendering of normal Markdown, recovered messages,
and truncation states with disposable data. Check that log-origin resource
requests or scripts do not execute. A local read-only replay of the reported
log may supplement synthetic tests, but its content must remain local and its
file must not be changed.

Run focused tests while iterating and the complete suite before branch review
and integration. Any necessary test server uses disposable configuration with
no scheduled work and must not drain the user's existing queue.

## Alternatives And Review Gate

A plain-text-only viewer was considered as a small performance fix, but the
user requested the previous readable log experience. A full structured event
viewer is unnecessary for restoring that behavior. The approved approach is
parser repair plus safe, bounded presentation of existing logs.

No visual mockup was needed or approved; retain the existing layout rather
than introducing a new design. The fresh isolated baseline is 2,047 passed
and 6 skipped in 271.62 seconds on 2026-09-06.

Review this written specification, including the explicit preview bounds,
before writing and separately committing the implementation plan.