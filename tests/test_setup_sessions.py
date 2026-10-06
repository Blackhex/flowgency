"""Tests for the app-owned reconnectable setup session manager."""

from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import threading
import time
from pathlib import Path

import pytest

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.connected_process import ConnectedLaunchError
from flowgency.jobs.processes import ProcessStopEvidence, process_identity_state, read_process_identity
from flowgency.web.setup_completion import SetupCompletionCommand
from flowgency.web.setup_sessions import SetupSessionConflict, SetupSessionManager

from tests._connected_setup_helpers import FakeProcess


if os.name == "nt":
    import win32api
    import win32process


def _wait_for_text(path: Path, *, timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {path}")


def _native_python_launch() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    env["__PYVENV_LAUNCHER__"] = __import__("sys").executable
    return win32process.GetModuleFileNameEx(win32api.GetCurrentProcess(), 0), env


def _write_script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


class _UnconfirmedThenConfirmedProcess(FakeProcess):
    """Its first Stop cannot prove the tree gone while the process stays
    potentially alive; its second Stop confirms the tree is gone. The first
    Stop deliberately does *not* wake the parked reader, so the interleaving is
    deterministic and no natural-exit finalize can race the explicit Stop."""

    def __init__(self):
        super().__init__()
        self.stop_calls = 0

    def stop(self, lifecycle):
        self.stop_calls += 1
        if self.stop_calls == 1:
            return ProcessStopEvidence(
                lifecycle.job_id, lifecycle.generation, False, "descendants-still-running"
            )
        self.running = False
        self.output.put(None)  # release the parked reader thread
        return ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, True, "stopped")


_REVISION = "b" * 64
_OTHER_REVISION = "c" * 64


class _NonZeroExitProcess(FakeProcess):
    def exit_code(self):
        return None if self.running else 3


def _completion_command(launch_id: str, revision: str = _REVISION, **overrides) -> SetupCompletionCommand:
    values = {
        "launch_id": launch_id,
        "revision": revision,
        "scheduler_result": "manual-only",
        "all_questions_answered": True,
        "summary_delivered": True,
    }
    values.update(overrides)
    return SetupCompletionCommand(**values)


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


async def _connected_completion(manager: SetupSessionManager, root: Path, owner: str = "owner"):
    prepared = await manager.prepare_completion(
        owner, "copilot", root, root / "config.yaml", "http://127.0.0.1:8500"
    )
    launch = RuntimeLaunch(("copilot",), root, {}, "connected")
    await manager.start(owner, "copilot", launch, "fallback")
    return prepared


