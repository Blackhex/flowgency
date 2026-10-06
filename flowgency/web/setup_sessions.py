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

Each session's ``write_lock`` serializes whole PTY input writes (user input and
the completion ``/exit``) and is held until the worker thread has finished, even
if the caller is cancelled. It is taken before ``_state_lock``, never the reverse.

The reader's end-of-stream path finalizes using ``_state_lock`` alone and relies
on the process layer's idempotent Stop, so it can never invert against a Stop
that holds ``_lifecycle_lock``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, TypeVar
from uuid import uuid4

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.connected_process import (
    ConnectedLaunchError,
    ConnectedProcess,
    _LaunchCleanup,
    _retry_unconfirmed,
    start_connected_process,
)
from flowgency.jobs.processes import ProcessStopEvidence, RuntimeProcessLifecycle
from flowgency.web.setup_completion import (
    SetupCompletionCommand,
    SetupCompletionDecision,
    SetupCompletionLaunch,
    SetupExitAdapter,
    UnsupportedExitAdapter,
)

SetupSessionState = Literal["starting", "running", "exited", "stopped", "failed"]

_MAX_INPUT_BYTES = 64 * 1024
_MIN_ROWS, _MAX_ROWS = 2, 200
_MIN_COLS, _MAX_COLS = 20, 400
_IDLE_TIMEOUT_SECONDS = 3600.0
_MAX_LIFETIME_SECONDS = 14400.0
_SWEEP_INTERVAL_SECONDS = 30.0
_DEFAULT_REPLAY_LIMIT = 2 * 1024 * 1024
_DEFAULT_CLIENT_LIMIT = 1 << 18
_SANITIZED_START_FAILURE = "Setup could not be started; cleanup could not be confirmed."
_COMPLETION_REJECTED = "Setup completion was rejected."
_EXIT_COMMAND = b"/exit\r"

ProcessFactory = Callable[[RuntimeLaunch], ConnectedProcess]
_ExternalResult = TypeVar("_ExternalResult")


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


@dataclass(frozen=True)
class _Acknowledgement:
    revision: str
    scheduler_result: str
    limitations_acknowledged: bool


@dataclass(eq=False)
class _CompletionAttempt:
    """Launch-bound completion state, separate from the process state."""

    owner: str
    integration_name: str
    data_root: Path
    config_path: Path
    launch: SetupCompletionLaunch = field(repr=False)
    created: float
    session: "_Session | None" = field(default=None, repr=False)
    acknowledgement: _Acknowledgement | None = None
    cancelled: bool = False
    unexpected_failure: bool = False
    exit_capability_verified: bool = False
    exit_requested: bool = False
    input_count_at_acknowledgement: int = 0
    validation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def launch_id(self) -> str:
        return self.launch.launch_id

    @property
    def acknowledged_revision(self) -> str | None:
        return None if self.acknowledgement is None else self.acknowledgement.revision

    def selection(self) -> tuple[str, Path, Path]:
        return self.integration_name, self.data_root, self.config_path


def _blocked_decision(attempt: _CompletionAttempt) -> SetupCompletionDecision:
    if attempt.cancelled:
        return SetupCompletionDecision(
            attempt.launch_id, "cancelled", False, "Setup was stopped before it completed."
        )
    return SetupCompletionDecision(
        attempt.launch_id, "attention", False,
        "Setup ended without reporting completion; review the terminal output.",
    )


def _pending_decision(attempt: _CompletionAttempt) -> SetupCompletionDecision:
    return SetupCompletionDecision(
        attempt.launch_id, "pending", False, "Waiting for setup to report completion."
    )


def _exit_is_clean(session: _Session) -> bool:
    evidence = session.stop_evidence
    return (
        session.state == "exited"
        and session.exit_code == 0
        and evidence is not None
        and evidence.confirmed
    )


def _decision_from_confirmed_exit(attempt: _CompletionAttempt) -> SetupCompletionDecision:
    session = attempt.session
    assert session is not None
    if not session.finalized:
        return SetupCompletionDecision(
            attempt.launch_id, "acknowledged", False,
            "Setup reported completion; waiting for the setup process to exit.",
        )
    if _exit_is_clean(session):
        return SetupCompletionDecision(attempt.launch_id, "complete", True, "Setup is complete.")
    return SetupCompletionDecision(
        attempt.launch_id, "attention", False,
        "The setup process did not exit cleanly; review the terminal output.",
    )


