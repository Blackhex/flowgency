from __future__ import annotations

import contextlib
import importlib
import json
import os
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import BinaryIO

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.connected_process import ConnectedLaunchError, _OwnedTerminal
from flowgency.jobs.windows_job import WindowsJobLaunchError, WindowsJobOwner
from flowgency.jobs.windows_pty_protocol import FrameType, MAX_DATA_BYTES, MAX_START_BYTES, encode_start, write_frame


_FRAME_HEADER = struct.Struct("!BI")
_HELPER_START_TIMEOUT_SECONDS = 10.0
_OUTPUT_BUFFER_LIMIT = MAX_DATA_BYTES * 16
_READ_POLL_SECONDS = 0.01
_READER_JOIN_TIMEOUT_SECONDS = 1.0


def _has_attr(module: object, name: str) -> bool:
    return getattr(module, name, None) is not None


def _websocket_protocol_available() -> bool:
    try:
        module = importlib.import_module("uvicorn.protocols.websockets.auto")
    except ImportError:
        return False
    return _has_attr(module, "AutoWebSocketsProtocol")


def windows_connected_process_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import pywintypes  # noqa: F401
        import win32api  # noqa: F401
        import win32con  # noqa: F401
        import win32event  # noqa: F401
        import win32job  # noqa: F401
        import win32pipe  # noqa: F401
        import win32process  # noqa: F401
        import win32security  # noqa: F401
        import winpty
    except ImportError:
        return False
    if getattr(getattr(winpty, "Backend", None), "ConPTY", None) is None:
        return False
    if not _websocket_protocol_available():
        return False
    required = (
        (win32job, "AssignProcessToJobObject"),
        (win32job, "IsProcessInJob"),
        (win32job, "QueryInformationJobObject"),
        (win32job, "TerminateJobObject"),
        (win32pipe, "CreatePipe"),
        (win32pipe, "PeekNamedPipe"),
        (win32process, "CreateProcess"),
        (win32process, "ResumeThread"),
    )
    return all(_has_attr(module, name) for module, name in required)


def _helper_launch() -> tuple[tuple[str, ...], dict[str, str]]:
    import win32api
    import win32process

    env = os.environ.copy()
    env.setdefault("__PYVENV_LAUNCHER__", sys.executable)
    native_python = win32process.GetModuleFileNameEx(win32api.GetCurrentProcess(), 0)
    return (native_python, "-u", "-m", "flowgency.jobs.windows_pty_helper"), env


def _close_handle(handle) -> None:
    import win32api

    with contextlib.suppress(Exception):
        win32api.CloseHandle(handle)


def _decode_frame_header(buffer: bytearray) -> tuple[FrameType, int] | None:
    if len(buffer) < _FRAME_HEADER.size:
        return None
    kind_value, size = _FRAME_HEADER.unpack(bytes(buffer[: _FRAME_HEADER.size]))
    try:
        frame_type = FrameType(kind_value)
    except ValueError as error:
        raise ValueError(f"Unknown frame type: {kind_value}") from error
    limit = MAX_START_BYTES if frame_type is FrameType.START else MAX_DATA_BYTES
    if size > limit:
        raise ValueError("PTY frame is too large")
    return frame_type, size


def _pop_buffered_frame(buffer: bytearray) -> tuple[FrameType, bytes] | None:
    header = _decode_frame_header(buffer)
    if header is None:
        return None
    frame_type, size = header
    total = _FRAME_HEADER.size + size
    if len(buffer) < total:
        return None
    payload = bytes(buffer[_FRAME_HEADER.size:total])
    del buffer[:total]
    return frame_type, payload


class _PipeFrameReader:
    def __init__(self, stream: BinaryIO) -> None:
        import msvcrt

        self._stream = stream
        self._buffer = bytearray()
        self._handle = int(msvcrt.get_osfhandle(stream.fileno()))

    def read(self, *, deadline: float | None = None) -> tuple[FrameType, bytes] | None:
        import win32pipe

        while True:
            frame = _pop_buffered_frame(self._buffer)
            if frame is not None:
                return frame
            available, eof = self._probe(win32pipe)
            if available > 0:
                chunk = self._read_chunk(available)
                if chunk:
                    self._buffer.extend(chunk)
                    continue
                eof = True
            if eof:
                if self._buffer:
                    raise ValueError("Truncated PTY frame")
                return None
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for PTY helper response")
            time.sleep(_READ_POLL_SECONDS)

    def _probe(self, win32pipe) -> tuple[int, bool]:
        try:
            _data, available, _remaining = win32pipe.PeekNamedPipe(self._handle, 0)
        except Exception as error:
            if getattr(error, "winerror", None) in {109, 232, 233}:
                return 0, True
            raise
        return int(available), False

    def _read_chunk(self, available: int) -> bytes:
        header = _decode_frame_header(self._buffer)
        if header is None:
            wanted = _FRAME_HEADER.size - len(self._buffer)
        else:
            _frame_type, size = header
            wanted = (_FRAME_HEADER.size + size) - len(self._buffer)
        reader = getattr(self._stream, "read1", None)
        if callable(reader):
            return bytes(reader(min(available, wanted)))
        return bytes(self._stream.read(min(available, wanted)))


