# Log Ordering and Time Display Design

## Goal

Make the execution log list show when each log was written and order logs within each day by that time.

## Behavior

- Date groups remain ordered newest first.
- Entries within each date group are ordered by filesystem modification time, newest first.
- The timestamp source matches the agent detail activity timeline: `Path.stat().st_mtime`, converted to a local `datetime`.
- Each row displays `HH:MM` before the OUT or ERR badge. The date is omitted because the row is already inside a date group.
- When matching OUT and ERR files have the same modification time, OUT is shown before ERR.
- Existing behavior that hides empty ERR files remains unchanged.

## Implementation

`collect_logs()` will stat each visible file once and add a `timestamp` field alongside its name, path, suffix, and size. It will sort the collected entries by timestamp descending, with a deterministic suffix priority for equal timestamps.

The logs template will render the timestamp using the same small monospaced visual treatment as the agent timeline. No filesystem access or timestamp parsing will be added to the template.

## Error Handling

The current log collection behavior for filesystem errors remains unchanged. This feature does not introduce fallback timestamps because a fallback could silently misorder entries.

## Tests

Tests will set explicit file modification times and verify:

- Newer entries appear before older entries within a date.
- Equal-time OUT and ERR files appear in OUT-before-ERR order.
- Entries expose the local modification-time datetime used by the template.
- The rendered log list includes the expected `HH:MM` value.
- Empty ERR files remain omitted.
