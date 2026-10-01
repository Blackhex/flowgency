from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest


def test_windows_conpty_module_imports_safely() -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    assert hasattr(windows_conpty, "native_conpty_available")


def test_create_suspended_conpty_process_uses_atomic_job_and_conpty_attributes(monkeypatch) -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    class FakeBindings:
        HANDLE = ctypes.c_void_p
        HPCON = ctypes.c_void_p
        DWORD = ctypes.c_uint32
        SIZE_T = ctypes.c_size_t
        BOOL = ctypes.c_int
        COORD = windows_conpty._COORD
        SECURITY_ATTRIBUTES = windows_conpty._SECURITY_ATTRIBUTES
        STARTUPINFOEXW = windows_conpty._STARTUPINFOEXW
        PROCESS_INFORMATION = windows_conpty._PROCESS_INFORMATION

        def __init__(self) -> None:
            self.initialized_sizes: list[int] = []
            self.attributes: dict[int, object] = {}
            self.create_process_calls: list[dict[str, object]] = []

        def InitializeProcThreadAttributeList(self, pointer, count, flags, size_ptr):
            del pointer, count, flags
            size_ptr._obj.value = 64
            self.initialized_sizes.append(64)
            return 1

        def UpdateProcThreadAttribute(self, pointer, flags, attribute, value, size, previous, return_size):
            del pointer, flags, previous, return_size
            if attribute == 0x0002000D:
                job_array = ctypes.cast(value, ctypes.POINTER(self.HANDLE))
                self.attributes[attribute] = int(job_array[0])
            elif attribute == 0x00020016:
                self.attributes[attribute] = int(value.value)
            else:
                self.attributes[attribute] = size
            return 1

        def DeleteProcThreadAttributeList(self, pointer):
            del pointer

        def CreateProcessW(
            self,
            application_name,
            command_line,
            process_attributes,
            thread_attributes,
            inherit_handles,
            creation_flags,
            environment,
            cwd,
            startup,
            process_info,
        ):
            del application_name, process_attributes, thread_attributes
            chars = []
            cursor = ctypes.cast(environment, ctypes.POINTER(ctypes.c_wchar))
            index = 0
            saw_nul = False
            while True:
                char = cursor[index]
                chars.append(char)
                if char == "\x00":
                    if saw_nul:
                        break
                    saw_nul = True
                else:
                    saw_nul = False
                index += 1
            self.create_process_calls.append(
                {
                    "command_line": ctypes.wstring_at(command_line),
                    "inherit_handles": bool(inherit_handles),
                    "creation_flags": int(creation_flags),
                    "environment": "".join(chars),
                    "cwd": cwd,
                    "startup_cb": startup._obj.StartupInfo.cb,
                    "startup_flags": startup._obj.StartupInfo.dwFlags,
                    "std_input": int(startup._obj.StartupInfo.hStdInput or 0),
                    "std_output": int(startup._obj.StartupInfo.hStdOutput or 0),
                    "std_error": int(startup._obj.StartupInfo.hStdError or 0),
                    "attribute_list": startup._obj.lpAttributeList,
                }
            )
            process_info._obj.hProcess = self.HANDLE(0x1111)
            process_info._obj.hThread = self.HANDLE(0x2222)
            process_info._obj.dwProcessId = 3333
            process_info._obj.dwThreadId = 4444
            return 1

    class FakeJobHandle:
        def __int__(self) -> int:
            return 0xABCD

    fake = FakeBindings()
    monkeypatch.setattr(windows_conpty, "_get_bindings", lambda: fake)

    result = windows_conpty.create_suspended_conpty_process(
        ("C:/Program Files/Python/python.exe", "-u", "script.py", "zażółć"),
        Path("C:/tmp/work"),
        {"FLOWGENCY_TEST": "žluťoučký"},
        0x1234,
        FakeJobHandle(),
    )

    assert result == (0x1111, 0x2222, 3333, 4444)
    assert fake.attributes[0x0002000D] == 0xABCD
    assert fake.attributes[0x00020016] == 0x1234
    assert len(fake.create_process_calls) == 1
    call = fake.create_process_calls[0]
    assert call["command_line"] == subprocess.list2cmdline(
        ["C:/Program Files/Python/python.exe", "-u", "script.py", "zażółć"]
    )
    assert call["inherit_handles"] is False
    assert call["creation_flags"] == 0x00000004 | 0x00080000 | 0x00000400
    assert call["environment"] == "FLOWGENCY_TEST=žluťoučký\x00\x00"
    assert str(call["cwd"]).replace("\\", "/") == "C:/tmp/work"
    assert call["startup_cb"] == ctypes.sizeof(windows_conpty._STARTUPINFOEXW)
    assert call["startup_flags"] & 0x00000100
    assert call["std_input"] == 0
    assert call["std_output"] == 0
    assert call["std_error"] == 0
    assert call["attribute_list"]


