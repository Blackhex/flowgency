# Compact Scheduled Next-Run Labels - Design

**Date:** 2026-09-06
**Status:** Approved (design), pending implementation plan
**Topic:** Render scheduled next runs as compact relative durations

## Problem

The dashboard agent card renders a future next run through the shared
`relative_future` formatter. Near-term values use phrases such as `5m away`,
while values beyond tomorrow become absolute timestamps such as
`2026-09-09 19:44`. The changing grammar and width make the schedule harder to
scan beside the compact last-run value.

The dashboard also gives an ordinary future schedule a sky-colored link, while
the adjacent last-run link uses a neutral gray. Neither value is a warning, so
the color difference gives the next run unnecessary emphasis.

## Goal

Use one compact vocabulary for future schedules on every surface backed by the
shared formatter:

- `due in 1m`
- `due in 7m`
- `due in 2h`
- `due in 7d`

On dashboard agent cards, render an ordinary future schedule link with the
same neutral text colors as the last-run link.

## Design

### Shared future formatter

`flowgency.health.relative_future()` remains the single formatter for future
schedule text. It will preserve the current coarse rounding and return:

| Time relative to now | Text |
| --- | --- |
| At or before now, or positive under one second | `due now` |
| At least one second and less than one minute | `due in 1m` |
| Less than one hour | `due in Nm` |
| Less than one day | `due in Nh` |
| One day or more | `due in Nd` |

Minute, hour, and day values remain single-unit, compact durations. Positive
intervals below one second are truncated to zero seconds and still render as
`due now`; one second through less than one minute renders as `due in 1m`.
Hours and days discard the smaller unit, matching the existing coarse
presentation. The formatter no longer switches to `tomorrow HH:MM` or an
absolute timestamp.

Because both the dashboard and the agent Routines tab consume this formatter,
both surfaces receive the same wording without introducing a second template
filter.

### Dashboard color

The dashboard link for an ordinary future schedule changes from
`text-sky-700 dark:text-sky-300` to the last-run link's
`text-gray-600 dark:text-gray-300`. Its destination and hover underline remain
unchanged.

Warning states remain semantic and unchanged:

- `due now` remains amber.
- `overdue` remains rose.
- Active job-status links keep their current color.

The `no schedule` link is not a scheduled next run and remains unchanged.

## Testing

- Update formatter tests to cover minute, hour, and sub-minute output with the
  `due in` prefix.
- Replace the tomorrow-specific expectation with a day-duration expectation,
  including a seven-day case that prevents a return to absolute dates.
- Verify the agent Routines page renders the shared `due in` wording.
- Verify a dashboard future-schedule link uses the same light and dark text
  color classes as the last-run link.
- Keep focused coverage for `due now` so warning-state wording does not regress.

## Out of Scope

- Changing schedule computation or dispatch timing.
- Changing `due now`, `overdue`, job-status, or no-schedule colors.
- Adding seconds or compound durations.
- Adding client-side countdown updates.