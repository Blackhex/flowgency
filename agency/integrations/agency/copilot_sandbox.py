from __future__ import annotations

from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def build_sandbox_settings(
    policy: EffectiveRuntimePolicy,
) -> tuple[dict, tuple[ResolvedPermissionRule, ...]]:
    """Translate permission rules into a Copilot sandbox settings mapping.

    Returns the settings dict and any rules that could not be expressed
    (path is None — a filesystem policy cannot represent a pathless grant).
    """
    readonly: list[str] = []
    readwrite: list[str] = []
    unenforceable: list[ResolvedPermissionRule] = []

    for r in policy.rules:
        if r.path is None:
            unenforceable.append(r)
            continue

        path_str = str(r.path)

        if r.tools is None or "write" in r.tools:
            readwrite.append(path_str)
        elif r.tools:
            readonly.append(path_str)
        # empty tools tuple: omission is the denial — add to neither list

    readwrite_set = set(readwrite)
    readonly = [p for p in readonly if p not in readwrite_set]

    # Copilot's filesystem policy is an allowlist: a path it does not name is
    # denied. An unrestricted policy that confines nothing cannot be rendered
    # at all -- an empty allowlist would deny everything, the exact inverse of
    # what the policy grants, and the CLI hangs against it.
    #
    # Only AUTHORED rules decide this. Every job carries generated launch-zone
    # grants, so counting them would make this test always true and re-create
    # the defect on the one path that matters: an unrestricted policy with no
    # authored rule would render an allowlist holding only the zones, denying
    # the agent its own workspace while the launch arguments say allow-all-paths.
    confines = any(
        not r.generated and r.path is not None and (r.tools is None or r.tools)
        for r in policy.rules
    ) or policy.mode == "restricted"
    if not confines:
        # Turning the sandbox off drops any denial the operator did author.
        unenforceable.extend(
            r for r in policy.rules if r.path is not None and r.tools == ()
        )

    return {
        "sandbox": {
            "enabled": confines,
            "allowBypass": False,
            "addCurrentWorkingDirectory": False,
            "userPolicy": {
                "filesystem": {
                    "readonlyPaths": readonly,
                    "readwritePaths": readwrite,
                },
            },
        },
    }, tuple(unenforceable)
