from __future__ import annotations

from pathlib import Path

from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def build_sandbox_settings(
    policy: EffectiveRuntimePolicy,
    *,
    launch_dir: Path,
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

    return {
        "sandbox": {
            "enabled": True,
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
