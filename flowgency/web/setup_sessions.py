"""App-owned reconnectable connected-setup terminal session.

Flowgency serves exactly one interactive setup terminal per server process. This
module owns that single session's whole lifecycle: it spawns the connected PTY,
fans its output out to reconnecting browsers under a bounded replay buffer, lets
one connection at a time drive input, and — crucially — proves the entire process
tree is stopped before it ever permits a replacement.

Two locks keep that promise:

* ``_lifecycle_lock`` serializes Start, Stop and shutdown across the *entire*
  ``asyncio.to_thread`` spawn and the process Stop/cleanup, so a competing Start
  can never spawn a second tree while the first launch is still in flight.
* ``_state_lock`` guards only the fan-out, ownership and snapshot state and is
  never held across blocking PTY I/O.

The reader's end-of-stream path finalizes using ``_state_lock`` alone and relies
on the process layer's idempotent Stop, so it can never invert against a Stop
that holds ``_lifecycle_lock``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.connected_process import (
    ConnectedLaunchError,
    ConnectedProcess,
    start_connected_process,
)
from flowgency.jobs.processes import ProcessStopEvidence, RuntimeProcessLifecycle

SetupSessionState = Literal["starting", "running", "exited", "stopped", "failed"]

_MAX_INPUT_BYTES = 64 * 1024
_MIN_ROWS, _MAX_ROWS = 2, 200
_MIN_COLS, _MAX_COLS = 20, 400
_IDLE_TIMEOUT_SECONDS = 3600.0
_MAX_LIFETIME_SECONDS = 14400.0
_SWEEP_INTERVAL_SECONDS = 30.0
_DEFAULT_REPLAY_LIMIT = 1 << 18
_DEFAULT_CLIENT_LIMIT = 1 << 18

ProcessFactory = Callable[[RuntimeLaunch], ConnectedProcess]


class SetupSessionConflict(Exception):
    """Raised when a request cannot own or drive the single setup session."""


@dataclass(frozen=True)
class SetupSessionSnapshot:
    state: SetupSessionState
    integration_name: str
    data_root: Path
    output: bytes
    truncated: bool
    fallback_command: str
    exit_code: int | None
    message: str


class _Subscriber:
    __slots__ = ("connection_id", "queue", "pending_bytes")

    def __init__(self, connection_id: str, output_queue: "asyncio.Queue[bytes | None]") -> None:
        self.connection_id = connection_id
        self.queue = output_queue
        self.pending_bytes = 0


class _Session:
    """Mutable per-session state. Mutated only under the manager's state lock."""

    def __init__(
        self,
        owner: str,
        integration_name: str,
        launch: RuntimeLaunch,
        fallback_command: str,
        *,
        now: Callable[[], float],
        replay_limit: int,
        client_limit: int,
    ) -> None:
        self.owner = owner
        self.integration_name = integration_name
        self.data_root = launch.cwd
        self.fallback_command = fallback_command
        self.lifecycle = RuntimeProcessLifecycle(job_id="setup-session", generation=uuid4().hex)
        self.process: ConnectedProcess | None = None
        self.state: SetupSessionState = "starting"
        self.exit_code: int | None = None
        self.message: str = ""
        self.reader_task: asyncio.Task[None] | None = None
        self.stop_evidence: ProcessStopEvidence | None = None
        self.finalized = False
        self.eof_seen = False
        self.replay_limit = replay_limit
        self.client_limit = client_limit
        self._now = now
        self._output = bytearray()
        self._truncated = False
        self._subscribers: dict[str, _Subscriber] = {}
        self._writer: str | None = None
        self._started = now()
        self._last_activity = now()

    def append_output(self, chunk: bytes) -> None:
        self._output.extend(chunk)
        if len(self._output) > self.replay_limit:
            self._output = self._output[-self.replay_limit:]
            self._truncated = True
        self._last_activity = self._now()
        for subscriber in tuple(self._subscribers.values()):
            if subscriber.pending_bytes + len(chunk) > self.client_limit:
                subscriber.queue.put_nowait(None)
                self._subscribers.pop(subscriber.connection_id, None)
                if self._writer == subscriber.connection_id:
                    self._writer = None
            else:
                subscriber.pending_bytes += len(chunk)
                subscriber.queue.put_nowait(chunk)


