# Eager Ticket MCP Tool Exposure

Date: 2026-10-04
Branch: `feature/workflow-live-reconciliation`
Status: exact one-field correction approved by the user.

## Evidence And Scope

The authenticated ticket endpoint advertises its fixed12tools, but the readonly
Copilot acceptance session performs no broker call after empty native searches.
Changing the task's name did not help and was withdrawn. The original prompt
and strict assertions remain the acceptance criteria.

Official Copilot CLI issue3787 documents per-server `deferTools: "never"` for
initial tool exposure, and release1.0.63 introduces it. Installed1.0.92-3 is
newer. The generated server entry currently leaves exposure to default lazy
retrieval. These facts support the eager-exposure hypothesis; live acceptance
is still required to establish the correction.

Sources:
- https://github.com/github/copilot-cli/issues/3787
- https://github.com/github/copilot-cli/releases/tag/v1.0.63
- https://github.com/github/copilot-cli/issues/3875 (older subagent interaction
  reported resolved in1.0.86; do not substitute that report for current gates)

## Approved Change

Add only `"deferTools": "never"` to the server entry produced by
flowgency/integrations/ticket_tools.py::write_copilot_ticket_config. This is
disposable per-job projection metadata, not canonical configuration or a new
user setting. It changes when the existing12schemas are visible, not which
operations, paths, credentials, or authority are granted.

Preserve type, URL, headers, tools list, atomic replacement, CLI flags,
experimental/sandbox enforcement, auth, grants, network consent, model,
transport, lifecycle, original task wording, and every live assertion. Leave
builtin and other MCP server policies unchanged. The trade-off is bounded
additional initial context instead of lazy discovery.

## Validation And Handoff

Extend the existing exact JSON and supervised-config checks in
tests/test_copilot_ticket_tools.py first. Observe their failure, make the
one-field writer edit, and rerun them plus the nearby launch/consent/credential
checks. Independently review the task before the controller runs the original
strict readonly acceptance with unchanged prompt and assertions.

If live acceptance fails, retain its evidence, do not claim success, and do not
stack a model/grant/prompt/transport workaround. If it passes, obtain complete
isolated Python and browser baselines sequentially before resuming the approved
UI plan. Keep CLI compatibility claims limited to verified behavior; this
correction does not independently establish a new version-support range.