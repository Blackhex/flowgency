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


class _QueueInput(io.RawIOBase):
    def __init__(self, payload: bytes = b"") -> None:
        self._buffer = bytearray(payload)
        self._condition = threading.Condition()
        self._closed = False

    def readable(self) -> bool:
        return True

    def feed(self, payload: bytes) -> None:
        with self._condition:
            self._buffer.extend(payload)
            self._condition.notify_all()

    def close_input(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def bytes_available(self) -> int:
        with self._condition:
            return len(self._buffer)

    def input_closed(self) -> bool:
        with self._condition:
            return self._closed and not self._buffer

    def read(self, size: int = -1) -> bytes:
        with self._condition:
            while not self._buffer and not self._closed:
                self._condition.wait(timeout=5)
            if not self._buffer:
                return b""
            if size < 0 or size > len(self._buffer):
                size = len(self._buffer)
            chunk = bytes(self._buffer[:size])
            del self._buffer[:size]
            return chunk


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
    read_error: Exception | None = None
    cancel_error: Exception | None = None
    block_reads = False
    read_error_ends_process = False
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
        self._cancel_event = threading.Event()
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
        if type(self).read_error is not None:
            error = type(self).read_error
            type(self).read_error = None
            if type(self).read_error_ends_process:
                self._alive = False
            raise error
        if type(self).block_reads and not self._cancelled and not self._chunks:
            self._cancel_event.wait(timeout=5)
        if self._cancelled:
            self._alive = False
            if type(self).cancel_error is not None:
                error = type(self).cancel_error
                type(self).cancel_error = None
                raise error
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
        self._cancel_event.set()
        return True


def _install_fake_winpty(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakePTY.instances = []
    _FakePTY.output_chunks = []
    _FakePTY.spawn_result = True
    _FakePTY.spawn_error = None
    _FakePTY.read_error = None
    _FakePTY.cancel_error = None
    _FakePTY.block_reads = False
    _FakePTY.read_error_ends_process = False
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


def test_main_rejects_unsupported_control_frame_after_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_winpty(monkeypatch)
    _FakePTY.block_reads = True
    launch = RuntimeLaunch(("C:/Program Files/App/app.exe",), tmp_path, {}, "connected")
    stdout = io.BytesIO()

    assert main(
        _QueueInput(
            _frame_bytes(
                [
                    (FrameType.START, encode_start(launch, 24, 80)),
                    (FrameType.READY, b"{}"),
                ]
            )
        ),
        stdout,
    ) == 0

    frames = _read_all_frames(stdout.getvalue())
    assert frames[0] == (FrameType.READY, b'{"pid":43210}')
    assert frames[1][0] is FrameType.ERROR
    assert json.loads(frames[1][1])["message"] == "Unsupported PTY control frame: READY"
    assert frames[2] == (FrameType.EXIT, b'{"exit_code":23}')


def test_main_returns_after_output_exit_while_control_pipe_stays_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_winpty(monkeypatch)
    _FakePTY.output_chunks = [""]
    launch = RuntimeLaunch(("C:/Program Files/App/app.exe",), tmp_path, {}, "connected")
    stdin = _QueueInput(_frame_bytes([(FrameType.START, encode_start(launch, 24, 80))]))
    stdout = io.BytesIO()
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(main(stdin, stdout)), daemon=False)

    thread.start()
    thread.join(timeout=0.5)
    stdin.close_input()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert result == [0]
    frames = _read_all_frames(stdout.getvalue())
    assert frames == [
        (FrameType.READY, b'{"pid":43210}'),
        (FrameType.EXIT, b'{"exit_code":23}'),
    ]


def test_main_suppresses_cancelled_output_read_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_winpty(monkeypatch)
    _FakePTY.block_reads = True
    _FakePTY.cancel_error = RuntimeError("cancelled by teardown")
    launch = RuntimeLaunch(("C:/Program Files/App/app.exe",), tmp_path, {}, "connected")
    stdin = _QueueInput(_frame_bytes([(FrameType.START, encode_start(launch, 24, 80))]))
    stdout = io.BytesIO()
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(main(stdin, stdout)), daemon=False)

    thread.start()
    stdin.close_input()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert result == [0]
    frames = _read_all_frames(stdout.getvalue())
    assert frames == [
        (FrameType.READY, b'{"pid":43210}'),
        (FrameType.EXIT, b'{"exit_code":23}'),
    ]


def test_main_suppresses_eof_output_read_errors_on_child_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_winpty(monkeypatch)
    _FakePTY.read_error = EOFError("end of file")
    _FakePTY.read_error_ends_process = True
    launch = RuntimeLaunch(("C:/Program Files/App/app.exe",), tmp_path, {}, "connected")
    stdout = io.BytesIO()

    assert main(io.BytesIO(_frame_bytes([(FrameType.START, encode_start(launch, 24, 80))])), stdout) == 0

    frames = _read_all_frames(stdout.getvalue())
    assert frames == [
        (FrameType.READY, b'{"pid":43210}'),
        (FrameType.EXIT, b'{"exit_code":23}'),
    ]


def test_main_reports_real_output_read_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_winpty(monkeypatch)
    _FakePTY.read_error = RuntimeError("read exploded")
    launch = RuntimeLaunch(("C:/Program Files/App/app.exe",), tmp_path, {}, "connected")
    stdout = io.BytesIO()

    assert main(io.BytesIO(_frame_bytes([(FrameType.START, encode_start(launch, 24, 80))])), stdout) == 0

    frames = _read_all_frames(stdout.getvalue())
    assert frames[0] == (FrameType.READY, b'{"pid":43210}')
    assert frames[1][0] is FrameType.ERROR
    assert json.loads(frames[1][1])["message"] == "PTY output failed"