def test_completion_ready_configuration_without_acknowledgement_stays_pending(tmp_path: Path):
    async def exercise():
        fake = FakeProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake, sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            decision = manager.completion_decision("owner", _REVISION, True)
            assert decision.phase == "pending"
            assert decision.redirect_allowed is False
            assert decision.launch_id == prepared.launch_id
            assert fake.running is True
            assert manager.completion_decision("other", _REVISION, True).redirect_allowed is False
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_capability_is_private_and_bound_to_lifecycle(tmp_path: Path):
    async def exercise():
        manager = SetupSessionManager(process_factory=lambda launch: FakeProcess(), sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            assert len(prepared.token) >= 43
            assert prepared.token not in repr(prepared)
            assert len(prepared.launch_id) == 32
            assert prepared.origin == "http://127.0.0.1:8500"
            assert manager._session.lifecycle.generation == prepared.launch_id
            snapshot = manager.snapshot("owner")
            assert prepared.token not in repr(snapshot)
            assert not hasattr(snapshot, "token")
            assert prepared.token not in manager.completion_decision("owner", _REVISION, True).message
            assert manager.require_completion_token(prepared.token) == prepared.launch_id
            with pytest.raises(SetupSessionConflict) as bad:
                manager.require_completion_token(prepared.token + "x")
            assert prepared.token not in str(bad.value)
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_acknowledgement_completes_without_stopping_process(tmp_path: Path):
    async def exercise():
        fake = FakeProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake, sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            command = _completion_command(prepared.launch_id)
            decision = await manager.acknowledge_completion(prepared.token, command, _REVISION)
            assert decision.phase == "complete"
            assert decision.redirect_allowed is True
            assert fake.running is True
            assert manager.snapshot("owner").state == "running"
            assert manager.completion_decision("owner", _REVISION, True).redirect_allowed is True
            assert manager.completion_decision("owner", _REVISION, False).phase == "pending"
            assert manager.completion_decision("owner", _OTHER_REVISION, True).phase == "pending"
            assert manager.completion_decision("owner", None, True).redirect_allowed is False
            assert manager.completion_decision("other", _REVISION, True).redirect_allowed is False
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_acknowledgement_rejects_mismatches(tmp_path: Path):
    async def exercise():
        manager = SetupSessionManager(process_factory=lambda launch: FakeProcess(), sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            good = _completion_command(prepared.launch_id)
            for token, command, revision in (
                (prepared.token + "x", good, _REVISION),
                ("", good, _REVISION),
                (prepared.token, _completion_command("a" * 32), _REVISION),
                (prepared.token, good, _OTHER_REVISION),
                (prepared.token, _completion_command(prepared.launch_id, _OTHER_REVISION), _REVISION),
            ):
                with pytest.raises(SetupSessionConflict) as error:
                    await manager.acknowledge_completion(token, command, revision)
                assert prepared.token not in str(error.value)
            assert manager.completion_decision("owner", _REVISION, True).redirect_allowed is False
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_duplicate_acknowledgement_is_idempotent(tmp_path: Path):
    async def exercise():
        manager = SetupSessionManager(process_factory=lambda launch: FakeProcess(), sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            command = _completion_command(prepared.launch_id)
            first = await manager.acknowledge_completion(prepared.token, command, _REVISION)
            second = await manager.acknowledge_completion(prepared.token, command, _REVISION)
            assert first == second
            assert second.redirect_allowed is True
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_changed_revision_requires_new_acknowledgement(tmp_path: Path):
    async def exercise():
        manager = SetupSessionManager(process_factory=lambda launch: FakeProcess(), sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            await manager.acknowledge_completion(
                prepared.token, _completion_command(prepared.launch_id), _REVISION
            )
            assert manager.completion_decision("owner", _OTHER_REVISION, True).phase == "pending"
            renewed = await manager.acknowledge_completion(
                prepared.token, _completion_command(prepared.launch_id, _OTHER_REVISION), _OTHER_REVISION
            )
            assert renewed.redirect_allowed is True
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_reattach_reuses_credential_and_rejects_other_selection(tmp_path: Path):
    async def exercise():
        manager = SetupSessionManager(process_factory=lambda launch: FakeProcess(), sweep_interval=0)
        try:
            first = await _connected_completion(manager, tmp_path)
            again = await manager.prepare_completion(
                "owner", "copilot", tmp_path, tmp_path / "config.yaml", "http://127.0.0.1:8500"
            )
            assert again == first
            assert again.token == first.token
            for owner, integration, root in (
                ("other", "copilot", tmp_path),
                ("owner", "codex", tmp_path),
                ("owner", "copilot", tmp_path / "elsewhere"),
            ):
                with pytest.raises(SetupSessionConflict):
                    await manager.prepare_completion(
                        owner, integration, root, tmp_path / "config.yaml", "http://127.0.0.1:8500"
                    )
            assert manager.require_completion_token(first.token) == first.launch_id
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_replacement_attempt_rejects_old_token(tmp_path: Path):
    async def exercise():
        processes = []

        def factory(launch):
            processes.append(FakeProcess())
            return processes[-1]

        manager = SetupSessionManager(process_factory=factory, sweep_interval=0)
        try:
            old = await _connected_completion(manager, tmp_path)
            assert (await manager.stop("owner")).confirmed is True
            new = await _connected_completion(manager, tmp_path)
            assert new.launch_id != old.launch_id
            assert new.token != old.token
            with pytest.raises(SetupSessionConflict):
                await manager.acknowledge_completion(
                    old.token, _completion_command(old.launch_id), _REVISION
                )
            with pytest.raises(SetupSessionConflict):
                await manager.acknowledge_completion(
                    old.token, _completion_command(new.launch_id), _REVISION
                )
            with pytest.raises(SetupSessionConflict):
                await manager.acknowledge_completion(
                    new.token, _completion_command(old.launch_id), _REVISION
                )
            assert manager.completion_decision("owner", _REVISION, True).phase == "pending"
            decision = await manager.acknowledge_completion(
                new.token, _completion_command(new.launch_id), _REVISION
            )
            assert decision.redirect_allowed is True
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("acknowledge_first", [False, True])
def test_completion_stop_is_cancelled_and_cannot_be_acknowledged(tmp_path: Path, acknowledge_first):
    async def exercise():
        manager = SetupSessionManager(process_factory=lambda launch: FakeProcess(), sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            command = _completion_command(prepared.launch_id)
            if acknowledge_first:
                await manager.acknowledge_completion(prepared.token, command, _REVISION)
            assert (await manager.stop("owner")).confirmed is True
            decision = manager.completion_decision("owner", _REVISION, True)
            assert decision.phase == "cancelled"
            assert decision.redirect_allowed is False
            assert manager.snapshot("owner").state == "stopped"
            with pytest.raises(SetupSessionConflict):
                await manager.acknowledge_completion(prepared.token, command, _REVISION)
            with pytest.raises(SetupSessionConflict):
                manager.require_completion_token(prepared.token)
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_natural_exit_without_acknowledgement_requires_attention(tmp_path: Path):
    async def exercise():
        fake = FakeProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake, sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            fake.output.put(None)
            await _until(lambda: manager.snapshot("owner").state == "exited")
            decision = manager.completion_decision("owner", _REVISION, True)
            assert decision.phase == "attention"
            assert decision.redirect_allowed is False
            with pytest.raises(SetupSessionConflict):
                await manager.acknowledge_completion(
                    prepared.token, _completion_command(prepared.launch_id), _REVISION
                )
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_unconfirmed_stop_is_not_success(tmp_path: Path):
    async def exercise():
        fake = _UnconfirmedThenConfirmedProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake, sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            await manager.acknowledge_completion(
                prepared.token, _completion_command(prepared.launch_id), _REVISION
            )
            assert (await manager.stop("owner")).confirmed is False
            decision = manager.completion_decision("owner", _REVISION, True)
            assert decision.redirect_allowed is False
            assert decision.phase in {"attention", "cancelled"}
            assert (await manager.stop("owner")).confirmed is True
            assert manager.completion_decision("owner", _REVISION, True).redirect_allowed is False
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_verified_exit_waits_for_confirmed_natural_exit(tmp_path: Path):
    async def exercise():
        fake = FakeProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake, sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            manager._completion.exit_capability_verified = True
            decision = await manager.acknowledge_completion(
                prepared.token, _completion_command(prepared.launch_id), _REVISION
            )
            assert decision.phase == "acknowledged"
            assert decision.redirect_allowed is False
            fake.output.put(None)
            await _until(lambda: manager.snapshot("owner").state == "exited")
            decision = manager.completion_decision("owner", _REVISION, True)
            assert decision.phase == "complete"
            assert decision.redirect_allowed is True
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("verified", [False, True])
def test_completion_non_zero_exit_after_acknowledgement_is_not_success(tmp_path: Path, verified):
    async def exercise():
        fake = _NonZeroExitProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake, sweep_interval=0)
        try:
            prepared = await _connected_completion(manager, tmp_path)
            manager._completion.exit_capability_verified = verified
            await manager.acknowledge_completion(
                prepared.token, _completion_command(prepared.launch_id), _REVISION
            )
            fake.output.put(None)
            await _until(lambda: manager.snapshot("owner").state == "exited")
            decision = manager.completion_decision("owner", _REVISION, True)
            assert decision.phase == "attention"
            assert decision.redirect_allowed is False
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_cancelled_spawn_cancels_attempt(tmp_path: Path):
    async def exercise():
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        fake = FakeProcess()

        def factory(launch):
            entered.put(True)
            release.wait(5)
            return fake

        manager = SetupSessionManager(process_factory=factory, sweep_interval=0)
        try:
            prepared = await manager.prepare_completion(
                "owner", "copilot", tmp_path, tmp_path / "config.yaml", "http://127.0.0.1:8500"
            )
            launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
            starting = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
            await asyncio.to_thread(entered.get, True, 2)
            starting.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await starting
            assert manager.completion_decision("owner", _REVISION, True).phase == "cancelled"
            with pytest.raises(SetupSessionConflict):
                await manager.acknowledge_completion(
                    prepared.token, _completion_command(prepared.launch_id), _REVISION
                )
        finally:
            release.set()
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_external_attempt_uses_explicit_fallback(tmp_path: Path):
    async def exercise():
        manager = SetupSessionManager(process_factory=lambda launch: FakeProcess(), sweep_interval=0)
        try:
            prepared = await manager.prepare_completion(
                "owner", "codex", tmp_path, tmp_path / "config.yaml", "http://127.0.0.1:8500"
            )
            assert await manager._launch_external("owner", "codex", tmp_path, lambda: "launched") == "launched"
            assert manager.snapshot("owner") is None
            assert manager.completion_decision("owner", _REVISION, True).phase == "pending"
            with pytest.raises(SetupSessionConflict):
                await manager._launch_external("other", "codex", tmp_path, lambda: "launched")
            with pytest.raises(SetupSessionConflict):
                await manager.prepare_completion(
                    "other", "codex", tmp_path, tmp_path / "config.yaml", "http://127.0.0.1:8500"
                )
            manager._completion.exit_capability_verified = True
            decision = await manager.acknowledge_completion(
                prepared.token, _completion_command(prepared.launch_id), _REVISION
            )
            assert decision.phase == "complete"
            assert decision.redirect_allowed is True
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_completion_without_preparation_is_pending(tmp_path: Path):
    async def exercise():
        manager = SetupSessionManager(process_factory=lambda launch: FakeProcess(), sweep_interval=0)
        try:
            decision = manager.completion_decision("owner", _REVISION, True)
            assert decision.launch_id is None
            assert decision.phase == "pending"
            assert decision.redirect_allowed is False
            with pytest.raises(SetupSessionConflict):
                manager.require_completion_token("anything")
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_reuses_owner_and_transfers_single_writer(tmp_path: Path):
    async def exercise():
        fake = FakeProcess()
        launches = []

        def start(launch):
            launches.append(launch)
            return fake

        manager = SetupSessionManager(process_factory=start)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        first = await manager.start("owner", "copilot", launch, "copilot -i setup")
        repeated = await manager.start("owner", "copilot", launch, "copilot -i setup")
        assert first.state == repeated.state == "running"
        assert len(launches) == 1
        _, old_id, _ = await manager.attach("owner")
        _, new_id, _ = await manager.attach("owner")
        with pytest.raises(SetupSessionConflict):
            await manager.send_input("owner", old_id, b"no")
        await manager.send_input("owner", new_id, b"yes\r")
        await manager.resize("owner", new_id, 30, 100)
        assert fake.writes == [b"yes\r"]
        assert fake.sizes == [(30, 100)]
        with pytest.raises(SetupSessionConflict):
            await manager.start("other", "copilot", launch, "copilot -i setup")
        await manager.shutdown()
        assert fake.running is False

    asyncio.run(exercise())


def test_setup_session_rejects_owner_relaunch_with_different_selection(tmp_path: Path):
    async def exercise():
        launches: list[RuntimeLaunch] = []

        def start(launch):
            launches.append(launch)
            return FakeProcess()

        manager = SetupSessionManager(process_factory=start)
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()
        launch_a = RuntimeLaunch(("copilot",), root_a, {}, "connected")
        original = await manager.start("owner", "copilot", launch_a, "copilot -i setup")
        assert original.state == "running"
        assert len(launches) == 1

        # Same owner, different root while running: an explicit Stop is required;
        # the manager must not silently reattach to the old root nor spawn again.
        launch_b = RuntimeLaunch(("copilot",), root_b, {}, "connected")
        with pytest.raises(SetupSessionConflict) as different_root:
            await manager.start("owner", "copilot", launch_b, "copilot -i setup")
        assert "stop" in str(different_root.value).lower()

        # Same owner, different integration while running: same rule.
        with pytest.raises(SetupSessionConflict) as different_integration:
            await manager.start("owner", "codex", launch_a, "copilot -i setup")
        assert "stop" in str(different_integration.value).lower()

        assert len(launches) == 1  # no second process was spawned
        snapshot = manager.snapshot("owner")
        assert snapshot.state == "running"
        assert snapshot.data_root == root_a
        assert snapshot.integration_name == "copilot"
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_bounds_output_and_expires_when_idle(tmp_path: Path):
    async def exercise():
        clock = [0.0]
        fake = FakeProcess()
        manager = SetupSessionManager(
            process_factory=lambda launch: fake, now=lambda: clock[0],
            replay_limit=8, client_limit=4,
        )
        await manager.start("owner", "copilot", RuntimeLaunch(("copilot",), tmp_path, {}, "connected"), "fallback")
        _, connection_id, pending = await manager.attach("owner")
        fake.output.put(b"abcdefghij")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1
        while manager.snapshot("owner").output != b"cdefghij":
            assert loop.time() < deadline
            await asyncio.sleep(0.01)
        assert manager.snapshot("owner").truncated is True
        assert await asyncio.wait_for(pending.get(), timeout=1) is None
        assert fake.running is True
        clock[0] = 3601
        await manager.enforce_limits()
        assert manager.snapshot("owner").state == "stopped"
        await manager.detach("owner", connection_id)
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_expires_after_four_hours(tmp_path: Path):
    async def exercise():
        clock = [0.0]
        fake = FakeProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake, now=lambda: clock[0])
        await manager.start("owner", "copilot", RuntimeLaunch(("copilot",), tmp_path, {}, "connected"), "fallback")
        _, connection_id, _ = await manager.attach("owner")
        clock[0] = 14399
        await manager.send_input("owner", connection_id, b"stay alive\r")
        await manager.enforce_limits()
        assert manager.snapshot("owner").state == "running"
        clock[0] = 14401
        await manager.enforce_limits()
        assert manager.snapshot("owner").state == "stopped"
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_blocks_slot_when_stop_unconfirmed(tmp_path: Path):
    async def exercise():
        class Unconfirmed(FakeProcess):
            def stop(self, lifecycle):
                self.running = False
                self.output.put(None)
                return ProcessStopEvidence(
                    lifecycle.job_id, lifecycle.generation, False, "descendants-still-running"
                )

        fake = Unconfirmed()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        await manager.start("owner", "copilot", launch, "fallback")
        evidence = await manager.stop("owner")
        assert evidence.confirmed is False
        assert manager.snapshot("owner").state == "failed"
        with pytest.raises(SetupSessionConflict):
            await manager.start("owner", "copilot", launch, "fallback")
        await manager.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("uncertain", [False, True])
def test_setup_session_external_launch_requires_confirmed_clear_slot(tmp_path: Path, uncertain):
    async def exercise():
        process = _UnconfirmedThenConfirmedProcess() if uncertain else FakeProcess()
        manager = SetupSessionManager(process_factory=lambda launch: process, sweep_interval=0)
        launched = []

        def external():
            launched.append("codex")
            return "external-launched"

        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        try:
            await manager.start("owner", "copilot", launch, "fallback")
            if uncertain:
                assert (await manager.stop("owner")).confirmed is False
            with pytest.raises(SetupSessionConflict):
                await manager._launch_external("other", "codex", tmp_path, external)
            assert launched == []
            assert (await manager.stop("owner")).confirmed is True
            assert await manager._launch_external("other", "codex", tmp_path, external) == "external-launched"
            assert launched == ["codex"]
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancelled", [False, True])
def test_setup_session_external_launch_holds_atomic_exclusion_until_settled(tmp_path: Path, cancelled):
    async def exercise():
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        connected = FakeProcess()
        connected_launches = []

        def factory(launch):
            connected_launches.append(launch)
            return connected

        def external():
            entered.put(True)
            release.wait(2)
            return "external-launched"

        manager = SetupSessionManager(process_factory=factory, sweep_interval=0)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        starting = None
        try:
            external_task = asyncio.create_task(manager._launch_external("owner", "codex", tmp_path, external))
            await asyncio.to_thread(entered.get, True, 1)
            if cancelled:
                external_task.cancel()
            starting = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
            await asyncio.sleep(0.05)
            assert not external_task.done()
            assert not starting.done()
            assert connected_launches == []
            release.set()
            if cancelled:
                with pytest.raises(asyncio.CancelledError):
                    await external_task
            else:
                assert await external_task == "external-launched"
            assert (await starting).state == "running"
            assert connected_launches == [launch]
        finally:
            release.set()
            if starting is not None:
                with contextlib.suppress(Exception):
                    await starting
            await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_holds_lifecycle_lock_across_spawn(tmp_path: Path):
    async def exercise():
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        calls: list[RuntimeLaunch] = []
        fake = FakeProcess()

        def factory(launch):
            calls.append(launch)
            entered.put(True)
            release.wait(2)
            return fake

        manager = SetupSessionManager(process_factory=factory)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
        await asyncio.to_thread(entered.get, True, 1)
        # A competing start must not spawn a replacement while the first launch is in flight.
        start2 = asyncio.create_task(manager.start("other", "copilot", launch, "fallback"))
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        assert not start1.done()
        assert not start2.done()
        release.set()
        first = await start1
        assert first.state == "running"
        with pytest.raises(SetupSessionConflict):
            await start2
        assert len(calls) == 1
        await manager.shutdown()
        assert fake.running is False

    asyncio.run(exercise())


def test_setup_session_shutdown_settles_in_flight_start(tmp_path: Path):
    async def exercise():
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        fake = FakeProcess()

        def factory(launch):
            entered.put(True)
            release.wait(2)
            return fake

        manager = SetupSessionManager(process_factory=factory)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
        await asyncio.to_thread(entered.get, True, 1)
        shutdown = asyncio.create_task(manager.shutdown())
        await asyncio.sleep(0.05)
        assert not shutdown.done()
        assert fake.running is True
        release.set()
        await start1
        await shutdown
        assert fake.running is False
        assert manager.snapshot("owner").state == "stopped"

    asyncio.run(exercise())


def test_setup_session_cancelled_start_reaps_and_frees_slot(tmp_path: Path):
    async def exercise():
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        fakes: list[FakeProcess] = []

        def factory(launch):
            entered.put(True)
            release.wait(2)
            fake = FakeProcess()
            fakes.append(fake)
            return fake

        manager = SetupSessionManager(process_factory=factory)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
        await asyncio.to_thread(entered.get, True, 1)
        start1.cancel()
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await start1
        assert len(fakes) == 1
        assert fakes[0].running is False
        assert manager.snapshot("owner") is None
        snap = await manager.start("owner", "copilot", launch, "fallback")
        assert snap.state == "running"
        assert len(fakes) == 2
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_finalizes_on_empty_eof_read(tmp_path: Path):
    async def exercise():
        # The PTY adapter reports end-of-stream as an empty read (b""), not a
        # raised EOFError. The reader must treat that as terminal: confirm the
        # tree stopped, mark the session exited, close subscribers, and never
        # record the empty bytes as activity or spin on repeated empty reads.
        class EofProcess(FakeProcess):
            def __init__(self):
                super().__init__()
                self.reads = 0

            def read(self, size: int = 65536) -> bytes:
                self.reads += 1
                chunk = self.output.get()
                if chunk is None:
                    raise EOFError
                return chunk

        fake = EofProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        await manager.start("owner", "copilot", launch, "fallback")
        snap, connection_id, pending = await manager.attach("owner")
        assert snap.state == "running"
        reads_before = fake.reads
        fake.output.put(b"")  # real EOF contract: empty bytes, not a sentinel
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2
        try:
            while manager.snapshot("owner").state != "exited":
                assert loop.time() < deadline, "reader never finalized on empty EOF read"
                await asyncio.sleep(0.01)
            final = manager.snapshot("owner")
            assert final.state == "exited"
            assert final.exit_code == 0
            assert final.output == b""  # empty read is never recorded as output
            assert fake.reads == reads_before + 1  # no repeated empty-read loop
            assert fake.running is False  # confirmed stop of the whole tree
            assert await asyncio.wait_for(pending.get(), timeout=1) is None
        finally:
            if fake.running:
                fake.output.put(None)  # release any thread still blocked on read
            await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_attach_after_exit_yields_end_of_stream(tmp_path: Path):
    async def exercise():
        # A browser that reconnects to an already-exited session must be handed
        # the replay and a prompt end-of-stream. Finalization already notified —
        # and cleared — the prior subscribers, so a fresh queue would otherwise
        # never emit None and the Task 5 WebSocket would hang forever.
        fake = FakeProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        await manager.start("owner", "copilot", launch, "fallback")
        first_snap, _first_id, first_pending = await manager.attach("owner")
        assert first_snap.state == "running"
        fake.output.put(b"hello ")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2
        while manager.snapshot("owner").output != b"hello ":
            assert loop.time() < deadline
            await asyncio.sleep(0.01)
        fake.output.put(b"")  # natural EOF
        while manager.snapshot("owner").state != "exited":
            assert loop.time() < deadline
            await asyncio.sleep(0.01)
        # The original subscriber received its buffered bytes and then a closing
        # end-of-stream at finalization.
        drained: list[bytes | None] = []
        while True:
            item = await asyncio.wait_for(first_pending.get(), timeout=1)
            drained.append(item)
            if item is None:
                break
        assert drained[-1] is None
        assert b"".join(chunk for chunk in drained if chunk) == b"hello "
        # Reconnecting must not hang: end-of-stream arrives promptly and replay
        # is still available through the snapshot.
        snap, connection_id, pending = await manager.attach("owner")
        assert snap.state == "exited"
        assert snap.output == b"hello "
        assert await asyncio.wait_for(pending.get(), timeout=1) is None
        # No writer is granted on an exited session.
        with pytest.raises(SetupSessionConflict):
            await manager.send_input("owner", connection_id, b"x")
        await manager.detach("owner", connection_id)
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_natural_exit_wins_finalization_race(tmp_path: Path):
    async def exercise():
        # EOF (the child exiting on its own) reaches the reader first, but an
        # explicit Stop then races into finalization. The coherent outcome is a
        # natural exit with a real exit code, not a user abort with exit_code
        # None. A barrier on the fake's first Stop makes the interleaving
        # deterministic: the reader observes EOF and enters its natural-exit
        # Stop (blocked), and only then does the explicit Stop win the race.
        class RacingProcess(FakeProcess):
            def __init__(self):
                super().__init__()
                self.stop_calls = 0
                self.first_stop_entered = threading.Event()
                self.release_first_stop = threading.Event()

            def read(self, size: int = 65536) -> bytes:
                chunk = self.output.get()
                if chunk is None:
                    self.running = False  # child exited naturally
                    raise EOFError
                return chunk

            def stop(self, lifecycle):
                self.stop_calls += 1
                if self.stop_calls == 1:
                    self.first_stop_entered.set()
                    self.release_first_stop.wait(2)
                self.running = False
                return ProcessStopEvidence(
                    lifecycle.job_id, lifecycle.generation, True, "stopped"
                )

        fake = RacingProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        await manager.start("owner", "copilot", launch, "fallback")
        await manager.attach("owner")
        fake.output.put(None)  # EOF: reader enters natural-exit Stop, blocks
        await asyncio.to_thread(fake.first_stop_entered.wait, 2)
        stop_task = asyncio.create_task(manager.stop("owner"))
        await asyncio.sleep(0.05)  # let the explicit Stop win finalization
        fake.release_first_stop.set()
        await stop_task
        final = manager.snapshot("owner")
        assert final.state == "exited"
        assert final.exit_code == 0
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_rejects_input_after_eof_before_finalize(tmp_path: Path):
    async def exercise():
        # Once the reader observes EOF it sets eof_seen=True and then blocks in
        # the (still-running) explicit Stop. During that window state is still
        # "running", so a writer must not be able to push input or resize into a
        # child that has already gone away.
        class EofBlockingProcess(FakeProcess):
            def __init__(self):
                super().__init__()
                self.stop_entered = threading.Event()
                self.release_stop = threading.Event()

            def read(self, size: int = 65536) -> bytes:
                chunk = self.output.get()
                if chunk is None:
                    self.running = False  # child exited naturally
                    raise EOFError
                return chunk

            def stop(self, lifecycle):
                self.stop_entered.set()
                self.release_stop.wait(2)
                self.running = False
                return ProcessStopEvidence(
                    lifecycle.job_id, lifecycle.generation, True, "stopped"
                )

        fake = EofBlockingProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        try:
            await manager.start("owner", "copilot", launch, "fallback")
            _, writer_id, _ = await manager.attach("owner")
            fake.output.put(None)  # EOF: reader sets eof_seen, blocks in Stop
            await asyncio.to_thread(fake.stop_entered.wait, 2)
            # eof_seen is True and state is still "running" here.
            with pytest.raises(SetupSessionConflict):
                await manager.send_input("owner", writer_id, b"late")
            with pytest.raises(SetupSessionConflict):
                await manager.resize("owner", writer_id, 30, 100)
            assert fake.writes == []
            assert fake.sizes == []
        finally:
            fake.release_stop.set()  # unblock the parked Stop before teardown
            await manager.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancelled", [False, True])
def test_setup_session_failed_start_retains_cleanup_for_public_stop_retry(tmp_path: Path, cancelled):
    async def exercise():
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        cleanup = _UnconfirmedThenConfirmedProcess()
        replacement = FakeProcess()
        calls = 0

        def factory(launch):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.put(True)
                release.wait(2)
                raise ConnectedLaunchError("failed native launch", cleanup_confirmed=False, _cleanup=cleanup)
            return replacement

        manager = SetupSessionManager(process_factory=factory, sweep_interval=0)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        try:
            starting = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
            await asyncio.to_thread(entered.get, True, 1)
            _, _, pending = await manager.attach("owner")
            if cancelled:
                starting.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError if cancelled else ConnectedLaunchError):
                await starting

            assert manager.snapshot("owner").state == "failed"
            assert await asyncio.wait_for(pending.get(), timeout=1) is None
            first = await manager.stop("owner")
            assert first.confirmed is False
            with pytest.raises(SetupSessionConflict):
                await manager.start("other", "codex", launch, "fallback")
            assert calls == 1

            second = await manager.stop("owner")
            assert second.confirmed is True
            assert cleanup.running is False
            assert manager.snapshot("owner").state == "stopped"
            assert (await manager.start("other", "codex", launch, "fallback")).state == "running"
            assert calls == 2
        finally:
            release.set()
            await manager.shutdown()
            cleanup.stop(manager._session.lifecycle)

    asyncio.run(exercise())


def test_setup_session_owner_stop_retries_unconfirmed_until_confirmed(tmp_path: Path):
    async def exercise():
        # A Stop that cannot confirm the whole tree is gone must block the slot
        # *without* wedging it forever: a later explicit owner Stop retries
        # process.stop under the same lifecycle lock and, only once the tree is
        # proven gone, records `stopped` and frees the slot for a replacement.
        fake = _UnconfirmedThenConfirmedProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        try:
            await manager.start("owner", "copilot", launch, "fallback")
            first = await manager.stop("owner")
            assert first.confirmed is False
            assert manager.snapshot("owner").state == "failed"
            assert fake.stop_calls == 1
            # Refused while the previous tree is not proven gone.
            with pytest.raises(SetupSessionConflict):
                await manager.start("owner", "copilot", launch, "fallback")
            # An explicit owner Stop retries and now proves the tree gone.
            second = await manager.stop("owner")
            assert second.confirmed is True
            assert fake.stop_calls == 2
            assert manager.snapshot("owner").state == "stopped"
            # Only confirmed evidence frees the slot for a replacement.
            replacement = await manager.start("owner", "copilot", launch, "fallback")
            assert replacement.state == "running"
        finally:
            if fake.running:
                fake.output.put(None)  # release any parked reader thread
            await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_shutdown_retries_unconfirmed_stop(tmp_path: Path):
    async def exercise():
        # shutdown() must also retry an earlier unconfirmed cleanup before it
        # returns, rather than skipping a still-finalized-but-unproven session
        # and leaving an owned tree behind.
        fake = _UnconfirmedThenConfirmedProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        try:
            await manager.start("owner", "copilot", launch, "fallback")
            first = await manager.stop("owner")
            assert first.confirmed is False
            assert manager.snapshot("owner").state == "failed"
            assert fake.stop_calls == 1
            # Refused while the previous tree is not proven gone.
            with pytest.raises(SetupSessionConflict):
                await manager.start("owner", "copilot", launch, "fallback")
            await manager.shutdown()
            assert fake.stop_calls == 2  # shutdown retried the unconfirmed stop
            assert manager.snapshot("owner").state == "stopped"
            assert fake.running is False
        finally:
            if fake.running:
                fake.output.put(None)  # release any parked reader thread

    asyncio.run(exercise())


def test_setup_session_default_replay_budget_retains_2mib(tmp_path: Path):
    async def exercise():
        # The production default replay budget is 2 MiB, not 256 KiB. Output
        # larger than 256 KiB (the per-client budget) must survive untruncated,
        # while output beyond 2 MiB is trimmed to the last 2 MiB.
        fake = FakeProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        try:
            await manager.start(
                "owner", "copilot", RuntimeLaunch(("copilot",), tmp_path, {}, "connected"), "fallback"
            )
            loop = asyncio.get_running_loop()

            async def wait_for_len(expected: int) -> None:
                deadline = loop.time() + 2
                while len(manager.snapshot("owner").output) != expected:
                    assert loop.time() < deadline
                    await asyncio.sleep(0.005)

            big = b"a" * (300 * 1024)  # 300 KiB > 256 KiB per-client budget
            fake.output.put(big)
            await wait_for_len(len(big))
            assert manager.snapshot("owner").truncated is False

            overflow = b"b" * (2 * 1024 * 1024)  # push total beyond the 2 MiB budget
            fake.output.put(overflow)
            await wait_for_len(2 * 1024 * 1024)
            assert manager.snapshot("owner").truncated is True
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_records_sanitized_reader_failure_message(tmp_path: Path):
    async def exercise():
        class ReadFailureProcess(FakeProcess):
            def read(self, size: int = 65536) -> bytes:
                del size
                raise OSError("PTY output failed")

            def stop(self, lifecycle):
                self.running = False
                return ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, True, "stopped")

        fake = ReadFailureProcess()
        manager = SetupSessionManager(process_factory=lambda launch: fake)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        await manager.start("owner", "copilot", launch, "fallback")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2
        try:
            while manager.snapshot("owner").state != "exited":
                assert loop.time() < deadline
                await asyncio.sleep(0.01)
            snapshot = manager.snapshot("owner")
            assert snapshot.message == "PTY output failed"
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_cancelled_start_with_unconfirmed_error_blocks_slot(tmp_path: Path):
    async def exercise():
        # A cancelled Start whose shielded spawn then raises an *unconfirmed*
        # ConnectedLaunchError must fail closed: keep the session blocked as
        # `failed` and refuse a fresh Start, exactly like the normal spawn path.
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()

        def factory(launch):
            entered.put(True)
            release.wait(2)
            raise ConnectedLaunchError("launch aborted mid-cleanup", cleanup_confirmed=False)

        manager = SetupSessionManager(process_factory=factory)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
        await asyncio.to_thread(entered.get, True, 1)
        start1.cancel()
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await start1
        snapshot = manager.snapshot("owner")
        assert snapshot is not None
        assert snapshot.state == "failed"
        with pytest.raises(SetupSessionConflict):
            await manager.start("owner", "copilot", launch, "fallback")
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_cancelled_start_unconfirmed_stop_preserves_handle_for_retry(tmp_path: Path):
    async def exercise():
        # A cancelled Start whose shielded spawn *succeeds* but whose immediate
        # cleanup Stop is unconfirmed must retain the process handle and the
        # evidence, block the slot, and let a later owner Stop retry cleanup
        # under the same lifecycle lock until the tree is proven gone.
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        fake = _UnconfirmedThenConfirmedProcess()

        def factory(launch):
            entered.put(True)
            release.wait(2)
            return fake

        manager = SetupSessionManager(process_factory=factory)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        try:
            start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
            await asyncio.to_thread(entered.get, True, 1)
            start1.cancel()
            await asyncio.sleep(0.05)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await start1
            snapshot = manager.snapshot("owner")
            assert snapshot is not None
            assert snapshot.state == "failed"
            assert fake.stop_calls == 1  # the immediate reap Stop was unconfirmed
            with pytest.raises(SetupSessionConflict):
                await manager.start("owner", "copilot", launch, "fallback")
            # The preserved handle lets an explicit owner Stop retry and confirm.
            evidence = await manager.stop("owner")
            assert evidence.confirmed is True
            assert fake.stop_calls == 2
            assert manager.snapshot("owner").state == "stopped"
            replacement = await manager.start("owner", "copilot", launch, "fallback")
            assert replacement.state == "running"
        finally:
            if fake.running:
                fake.output.put(None)  # release any parked reader thread
            await manager.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("structured", [False, True])
def test_setup_session_failed_start_diagnostics_do_not_expose_private_exception_detail(tmp_path: Path, structured):
    async def exercise():
        def factory(launch):
            if structured:
                raise ConnectedLaunchError("private launch detail", cleanup_confirmed=False)
            raise RuntimeError("private launch detail")

        manager = SetupSessionManager(process_factory=factory, sweep_interval=0)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        try:
            with pytest.raises(ConnectedLaunchError if structured else RuntimeError):
                await manager.start("owner", "copilot", launch, "fallback")

            snapshot = manager.snapshot("owner")
            assert snapshot.state == "failed"
            assert "private launch detail" not in snapshot.message
            assert "cleanup could not be confirmed" in snapshot.message
            assert (await manager.stop("owner")).confirmed is False
        finally:
            await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_failed_spawn_releases_starting_subscriber(tmp_path: Path):
    async def exercise():
        # A subscriber that attached while the session was still `starting`
        # must be released (queue receives None) when a clean spawn failure
        # frees the slot, so no WebSocket writer lingers on a dead session.
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()

        def factory(launch):
            entered.put(True)
            release.wait(2)
            raise ConnectedLaunchError("no pty backend", cleanup_confirmed=True)

        manager = SetupSessionManager(process_factory=factory)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
        await asyncio.to_thread(entered.get, True, 1)
        snapshot, _connection_id, pending = await manager.attach("owner")
        assert snapshot.state == "starting"
        release.set()
        with pytest.raises(ConnectedLaunchError):
            await start1
        assert await asyncio.wait_for(pending.get(), timeout=1) is None
        assert manager.snapshot("owner") is None  # clean failure frees the slot
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_unproven_spawn_error_blocks_slot_and_releases_subscriber(tmp_path: Path):
    async def exercise():
        # An arbitrary exception from the spawn (not a confirmed-clean
        # ConnectedLaunchError) is no proof the process tree was cleaned up.
        # It must fail closed exactly like an unconfirmed ConnectedLaunchError:
        # keep the slot blocked as `failed`, refuse a fresh Start, and still
        # release any subscriber that attached while `starting`.
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        calls = 0

        def factory(launch):
            nonlocal calls
            calls += 1
            entered.put(True)
            release.wait(2)
            raise RuntimeError("unexpected after possible spawn")

        manager = SetupSessionManager(process_factory=factory)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
        await asyncio.to_thread(entered.get, True, 1)
        snapshot, _connection_id, pending = await manager.attach("owner")
        assert snapshot.state == "starting"
        release.set()
        with pytest.raises(RuntimeError):
            await start1
        assert await asyncio.wait_for(pending.get(), timeout=1) is None
        failed = manager.snapshot("owner")
        assert failed is not None
        assert failed.state == "failed"
        assert failed.message
        evidence = await manager.stop("owner")
        assert evidence.confirmed is False
        assert manager.snapshot("owner").state == "failed"
        with pytest.raises(SetupSessionConflict):
            await manager.start("owner", "copilot", launch, "fallback")
        assert calls == 1  # blocked slot refuses a second factory call
        await manager.shutdown()

    asyncio.run(exercise())


def test_setup_session_confirmed_cancel_releases_starting_subscriber(tmp_path: Path):
    async def exercise():
        # A subscriber that attached during `starting` must also be released
        # when a cancelled Start confirms its cleanup and frees the slot.
        release = threading.Event()
        entered: queue.Queue[bool] = queue.Queue()
        fakes: list[FakeProcess] = []

        def factory(launch):
            entered.put(True)
            release.wait(2)
            fake = FakeProcess()
            fakes.append(fake)
            return fake

        manager = SetupSessionManager(process_factory=factory)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
        await asyncio.to_thread(entered.get, True, 1)
        _snapshot, _connection_id, pending = await manager.attach("owner")
        start1.cancel()
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await start1
        assert await asyncio.wait_for(pending.get(), timeout=1) is None
        assert manager.snapshot("owner") is None  # confirmed clean cancel frees the slot
        assert fakes[0].running is False
        await manager.shutdown()

    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "nt", reason="Windows connected startup cancellation is Windows-specific")
