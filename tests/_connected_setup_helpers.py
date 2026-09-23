"""Deterministic fake connected PTY used by setup-session and route tests."""

from __future__ import annotations

import queue

from flowgency.jobs.processes import ProcessStopEvidence, RuntimeProcessLifecycle


class FakeProcess:
    pid = 4321

    def __init__(self):
        self.output: queue.Queue[bytes | None] = queue.Queue()
        self.writes: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.running = True

    def read(self, size: int = 65536) -> bytes:
        chunk = self.output.get()
        if chunk is None:
            raise EOFError
        return chunk

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))

    def alive(self) -> bool:
        return self.running

    def exit_code(self) -> int | None:
        return None if self.running else 0

    def stop(self, lifecycle: RuntimeProcessLifecycle) -> ProcessStopEvidence:
        self.running = False
        self.output.put(None)
        return ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, True, "stopped")
