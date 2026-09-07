from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from flowgency.integrations import BaseIntegration


RuleTarget = Literal["path", "no_path"]
_KNOWN_TARGETS = frozenset({"path", "no_path"})
_UNAVAILABLE_WARNING = "Tool availability could not be determined."


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    targets: tuple[RuleTarget, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Tool descriptor names must be nonempty strings.")
        if any(target not in _KNOWN_TARGETS for target in self.targets):
            raise ValueError("Tool descriptor targets must be 'path' or 'no_path'.")


@dataclass(frozen=True)
class ToolCatalog:
    integration: str
    version: str
    tools: tuple[ToolDescriptor, ...] = ()
    complete: bool = False
    warning: str | None = None

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for tool in self.tools:
            if tool.name in seen:
                raise ValueError("Tool catalog names must be unique.")
            seen.add(tool.name)


def catalog_id(catalog: ToolCatalog) -> str:
    payload = {
        "integration": catalog.integration,
        "version": catalog.version,
        "tools": [(tool.name, tool.targets) for tool in catalog.tools],
        "complete": catalog.complete,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def available_names(catalog: ToolCatalog, target: RuleTarget) -> tuple[str, ...]:
    return tuple(
        tool.name for tool in catalog.tools if not tool.targets or target in tool.targets
    )


def get_tool_catalog(integration: BaseIntegration) -> ToolCatalog:
    try:
        return integration.permission_tool_catalog()
    except Exception:
        return ToolCatalog(
            integration=integration.name,
            version="unavailable",
            complete=False,
            warning=_UNAVAILABLE_WARNING,
        )