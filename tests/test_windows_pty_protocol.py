from __future__ import annotations

import io
from pathlib import Path

import pytest

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.windows_pty_protocol import (
    FrameType,
    decode_start,
    encode_start,
    read_frame,
    write_frame,
)


def test_output_frame_round_trips_terminal_bytes():
    data = io.BytesIO()
    write_frame(data, FrameType.OUTPUT, b"\x1b[31mhello\x00\xff")
    data.seek(0)
    assert read_frame(data) == (FrameType.OUTPUT, b"\x1b[31mhello\x00\xff")
    assert read_frame(data) is None


def test_start_round_trips_launch_and_unicode(tmp_path: Path):
    launch = RuntimeLaunch(("copilot", "-i", "Příprava"), tmp_path, {"SAFE": "ž"}, "connected")
    assert decode_start(encode_start(launch, 24, 80)) == (launch, 24, 80)


@pytest.mark.parametrize("wire", [b"\x01", b"\xff\x00\x00\x00\x00", b"\x05\x00\x01\x00\x01"])
def test_rejects_partial_unknown_or_oversized_frame(wire: bytes):
    with pytest.raises(ValueError):
        read_frame(io.BytesIO(wire))


def test_rejects_truncated_payload():
    data = io.BytesIO()
    write_frame(data, FrameType.INPUT, b"abc")
    truncated = data.getvalue()[:-1]
    with pytest.raises(ValueError):
        read_frame(io.BytesIO(truncated))


def test_rejects_oversized_start_payload(tmp_path: Path):
    launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
    payload = encode_start(launch, 24, 80)
    with pytest.raises(ValueError):
        write_frame(io.BytesIO(), FrameType.START, payload + b"x" * ((1 << 20) - len(payload) + 1))


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"not-json", "JSON"),
        (b"[]", "object"),
        (b'{"argv": "copilot", "cwd": "C:/tmp", "env": {}, "mode": "connected", "rows": 24, "cols": 80}', "argv"),
        (b'{"argv": ["copilot"], "cwd": "relative", "env": {}, "mode": "connected", "rows": 24, "cols": 80}', "cwd"),
        (b'{"argv": ["copilot"], "cwd": "C:/tmp", "env": {"SAFE": 1}, "mode": "connected", "rows": 24, "cols": 80}', "environment"),
        (b'{"argv": ["copilot"], "cwd": "C:/tmp", "env": {}, "mode": "connected", "rows": 1, "cols": 80}', "rows"),
        (b'{"argv": ["copilot"], "cwd": "C:/tmp", "env": {}, "mode": "connected", "rows": 24, "cols": 19}', "cols"),
    ],
)
def test_decode_start_rejects_invalid_json_and_shape(payload: bytes, match: str):
    with pytest.raises(ValueError, match=match):
        decode_start(payload)


@pytest.mark.parametrize(
    "launch, rows, cols, match",
    [
        (RuntimeLaunch(("",), Path("C:/tmp"), {}, "connected"), 24, 80, "argv"),
        (RuntimeLaunch(("copilot",), Path("relative"), {}, "connected"), 24, 80, "cwd"),
        (RuntimeLaunch(("copilot",), Path("C:/tmp"), {"SAFE": 1}, "connected"), 24, 80, "environment"),
        (RuntimeLaunch(("copilot",), Path("C:/tmp"), {"SAFE": "ok", "NUL": "bad\x00value"}, "connected"), 24, 80, "NUL"),
        (RuntimeLaunch(("copilot",), Path("C:/tmp"), {"SAFE": "ok"}, "headless"), 24, 80, "connected"),
        (RuntimeLaunch(("copilot",), Path("C:/tmp"), {"SAFE": "ok"}, "connected"), 1, 80, "rows"),
        (RuntimeLaunch(("copilot",), Path("C:/tmp"), {"SAFE": "ok"}, "connected"), 24, 19, "cols"),
    ],
)
def test_encode_start_rejects_invalid_launch_and_dimensions(launch, rows, cols, match):
    with pytest.raises(ValueError, match=match):
        encode_start(launch, rows, cols)