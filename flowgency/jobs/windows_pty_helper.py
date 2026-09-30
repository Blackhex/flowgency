from __future__ import annotations

import codecs
import json
import subprocess
import sys
import threading
from typing import BinaryIO

from flowgency.jobs.windows_pty_protocol import (
    FrameType,
    _validate_dimensions,
    decode_start,
    read_frame,
    write_frame,
)


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


def _read_output(pty, writer: _FrameWriter, done: threading.Event) -> None:
    try:
        while True:
            chunk = pty.read(blocking=True)
            if chunk:
                writer.send(FrameType.OUTPUT, chunk.encode("utf-8"))
                continue
            if not pty.isalive() or pty.iseof():
                break
    except Exception as error:
        writer.send(FrameType.ERROR, _error_payload(f"PTY output failed: {error}"))
    finally:
        if not pty.isalive() or pty.iseof():
            writer.send_json(FrameType.EXIT, {"exit_code": pty.get_exitstatus()})
        done.set()


def _consume_control(stdin: BinaryIO, pty, writer: _FrameWriter, done: threading.Event) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        while True:
            frame = read_frame(stdin)
            if frame is None:
                return
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
        tail = decoder.decode(b"", final=True)
        if tail:
            pty.write(tail)
    except Exception as error:
        writer.send(FrameType.ERROR, _error_payload(f"PTY control failed: {error}"))
    finally:
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
    output_thread = threading.Thread(target=_read_output, args=(pty, writer, output_done), daemon=False)
    control_thread = threading.Thread(
        target=_consume_control,
        args=(stdin, pty, writer, output_done),
        daemon=False,
    )
    output_thread.start()
    control_thread.start()
    output_thread.join()
    try:
        stdin.close()
    except Exception:
        pass
    control_thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.stdin.buffer, sys.stdout.buffer))