from __future__ import annotations

import contextlib
import importlib
import os
import threading
import time
from collections import deque

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.connected_process import ConnectedLaunchError, _OwnedTerminal
from flowgency.jobs.windows_conpty import WindowsConPTY, native_conpty_available
from flowgency.jobs.windows_job import WindowsJobLaunchError, WindowsJobOwner, _UncertainWindowsJob


MAX_DATA_BYTES = 65536
_OUTPUT_BUFFER_LIMIT = MAX_DATA_BYTES * 16
_READ_POLL_SECONDS = 0.01
_READER_JOIN_TIMEOUT_SECONDS = 1.0
_SANITIZED_READER_FAILURE = "PTY output failed"


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
    if not native_conpty_available():
        return False
    try:
        import pywintypes  # noqa: F401
        import win32api  # noqa: F401
        import win32con  # noqa: F401
        import win32event  # noqa: F401
        import win32job  # noqa: F401
        import win32process  # noqa: F401
    except ImportError:
        return False
    if not _websocket_protocol_available():
        return False
    required = (
        (win32job, "AssignProcessToJobObject"),
        (win32job, "IsProcessInJob"),
        (win32job, "QueryInformationJobObject"),
        (win32job, "TerminateJobObject"),
        (win32process, "ResumeThread"),
    )
    return all(_has_attr(module, name) for module, name in required)


