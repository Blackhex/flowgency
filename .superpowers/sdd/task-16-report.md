# Task 16 Report

Date: 2026-07-16
Base commit before Task 16: 8ed2590a198598f3a86475cccb7f764085689f7c

## Review follow-up

Named-risk review of the local Task 16 diff found one concrete defect in the
admin Agent Library list route:

- When the configured Agent Library root was missing or unreadable, the admin
	list page could propagate filesystem errors instead of returning an actionable
	admin response.

Fix applied:

- `agency/web/routes/admin_library.py` now mirrors the roster page's guarded
	library loading path: missing root, non-directory root, invalid blueprint
	assets, and generic OS read errors render the Agent Library page with a 409
	and the exception message.
- Added a focused regression test proving `/admin/agent-library` returns an
	actionable 409 response instead of failing when the library root is absent.
- Cleared remaining Task 16 long-line/newline diagnostics in the touched test
	files and route export module.

## Focused validation

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_agent_library_routes.py tests/test_memory_channel_routes.py -v
```

Result:

```text
13 passed in 8.96s
```

Includes the added regression:

- `test_library_list_handles_missing_root_actionably`

## Adjacent slice

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_agent_detail.py tests/test_agent_roster.py tests/test_admin_agent_create.py tests/test_admin_dispatch.py tests/test_integrations.py tests/test_integration_contract.py -q
```

Result:

```text
212 passed in 13.08s
```

## Full suite

Command:

```text
.venv\Scripts\python.exe -m pytest tests/ -q
```

Result:

```text
1051 passed, 3 skipped in 85.39s
```

The previously noted Windows detached-worker flake did not reproduce on this
fresh full-suite run, so no isolated rerun fallback was required.

## Risk audit summary

- Blueprint source writes remain serialized per blueprint key via the
	per-key lock path under the library root.
- Expected digest is checked while the lock is held, before publish.
- Validation is performed against the fully staged source tree via
	`inspect_blueprint(stage_parent, key)`.
- Source capture rejects symlinks, junctions, reparse points, duplicate
	normalized paths, and path traversal escapes.
- Editable paths remain limited to `AGENTS.md` or files under exactly one
	`.agents/skills/<slug>/...` tree.
- Channel metadata config revisions and memory content revisions remain
	separate; 409/423 responses leave the other resource unchanged.
- Unknown channel reads still 404 without creating store directories.
- Rekey/delete remain blocked when config references exist.
- Channel pages do not display the internal content hash/revision.
- Request-scoped services are used by the new routes; templates rely on Jinja
	escaping and do not introduce `Markup` or raw user HTML rendering.
- Router inclusion is live in `agency/app.py` and there are no duplicate Task 16
	route registrations.

## Review Fix 1 local report

Fixed the Agent Library save-path regression that was leaving `_locks` and
other save-time infrastructure inside the standards-only source root.

What changed:

- Save locks now live under a hidden sibling infrastructure root derived from
	the configured library path, not inside the source tree.
- Per-blueprint lock filenames are hashed with SHA-256, so the lock leaf is a
	safe opaque key instead of a user-controlled path segment.
- Staging and backup temp roots now also come from the same hidden sibling
	infrastructure area, which keeps captures and source listings clean.
- The route now verifies the created save infra is not a symlink or reparse
	point before using it.

Evidence:

- `tests/test_agent_library_routes.py` now checks that a successful save leaves
	no `_locks` or `.agency-agent-library` directories under the library root.
- The same test file covers concurrent save serialization and stale digest
	rejection.
- Focused validation passed:
	- `python -m pytest tests/test_agent_library_routes.py -v`
	- `python -m pytest tests/test_blueprint_library.py tests/test_cache_locking.py tests/test_compilation_cache.py tests/test_blueprint_digest.py -v`
	- `python -m pytest tests/ -q`
