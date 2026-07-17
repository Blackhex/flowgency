from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from agency.configuration.models import MemoryChannel, MemorySelector

from .models import ResolvedMemory


_MEMORY_HASH_DOMAIN = b"agency-memory:v1\0"


def resolved_memory_from_canonical(
    selector: MemorySelector,
    canonical_json: str,
    *,
    store_root: Path,
) -> ResolvedMemory:
    _validate_selector_shape(selector)
    try:
        criteria = json.loads(canonical_json)
    except json.JSONDecodeError as exc:
        raise ValueError("memory canonical JSON is invalid") from exc
    if not isinstance(criteria, dict):
        raise ValueError("memory canonical JSON must encode an object")
    normalized = json.dumps(
        criteria,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if normalized != canonical_json:
        raise ValueError("memory canonical JSON is not canonical")
    if criteria.get("version") != 1 or criteria.get("scope") != selector.scope:
        raise ValueError("memory canonical criteria do not match selector")
    expected_keys = {
        "run": {"version", "scope", "job"},
        "routine": {"version", "scope", "group", "agent", "routine"},
        "agent": {"version", "scope", "group", "agent"},
        "group": {"version", "scope", "group"},
        "channel": {"version", "scope", "channel"},
    }[selector.scope]
    if set(criteria) != expected_keys:
        raise ValueError("memory canonical criteria keys are invalid")
    if selector.scope == "channel" and criteria["channel"] != selector.channel:
        raise ValueError("memory canonical channel does not match selector")
    memory_hash = hashlib.sha256(
        _MEMORY_HASH_DOMAIN + canonical_json.encode("utf-8")
    ).hexdigest()
    root = Path(store_root).expanduser().resolve()
    return ResolvedMemory(
        selector=selector,
        canonical_json=canonical_json,
        memory_hash=memory_hash,
        directory=root / memory_hash,
    )


def _validate_selector_shape(selector: MemorySelector) -> None:
    if selector.scope != "channel" and selector.channel is not None:
        raise ValueError("channel field is only valid for channel scope")


def select_effective_memory(
    manual_override: MemorySelector | None,
    routine_selector: MemorySelector | None,
    agent_default: MemorySelector | None,
) -> MemorySelector:
    return (
        manual_override
        or routine_selector
        or agent_default
        or MemorySelector(scope="run")
    )


def resolve_memory_selector(
    selector: MemorySelector,
    *,
    job_id: str,
    group_key: str,
    agent_name: str,
    routine_id: str | None,
    channels: Mapping[str, MemoryChannel],
    store_root: Path,
) -> ResolvedMemory:
    _validate_selector_shape(selector)
    criteria: dict[str, object] = {"version": 1, "scope": selector.scope}
    if selector.scope == "run":
        criteria["job"] = job_id
    elif selector.scope == "routine":
        if routine_id is None:
            raise ValueError("routine memory requires a routine ID")
        criteria.update(group=group_key, agent=agent_name, routine=routine_id)
    elif selector.scope == "agent":
        criteria.update(group=group_key, agent=agent_name)
    elif selector.scope == "group":
        criteria["group"] = group_key
    elif selector.scope == "channel":
        if selector.channel not in channels:
            raise ValueError(
                f"unknown global memory channel: {selector.channel}"
            )
        criteria["channel"] = selector.channel

    canonical_json = json.dumps(
        criteria,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    memory_hash = hashlib.sha256(
        _MEMORY_HASH_DOMAIN + canonical_json.encode("utf-8")
    ).hexdigest()
    root = Path(store_root).expanduser().resolve()
    return ResolvedMemory(
        selector=selector,
        canonical_json=canonical_json,
        memory_hash=memory_hash,
        directory=root / memory_hash,
    )
