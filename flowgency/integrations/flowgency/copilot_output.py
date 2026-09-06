from collections.abc import Iterator
import json


def object_fields(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def argument_object(value: object) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return object_fields(value)


def event_objects(raw: str) -> Iterator[dict]:
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            yield value


def assistant_message(event: dict) -> str | None:
    if event.get("type") != "assistant.message":
        return None
    content = object_fields(event.get("data")).get("content")
    return content if isinstance(content, str) and content else None


def line_count(value: object) -> int:
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (ValueError, OverflowError):
        return 0