@pytest.mark.skipif(os.name != "nt", reason="ConPTY handle conversion is Windows-specific")
def test_job_handle_conversion_preserves_pywin32_handle() -> None:
    from flowgency.jobs.processes import create_kill_on_close_windows_job
    from flowgency.jobs.windows_conpty import handle_value

    job = create_kill_on_close_windows_job()
    try:
        assert handle_value(job) == int(job) != 0
    finally:
        job.Close()


def test_windows_conpty_read_write_resize_and_close_contract(monkeypatch) -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    class FakeBindings:
        HANDLE = ctypes.c_void_p
        HPCON = ctypes.c_void_p
        DWORD = ctypes.c_uint32
        COORD = windows_conpty._COORD

        def __init__(self) -> None:
            self.peek_mode = "idle"
            self.writes: list[bytes] = []
            self.resize_calls: list[tuple[int, int, int]] = []
            self.closed_handles: list[int] = []
            self.close_calls: list[int] = []
            self.close_gate = threading.Event()

        def PeekNamedPipe(self, handle, buffer, size, bytes_read, available, remaining):
            del handle, buffer, size, bytes_read, remaining
            if self.peek_mode == "broken":
                error = OSError("broken pipe")
                error.winerror = 109
                raise error
            available._obj.value = 0 if self.peek_mode == "idle" else 4
            return 1

        def ReadFile(self, handle, buffer, size, bytes_read, overlapped):
            del handle, size, overlapped
            ctypes.memmove(buffer, b"pong", 4)
            bytes_read._obj.value = 4
            return 1

        def WriteFile(self, handle, buffer, size, bytes_written, overlapped):
            del handle, overlapped
            self.writes.append(ctypes.string_at(buffer, size))
            bytes_written._obj.value = size
            return 1

        def ResizePseudoConsole(self, hpcon, coord):
            self.resize_calls.append((int(hpcon.value), int(coord.Y), int(coord.X)))
            return 0

        def CloseHandle(self, handle):
            self.closed_handles.append(int(handle.value))
            return 1

        def ClosePseudoConsole(self, hpcon):
            self.close_calls.append(int(hpcon.value))
            self.close_gate.wait(0.2)

    fake = FakeBindings()
    monkeypatch.setattr(windows_conpty, "_get_bindings", lambda: fake)

    terminal = windows_conpty.WindowsConPTY(24, 80)
    terminal._pseudoconsole = 0x1234
    terminal._input_write = 0x2001
    terminal._output_read = 0x2002
    terminal._input_read = 0x2003
    terminal._output_write = 0x2004

    assert terminal.read() is None
    fake.peek_mode = "data"
    assert terminal.read() == b"pong"
    fake.peek_mode = "broken"
    assert terminal.read() == b""

    terminal.write(b"ping")
    assert fake.writes == [b"ping"]

    terminal.resize(40, 100)
    assert fake.resize_calls == [(0x1234, 40, 100)]

    terminal.begin_close()
    terminal.begin_close()
    assert terminal.wait_closed(time.monotonic() + 0.01) is False
    fake.close_gate.set()
    assert terminal.wait_closed(time.monotonic() + 1.0) is True
    assert fake.close_calls == [0x1234]

    terminal.close_streams()
    assert fake.closed_handles == [0x2001, 0x2002, 0x2003, 0x2004]