def test_windows_external_launch_retains_global_job_cleanup_for_public_stop(tmp_path: Path, monkeypatch):
    import pywintypes
    import win32con
    import win32job
    import flowgency.jobs.windows_job as windows_job
    from tests.test_windows_job import _close_all, _make_std_pipes, _wait_for_known_exit

    pipes = _make_std_pipes()
    native_python, native_env = _native_python_launch()
    original_query = windows_job._job_exit_status
    original_process = None

    def fail_assign(job, process):
        raise pywintypes.error(5, "AssignProcessToJobObject", "denied")

    async def exercise():
        manager = SetupSessionManager(sweep_interval=0)
        launched = []

        def external():
            launched.append("codex")
            return "external-launched"

        try:
            with pytest.raises(ConnectedLaunchError):
                await manager._launch_external("owner", "codex", tmp_path, external)
            assert launched == []
            assert manager.snapshot("owner").state == "failed"
            assert (await manager.stop("owner")).confirmed is False
            with pytest.raises(SetupSessionConflict):
                await manager._launch_external("owner", "codex", tmp_path, external)

            monkeypatch.setattr(windows_job, "_job_exit_status", original_query)
            assert (await manager.stop("owner")).confirmed is True
            _wait_for_known_exit(original_process, timeout=1.0)
            assert windows_job._uncertain == []
            assert await manager._launch_external("owner", "codex", tmp_path, external) == "external-launched"
            assert launched == ["codex"]
        finally:
            monkeypatch.setattr(windows_job, "_job_exit_status", original_query)
            await manager.shutdown()

    monkeypatch.setattr(windows_job, "_job_exit_status", lambda job, deadline: "unknown")
    try:
        with monkeypatch.context() as patched:
            patched.setattr(win32job, "AssignProcessToJobObject", fail_assign)
            with pytest.raises(windows_job.WindowsJobLaunchError) as raised:
                windows_job.WindowsJobOwner.launch(
                    (native_python, "-c", "pass"), tmp_path, native_env, int(pipes[0]), int(pipes[3])
                )
            assert raised.value.cleanup_confirmed is False
        original_process = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.SYNCHRONIZE, False, raised.value._cleanup_owners[0].pid
        )
        asyncio.run(exercise())
    finally:
        monkeypatch.setattr(windows_job, "_job_exit_status", original_query)
        windows_job._retry_uncertain(time.monotonic() + 5.0)
        if original_process is not None:
            _close_all(original_process)
        _close_all(*pipes)