class SetupSessionManager:
    """Owns one reconnectable connected-setup terminal for the whole server."""

    def __init__(
        self,
        process_factory: ProcessFactory = start_connected_process,
        *,
        now: Callable[[], float] = time.monotonic,
        replay_limit: int = _DEFAULT_REPLAY_LIMIT,
        client_limit: int = _DEFAULT_CLIENT_LIMIT,
        idle_timeout: float = _IDLE_TIMEOUT_SECONDS,
        max_lifetime: float = _MAX_LIFETIME_SECONDS,
        sweep_interval: float = _SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._factory = process_factory
        self._now = now
        self._replay_limit = replay_limit
        self._client_limit = client_limit
        self._idle_timeout = idle_timeout
        self._max_lifetime = max_lifetime
        self._sweep_interval = sweep_interval
        self._lifecycle_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._session: _Session | None = None
        self._sweeper: asyncio.Task[None] | None = None
        self._closing = False

    # ── Start ────────────────────────────────────────────────────────────

    async def start(
        self, owner: str, integration_name: str, launch: RuntimeLaunch, fallback_command: str
    ) -> SetupSessionSnapshot:
        async with self._lifecycle_lock:
            if self._closing:
                raise SetupSessionConflict("Setup is shutting down")
            async with self._state_lock:
                existing = self._session
            if existing is not None:
                if existing.state in {"starting", "running"}:
                    if existing.owner == owner:
                        if (
                            existing.data_root == launch.cwd
                            and existing.integration_name == integration_name
                        ):
                            return self._snapshot_of(existing)
                        raise SetupSessionConflict(
                            "Stop the running setup session before launching a "
                            "different root or integration"
                        )
                    raise SetupSessionConflict("Another browser owns the setup session")
                if existing.state == "failed":
                    raise SetupSessionConflict(
                        "The previous setup process could not be confirmed stopped"
                    )
                # exited or stopped sessions are replaceable.
            session = _Session(
                owner,
                integration_name,
                launch,
                fallback_command,
                now=self._now,
                replay_limit=self._replay_limit,
                client_limit=self._client_limit,
            )
            async with self._state_lock:
                self._session = session
            spawn_task: asyncio.Task[ConnectedProcess] = asyncio.ensure_future(
                asyncio.to_thread(self._factory, launch)
            )
            try:
                # Held across the whole spawn: no competing Start can replace it.
                process = await asyncio.shield(spawn_task)
            except asyncio.CancelledError:
                await self._reap_cancelled_spawn(session, spawn_task)
                raise
            except BaseException as error:
                await self._fail_spawn(session, error)
                raise
            async with self._state_lock:
                session.process = process
                session.state = "running"
                session.reader_task = asyncio.ensure_future(self._read_loop(session))
            self._ensure_sweeper()
            return self._snapshot_of(session)

    def _ensure_sweeper(self) -> None:
        if self._sweeper is None and self._sweep_interval > 0:
            self._sweeper = asyncio.ensure_future(self._sweep())

    async def _reap_cancelled_spawn(
        self, session: _Session, spawn_task: "asyncio.Task[ConnectedProcess]"
    ) -> None:
        process = await self._await_shielded(spawn_task)
        confirmed = True
        if process is not None:
            evidence = await asyncio.to_thread(process.stop, session.lifecycle)
            confirmed = evidence.confirmed
        async with self._state_lock:
            if self._session is session:
                if confirmed:
                    self._session = None
                else:
                    session.state = "failed"
                    session.message = "Cancelled setup launch could not be confirmed stopped"
                    session.finalized = True

    @staticmethod
    async def _await_shielded(spawn_task: "asyncio.Task[ConnectedProcess]") -> ConnectedProcess | None:
        # The spawn was shielded, so it keeps running even though we were cancelled.
        # Wait it out to completion before deciding how to clean up.
        while not spawn_task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(spawn_task)
        if spawn_task.cancelled() or spawn_task.exception() is not None:
            return None
        return spawn_task.result()

    async def _fail_spawn(self, session: _Session, error: BaseException) -> None:
        async with self._state_lock:
            if self._session is not session:
                return
            if isinstance(error, ConnectedLaunchError) and not error.cleanup_confirmed:
                session.state = "failed"
                session.message = str(error)
                session.finalized = True
            else:
                # A confirmed-clean failed start frees the slot for an external fallback.
                self._session = None

    # ── Attach / streaming ───────────────────────────────────────────────

    async def attach(
        self, owner: str
    ) -> tuple[SetupSessionSnapshot, str, "asyncio.Queue[bytes | None]"]:
        async with self._state_lock:
            session = self._require_owned(owner)
            connection_id = uuid4().hex
            output_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            session._subscribers[connection_id] = _Subscriber(connection_id, output_queue)
            if session.finalized:
                # The stream already ended and prior subscribers were closed.
                # Hand back the replay via the snapshot and signal end-of-stream
                # at once so a reconnecting client never blocks, and grant no
                # writer on an exited session.
                output_queue.put_nowait(None)
            else:
                session._writer = connection_id
            snapshot = self._snapshot_of(session)
        return snapshot, connection_id, output_queue

    async def send_input(self, owner: str, connection_id: str, data: bytes) -> None:
        async with self._state_lock:
            session = self._require_owned(owner)
            if session._writer != connection_id:
                raise SetupSessionConflict("Another connection controls setup input")
            if session.state != "running" or session.eof_seen or session.process is None:
                raise SetupSessionConflict("The setup session is not accepting input")
            if len(data) > _MAX_INPUT_BYTES:
                raise ValueError("Setup input exceeds 64 KiB")
            process = session.process
            session._last_activity = self._now()
        await asyncio.to_thread(process.write, bytes(data))

    async def resize(self, owner: str, connection_id: str, rows: int, cols: int) -> None:
        async with self._state_lock:
            session = self._require_owned(owner)
            if session._writer != connection_id:
                raise SetupSessionConflict("Another connection controls setup input")
            if not (_MIN_ROWS <= rows <= _MAX_ROWS and _MIN_COLS <= cols <= _MAX_COLS):
                raise ValueError("Terminal size out of bounds")
            if session.state != "running" or session.eof_seen or session.process is None:
                raise SetupSessionConflict("The setup session is not running")
            process = session.process
        await asyncio.to_thread(process.resize, rows, cols)

    async def consumed(self, owner: str, connection_id: str, byte_count: int) -> None:
        async with self._state_lock:
            session = self._session
            if session is None or session.owner != owner:
                return
            subscriber = session._subscribers.get(connection_id)
            if subscriber is not None:
                subscriber.pending_bytes = max(0, subscriber.pending_bytes - byte_count)

    async def detach(self, owner: str, connection_id: str) -> None:
        async with self._state_lock:
            session = self._session
            if session is None or session.owner != owner:
                return
            session._subscribers.pop(connection_id, None)
            if session._writer == connection_id:
                session._writer = None

    # ── Snapshot ─────────────────────────────────────────────────────────

    def snapshot(self, owner: str) -> SetupSessionSnapshot | None:
        session = self._session
        if session is None or session.owner != owner:
            return None
        return self._snapshot_of(session)

    def _snapshot_of(self, session: _Session) -> SetupSessionSnapshot:
        return SetupSessionSnapshot(
            state=session.state,
            integration_name=session.integration_name,
            data_root=session.data_root,
            output=bytes(session._output),
            truncated=session._truncated,
            fallback_command=session.fallback_command,
            exit_code=session.exit_code,
            message=session.message,
        )

    def _require_owned(self, owner: str) -> _Session:
        session = self._session
        if session is None or session.owner != owner:
            raise SetupSessionConflict("No setup session for this browser")
        return session

    @staticmethod
    def _confirmed_gone(session: _Session) -> bool:
        # True only when a prior Stop *proved* the whole tree gone. A session
        # finalized as ``failed`` on an unconfirmed Stop is not gone, so its
        # cleanup must be retried rather than trusted as idempotent.
        return (
            session.finalized
            and session.stop_evidence is not None
            and session.stop_evidence.confirmed
        )

    # ── Reader / finalize ────────────────────────────────────────────────

    async def _read_loop(self, session: _Session) -> None:
        assert session.process is not None
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(session.process.read)
                except asyncio.CancelledError:
                    return
                except (EOFError, OSError):
                    break
                if not chunk:
                    break  # PTY adapter reports end-of-stream as an empty read
                async with self._state_lock:
                    if self._session is not session or session.finalized:
                        return
                    session.append_output(chunk)
        except asyncio.CancelledError:
            return
        # Natural end of stream. Record that EOF was observed under the state
        # lock *before* the blocking Stop, so a racing explicit Stop that wins
        # finalization still resolves coherently as a natural exit rather than a
        # user abort with an unknown exit code.
        async with self._state_lock:
            if self._session is not session or session.finalized:
                return
            session.eof_seen = True
        # Confirm the whole tree stopped before freeing the slot.
        try:
            evidence = await asyncio.to_thread(session.process.stop, session.lifecycle)
        except asyncio.CancelledError:
            return
        await self._finalize(session, evidence, natural_exit=True)

    async def _finalize(
        self, session: _Session, evidence: ProcessStopEvidence, *, natural_exit: bool
    ) -> None:
        async with self._state_lock:
            # A confirmed finalize is terminal (and wins any natural-exit vs
            # explicit-Stop race). An earlier *unconfirmed* failure may be
            # re-finalized by a retry that finally proves the tree gone.
            if self._confirmed_gone(session):
                return
            session.finalized = True
            session.stop_evidence = evidence
            natural = natural_exit or session.eof_seen
            if natural and session.process is not None:
                with contextlib.suppress(Exception):
                    session.exit_code = session.process.exit_code()
            if evidence.confirmed:
                session.state = "exited" if natural else "stopped"
            else:
                session.state = "failed"
                if not session.message:
                    session.message = evidence.reason
            self._close_subscribers(session)

    def _close_subscribers(self, session: _Session) -> None:
        for subscriber in tuple(session._subscribers.values()):
            subscriber.queue.put_nowait(None)
        session._subscribers.clear()
        session._writer = None

    # ── Stop / limits / shutdown ─────────────────────────────────────────

    async def stop(self, owner: str) -> ProcessStopEvidence:
        async with self._lifecycle_lock:
            session = self._session
            if session is None or session.owner != owner:
                raise SetupSessionConflict("No setup session for this browser")
            return await self._stop_session(session)

    async def _stop_session(self, session: _Session) -> ProcessStopEvidence:
        if session.reader_task is not None:
            session.reader_task.cancel()
        if self._confirmed_gone(session):
            assert session.stop_evidence is not None
            evidence = session.stop_evidence
        elif session.process is not None:
            evidence = await asyncio.to_thread(session.process.stop, session.lifecycle)
            await self._finalize(session, evidence, natural_exit=False)
        else:
            evidence = ProcessStopEvidence(
                session.lifecycle.job_id, session.lifecycle.generation, False, "no-process"
            )
            await self._finalize(session, evidence, natural_exit=False)
        if session.reader_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await session.reader_task
        return evidence

    async def enforce_limits(self) -> None:
        async with self._lifecycle_lock:
            session = self._session
            if session is None or session.finalized or session.state not in {"starting", "running"}:
                return
            now = self._now()
            idle = now - session._last_activity > self._idle_timeout
            aged = now - session._started > self._max_lifetime
            if idle or aged:
                await self._stop_session(session)

    async def _sweep(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._sweep_interval)
                await self.enforce_limits()
        except asyncio.CancelledError:
            return

    async def shutdown(self) -> None:
        self._closing = True
        sweeper = self._sweeper
        self._sweeper = None
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
        async with self._lifecycle_lock:
            session = self._session
            if session is None:
                return
            # Retry an unconfirmed prior cleanup; only a proven-gone tree is skipped.
            if not self._confirmed_gone(session):
                await self._stop_session(session)