def test_windows_conpty_wait_closed_requires_confirmed_close(monkeypatch) -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    class FakeBindings:
        HANDLE = ctypes.c_void_p
        HPCON = ctypes.c_void_p

        def __init__(self) -> None:
            self.close_calls: list[int] = []

        def ClosePseudoConsole(self, hpcon):
            self.close_calls.append(int(hpcon.value))
            raise RuntimeError("close failed")

    fake = FakeBindings()
    monkeypatch.setattr(windows_conpty, "_get_bindings", lambda: fake)

    terminal = windows_conpty.WindowsConPTY(24, 80)
    terminal._pseudoconsole = 0x1234

    terminal.begin_close()

    assert terminal.wait_closed(time.monotonic() + 1.0) is False
    assert terminal.wait_closed(time.monotonic() + 1.0) is False
    assert terminal.pseudoconsole == 0x1234
    assert fake.close_calls == [0x1234]


def test_windows_conpty_close_streams_retains_failed_handle_for_retry(monkeypatch) -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    class FakeBindings:
        HANDLE = ctypes.c_void_p

        def __init__(self) -> None:
            self.closed_handles: list[int] = []
            self.failures: set[int] = {0x2001}

        def CloseHandle(self, handle):
            value = int(handle.value)
            self.closed_handles.append(value)
            return 0 if value in self.failures else 1

    fake = FakeBindings()
    monkeypatch.setattr(windows_conpty, "_get_bindings", lambda: fake)

    terminal = windows_conpty.WindowsConPTY(24, 80)
    terminal._input_write = 0x2001
    terminal._output_read = 0x2002

    with pytest.raises(OSError):
        terminal.close_streams()

    assert terminal._input_write == 0x2001
    assert terminal._output_read == 0
    assert fake.closed_handles == [0x2001, 0x2002]

    fake.failures.clear()
    terminal.close_streams()

    assert terminal._input_write == 0
    assert terminal._output_read == 0
    assert fake.closed_handles == [0x2001, 0x2002, 0x2001]


def test_windows_conpty_open_retains_child_end_handles_until_explicit_close(monkeypatch) -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    class FakeBindings:
        HANDLE = ctypes.c_void_p
        HPCON = ctypes.c_void_p
        DWORD = ctypes.c_uint32
        BOOL = ctypes.c_int
        COORD = windows_conpty._COORD
        SECURITY_ATTRIBUTES = windows_conpty._SECURITY_ATTRIBUTES

        def __init__(self) -> None:
            self._pipe_values = iter((0x101, 0x102, 0x201, 0x202))
            self.closed_handles: list[int] = []

        def CreatePipe(self, read_handle, write_handle, security, size):
            del security, size
            read_handle._obj.value = next(self._pipe_values)
            write_handle._obj.value = next(self._pipe_values)
            return 1

        def SetHandleInformation(self, handle, mask, flags):
            del handle, mask, flags
            return 1

        def CreatePseudoConsole(self, coord, input_read, output_write, flags, hpcon):
            del coord, input_read, output_write, flags
            hpcon._obj.value = 0x999
            return 0

        def CloseHandle(self, handle):
            self.closed_handles.append(int(handle.value))
            return 1

    fake = FakeBindings()
    monkeypatch.setattr(windows_conpty, "_get_bindings", lambda: fake)

    terminal = windows_conpty.WindowsConPTY(24, 80)
    terminal.open()

    assert terminal.pseudoconsole == 0x999
    assert terminal._input_read == 0x101
    assert terminal._input_write == 0x102
    assert terminal._output_read == 0x201
    assert terminal._output_write == 0x202
    assert fake.closed_handles == []

    terminal.close_child_ends()

    assert terminal._input_read == 0
    assert terminal._output_write == 0
    assert terminal._input_write == 0x102
    assert terminal._output_read == 0x201
    assert fake.closed_handles == [0x101, 0x202]


