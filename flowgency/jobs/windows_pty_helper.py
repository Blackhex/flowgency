from __future__ import annotations

import codecs
import io
import json
import subprocess
import sys
import threading
import time
from typing import BinaryIO

from flowgency.jobs.windows_pty_protocol import (
    FrameType,
    MAX_DATA_BYTES,
    MAX_START_BYTES,
    _validate_dimensions,
    decode_start,
    read_frame,
    write_frame,
)


_FRAME_HEADER_BYTES = 5
_CONTROL_POLL_SECONDS = 0.05
_CANCEL_GRACE_SECONDS = 0.2


class _ControlReadCancelled(Exception):
    pass


class _FrameWriter:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, kind: FrameType, payload: bytes) -> None:
        with self._lock:
            write_frame(self._stream, kind, payload)

    def send_json(self, kind: FrameType, payload: object) -> None:
        self.send(
            kind,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )


def _error_payload(message: str) -> bytes:
    return json.dumps({"message": message}, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _environment_block(environment: dict[str, str]) -> str:
    return "\0".join(f"{key}={value}" for key, value in environment.items()) + "\0"


def _decode_resize(payload: bytes) -> tuple[int, int]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("RESIZE payload is not valid JSON") from error
    if not isinstance(decoded, dict) or set(decoded) != {"rows", "cols"}:
        raise ValueError("RESIZE payload must contain rows and cols")
    return _validate_dimensions(decoded["rows"], decoded["cols"])


def _is_expected_output_shutdown_error(error: Exception, *, shutting_down: bool) -> bool:
    if not shutting_down:
        return False
    if isinstance(error, (BrokenPipeError, EOFError)):
        return True
    if type(error).__name__ == "WinptyError" and not str(error):
        return True
    winerror = getattr(error, "winerror", None)
    if winerror in {109, 232, 233, 995}:
        return True
    message = str(error).lower()
    return any(
        token in message
        for token in (
            "cancelled",
            "canceled",
            "operation aborted",
            "broken pipe",
            "end of file",
            "pipe has been ended",
            "teardown",
            "eof",
        )
    )


def _output_error_message(error: Exception, *, shutdown_requested: bool, pty) -> str | None:
    shutting_down = shutdown_requested or not pty.isalive() or pty.iseof()
    if _is_expected_output_shutdown_error(error, shutting_down=shutting_down):
        return None
    return "PTY output failed"


def _frame_limit(frame_type: FrameType) -> int:
    return MAX_START_BYTES if frame_type is FrameType.START else MAX_DATA_BYTES


def _decode_frame_header(buffer: bytearray) -> tuple[FrameType, int] | None:
    if len(buffer) < _FRAME_HEADER_BYTES:
        return None
    kind_value = buffer[0]
    try:
        frame_type = FrameType(kind_value)
    except ValueError as error:
        raise ValueError(f"Unknown frame type: {kind_value}") from error
    size = int.from_bytes(buffer[1:_FRAME_HEADER_BYTES], "big")
    if size > _frame_limit(frame_type):
        raise ValueError("PTY frame is too large")
    return frame_type, size


def _pop_buffered_frame(buffer: bytearray) -> tuple[FrameType, bytes] | None:
    header = _decode_frame_header(buffer)
    if header is None:
        return None
    frame_type, size = header
    total = _FRAME_HEADER_BYTES + size
    if len(buffer) < total:
        return None
    payload = bytes(buffer[_FRAME_HEADER_BYTES:total])
    del buffer[:total]
    return frame_type, payload


class _ControlFrameReader:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._buffer = bytearray()
        self._probe = self._build_probe(stream)

    def read(self, done: threading.Event) -> tuple[FrameType, bytes] | None:
        if self._probe is None:
            return read_frame(self._stream)

        cancel_deadline: float | None = None
        while True:
            frame = _pop_buffered_frame(self._buffer)
            if frame is not None:
                return frame

            available, eof = self._probe()
            if available > 0:
                chunk = self._read_available_chunk(available)
                if chunk:
                    self._buffer.extend(chunk)
                    cancel_deadline = None
                    continue
                eof = True

            if eof:
                if self._buffer:
                    raise ValueError("Truncated PTY frame")
                return None

            if done.is_set():
                if not self._buffer:
                    raise _ControlReadCancelled()
                if cancel_deadline is None:
                    cancel_deadline = time.monotonic() + _CANCEL_GRACE_SECONDS
                elif time.monotonic() >= cancel_deadline:
                    raise ValueError("Truncated PTY frame")

            done.wait(_CONTROL_POLL_SECONDS)

    def _read_available_chunk(self, available: int) -> bytes:
        header = _decode_frame_header(self._buffer)
        if header is None:
            wanted = _FRAME_HEADER_BYTES - len(self._buffer)
        else:
            _frame_type, size = header
            wanted = (_FRAME_HEADER_BYTES + size) - len(self._buffer)
        reader = getattr(self._stream, "read1", None)
        if callable(reader):
            return bytes(reader(min(available, wanted)))
        return bytes(self._stream.read(min(available, wanted)))

    @staticmethod
    def _build_probe(stream: BinaryIO):
        handle = _ControlFrameReader._pipe_handle(stream)
        if handle is not None:
            import win32pipe

            def probe() -> tuple[int, bool]:
                try:
                    _data, available, _remaining = win32pipe.PeekNamedPipe(handle, 0)
                except Exception as error:
                    if getattr(error, "winerror", None) in {109, 232, 233}:
                        return 0, True
                    raise
                return int(available), False

            return probe

        if hasattr(stream, "bytes_available") and hasattr(stream, "input_closed"):

            def probe() -> tuple[int, bool]:
                return int(stream.bytes_available()), bool(stream.input_closed())

            return probe
        return None

    @staticmethod
    def _pipe_handle(stream: BinaryIO) -> int | None:
        try:
            fileno = stream.fileno()
        except (AttributeError, io.UnsupportedOperation, OSError):
            return None
        import msvcrt

        return int(msvcrt.get_osfhandle(fileno))


def _read_output(
    pty,
    writer: _FrameWriter,
    done: threading.Event,
    shutdown_requested: threading.Event,
) -> None:
    try:
        while True:
            chunk = pty.read(blocking=True)
            if chunk:
                writer.send(FrameType.OUTPUT, chunk.encode("utf-8"))
                continue
            if not pty.isalive() or pty.iseof():
                break
    except Exception as error:
        message = _output_error_message(
            error,
            shutdown_requested=shutdown_requested.is_set(),
            pty=pty,
        )
        if message is not None:
            writer.send(FrameType.ERROR, _error_payload(message))
    finally:
        if not pty.isalive() or pty.iseof():
            writer.send_json(FrameType.EXIT, {"exit_code": pty.get_exitstatus()})
        done.set()


def _consume_control(
    stdin: BinaryIO,
    pty,
    writer: _FrameWriter,
    done: threading.Event,
    shutdown_requested: threading.Event,
) -> None:
    reader = _ControlFrameReader(stdin)
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    saw_eof = False
    try:
        while True:
            frame = reader.read(done)
            if frame is None:
                saw_eof = True
                break
            kind, payload = frame
            if kind is FrameType.INPUT:
                text = decoder.decode(payload, final=False)
                if text:
                    pty.write(text)
                continue
            if kind is FrameType.RESIZE:
                rows, cols = _decode_resize(payload)
                pty.set_size(cols, rows)
                continue
            writer.send(FrameType.ERROR, _error_payload(f"Unsupported PTY control frame: {kind.name}"))
            return
        if saw_eof:
            tail = decoder.decode(b"", final=True)
            if tail:
                pty.write(tail)
    except _ControlReadCancelled:
        return
    except UnicodeDecodeError:
        writer.send(FrameType.ERROR, _error_payload("PTY control failed"))
    except ValueError as error:
        writer.send(FrameType.ERROR, _error_payload(str(error)))
    except Exception as error:
        writer.send(FrameType.ERROR, _error_payload("PTY control failed"))
    finally:
        shutdown_requested.set()
        try:
            pty.cancel_io()
        except Exception:
            pass


def main(stdin: BinaryIO, stdout: BinaryIO) -> int:
    writer = _FrameWriter(stdout)
    frame = read_frame(stdin)
    if frame is None:
        writer.send(FrameType.ERROR, _error_payload("Expected START frame"))
        return 1
    kind, payload = frame
    if kind is not FrameType.START:
        writer.send(FrameType.ERROR, _error_payload("Expected START frame"))
        return 1
    try:
        launch, rows, cols = decode_start(payload)
    except ValueError as error:
        writer.send(FrameType.ERROR, _error_payload(str(error)))
        return 1

    try:
        import winpty

        pty = winpty.PTY(cols, rows, backend=winpty.Backend.ConPTY)
        spawned = pty.spawn(
            appname=launch.argv[0],
            cmdline=subprocess.list2cmdline(list(launch.argv[1:])),
            cwd=str(launch.cwd),
            env=_environment_block(dict(launch.env)),
        )
        if not spawned:
            raise RuntimeError("ConPTY spawn returned false")
        if not isinstance(pty.pid, int) or pty.pid <= 0:
            raise RuntimeError("ConPTY did not report a child pid")
    except Exception as error:
        writer.send(FrameType.ERROR, _error_payload(f"PTY startup failed: {error}"))
        return 1

    writer.send_json(FrameType.READY, {"pid": pty.pid})
    output_done = threading.Event()
    control_shutdown = threading.Event()
    output_thread = threading.Thread(
        target=_read_output,
        args=(pty, writer, output_done, control_shutdown),
        daemon=False,
    )
    control_thread = threading.Thread(
        target=_consume_control,
        args=(stdin, pty, writer, output_done, control_shutdown),
        daemon=False,
    )
    output_thread.start()
    control_thread.start()
    output_thread.join()
    control_thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.stdin.buffer, sys.stdout.buffer))