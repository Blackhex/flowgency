"""Connected PTY processes whose whole process tree Flowgency owns and must prove stopped."""

from __future__ import annotations

import contextlib
import os
import signal
import threading
import time
from pathlib import Path
from typing import Protocol

from flowgency.integrations.errors import IntegrationError
from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.processes import (
    OwnedPosixProcessGroup,
    ProcessStopEvidence,
    RuntimeProcessLifecycle,
    _capture_posix_group_identity,
    _group_exit_status,
    _owned_posix_group_state,
    _parse_proc_stat_snapshot,
    terminate_owned_posix_group,
)


_STOP_TIMEOUT_SECONDS = 5.0
_MAX_TERMINAL_DIMENSION = 32767
_POSIX_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


class ConnectedLaunchError(IntegrationError):
    def __init__(self, message: str, *, cleanup_confirmed: bool):
        super().__init__(message)
        self.cleanup_confirmed = cleanup_confirmed


class ConnectedProcess(Protocol):
    pid: int

    def read(self, size: int = 65536) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def alive(self) -> bool: ...

    def exit_code(self) -> int | None: ...

    def stop(self, lifecycle: RuntimeProcessLifecycle) -> ProcessStopEvidence: ...


# Trees whose stop could not be confirmed; they block new launches until a retry confirms them.
# The condition also gates launches on any stop still deciding its outcome (see `_enter_stop`),
# so a launch can never observe an empty pending list while that decision is unrecorded.
_unconfirmed_lock = threading.Condition()
_unconfirmed: list["_OwnedTerminal"] = []
_stops_in_flight = 0


def connected_process_available() -> bool:
    # Windows has no supported connected-PTY backend; it must never attempt to import one.
    if os.name == "nt":
        return False
    try:
        import ptyprocess  # noqa: F401
    except ImportError:
        return False
    return _posix_group_proof_available()


def start_connected_process(launch: RuntimeLaunch, *, rows: int = 24, cols: int = 80) -> ConnectedProcess:
    if launch.mode != "connected":
        raise ValueError("A connected PTY requires connected launch mode")
    if os.name == "nt":
        raise ConnectedLaunchError(
            "Connected setup is unavailable on Windows; use the external terminal",
            cleanup_confirmed=True,
        )
    _validate_size(rows, cols)
    _validate_launch(launch)
    _retry_unconfirmed()
    return PosixConnectedProcess.spawn(launch, rows=rows, cols=cols)


def _validate_size(rows: int, cols: int) -> None:
    for value in (rows, cols):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_TERMINAL_DIMENSION:
            raise ValueError(f"Terminal size must be 1..{_MAX_TERMINAL_DIMENSION} rows and columns")


def _validate_launch(launch: RuntimeLaunch) -> None:
    if not launch.argv:
        raise ValueError("A connected launch needs a command")
    if any("\0" in str(item) for item in launch.argv):
        raise ConnectedLaunchError("Connected launch arguments must not contain NUL", cleanup_confirmed=True)
    for name, value in launch.env.items():
        if not name or "=" in name or "\0" in name or "\0" in value:
            raise ConnectedLaunchError(
                f"Connected launch environment has an invalid entry: {name!r}",
                cleanup_confirmed=True,
            )


def _retry_unconfirmed() -> None:
    with _unconfirmed_lock:
        # Wait for every in-flight stop to record its outcome before trusting the list;
        # otherwise a stop that is about to fail could still look confirmed-clear.
        while _stops_in_flight:
            _unconfirmed_lock.wait()
        pending = list(_unconfirmed)
    for process in pending:
        process._stop_tree()
    with _unconfirmed_lock:
        remaining = len(_unconfirmed)
    if remaining:
        raise ConnectedLaunchError(
            f"{remaining} earlier connected process tree(s) could not be confirmed stopped",
            cleanup_confirmed=False,
        )


def _track(process: "_OwnedTerminal", confirmed: bool) -> None:
    with _unconfirmed_lock:
        if confirmed:
            if process in _unconfirmed:
                _unconfirmed.remove(process)
        elif process not in _unconfirmed:
            _unconfirmed.append(process)