def test_windows_conpty_open_refuses_reentry_after_failed_pseudoconsole_creation(monkeypatch) -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    class FakeBindings:
        HANDLE = ctypes.c_void_p
        HPCON = ctypes.c_void_p
        DWORD = ctypes.c_uint32
        BOOL = ctypes.c_int
        COORD = windows_conpty._COORD
        SECURITY_ATTRIBUTES = windows_conpty._SECURITY_ATTRIBUTES

        def __init__(self) -> None:
            self.round = 0
            self.create_pipe_calls = 0

        def CreatePipe(self, read_handle, write_handle, security, size):
            del security, size
            pairs = ((0x101, 0x102), (0x201, 0x202), (0x301, 0x302), (0x401, 0x402))
            read_value, write_value = pairs[self.round]
            self.round += 1
            self.create_pipe_calls += 1
            read_handle._obj.value = read_value
            write_handle._obj.value = write_value
            return 1

        def SetHandleInformation(self, handle, mask, flags):
            del handle, mask, flags
            return 1

        def CreatePseudoConsole(self, coord, input_read, output_write, flags, hpcon):
            del coord, input_read, output_write, flags, hpcon
            return -1

    fake = FakeBindings()
    monkeypatch.setattr(windows_conpty, "_get_bindings", lambda: fake)

    terminal = windows_conpty.WindowsConPTY(24, 80)

    with pytest.raises(OSError, match="CreatePseudoConsole failed"):
        terminal.open()

    assert terminal.pseudoconsole == 0
    assert terminal._input_read == 0x101
    assert terminal._input_write == 0x102
    assert terminal._output_read == 0x201
    assert terminal._output_write == 0x202
    assert fake.create_pipe_calls == 2

    with pytest.raises(RuntimeError, match="previous open attempt still owns resources"):
        terminal.open()

    assert terminal._input_read == 0x101
    assert terminal._input_write == 0x102
    assert terminal._output_read == 0x201
    assert terminal._output_write == 0x202
    assert fake.create_pipe_calls == 2


@pytest.mark.parametrize(
    ("failure_stage", "expected_message", "expected_handles"),
    [
        ("output-pipe", "CreatePipe failed for ConPTY output", (0x101, 0x102, 0, 0)),
        (
            "input-writer-inherit",
            "SetHandleInformation failed for ConPTY input writer",
            (0x101, 0x102, 0x201, 0x202),
        ),
        (
            "output-reader-inherit",
            "SetHandleInformation failed for ConPTY output reader",
            (0x101, 0x102, 0x201, 0x202),
        ),
    ],
)
def test_windows_conpty_open_refuses_reentry_after_partial_open_failures(
    monkeypatch,
    failure_stage,
    expected_message,
    expected_handles,
) -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    class FakeBindings:
        HANDLE = ctypes.c_void_p
        HPCON = ctypes.c_void_p
        DWORD = ctypes.c_uint32
        BOOL = ctypes.c_int
        COORD = windows_conpty._COORD
        SECURITY_ATTRIBUTES = windows_conpty._SECURITY_ATTRIBUTES

        def __init__(self) -> None:
            self._pipe_values = iter((0x101, 0x102, 0x201, 0x202, 0x301, 0x302, 0x401, 0x402))
            self.create_pipe_calls = 0
            self.set_handle_calls = 0

        def CreatePipe(self, read_handle, write_handle, security, size):
            del security, size
            self.create_pipe_calls += 1
            if failure_stage == "output-pipe" and self.create_pipe_calls == 2:
                return 0
            read_handle._obj.value = next(self._pipe_values)
            write_handle._obj.value = next(self._pipe_values)
            return 1

        def SetHandleInformation(self, handle, mask, flags):
            del handle, mask, flags
            self.set_handle_calls += 1
            if failure_stage == "input-writer-inherit" and self.set_handle_calls == 1:
                return 0
            if failure_stage == "output-reader-inherit" and self.set_handle_calls == 2:
                return 0
            return 1

        def CreatePseudoConsole(self, coord, input_read, output_write, flags, hpcon):
            del coord, input_read, output_write, flags, hpcon
            pytest.fail("CreatePseudoConsole should not run after earlier open-stage failure")

    fake = FakeBindings()
    monkeypatch.setattr(windows_conpty, "_get_bindings", lambda: fake)

    terminal = windows_conpty.WindowsConPTY(24, 80)

    with pytest.raises(OSError, match=expected_message):
        terminal.open()

    assert terminal.pseudoconsole == 0
    assert (
        terminal._input_read,
        terminal._input_write,
        terminal._output_read,
        terminal._output_write,
    ) == expected_handles

    with pytest.raises(RuntimeError, match="previous open attempt still owns resources"):
        terminal.open()

    assert (
        terminal._input_read,
        terminal._input_write,
        terminal._output_read,
        terminal._output_write,
    ) == expected_handles
    assert fake.create_pipe_calls == 2