class WindowsConnectedProcess(_OwnedTerminal):
    _reads_cancellable = True

    def __init__(
        self,
        owner: WindowsJobOwner,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
    ) -> None:
        super().__init__(owner.pid)
        self._owner = owner
        self._input_stream = input_stream
        self._output_stream = output_stream
        self._frame_reader = _PipeFrameReader(output_stream)
        self._write_lock = threading.Lock()
        self._output_lock = threading.Condition()
        self._output_chunks: deque[bytes] = deque()
        self._buffered_output = 0
        self._child_pid: int | None = None
        self._child_exit_code: int | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_active = False
        self._stream_ended = False
        self._reader_error: str | None = None

    @classmethod
    def spawn(cls, launch: RuntimeLaunch, *, rows: int, cols: int) -> "WindowsConnectedProcess":
        import msvcrt
        import win32api
        import win32con
        import win32pipe
        import win32security

        helper_argv, helper_env = _helper_launch()
        security = win32security.SECURITY_ATTRIBUTES()
        security.bInheritHandle = 1
        child_stdin = parent_stdin = parent_stdout = child_stdout = None
        owner = None
        input_stream = output_stream = None
        process = None
        try:
            child_stdin, parent_stdin = win32pipe.CreatePipe(security, 0)
            parent_stdout, child_stdout = win32pipe.CreatePipe(security, 0)
            win32api.SetHandleInformation(parent_stdin, win32con.HANDLE_FLAG_INHERIT, 0)
            win32api.SetHandleInformation(parent_stdout, win32con.HANDLE_FLAG_INHERIT, 0)
            owner = WindowsJobOwner.launch(helper_argv, launch.cwd, helper_env, int(child_stdin), int(child_stdout))
            _close_handle(child_stdin)
            child_stdin = None
            _close_handle(child_stdout)
            child_stdout = None
            input_stream = os.fdopen(
                msvcrt.open_osfhandle(parent_stdin.Detach(), os.O_WRONLY | os.O_BINARY),
                "wb",
                buffering=0,
            )
            parent_stdin = None
            output_stream = os.fdopen(
                msvcrt.open_osfhandle(parent_stdout.Detach(), os.O_RDONLY | os.O_BINARY),
                "rb",
                buffering=0,
            )
            parent_stdout = None
            process = cls(owner, input_stream, output_stream)
            process._start_helper(launch, rows=rows, cols=cols)
            return process
        except ConnectedLaunchError:
            raise
        except WindowsJobLaunchError as error:
            raise ConnectedLaunchError(str(error), cleanup_confirmed=False) from error
        except Exception as error:
            if process is not None:
                process._startup_failure(f"Could not start {launch.argv[0]} in a PTY: {error}", cause=error)
            if output_stream is not None:
                output_stream.close()
            if input_stream is not None:
                input_stream.close()
            if owner is not None:
                confirmed, _reason = owner.stop(time.monotonic() + 5.0)
                if confirmed:
                    owner.close_confirmed()
                raise ConnectedLaunchError(
                    f"Could not start {launch.argv[0]} in a PTY: {error}",
                    cleanup_confirmed=confirmed,
                ) from error
            raise ConnectedLaunchError(
                f"Could not start {launch.argv[0]} in a PTY: {error}",
                cleanup_confirmed=True,
            ) from error
        finally:
            if child_stdin is not None:
                _close_handle(child_stdin)
            if child_stdout is not None:
                _close_handle(child_stdout)
            if parent_stdin is not None:
                _close_handle(parent_stdin)
            if parent_stdout is not None:
                _close_handle(parent_stdout)

    def _start_helper(self, launch: RuntimeLaunch, *, rows: int, cols: int) -> None:
        deadline = time.monotonic() + _HELPER_START_TIMEOUT_SECONDS
        try:
            with self._write_lock:
                write_frame(self._input_stream, FrameType.START, encode_start(launch, rows, cols))
            frame = self._frame_reader.read(deadline=deadline)
            if frame is None:
                self._startup_failure("PTY helper exited before reporting READY")
            kind, payload = frame
            if kind is FrameType.ERROR:
                self._startup_failure(self._decode_error_payload(payload))
            if kind is not FrameType.READY:
                self._startup_failure(f"PTY helper reported {kind.name} before READY")
            child_pid = self._decode_ready_payload(payload)
            if not self._owner.contains(child_pid):
                self._startup_failure(
                    f"PTY helper reported an uncontained child pid: {child_pid}"
                )
            self._child_pid = child_pid
            self._reader_thread = threading.Thread(target=self._read_frames, name="windows-connected-pty", daemon=False)
            self._reader_thread.start()
        except TimeoutError as error:
            self._startup_failure(str(error), cause=error)
        except ValueError as error:
            self._startup_failure(f"PTY helper protocol failed: {error}", cause=error)
        except OSError as error:
            self._startup_failure(f"PTY helper I/O failed: {error}", cause=error)

    def _startup_failure(self, message: str, *, cause: Exception | None = None) -> None:
        confirmed, _reason = self._stop_tree()
        error = ConnectedLaunchError(message, cleanup_confirmed=confirmed)
        if cause is not None:
            raise error from cause
        raise error

    def alive(self) -> bool:
        if self._stopped:
            return False
        return self._owner.alive()

    def exit_code(self) -> int | None:
        if self.alive():
            return None
        return self._child_exit_code

    def _read_terminal(self, size: int) -> bytes:
        del size
        while True:
            with self._output_lock:
                if self._output_chunks:
                    chunk = self._output_chunks.popleft()
                    self._buffered_output -= len(chunk)
                    return chunk
                if self._stream_ended:
                    return b""
            with self._io_lock:
                if self._io_closed:
                    return b""
            time.sleep(_READ_POLL_SECONDS)

    def _write_terminal(self, data: bytes) -> None:
        try:
            with self._write_lock:
                write_frame(self._input_stream, FrameType.INPUT, data)
        except OSError as error:
            raise BrokenPipeError(str(error)) from error

    def _resize_terminal(self, rows: int, cols: int) -> None:
        payload = json.dumps({"rows": rows, "cols": cols}, separators=(",", ":")).encode("utf-8")
        try:
            with self._write_lock:
                write_frame(self._input_stream, FrameType.RESIZE, payload)
        except OSError as error:
            raise BrokenPipeError(str(error)) from error

    def _terminate_tree(self, deadline: float) -> tuple[bool, str]:
        return self._owner.stop(deadline)

    def _cancel_reads(self) -> None:
        with self._output_lock:
            self._stream_ended = True

    def _stop_outcome(
        self,
        confirmed: bool,
        reason: str,
        drained: bool,
        released: bool,
    ) -> tuple[bool, str]:
        if not confirmed:
            return False, reason
        if not drained:
            return False, "reader-undrained"
        if not released:
            return False, "reader-unreleased"
        return True, reason

    def _release(self, drained: bool) -> bool:
        if not drained:
            return False
        reader = self._reader_thread
        if reader is not None:
            reader.join(timeout=_READER_JOIN_TIMEOUT_SECONDS)
            if reader.is_alive():
                return False
        with contextlib.suppress(Exception):
            self._input_stream.close()
        with contextlib.suppress(Exception):
            self._output_stream.close()
        self._owner.close_confirmed()
        return True

    def _read_frames(self) -> None:
        try:
            while True:
                with self._io_lock:
                    self._reader_active = True
                try:
                    frame = self._frame_reader.read()
                finally:
                    with self._io_lock:
                        self._reader_active = False
                if frame is None:
                    return
                kind, payload = frame
                if kind is FrameType.OUTPUT:
                    self._append_output(payload)
                    continue
                if kind is FrameType.EXIT:
                    self._child_exit_code = self._decode_exit_payload(payload)
                    continue
                if kind is FrameType.ERROR:
                    self._reader_error = self._decode_error_payload(payload)
                    return
                self._reader_error = f"Unsupported PTY helper frame: {kind.name}"
                return
        except Exception as error:
            self._reader_error = str(error)
        finally:
            with self._output_lock:
                self._stream_ended = True

    def _release_reader(self, deadline: float) -> bool:
        while True:
            self._cancel_reads()
            with self._io_lock:
                if self._ops_in_flight == 0 and not self._reader_active:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def _append_output(self, payload: bytes) -> None:
        if not payload:
            return
        with self._output_lock:
            if len(payload) >= _OUTPUT_BUFFER_LIMIT:
                self._output_chunks.clear()
                self._buffered_output = 0
                payload = payload[-_OUTPUT_BUFFER_LIMIT:]
            while self._buffered_output + len(payload) > _OUTPUT_BUFFER_LIMIT and self._output_chunks:
                dropped = self._output_chunks.popleft()
                self._buffered_output -= len(dropped)
            self._output_chunks.append(payload)
            self._buffered_output += len(payload)

    @staticmethod
    def _decode_ready_payload(payload: bytes) -> int:
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("READY payload is not valid JSON") from error
        pid = decoded.get("pid") if isinstance(decoded, dict) else None
        if type(pid) is not int or pid <= 0:
            raise ValueError("READY payload must contain a positive pid")
        return pid

    @staticmethod
    def _decode_exit_payload(payload: bytes) -> int | None:
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("EXIT payload is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError("EXIT payload must be a JSON object")
        exit_code = decoded.get("exit_code")
        if exit_code is None:
            return None
        if type(exit_code) is not int:
            raise ValueError("EXIT payload exit_code must be an integer")
        return exit_code

    @staticmethod
    def _decode_error_payload(payload: bytes) -> str:
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return payload.decode("utf-8", errors="replace") or "PTY helper reported an error"
        if isinstance(decoded, dict) and type(decoded.get("message")) is str and decoded["message"]:
            return decoded["message"]
        return "PTY helper reported an error"