def _enter_stop() -> None:
    global _stops_in_flight
    with _unconfirmed_lock:
        _stops_in_flight += 1


def _exit_stop() -> None:
    global _stops_in_flight
    with _unconfirmed_lock:
        _stops_in_flight -= 1
        _unconfirmed_lock.notify_all()


def _posix_group_proof_available() -> bool:
    try:
        text = Path("/proc/self/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return _parse_proc_stat_snapshot(text) is not None


class _OwnedTerminal:
    """Shared read/stop bookkeeping; platform classes own the tree and terminal handles."""

    _reads_cancellable = False

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._stop_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._io_closed = False
        self._ops_in_flight = 0
        self._pending_output = b""
        self._stopped = False

    def read(self, size: int = 65536) -> bytes:
        if size <= 0:
            raise ValueError("read size must be positive")
        with self._io_lock:
            if self._pending_output:
                chunk, self._pending_output = self._pending_output[:size], self._pending_output[size:]
                return chunk
            if self._io_closed:
                return b""
            self._ops_in_flight += 1
        try:
            data = self._read_terminal(size)
        finally:
            self._end_op()
        if len(data) <= size:
            return data
        with self._io_lock:
            self._pending_output += data[size:]
        return data[:size]

    def write(self, data: bytes) -> None:
        payload = bytes(data)
        self._begin_op()
        try:
            self._write_terminal(payload)
        finally:
            self._end_op()

    def resize(self, rows: int, cols: int) -> None:
        _validate_size(rows, cols)
        self._begin_op()
        try:
            self._resize_terminal(rows, cols)
        finally:
            self._end_op()

    def _begin_op(self) -> None:
        with self._io_lock:
            if self._io_closed:
                raise BrokenPipeError("The connected process has been stopped")
            self._ops_in_flight += 1

    def _end_op(self) -> None:
        with self._io_lock:
            self._ops_in_flight -= 1

    def stop(self, lifecycle: RuntimeProcessLifecycle) -> ProcessStopEvidence:
        confirmed, reason = self._stop_tree()
        return ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, confirmed, reason)

    def _stop_tree(self) -> tuple[bool, str]:
        with self._stop_lock:
            if self._stopped:
                return True, "stopped"
            _enter_stop()
            try:
                deadline = time.monotonic() + _STOP_TIMEOUT_SECONDS
                confirmed, reason = self._terminate_tree(deadline)
                with self._io_lock:
                    self._io_closed = True
                drained = self._release_reader(deadline if confirmed or self._reads_cancellable else time.monotonic())
                if confirmed:
                    self._release(drained)
                    self._stopped = True
                # Recorded while still "in flight" so a concurrent launch check that is
                # waiting on `_enter_stop`/`_exit_stop` never sees a stale, empty pending list.
                _track(self, confirmed)
                return confirmed, reason
            finally:
                _exit_stop()

    def _release_reader(self, deadline: float) -> bool:
        while True:
            self._cancel_reads()
            with self._io_lock:
                if self._ops_in_flight == 0:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def _read_terminal(self, size: int) -> bytes:
        raise NotImplementedError

    def _write_terminal(self, data: bytes) -> None:
        raise NotImplementedError

    def _resize_terminal(self, rows: int, cols: int) -> None:
        raise NotImplementedError

    def _terminate_tree(self, deadline: float) -> tuple[bool, str]:
        raise NotImplementedError

    def _cancel_reads(self) -> None:
        return None

    def _release(self, drained: bool) -> None:
        raise NotImplementedError