@pytest.mark.skipif(os.name != "nt", reason="Windows connected startup cancellation is Windows-specific")
@pytest.mark.parametrize("external", [False, True])
def test_windows_setup_session_recovers_global_failed_launch_ownership_through_stop(tmp_path: Path, monkeypatch, external):
    import flowgency.jobs.connected_process as connected_process
    import flowgency.jobs.windows_job as windows_job
    from flowgency.jobs.windows_connected_process import WindowsConnectedProcess
    from flowgency.jobs.windows_conpty import WindowsConPTY
    from flowgency.jobs.windows_job import WindowsJobOwner

    native_python, native_env = _native_python_launch()
    launch = RuntimeLaunch((native_python, "-u", "-c", "pass"), tmp_path, native_env, "connected")
    original_query = windows_job._job_exit_status
    original_wait = WindowsConPTY.wait_closed
    blocked_console = True

    def wait_closed(terminal, deadline):
        if blocked_console:
            return False
        return original_wait(terminal, deadline)

    async def exercise():
        nonlocal blocked_console
        manager = SetupSessionManager(sweep_interval=0)
        try:
            with pytest.raises(ConnectedLaunchError):
                WindowsConnectedProcess.spawn(launch, rows=24, cols=80)
            with pytest.raises(ConnectedLaunchError):
                if external:
                    await manager._launch_external("owner", "codex", tmp_path, lambda: pytest.fail("unsafe external launch"))
                else:
                    await manager.start("owner", "copilot", launch, "fallback")

            assert manager.snapshot("owner").state == "failed"
            assert (await manager.stop("owner")).confirmed is False
            monkeypatch.setattr(windows_job, "_job_exit_status", original_query)
            assert (await manager.stop("owner")).confirmed is False
            assert manager.snapshot("owner").state == "failed"
            with pytest.raises(SetupSessionConflict):
                await manager.start("other", "codex", launch, "fallback")

            blocked_console = False
            assert (await manager.stop("owner")).confirmed is True
            assert manager.snapshot("owner").state == "stopped"
            assert windows_job._uncertain == []
            connected_process._retry_unconfirmed()
        finally:
            blocked_console = False
            monkeypatch.setattr(windows_job, "_job_exit_status", original_query)
            monkeypatch.setattr(WindowsConPTY, "wait_closed", original_wait)
            await manager.shutdown()
            windows_job._retry_uncertain(time.monotonic() + 5.0)
            connected_process._retry_unconfirmed()

    monkeypatch.setattr(connected_process, "_STOP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(WindowsJobOwner, "contains", lambda self, pid: False)
    monkeypatch.setattr(windows_job, "_job_exit_status", lambda job, deadline: "unknown")
    monkeypatch.setattr(WindowsConPTY, "wait_closed", wait_closed)
    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "nt", reason="Windows connected startup cancellation is Windows-specific")