def test_create_suspended_conpty_process_retains_created_handles_on_attribute_cleanup_failure(monkeypatch) -> None:
    import flowgency.jobs.windows_conpty as windows_conpty

    class FakeBindings:
        HANDLE = ctypes.c_void_p
        HPCON = ctypes.c_void_p
        DWORD = ctypes.c_uint32
        SIZE_T = ctypes.c_size_t
        BOOL = ctypes.c_int
        COORD = windows_conpty._COORD
        SECURITY_ATTRIBUTES = windows_conpty._SECURITY_ATTRIBUTES
        STARTUPINFOEXW = windows_conpty._STARTUPINFOEXW
        PROCESS_INFORMATION = windows_conpty._PROCESS_INFORMATION

        def InitializeProcThreadAttributeList(self, pointer, count, flags, size_ptr):
            del pointer, count, flags
            size_ptr._obj.value = 64
            return 1

        def UpdateProcThreadAttribute(self, pointer, flags, attribute, value, size, previous, return_size):
            del pointer, flags, attribute, value, size, previous, return_size
            return 1

        def DeleteProcThreadAttributeList(self, pointer):
            del pointer
            raise RuntimeError("attribute cleanup failed")

        def CreateProcessW(
            self,
            application_name,
            command_line,
            process_attributes,
            thread_attributes,
            inherit_handles,
            creation_flags,
            environment,
            cwd,
            startup,
            process_info,
        ):
            del application_name, command_line, process_attributes, thread_attributes
            del inherit_handles, creation_flags, environment, cwd, startup
            process_info._obj.hProcess = self.HANDLE(0x1111)
            process_info._obj.hThread = self.HANDLE(0x2222)
            process_info._obj.dwProcessId = 3333
            process_info._obj.dwThreadId = 4444
            return 1

    class FakeJobHandle:
        def __int__(self) -> int:
            return 0xABCD

    fake = FakeBindings()
    monkeypatch.setattr(windows_conpty, "_get_bindings", lambda: fake)

    with pytest.raises(windows_conpty.WindowsConPTYCreateProcessError) as raised:
        windows_conpty.create_suspended_conpty_process(
            ("python.exe", "-u", "script.py"),
            Path("C:/tmp/work"),
            {},
            0x1234,
            FakeJobHandle(),
        )

    assert raised.value.process_handle == 0x1111
    assert raised.value.thread_handle == 0x2222
    assert raised.value.pid == 3333
    assert raised.value.thread_id == 4444