class PosixConnectedProcess(_OwnedTerminal):
    def __init__(self, pty, group: OwnedPosixProcessGroup | None) -> None:
        super().__init__(int(pty.pid))
        self._pty = pty
        self._group = group
        self._tree_finished = False
        self._exit_code: int | None = None
        self._leader_reaped = False

    @classmethod
    def spawn(cls, launch: RuntimeLaunch, *, rows: int, cols: int) -> "PosixConnectedProcess":
        if not _posix_group_proof_available():
            raise ConnectedLaunchError(
                "A connected PTY needs readable /proc process-group evidence",
                cleanup_confirmed=True,
            )
        try:
            from ptyprocess import PtyProcess
        except ImportError as error:
            raise ConnectedLaunchError(f"POSIX PTY support is unavailable: {error}", cleanup_confirmed=True) from error
        try:
            pty = PtyProcess.spawn(
                list(launch.argv),
                cwd=str(launch.cwd),
                env=dict(launch.env),
                dimensions=(rows, cols),
            )
        except Exception as error:
            # ptyprocess raises before forking or after the child reported a failed exec.
            raise ConnectedLaunchError(
                f"Could not start {launch.argv[0]} in a PTY: {error}",
                cleanup_confirmed=True,
            ) from error
        try:
            group = _capture_posix_group_identity(pty.pid)
        except Exception as error:
            # The PTY is already spawned; a raised capture must still go through the same
            # safe stop path as a confirmed-absent group, not bypass it and leak the child.
            confirmed, _ = cls(pty, None)._stop_tree()
            raise ConnectedLaunchError(
                "Could not prove ownership of the connected PTY process group",
                cleanup_confirmed=confirmed,
            ) from error
        process = cls(pty, group)
        if process._group is None:
            confirmed, _ = process._stop_tree()
            raise ConnectedLaunchError(
                "Could not prove ownership of the connected PTY process group",
                cleanup_confirmed=confirmed,
            )
        return process

    def alive(self) -> bool:
        if self._stopped or self._tree_finished:
            return False
        if self._group is None or _owned_posix_group_state(self._group) != "empty":
            return True
        with self._stop_lock:
            # ptyprocess.isalive() reaps the leader, so only call it once the whole group is gone.
            if not self._tree_finished and self._reap_leader(time.monotonic()):
                self._tree_finished = True
        return not self._tree_finished

    def exit_code(self) -> int | None:
        if self.alive():
            return None
        return self._exit_code

    def _read_terminal(self, size: int) -> bytes:
        try:
            return self._pty.read(size)
        except (EOFError, OSError, ValueError):
            return b""

    def _write_terminal(self, data: bytes) -> None:
        try:
            self._pty.write(data)
        except (OSError, ValueError) as error:
            raise BrokenPipeError(str(error)) from error

    def _resize_terminal(self, rows: int, cols: int) -> None:
        self._pty.setwinsize(rows, cols)

    def _terminate_tree(self, deadline: float) -> tuple[bool, str]:
        if self._tree_finished:
            return True, "stopped"
        if self._group is None:
            return self._terminate_unproven_group(deadline)
        status = terminate_owned_posix_group(self._group, timeout=max(0.0, deadline - time.monotonic()))
        if status != "empty":
            return False, status
        if not self._reap_leader(deadline):
            return False, "root-unreaped"
        self._tree_finished = True
        return True, "stopped"

    def _terminate_unproven_group(self, deadline: float) -> tuple[bool, str]:
        if not self._leader_reaped:
            # The unreaped leader pins its pid, so neither number can name someone else's process
            # or group yet. Once reaped, that pid may be recycled -- never signal it again below.
            for kill in (os.killpg, os.kill):
                with contextlib.suppress(OSError):
                    kill(self.pid, _POSIX_KILL_SIGNAL)
            if not self._reap_leader(deadline):
                return False, "root-unreaped"
        status = _group_exit_status(self.pid, deadline)
        if status != "empty":
            return False, status
        self._tree_finished = True
        return True, "stopped"

    def _reap_leader(self, deadline: float) -> bool:
        while True:
            try:
                if not self._pty.isalive():
                    status = self._pty.exitstatus
                    self._exit_code = status if status is not None else -int(self._pty.signalstatus or 0)
                    self._leader_reaped = True
                    return True
            except Exception:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def _release(self, drained: bool) -> None:
        # Closing an fd an operation is still inside could hand its number to an unrelated file.
        if drained:
            with contextlib.suppress(Exception):
                self._pty.close(force=True)