def test_windows_setup_session_cancelled_start_before_ready_confirms_cleanup(tmp_path: Path, monkeypatch):
    from flowgency.jobs.connected_process import start_connected_process
    from flowgency.jobs.windows_job import WindowsJobOwner

    native_python, native_env = _native_python_launch()
    child_pid_path = tmp_path / "cancel-child.pid"
    launch_started = tmp_path / "launch-started.txt"
    release_launch = tmp_path / "release-launch.txt"
    child_script = _write_script(
        tmp_path / "cancel_child.py",
        (
            "import os, pathlib, sys, time\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(120)\n"
        ),
    )

    real_launch_conpty = WindowsJobOwner.launch_conpty

    def delayed_launch(argv, cwd, env, pseudoconsole):
        owner = real_launch_conpty(argv, cwd, env, pseudoconsole)
        launch_started.write_text("started", encoding="utf-8")
        while not release_launch.exists():
            time.sleep(0.02)
        return owner

    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(delayed_launch))

    async def exercise():
        manager = SetupSessionManager(process_factory=start_connected_process)
        launch = RuntimeLaunch(
            (native_python, "-u", str(child_script), str(child_pid_path)),
            tmp_path,
            native_env,
            "connected",
        )
        start1: asyncio.Task[object] | None = None
        primary_error: BaseException | None = None
        try:
            start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
            await asyncio.to_thread(_wait_for_text, launch_started)
            child_identity = read_process_identity(int(await asyncio.to_thread(_wait_for_text, child_pid_path)))
            assert child_identity is not None
            start1.cancel()
            await asyncio.sleep(0.05)
            release_launch.write_text("release", encoding="utf-8")
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(start1, timeout=15)
            assert manager.snapshot("owner") is None
            deadline = time.monotonic() + 5
            while process_identity_state(child_identity) == "alive" and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert process_identity_state(child_identity) != "alive"

            running = await asyncio.wait_for(manager.start("owner", "copilot", launch, "fallback"), timeout=15)
            assert running.state == "running"
            evidence = await asyncio.wait_for(manager.stop("owner"), timeout=15)
            assert evidence.confirmed is True
        except BaseException as error:
            primary_error = error
            raise
        finally:
            with contextlib.suppress(Exception):
                release_launch.write_text("release", encoding="utf-8")
            if start1 is not None and not start1.done():
                start1.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(start1, timeout=15)
            try:
                await asyncio.wait_for(manager.shutdown(), timeout=15)
            except Exception:
                if primary_error is None:
                    raise

    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "nt", reason="Windows connected startup cancellation is Windows-specific")
