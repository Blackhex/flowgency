# Manual Launch Memory Selector Design

## Problem

The agents-page manual launcher validates memory form fields, but stores a valid
override as a plain dictionary on `JobRequest`. Job resolution passes that value
to `resolve_memory_selector()`, which requires a `MemorySelector` and reads its
`scope` attribute. Any manual launch with an explicit memory target therefore
fails with HTTP 500 before a job can be queued.

The CLI already constructs `MemorySelector` values for the same request field.
The web producer and the job request type should follow that established
contract.

## Scope

Correct explicit memory overrides for both saved-prompt and one-off launches.
Both modes share the same form parsing and `JobRequest` construction path.

This change does not alter the launcher UI, memory precedence, durable job
schema, configuration schema, CLI behavior, or worker launch behavior.

## Architecture

The manual launch route remains responsible for parsing form values and
returning field-appropriate HTTP 400 responses. After validating the selected
scope and optional channel, it constructs a `MemorySelector` rather than a
dictionary.

`JobRequest.memory_override` declares the existing domain contract as
`MemorySelector | None`. Job resolution continues to select the effective
memory and resolve its canonical identity without coercion or fallback logic.
This keeps malformed producer values visible instead of silently normalizing
them downstream.

## Data Flow

1. The route resolves saved-prompt or one-off task input as it does today.
2. It validates the optional memory scope, channel membership, channel/scope
   pairing, and the requirement that routine memory target a selected routine.
3. It creates `MemorySelector(scope=..., channel=...)` for a valid override.
4. It submits a `JobRequest` containing that selector.
5. Existing effective-memory selection and canonical resolution produce the
   job's immutable memory binding.

## Error Handling

Existing invalid form values continue to return HTTP 400 with their current
messages. A valid explicit memory target must reach normal submission and return
HTTP 202 rather than raising `AttributeError` and returning HTTP 500. No silent
defaulting is introduced.

## Testing

Route regression coverage submits both a saved prompt and a one-off task with
an explicit memory target, captures each resulting `JobRequest`, and verifies
that `memory_override` is the expected `MemorySelector`. The existing routine
memory test is updated from a dictionary expectation to the typed contract.

Run `pytest tests/test_agent_run.py -q` as the focused check, followed by the
complete `pytest tests/ -q` suite before completion.