def _completed_fallback_decision(attempt: _CompletionAttempt) -> SetupCompletionDecision:
    session = attempt.session
    if session is not None and session.finalized and not _exit_is_clean(session):
        return SetupCompletionDecision(
            attempt.launch_id, "attention", False,
            "The setup process did not exit cleanly; review the terminal output.",
        )
    return SetupCompletionDecision(attempt.launch_id, "complete", True, "Setup is complete.")


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
        data_root: Path,
        fallback_command: str,
        *,
        now: Callable[[], float],
        replay_limit: int,
        client_limit: int,
        generation: str | None = None,
    ) -> None:
        self.owner = owner
        self.integration_name = integration_name
        self.data_root = data_root
        self.fallback_command = fallback_command
        self.lifecycle = RuntimeProcessLifecycle(
            job_id="setup-session", generation=generation or uuid4().hex
        )
        self.process: ConnectedProcess | None = None
        self.cleanup: _LaunchCleanup | None = None
        self.state: SetupSessionState = "starting"
        self.exit_code: int | None = None
        self.message: str = ""
        self.reader_task: asyncio.Task[None] | None = None
        self.stop_evidence: ProcessStopEvidence | None = None
        self.finalized = False
        self.eof_seen = False
        self.input_count = 0
        self.write_lock = asyncio.Lock()
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
        exit_adapter: SetupExitAdapter | None = None,
    ) -> None:
        self._factory = process_factory
        self._exit_adapter = exit_adapter if exit_adapter is not None else UnsupportedExitAdapter()
        self._now = now
        self._replay_limit = replay_limit
        self._client_limit = client_limit
        self._idle_timeout = idle_timeout
        self._max_lifetime = max_lifetime
        self._sweep_interval = sweep_interval
        self._lifecycle_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._session: _Session | None = None
        self._completion: _CompletionAttempt | None = None
        self._sweeper: asyncio.Task[None] | None = None
        self._closing = False

    # ── Completion ───────────────────────────────────────────────────────

    async def prepare_completion(
        self,
        owner: str,
        integration_name: str,
        data_root: Path,
        config_path: Path,
        origin: str,
    ) -> SetupCompletionLaunch:
        async with self._state_lock:
            if self._closing:
                raise SetupSessionConflict("Setup is shutting down")
            attempt = self._completion
            if attempt is not None and self._holds_slot(attempt):
                if attempt.owner == owner and attempt.selection() == (
                    integration_name, data_root, config_path
                ):
                    return attempt.launch
                if not self._supersedable(attempt, owner):
                    raise SetupSessionConflict("Another setup launch owns the setup session")
            session = self._session
            if session is not None:
                if session.state == "failed":
                    raise SetupSessionConflict(
                        "The previous setup process could not be confirmed stopped"
                    )
                if session.state in {"starting", "running"}:
                    raise SetupSessionConflict(
                        "Stop the running setup session before preparing a new launch"
                    )
            launch = SetupCompletionLaunch(
                launch_id=uuid4().hex, origin=origin, token=secrets.token_urlsafe(32)
            )
            self._completion = _CompletionAttempt(
                owner, integration_name, data_root, config_path, launch, created=self._now(),
                exit_capability_verified=self._exit_adapter.capability().supported,
            )
            return launch

    def require_completion_token(self, token: str) -> str:
        attempt = self._attempt_for_token(token)
        return attempt.launch_id

    def completion_context(self, token: str) -> tuple[str, Path]:
        attempt = self._attempt_for_token(token)
        return attempt.launch.origin, attempt.config_path

    async def acknowledge_validated(
        self,
        token: str,
        command: SetupCompletionCommand,
        validate: Callable[[Path, str], str],
    ) -> SetupCompletionDecision:
        """Validate and acknowledge one attempt at a time, replaying identical reports."""
        attempt = self._attempt_for_token(token)
        async with attempt.validation_lock:
            self._attempt_for_token(token)
            if not hmac.compare_digest(
                command.launch_id.encode("utf-8"), attempt.launch_id.encode("utf-8")
            ):
                raise SetupSessionConflict(_COMPLETION_REJECTED)
            reported = _Acknowledgement(
                command.revision, command.scheduler_result, command.limitations_acknowledged
            )
            if attempt.acknowledgement == reported:
                validated = command.revision
            else:
                check = asyncio.ensure_future(
                    asyncio.to_thread(validate, attempt.config_path, command.revision)
                )
                try:
                    validated = await asyncio.shield(check)
                except asyncio.CancelledError:
                    # Keep the lock until the worker thread ends so checks never overlap.
                    while not check.done():
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await asyncio.shield(check)
                    if not check.cancelled():
                        check.exception()
                    raise
            return await self.acknowledge_completion(token, command, validated)

    async def acknowledge_completion(
        self, token: str, command: SetupCompletionCommand, validated_revision: str
    ) -> SetupCompletionDecision:
        async with self._state_lock:
            attempt = self._attempt_for_token(token)
            if not hmac.compare_digest(
                command.launch_id.encode("utf-8"), attempt.launch_id.encode("utf-8")
            ):
                raise SetupSessionConflict(_COMPLETION_REJECTED)
            if command.revision != validated_revision:
                raise SetupSessionConflict(_COMPLETION_REJECTED)
            acknowledgement = _Acknowledgement(
                validated_revision, command.scheduler_result, command.limitations_acknowledged
            )
            session = attempt.session
            live = session is None or not session.finalized
            if attempt.acknowledgement != acknowledgement:
                if not live:
                    raise SetupSessionConflict(_COMPLETION_REJECTED)
                attempt.acknowledgement = acknowledgement
                attempt.input_count_at_acknowledgement = (
                    0 if session is None else session.input_count
                )
            return self._decide(attempt, validated_revision, True)

    async def request_completion_exit(self, launch_id: str) -> bool:
        """Send ``/exit`` at most once, only at an adapter-verified safe boundary.

        Writes are ordered with user input by the session write lock: input that
        finished (or was in flight) after acknowledgement refuses the exit, and
        input arriving once the exit is claimed is refused.
        """
        async with self._state_lock:
            current = self._completion
            session = None if current is None else current.session
        if session is None:
            return False
        async with session.write_lock:
            async with self._state_lock:
                attempt = self._completion
                if attempt is None or attempt.session is not session:
                    return False
                if attempt.cancelled or attempt.unexpected_failure:
                    return False
                if not hmac.compare_digest(
                    launch_id.encode("utf-8"), attempt.launch_id.encode("utf-8")
                ):
                    return False
                if (
                    attempt.acknowledgement is None
                    or session.process is None
                    or session.state != "running"
                    or session.eof_seen
                    or session.finalized
                ):
                    return False
                if attempt.exit_requested:
                    return True
                adapter = self._exit_adapter
                if not adapter.capability().supported or adapter.boundary() != "safe":
                    return False
                if session.input_count != attempt.input_count_at_acknowledgement:
                    return False
                attempt.exit_requested = True
                process = session.process

            def settle(written: bool) -> None:
                attempt.exit_requested = written

            await self._write_pty(process, _EXIT_COMMAND, settle)
        return True

    def completion_decision(
        self, owner: str, current_revision: str | None, ready: bool
    ) -> SetupCompletionDecision:
        attempt = self._completion
        if attempt is None or attempt.owner != owner:
            return SetupCompletionDecision(
                None, "pending", False, "Waiting for setup to report completion."
            )
        return self._decide(attempt, current_revision, ready)

    def _decide(
        self, attempt: _CompletionAttempt, current_revision: str | None, ready: bool
    ) -> SetupCompletionDecision:
        if attempt.cancelled or attempt.unexpected_failure:
            return _blocked_decision(attempt)
        if not ready or current_revision != attempt.acknowledged_revision:
            return _pending_decision(attempt)
        if attempt.acknowledgement is None:
            return _pending_decision(attempt)
        if attempt.session is not None and attempt.exit_capability_verified:
            return _decision_from_confirmed_exit(attempt)
        return _completed_fallback_decision(attempt)

    def _attempt_for_token(self, token: str) -> _CompletionAttempt:
        attempt = self._completion
        presented = token.encode("utf-8") if isinstance(token, str) else b""
        expected = attempt.launch.token.encode("utf-8") if attempt is not None else b""
        matches = hmac.compare_digest(presented, expected)
        if attempt is None or not matches or attempt.cancelled or attempt.unexpected_failure:
            raise SetupSessionConflict(_COMPLETION_REJECTED)
        return attempt

    def _holds_slot(self, attempt: _CompletionAttempt) -> bool:
        if attempt.cancelled or attempt.unexpected_failure:
            return False
        if attempt.session is not None:
            return not attempt.session.finalized
        if attempt.acknowledgement is not None:
            return False
        # External exit is never observed, so an abandoned launch must expire.
        return self._now() - attempt.created <= self._max_lifetime

    def _claimable_attempt(
        self, owner: str, integration_name: str, data_root: Path
    ) -> _CompletionAttempt | None:
        attempt = self._completion
        if attempt is None or not self._holds_slot(attempt):
            return None
        if (
            attempt.owner != owner
            or attempt.integration_name != integration_name
            or attempt.data_root != data_root
        ):
            if not self._supersedable(attempt, owner):
                raise SetupSessionConflict("Another setup launch owns the setup session")
            self._completion = None
            return None
        return attempt if attempt.session is None else None

    @staticmethod
    def _supersedable(attempt: _CompletionAttempt, owner: str) -> bool:
        return (
            attempt.owner == owner
            and attempt.session is None
            and attempt.acknowledgement is None
        )

    def _end_attempt(self, session: _Session, *, failed: bool) -> None:
        attempt = self._completion
        if attempt is not None and attempt.session is session:
            if failed:
                attempt.unexpected_failure = True
            else:
                attempt.cancelled = True

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
            async with self._state_lock:
                attempt = self._claimable_attempt(owner, integration_name, launch.cwd)
                session = _Session(
                    owner,
                    integration_name,
                    launch.cwd,
                    fallback_command,
                    now=self._now,
                    replay_limit=self._replay_limit,
                    client_limit=self._client_limit,
                    generation=attempt.launch_id if attempt is not None else None,
                )
                if attempt is not None:
                    attempt.session = session
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

    async def _launch_external(
        self,
        owner: str,
        integration_name: str,
        data_root: Path,
        launch: Callable[[], _ExternalResult],
    ) -> _ExternalResult:
        async with self._lifecycle_lock:
            if self._closing:
                raise SetupSessionConflict("Setup is shutting down")
            async with self._state_lock:
                existing = self._session
                self._claimable_attempt(owner, integration_name, data_root)
            if existing is not None:
                if existing.state == "failed":
                    raise SetupSessionConflict("The previous setup process could not be confirmed stopped")
                if existing.state in {"starting", "running"}:
                    raise SetupSessionConflict("Stop the running setup session before launching an external terminal")

            async def operation() -> _ExternalResult:
                try:
                    await asyncio.to_thread(_retry_unconfirmed)
                except Exception as error:
                    failure = error if isinstance(error, ConnectedLaunchError) else ConnectedLaunchError(
                        _SANITIZED_START_FAILURE, cleanup_confirmed=False,
                    )
                    session = _Session(
                        owner, integration_name, data_root, "", now=self._now,
                        replay_limit=self._replay_limit, client_limit=self._client_limit,
                    )
                    async with self._state_lock:
                        self._session = session
                        attempt = self._claimable_attempt(owner, integration_name, data_root)
                        if attempt is not None:
                            attempt.session = session
                    await self._fail_spawn(session, failure)
                    if failure is error:
                        raise
                    raise failure from error
                return await asyncio.to_thread(launch)

            task = asyncio.ensure_future(operation())
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    with contextlib.suppress(BaseException):
                        await asyncio.shield(task)
                if not task.cancelled():
                    task.exception()
                raise

    def _ensure_sweeper(self) -> None:
        if self._sweeper is None and self._sweep_interval > 0:
            self._sweeper = asyncio.ensure_future(self._sweep())

    async def _reap_cancelled_spawn(
        self, session: _Session, spawn_task: "asyncio.Task[ConnectedProcess]"
    ) -> None:
        # The spawn was shielded, so it keeps running even though our Start was
        # cancelled. Wait it out, then fail closed: only a *proven*-clean outcome
        # frees the slot; anything unconfirmed keeps the slot blocked. Suppress
        # whatever the wait raises (our own re-cancellation or the spawn's own
        # error) and inspect the finished task explicitly below.
        while not spawn_task.done():
            with contextlib.suppress(BaseException):
                await asyncio.shield(spawn_task)
        if spawn_task.cancelled():
            # The spawn itself was cancelled; nothing was created to clean up.
            async with self._state_lock:
                if self._session is session:
                    self._end_attempt(session, failed=False)
                    self._close_subscribers(session)
                    self._session = None
            return
        error = spawn_task.exception()
        if error is not None:
            # A spawn that raised is cleaned up exactly like the normal failure
            # path, so an unconfirmed ConnectedLaunchError still blocks the slot.
            await self._fail_spawn(session, error)
            return
        process = spawn_task.result()
        evidence = await asyncio.to_thread(process.stop, session.lifecycle)
        async with self._state_lock:
            if self._session is not session:
                return
            if evidence.confirmed:
                self._end_attempt(session, failed=False)
                self._close_subscribers(session)
                self._session = None
            else:
                self._end_attempt(session, failed=True)
                # Keep the returned handle and the evidence so a later Stop or
                # shutdown can retry cleanup; block the slot until the tree is
                # proven gone rather than dropping an unconfirmed process.
                session.process = process
                session.stop_evidence = evidence
                session.state = "failed"
                session.message = "Cancelled setup launch could not be confirmed stopped"
                session.finalized = True
                self._close_subscribers(session)

    async def _fail_spawn(self, session: _Session, error: BaseException) -> None:
        async with self._state_lock:
            if self._session is not session:
                return
            if isinstance(error, ConnectedLaunchError) and error.cleanup_confirmed:
                # A confirmed-clean failed start frees the slot for an external fallback.
                self._end_attempt(session, failed=False)
                self._session = None
            else:
                self._end_attempt(session, failed=True)
                # Only a proven-clean ConnectedLaunchError may free the slot; any
                # other error (including an unconfirmed ConnectedLaunchError) is
                # no evidence the process tree was cleaned up, so fail closed.
                session.cleanup = error._cleanup if isinstance(error, ConnectedLaunchError) else None
                session.state = "failed"
                session.message = _SANITIZED_START_FAILURE
                session.finalized = True
            # Release any subscriber that attached while the session was still
            # `starting`, so no WebSocket writer lingers on a dead session.
            self._close_subscribers(session)

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
        async with session.write_lock:
            async with self._state_lock:
                session = self._require_owned(owner)
                if session._writer != connection_id:
                    raise SetupSessionConflict("Another connection controls setup input")
                if session.state != "running" or session.eof_seen or session.process is None:
                    raise SetupSessionConflict("The setup session is not accepting input")
                if len(data) > _MAX_INPUT_BYTES:
                    raise ValueError("Setup input exceeds 64 KiB")
                attempt = self._completion
                if attempt is not None and attempt.session is session and attempt.exit_requested:
                    raise SetupSessionConflict("The setup session is exiting")
                process = session.process
                session._last_activity = self._now()
            def settle(_written: bool) -> None:
                session.input_count += 1

            await self._write_pty(process, bytes(data), settle)

    @staticmethod
    async def _write_pty(
        process: ConnectedProcess, data: bytes, settle: Callable[[bool], None]
    ) -> None:
        # Callers hold write_lock: a cancelled caller must not release it while
        # the worker thread may still write, so wait the write out, then settle
        # exactly once with the real outcome and re-raise.
        task = asyncio.ensure_future(asyncio.to_thread(process.write, data))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                with contextlib.suppress(BaseException):
                    await asyncio.shield(task)
            raise
        finally:
            settle(not task.cancelled() and task.exception() is None)

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
                except EOFError:
                    break
                except OSError as error:
                    async with self._state_lock:
                        if self._session is not session or session.finalized:
                            return
                        if not session.message:
                            session.message = str(error) or "PTY output failed"
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
                attempt = self._completion
                if attempt is not None and attempt.session is session:
                    if not natural:
                        attempt.cancelled = True
                    elif attempt.acknowledgement is None:
                        attempt.unexpected_failure = True
            else:
                session.state = "failed"
                self._end_attempt(session, failed=True)
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
        elif session.process is not None or session.cleanup is not None:
            cleanup = session.process if session.process is not None else session.cleanup
            assert cleanup is not None
            evidence = await asyncio.to_thread(cleanup.stop, session.lifecycle)
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
