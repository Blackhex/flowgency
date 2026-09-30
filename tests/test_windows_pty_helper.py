from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.windows_pty_helper import main
from flowgency.jobs.windows_pty_protocol import FrameType, encode_start, read_frame, write_frame


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PTY helper is Windows-specific")


class _BlockingInput(io.RawIOBase):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self._blocked = threading.Event()

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            return b""
        if self._offset < len(self._payload):
            if size < 0:
                size = len(self._payload) - self._offset
            end = min(len(self._payload), self._offset + size)
            chunk = self._payload[self._offset:end]
            self._offset = end
            return chunk
        self._blocked.wait(timeout=5)
        return b""


def _frame_bytes(frames: list[tuple[FrameType, bytes]]) -> bytes:
    data = io.BytesIO()
    for kind, payload in frames:
        write_frame(data, kind, payload)
    return data.getvalue()


def _read_all_frames(data: bytes) -> list[tuple[FrameType, bytes]]:
    frames: list[tuple[FrameType, bytes]] = []
    stream = io.BytesIO(data)
    while True:
        frame = read_frame(stream)
        if frame is None:
            return frames
        frames.append(frame)


class _FakePTY:
    output_chunks: list[str] = []
    spawn_result = True
    spawn_error: Exception | None = None
    exit_status = 23
    instances: list["_FakePTY"] = []

    def __init__(self, cols: int, rows: int, backend=None) -> None:
        self.cols = cols
        self.rows = rows
        self.backend = backend
        self.spawn_call: dict[str, str] | None = None
        self.writes: list[str] = []
        self.sizes: list[tuple[int, int]] = []
        self.pid = 43210
        self._alive = True
        self._cancelled = False
        self._chunks = list(type(self).output_chunks)
        type(self).instances.append(self)

    def spawn(
        self,
        appname: str,
        cmdline: str | None = None,
        cwd: str | None = None,
        env: str | None = None,
    ) -> bool:
        self.spawn_call = {"appname": appname, "cmdline": cmdline, "cwd": cwd, "env": env}
        if type(self).spawn_error is not None:
            raise type(self).spawn_error
        return type(self).spawn_result

    def set_size(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))

    def read(self, blocking: bool = False) -> str:
        if self._cancelled:
            self._alive = False
            return ""
        if self._chunks:
            chunk = self._chunks.pop(0)
            if chunk == "":
                self._alive = False
                return ""
            return chunk
        self._alive = False
        return ""

    def write(self, to_write: str) -> int:
        self.writes.append(to_write)
        return len(to_write)

    def isalive(self) -> bool:
        return self._alive

    def get_exitstatus(self) -> int:
        return type(self).exit_status

    def iseof(self) -> bool:
        return not self._alive

    def cancel_io(self) -> bool:
        self._cancelled = True
        return True


def _install_fake_winpty(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakePTY.instances = []
    _FakePTY.output_chunks = []
    _FakePTY.spawn_result = True
    _FakePTY.spawn_error = None
    fake_winpty = types.SimpleNamespace(
        PTY=_FakePTY,
        Backend=types.SimpleNamespace(ConPTY=777),
    )
    monkeypatch.setitem(sys.modules, "winpty", fake_winpty)


def test_helper_module_imports() -> None:
    assert callable(main)


def test_main_spawns_conpty_and_relays_output_input_and_resize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_winpty(monkeypatch)
    _FakePTY.output_chunks = ["hello bytes", ""]
    launch = RuntimeLaunch(("C:/Program Files/App/app.exe", "arg one"), tmp_path, {"SAFE": "ž"}, "connected")
    stdin = _BlockingInput(
        _frame_bytes(
            [
                (FrameType.START, encode_start(launch, 24, 80)),
                (FrameType.INPUT, b"yes\r"),
                (FrameType.RESIZE, json.dumps({"rows": 30, "cols": 100}).encode("utf-8")),
            ]
        )
    )
    stdout = io.BytesIO()

    assert main(stdin, stdout) == 0

    fake = _FakePTY.instances[-1]
    assert fake.backend == 777
    assert fake.spawn_call == {
        "appname": "C:/Program Files/App/app.exe",
        "cmdline": subprocess.list2cmdline(["arg one"]),
        "cwd": str(tmp_path),
        "env": "SAFE=ž\0",
    }
    assert fake.writes == ["yes\r"]
    assert fake.sizes == [(100, 30)]

    frames = _read_all_frames(stdout.getvalue())
    assert frames[0] == (FrameType.READY, b'{"pid":43210}')
    assert frames[1] == (FrameType.OUTPUT, b"hello bytes")
    assert frames[2] == (FrameType.EXIT, b'{"exit_code":23}')


def test_main_decodes_incremental_utf8_input(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_winpty(monkeypatch)
    _FakePTY.output_chunks = [""]
    launch = RuntimeLaunch(("C:/Program Files/App/app.exe",), tmp_path, {}, "connected")
    stdin = _BlockingInput(
        _frame_bytes(
            [
                (FrameType.START, encode_start(launch, 24, 80)),
                (FrameType.INPUT, b"\xc5"),
                (FrameType.INPUT, b"\xbc\r"),
            ]
        )
    )

    assert main(stdin, io.BytesIO()) == 0
    assert _FakePTY.instances[-1].writes == ["ż\r"]


def test_main_rejects_non_start_initial_frame() -> None:
    stdout = io.BytesIO()
    stdin = io.BytesIO(_frame_bytes([(FrameType.INPUT, b"nope")]))

    assert main(stdin, stdout) == 1

    frames = _read_all_frames(stdout.getvalue())
    assert len(frames) == 1
    assert frames[0][0] is FrameType.ERROR
    assert "Expected START frame" in json.loads(frames[0][1])["message"]


def test_main_reports_spawn_failure_without_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_winpty(monkeypatch)
    _FakePTY.spawn_error = RuntimeError("boom")
    launch = RuntimeLaunch(("C:/Program Files/App/app.exe",), tmp_path, {}, "connected")
    stdout = io.BytesIO()

    assert main(io.BytesIO(_frame_bytes([(FrameType.START, encode_start(launch, 24, 80))])), stdout) == 1

    frames = _read_all_frames(stdout.getvalue())
    assert len(frames) == 1
    assert frames[0][0] is FrameType.ERROR
    assert "startup failed" in json.loads(frames[0][1])["message"]