def test_windows_setup_session_cancelled_start_before_ready_blocks_on_unknown_accounting(
    tmp_path: Path, monkeypatch
):
    from flowgency.jobs.connected_process import start_connected_process
    import flowgency.jobs.windows_job as windows_job
    from flowgency.jobs.windows_job import WindowsJobOwner

    native_python, native_env = _native_python_launch()
    child_pid_path = tmp_path / "unknown-child.pid"
    launch_started = tmp_path / "unknown-launch-started.txt"
    release_launch = tmp_path / "unknown-release-launch.txt"
    child_script = _write_script(
        tmp_path / "unknown_child.py",
        (
            "import os, pathlib, sys, time\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(120)\n"
        ),
    )

    real_launch_conpty = WindowsJobOwner.launch_conpty

    def delayed_launch(argv, cwd, env, pseudoconsole):
        owner = real_launch_conpty(argv, cwd, env, pseudoconsole)
        launch_started.write_text("started", encoding="utf-8")
        while not release_launch.exists():
            time.sleep(0.02)
        return owner

    monkeypatch.setattr(WindowsJobOwner, "launch_conpty", staticmethod(delayed_launch))
    real_job_exit_status = windows_job._job_exit_status
    calls = {"count": 0}

    def unknown_once(job_handle, deadline: float):
        calls["count"] += 1
        if calls["count"] == 1:
            return "unknown"
        return real_job_exit_status(job_handle, deadline)

    monkeypatch.setattr(windows_job, "_job_exit_status", unknown_once)

    async def exercise():
        manager = SetupSessionManager(process_factory=start_connected_process)
        launch = RuntimeLaunch(
            (native_python, "-u", str(child_script), str(child_pid_path)),
            tmp_path,
            native_env,
            "connected",
        )
        start1: asyncio.Task[object] | None = None
        primary_error: BaseException | None = None
        try:
            start1 = asyncio.create_task(manager.start("owner", "copilot", launch, "fallback"))
            await asyncio.to_thread(_wait_for_text, launch_started)
            child_identity = read_process_identity(int(await asyncio.to_thread(_wait_for_text, child_pid_path)))
            assert child_identity is not None
            start1.cancel()
            await asyncio.sleep(0.05)
            release_launch.write_text("release", encoding="utf-8")
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(start1, timeout=15)
            snapshot = manager.snapshot("owner")
            assert snapshot is not None
            assert snapshot.state == "failed"
            with pytest.raises(SetupSessionConflict):
                await manager.start("owner", "copilot", launch, "fallback")
            deadline = time.monotonic() + 5
            while process_identity_state(child_identity) == "alive" and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert process_identity_state(child_identity) != "alive"
        except BaseException as error:
            primary_error = error
            raise
        finally:
            with contextlib.suppress(Exception):
                release_launch.write_text("release", encoding="utf-8")
            if start1 is not None and not start1.done():
                start1.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(start1, timeout=15)
            try:
                await asyncio.wait_for(manager.shutdown(), timeout=15)
            except Exception:
                if primary_error is None:
                    raise

    asyncio.run(exercise())
