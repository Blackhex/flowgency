# Agent Card Run and Schedule Links - Design

**Date:** 2026-07-11
**Status:** Approved

## Goal

Make the relative run times on each regular agent card actionable:

- Clicking the last-run time opens that run's stdout log in the existing log viewer.
- Clicking the next-run time opens the exact editable schedule row that produced it on the Agent Prompts page.

The status dot, separator, and `Running` label remain informational.

## Scope

This change applies to regular agent cards on `/{group}/agents`, including their corresponding schedule rows on `/{group}/prompts`.

It does not add new endpoints, change dispatch semantics, alter the log viewer, or add client-side state. Collapsed subagent cards remain whole-card links to their profiles; adding nested run and schedule links there is out of scope.

## Existing Context

The agents page already renders `last_seen`, `running`, and `next_run` values from `collect_agents_with_identity()`. The relative values are currently plain text within one status span.

Completed jobs write paired `.out` and `.err` files under `shared/logs/YYYY-MM-DD/`. The existing `/{group}/logs/view?path=...` route validates a requested path against the group's log directory and renders `.out` content.

Dispatch rules live under `dispatch.agents.{agent}`. `compute_next_run()` currently returns only the earliest datetime, so the prompt and rule that produced that datetime are lost. The Agent Prompts page already exposes the same rules as editable assignment rows but gives those rows no fragment identifiers.

## Architecture

### Last Run Detail

Add a focused helper that resolves the latest stdout log for an agent. It returns either `None` or a small record containing:

- `at`: the file modification time as a `datetime`
- `path`: the absolute stdout log path

The helper considers only files that match the agent prefix and `.out` suffix, and chooses the maximum modification time. It must not rely on lexicographic filename order because job IDs do not encode execution order.

For agents with a stdout log, that timestamp supplies the card's relative last-run label and normal health calculation, so the displayed time, status color, and click destination refer to the same run. Existing last-seen behavior remains a fallback for historical activity that has no stdout file; such a label is rendered without a link.

### Next Run Detail

Extract the current candidate calculation into a detail helper that returns either `None` or a record containing:

- `when`: the next execution datetime
- `prompt`: the configured prompt filename
- `rule_index`: the rule's zero-based position in that agent's config list

The helper preserves current dispatch semantics:

- Disabled dispatch produces no next run.
- Rules without a prompt are ignored.
- Condition-triggered rules are ignored by the time scheduler.
- `at` and `every` values use the existing calculations and marker files.
- The earliest candidate wins.
- Equal datetimes resolve in config order.

Keep `compute_next_run()` as a compatibility wrapper that returns only the detail record's `when` value. Existing callers and tests that rely on its return type therefore remain valid.

`collect_agents_with_identity()` adds the detail record alongside the existing `next_run` datetime rather than replacing the public template field.

### Schedule Row Identity

While `collect_prompts()` inverts agent-centric dispatch config into prompt assignments, preserve each assignment's original `rule_index`. Each editable scheduled assignment row receives this fragment ID:

```text
schedule-{agent_name}-{rule_index}
```

The template HTML-escapes the row ID and URL-encodes the fragment used by the link, so configured agent names need no ad hoc normalization. The rule index makes the fragment unique even when one agent schedules the same prompt more than once.

The target row receives a scroll offset and subtle `:target` styling so the browser both positions and identifies the corresponding controls after navigation.

## User Interface

The regular card's idle status is rendered as separate elements:

1. A non-interactive health dot.
2. A last-run relative time.
3. A non-interactive separator when a next run exists.
4. A next-run relative time.

When a stdout target exists, the last-run time is an anchor to:

```text
/{group}/logs/view?path={urlencoded_stdout_path}
```

When a next-run detail exists and its prompt file is present as a normal editable prompt, the next-run time is an anchor to:

```text
/{group}/prompts#schedule-{agent_name}-{rule_index}
```

If the winning rule references a missing prompt file or an underscore-prefixed system prompt, which has no editable assignment row, the next-run link falls back to the agent's existing rule block in group dispatch settings:

```text
/admin/orgs/{group}/edit#rules-{agent_name}
```

Only the relative text is clickable. Links use the existing indigo hover/focus language, retain absolute timestamps in titles, and include descriptive accessible labels for opening stdout or editing the named prompt's schedule. They are normal same-tab navigation.

While an agent is active, the existing pulsing dot and non-link `Running` label remain unchanged. The current UI continues to hide idle last-run and next-run values until the run finishes and the page is refreshed.

## Data Flow

### Last Run

1. The agents route collects agent identity and status data.
2. The last-run helper resolves the newest stdout path and timestamp.
3. The template URL-encodes the path and renders the relative time as an anchor.
4. The existing log-view route validates and renders the selected `.out` file.

### Next Run

1. The next-run detail helper evaluates configured rules and retains the winning rule identity.
2. The agents template builds a prompt-page fragment from the agent name and rule index.
3. The prompts route independently preserves the same config rule index while building assignments.
4. The prompts template renders the corresponding row ID.
5. Native fragment navigation scrolls to the editable row and `:target` styling identifies it.

No API request or JavaScript click handler is required for either flow.

## Error Handling and Edge Cases

- No stdout log: preserve the existing non-link activity text, with no empty or dead anchor.
- Empty stdout file: link normally; an empty stdout log is still the correct artifact.
- Newer stderr file: ignore it when selecting the stdout destination.
- Missing or invalid next-run rule: render no next-run link, matching current behavior.
- Missing or system prompt: navigate to the agent's group dispatch rule block rather than a nonexistent editable row.
- Multiple rules with the same next time: use the first rule in config order.
- Log deleted after page render: allow the existing log viewer to return its normal 404; refreshing recomputes the latest target.
- Schedule changed after page render: normal navigation remains safe; refreshing recomputes the fragment target.
- Windows paths: URL-encode the complete stdout path before placing it in the query string.

## Testing

Focused tests cover:

- Latest stdout selection by modification time rather than filename.
- Ignoring `.err` files when resolving the click destination.
- Historical activity without stdout remaining visible but unlinked.
- Next-run detail containing the winning prompt and rule index.
- Deterministic config-order behavior for tied next-run candidates.
- Existing `compute_next_run()` datetime compatibility.
- Agent cards linking the last-run label to the URL-encoded stdout path.
- Agent cards linking the next-run label to the exact schedule fragment.
- No dead anchors when either target is absent.
- Prompt assignment rows exposing the matching fragment IDs.
- Missing and system prompt files falling back to the group dispatch rule block.
- `Running` retaining its current non-link presentation.

After focused tests pass, run the full pytest suite as regression verification.

## Alternatives Considered

### Resolver Endpoints

Dedicated last-run and next-run routes could resolve state at click time and redirect. This would keep long-lived pages slightly fresher, but adds routes, redirect behavior, and failure cases for values already computed during server rendering.

### Client-Side Resolution

JavaScript could fetch the latest targets before navigation. This adds an API call and client-state complexity without improving the normal server-rendered workflow enough to justify it.

The selected direct-link design matches the existing Jinja architecture and keeps the change local and testable.