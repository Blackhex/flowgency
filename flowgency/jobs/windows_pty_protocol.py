from __future__ import annotations

import json
import struct
from enum import IntEnum
from pathlib import Path
from typing import BinaryIO, Mapping

from flowgency.integrations.models import RuntimeLaunch


class FrameType(IntEnum):
    START = 1
    INPUT = 2
    RESIZE = 3
    READY = 4
    OUTPUT = 5
    EXIT = 6
    ERROR = 7


MAX_START_BYTES = 1 << 20
MAX_DATA_BYTES = 64 * 1024

_HEADER = struct.Struct("!BI")
_START_KEYS = ("argv", "cwd", "env", "mode", "rows", "cols")


def write_frame(stream: BinaryIO, kind: FrameType, payload: bytes) -> None:
    frame_type = _coerce_frame_type(kind)
    body = bytes(payload)
    limit = MAX_START_BYTES if frame_type is FrameType.START else MAX_DATA_BYTES
    if len(body) > limit:
        raise ValueError("PTY frame is too large")
    stream.write(_HEADER.pack(frame_type.value, len(body)) + body)
    stream.flush()


def read_frame(stream: BinaryIO) -> tuple[FrameType, bytes] | None:
    header = _read_exact(stream, _HEADER.size, allow_eof=True)
    if header is None:
        return None
    kind_value, size = _HEADER.unpack(header)
    try:
        frame_type = FrameType(kind_value)
    except ValueError as error:
        raise ValueError(f"Unknown frame type: {kind_value}") from error
    limit = MAX_START_BYTES if frame_type is FrameType.START else MAX_DATA_BYTES
    if size > limit:
        raise ValueError("PTY frame is too large")
    payload = _read_exact(stream, size)
    return frame_type, payload


def encode_start(launch: RuntimeLaunch, rows: int, cols: int) -> bytes:
    _validate_dimensions(rows, cols)
    argv = _validate_argv(launch.argv)
    cwd = _validate_cwd(launch.cwd)
    env = _validate_env(launch.env)
    if launch.mode != "connected":
        raise ValueError("START launch mode must be connected")
    payload = {
        "argv": argv,
        "cwd": str(cwd),
        "env": env,
        "mode": launch.mode,
        "rows": rows,
        "cols": cols,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_start(payload: bytes) -> tuple[RuntimeLaunch, int, int]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("START payload is not valid JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError("START payload must be a JSON object")
    if set(decoded) != set(_START_KEYS):
        raise ValueError("START payload has unexpected fields")

    argv = _validate_argv(decoded["argv"])
    cwd = _validate_cwd(decoded["cwd"])
    env = _validate_env(decoded["env"])
    mode = decoded["mode"]
    rows, cols = _validate_dimensions(decoded["rows"], decoded["cols"])
    if mode != "connected":
        raise ValueError("START launch mode must be connected")
    return RuntimeLaunch(argv, cwd, env, mode), rows, cols


def _coerce_frame_type(kind: FrameType | int) -> FrameType:
    if isinstance(kind, FrameType):
        return kind
    try:
        return FrameType(int(kind))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Unknown frame type: {kind!r}") from error


def _read_exact(stream: BinaryIO, size: int, *, allow_eof: bool = False) -> bytes | None:
    remaining = size
    chunks: list[bytes] = []
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            if allow_eof and not chunks:
                return None
            raise ValueError("Truncated PTY frame")
        chunk_bytes = bytes(chunk)
        if len(chunk_bytes) > remaining:
            chunk_bytes = chunk_bytes[:remaining]
        chunks.append(chunk_bytes)
        remaining -= len(chunk_bytes)
    return b"".join(chunks)


def _validate_dimensions(rows: int, cols: int) -> tuple[int, int]:
    rows_value = _validate_dimension(rows, "rows", 2, 200)
    cols_value = _validate_dimension(cols, "cols", 20, 400)
    return rows_value, cols_value


def _validate_dimension(value: int, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"START {name} must be between {minimum} and {maximum}")
    return value


def _validate_argv(argv: object) -> tuple[str, ...]:
    if not isinstance(argv, (list, tuple)):
        raise ValueError("START argv must be a non-empty sequence of strings")
    if not argv:
        raise ValueError("START argv must be a non-empty sequence of strings")
    values: list[str] = []
    for item in argv:
        if type(item) is not str or not item or "\0" in item:
            raise ValueError("START argv values must be non-empty strings without NUL bytes")
        values.append(item)
    return tuple(values)


def _validate_cwd(cwd: object) -> Path:
    if isinstance(cwd, Path):
        path = cwd
    elif type(cwd) is str and cwd:
        path = Path(cwd)
    else:
        raise ValueError("START cwd must be an absolute path string")
    if not path.is_absolute():
        raise ValueError("START cwd must be an absolute path string")
    return path.resolve(strict=False)


def _validate_env(env: object) -> dict[str, str]:
    if not isinstance(env, Mapping):
        raise ValueError("START environment must be a string-to-string mapping")
    validated: dict[str, str] = {}
    for key, value in env.items():
        if type(key) is not str or not key or "\0" in key or "=" in key:
            raise ValueError(
                "START environment keys must be non-empty strings without NUL bytes or '='"
            )
        if type(value) is not str or "\0" in value:
            raise ValueError("START environment values must be strings without NUL bytes")
        validated[key] = value
    return validated