class WindowsConnectedProcess(_OwnedTerminal):
    _reads_cancellable = True

    def __init__(self, terminal: WindowsConPTY, owner: WindowsJobOwner | None = None) -> None:
        super().__init__(owner.pid if owner is not None else 0)
        self._terminal = terminal
        self._owner = owner
        self._write_lock = threading.Lock()
        self._terminal_state_lock = threading.Lock()
        self._close_state_lock = threading.Lock()
        self._close_requested = False
        self._output_lock = threading.Condition()
        self._output_chunks: deque[bytes] = deque()
        self._buffered_output = 0
        self._exit_code: int | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_active = False
        self._stream_ended = False
        self._reader_error: str | None = None
        self._reader_error_delivered = False
        self._reader_wakeup = threading.Event()
        self._reader_done = threading.Event()
        self._reader_done.set()
        self._stop_deadline = 0.0
        self._cleanup_error: Exception | None = None
        self._failed_launch_owners: tuple[_UncertainWindowsJob, ...] = ()

    @classmethod
    def spawn(cls, launch: RuntimeLaunch, *, rows: int, cols: int) -> "WindowsConnectedProcess":
        process = cls(WindowsConPTY(rows, cols))
        job_launch_attempted = False
        try:
            process._terminal.open()
            process._start_reader()
            job_launch_attempted = True
            owner = WindowsJobOwner.launch_conpty(
                tuple(str(item) for item in launch.argv),
                launch.cwd,
                dict(launch.env),
                process._terminal.pseudoconsole,
            )
            process._owner = owner
            process.pid = owner.pid
            process._terminal.close_child_ends()
            return process
        except WindowsJobLaunchError as error:
            process._failed_launch_owners = error._cleanup_owners
            known_job_cleanup = error.cleanup_confirmed or bool(error._cleanup_owners)
            cleanup_confirmed = process._cleanup_after_launch_failure() and known_job_cleanup
            raise ConnectedLaunchError(
                f"Could not start {launch.argv[0]} in a PTY: {error}",
                cleanup_confirmed=cleanup_confirmed,
                _cleanup=process if known_job_cleanup and not cleanup_confirmed else None,
            ) from error
        except Exception as error:
            known_job_cleanup = not job_launch_attempted or process._owner is not None
            cleanup_confirmed = process._cleanup_after_launch_failure() and known_job_cleanup
            raise ConnectedLaunchError(
                f"Could not start {launch.argv[0]} in a PTY: {error}",
                cleanup_confirmed=cleanup_confirmed,
                _cleanup=process if known_job_cleanup and not cleanup_confirmed else None,
            ) from error

    def _start_reader(self) -> None:
        self._reader_done.clear()
        self._reader_wakeup.clear()
        try:
            thread = threading.Thread(target=self._read_terminal_output, name="windows-connected-conpty", daemon=False)
            thread.start()
        except BaseException:
            self._reader_done.set()
            raise
        self._reader_thread = thread

    def _close_child_ends(self) -> None:
        try:
            self._terminal.close_child_ends()
        except Exception as error:
            self._cleanup_error = error

    def _cleanup_after_launch_failure(self) -> bool:
        if self._owner is None:
            self._close_child_ends()
        confirmed, _reason = self._stop_tree()
        return confirmed

    def alive(self) -> bool:
        if self._stopped:
            return False
        if self._owner is None:
            return False
        return self._owner.alive()

    def exit_code(self) -> int | None:
        if self.alive():
            return None
        if self._exit_code is not None:
            return self._exit_code
        self._capture_exit_code()
        return self._exit_code

    def _read_terminal(self, size: int) -> bytes:
        del size
        while True:
            with self._output_lock:
                if self._output_chunks:
                    chunk = self._output_chunks.popleft()
                    self._buffered_output -= len(chunk)
                    return chunk
                if self._stream_ended:
                    if self._reader_error is not None and not self._reader_error_delivered:
                        self._reader_error_delivered = True
                        raise OSError(self._reader_error)
                    return b""
            with self._io_lock:
                if self._io_closed:
                    return b""
            time.sleep(_READ_POLL_SECONDS)

    def _write_terminal(self, data: bytes) -> None:
        try:
            with self._write_lock:
                self._terminal.write(data)
        except OSError as error:
            raise BrokenPipeError(str(error)) from error

    def _resize_terminal(self, rows: int, cols: int) -> None:
        with self._terminal_state_lock:
            with self._close_state_lock:
                if self._close_requested:
                    raise BrokenPipeError("The connected process has been stopped")
            try:
                self._terminal.resize(rows, cols)
            except OSError as error:
                raise BrokenPipeError(str(error)) from error

    def _request_close(self) -> bool:
        with self._close_state_lock:
            self._close_requested = True
        if not self._terminal_state_lock.acquire(blocking=False):
            return False
        try:
            self._terminal.begin_close()
            return True
        except Exception as error:
            self._cleanup_error = error
            raise
        finally:
            self._terminal_state_lock.release()

    def _terminate_tree(self, deadline: float) -> tuple[bool, str]:
        self._stop_deadline = deadline
        try:
            for owner in self._failed_launch_owners:
                confirmed, reason = owner.stop(deadline)
                if not confirmed:
                    return False, reason
            if self._owner is None:
                self._close_child_ends()
                self._request_close()
                if self._terminal.wait_closed(deadline):
                    return True, "stopped"
                return False, "console-close-pending"
            confirmed, reason = self._owner.stop(deadline)
            if confirmed:
                self._capture_exit_code()
                self._close_child_ends()
                self._request_close()
            return confirmed, reason
        except Exception as error:
            self._cleanup_error = error
            return False, "terminal-cleanup-failed"

    def _cancel_reads(self) -> None:
        self._reader_wakeup.set()

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
        try:
            reader = self._reader_thread
            if reader is not None:
                reader.join(timeout=_READER_JOIN_TIMEOUT_SECONDS)
                if reader.is_alive():
                    return False
            if not self._terminal.wait_closed(self._stop_deadline):
                return False
            self._terminal.close_streams()
            if self._owner is not None:
                self._owner.close_confirmed()
            for owner in self._failed_launch_owners:
                owner.close_confirmed()
        except Exception as error:
            self._cleanup_error = error
            return False
        return True

    def _read_terminal_output(self) -> None:
        try:
            while True:
                with self._io_lock:
                    self._reader_active = True
                try:
                    chunk = self._terminal.read(MAX_DATA_BYTES)
                finally:
                    with self._io_lock:
                        self._reader_active = False
                if chunk is None:
                    owner = self._owner
                    if owner is None:
                        with self._io_lock:
                            stopping = self._io_closed and not self._failed_launch_owners
                        if stopping:
                            self._request_close()
                    elif not owner.alive():
                        self._capture_exit_code()
                        self._request_close()
                    self._reader_wakeup.wait(_READ_POLL_SECONDS)
                    self._reader_wakeup.clear()
                    continue
                if chunk:
                    self._append_output(chunk)
                    continue
                self._capture_exit_code()
                return
        except Exception as error:
            del error
            self._reader_error = _SANITIZED_READER_FAILURE
        finally:
            self._reader_done.set()
            with self._output_lock:
                self._stream_ended = True

    def _release_reader(self, deadline: float) -> bool:
        while True:
            self._cancel_reads()
            with self._io_lock:
                if self._ops_in_flight == 0 and self._reader_done.is_set() and not self._reader_active:
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

    def _capture_exit_code(self) -> None:
        if self._exit_code is not None or self._owner is None:
            return
        with contextlib.suppress(Exception):
            self._exit_code = self._owner.exit_code()