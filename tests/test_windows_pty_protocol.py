from __future__ import annotations

import io
import json
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

# Sentinels stand in for a real absolute cwd; each test substitutes tmp_path
# before serialization/validation so cases stay portable across platforms.
_CWD_SENTINEL = "<sentinel-cwd>"
_CWD_PLACEHOLDER = Path("sentinel-cwd")


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
    ("template", "match"),
    [
        pytest.param(
            {"argv": "copilot", "cwd": _CWD_SENTINEL, "env": {}, "mode": "connected", "rows": 24, "cols": 80},
            "argv",
            id="argv-not-a-list",
        ),
        pytest.param(
            {"argv": ["copilot"], "cwd": "relative", "env": {}, "mode": "connected", "rows": 24, "cols": 80},
            "cwd",
            id="cwd-relative",
        ),
        pytest.param(
            {"argv": ["copilot"], "cwd": _CWD_SENTINEL, "env": {"SAFE": 1}, "mode": "connected", "rows": 24, "cols": 80},
            "environment",
            id="env-value-not-a-string",
        ),
        pytest.param(
            {"argv": ["copilot"], "cwd": _CWD_SENTINEL, "env": {}, "mode": "connected", "rows": 1, "cols": 80},
            "rows",
            id="rows-too-low",
        ),
        pytest.param(
            {"argv": ["copilot"], "cwd": _CWD_SENTINEL, "env": {}, "mode": "connected", "rows": 24, "cols": 19},
            "cols",
            id="cols-too-low",
        ),
    ],
)
def test_decode_start_rejects_invalid_field_values(template: dict, match: str, tmp_path: Path):
    body = dict(template)
    if body["cwd"] == _CWD_SENTINEL:
        body["cwd"] = str(tmp_path)
    payload = json.dumps(body).encode("utf-8")
    with pytest.raises(ValueError, match=match):
        decode_start(payload)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        pytest.param(b"not-json", "JSON", id="invalid-json"),
        pytest.param(b"[]", "object", id="top-level-not-object"),
    ],
)
def test_decode_start_rejects_malformed_json_and_shape(payload: bytes, match: str):
    with pytest.raises(ValueError, match=match):
        decode_start(payload)


@pytest.mark.parametrize(
    "launch, rows, cols, match",
    [
        (RuntimeLaunch(("",), _CWD_PLACEHOLDER, {}, "connected"), 24, 80, "argv"),
        (RuntimeLaunch(("copilot",), Path("relative"), {}, "connected"), 24, 80, "cwd"),
        (RuntimeLaunch(("copilot",), _CWD_PLACEHOLDER, {"SAFE": 1}, "connected"), 24, 80, "environment"),
        (RuntimeLaunch(("copilot",), _CWD_PLACEHOLDER, {"SAFE": "ok", "NUL": "bad\x00value"}, "connected"), 24, 80, "NUL"),
        (RuntimeLaunch(("copilot",), _CWD_PLACEHOLDER, {"SAFE": "ok"}, "headless"), 24, 80, "connected"),
        (RuntimeLaunch(("copilot",), _CWD_PLACEHOLDER, {"SAFE": "ok"}, "connected"), 1, 80, "rows"),
        (RuntimeLaunch(("copilot",), _CWD_PLACEHOLDER, {"SAFE": "ok"}, "connected"), 24, 19, "cols"),
    ],
)
def test_encode_start_rejects_invalid_launch_and_dimensions(launch, rows, cols, match, tmp_path: Path):
    if launch.cwd == _CWD_PLACEHOLDER:
        launch = RuntimeLaunch(launch.argv, tmp_path, launch.env, launch.mode)

    with pytest.raises(ValueError, match=match):
        encode_start(launch, rows, cols)