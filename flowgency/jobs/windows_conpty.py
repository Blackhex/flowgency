from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path


_HANDLE_FLAG_INHERIT = 0x00000001
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_ERROR_BROKEN_PIPE = 109
_ERROR_NO_DATA = 232
_ERROR_PIPE_NOT_CONNECTED = 233
_INFINITE = 0xFFFFFFFF

_BINDINGS = None
_BINDINGS_ERROR: Exception | None = None


class WindowsConPTYCreateProcessError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        process_handle: int,
        thread_handle: int,
        pid: int,
        thread_id: int,
    ) -> None:
        super().__init__(message)
        self.process_handle = int(process_handle)
        self.thread_handle = int(thread_handle)
        self.pid = int(pid)
        self.thread_id = int(thread_id)


def handle_value(handle: object | None) -> int:
    if handle is None:
        return 0
    value = getattr(handle, "value", handle)
    if value is None:
        return 0
    return int(value)


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _Bindings:
    HANDLE = wintypes.HANDLE
    HPCON = ctypes.c_void_p
    DWORD = wintypes.DWORD
    SIZE_T = ctypes.c_size_t
    BOOL = wintypes.BOOL
    HRESULT = ctypes.c_long
    COORD = _COORD
    SECURITY_ATTRIBUTES = _SECURITY_ATTRIBUTES
    STARTUPINFOEXW = _STARTUPINFOEXW
    PROCESS_INFORMATION = _PROCESS_INFORMATION

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32 = kernel32
        self.CreatePipe = kernel32.CreatePipe
        self.CreatePipe.argtypes = [
            ctypes.POINTER(self.HANDLE),
            ctypes.POINTER(self.HANDLE),
            ctypes.POINTER(self.SECURITY_ATTRIBUTES),
            self.DWORD,
        ]
        self.CreatePipe.restype = self.BOOL

        self.SetHandleInformation = kernel32.SetHandleInformation
        self.SetHandleInformation.argtypes = [self.HANDLE, self.DWORD, self.DWORD]
        self.SetHandleInformation.restype = self.BOOL

        self.CloseHandle = kernel32.CloseHandle
        self.CloseHandle.argtypes = [self.HANDLE]
        self.CloseHandle.restype = self.BOOL

        self.PeekNamedPipe = kernel32.PeekNamedPipe
        self.PeekNamedPipe.argtypes = [
            self.HANDLE,
            ctypes.c_void_p,
            self.DWORD,
            ctypes.POINTER(self.DWORD),
            ctypes.POINTER(self.DWORD),
            ctypes.POINTER(self.DWORD),
        ]
        self.PeekNamedPipe.restype = self.BOOL

        self.ReadFile = kernel32.ReadFile
        self.ReadFile.argtypes = [
            self.HANDLE,
            ctypes.c_void_p,
            self.DWORD,
            ctypes.POINTER(self.DWORD),
            ctypes.c_void_p,
        ]
        self.ReadFile.restype = self.BOOL

        self.WriteFile = kernel32.WriteFile
        self.WriteFile.argtypes = [
            self.HANDLE,
            ctypes.c_void_p,
            self.DWORD,
            ctypes.POINTER(self.DWORD),
            ctypes.c_void_p,
        ]
        self.WriteFile.restype = self.BOOL

        self.CreatePseudoConsole = kernel32.CreatePseudoConsole
        self.CreatePseudoConsole.argtypes = [
            self.COORD,
            self.HANDLE,
            self.HANDLE,
            self.DWORD,
            ctypes.POINTER(self.HPCON),
        ]
        self.CreatePseudoConsole.restype = self.HRESULT

        self.ResizePseudoConsole = kernel32.ResizePseudoConsole
        self.ResizePseudoConsole.argtypes = [self.HPCON, self.COORD]
        self.ResizePseudoConsole.restype = self.HRESULT

        self.ClosePseudoConsole = kernel32.ClosePseudoConsole
        self.ClosePseudoConsole.argtypes = [self.HPCON]
        self.ClosePseudoConsole.restype = None

        self.InitializeProcThreadAttributeList = kernel32.InitializeProcThreadAttributeList
        self.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            self.DWORD,
            self.DWORD,
            ctypes.POINTER(self.SIZE_T),
        ]
        self.InitializeProcThreadAttributeList.restype = self.BOOL

        self.UpdateProcThreadAttribute = kernel32.UpdateProcThreadAttribute
        self.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            self.DWORD,
            self.SIZE_T,
            ctypes.c_void_p,
            self.SIZE_T,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.UpdateProcThreadAttribute.restype = self.BOOL

        self.DeleteProcThreadAttributeList = kernel32.DeleteProcThreadAttributeList
        self.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        self.DeleteProcThreadAttributeList.restype = None

        self.CreateProcessW = kernel32.CreateProcessW
        self.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            self.BOOL,
            self.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(self.STARTUPINFOEXW),
            ctypes.POINTER(self.PROCESS_INFORMATION),
        ]
        self.CreateProcessW.restype = self.BOOL


def _get_bindings() -> _Bindings:
    global _BINDINGS, _BINDINGS_ERROR
    if _BINDINGS is not None:
        return _BINDINGS
    if os.name != "nt":
        raise OSError("ConPTY is only available on Windows")
    if _BINDINGS_ERROR is not None:
        raise _BINDINGS_ERROR
    try:
        _BINDINGS = _Bindings()
    except Exception as error:
        _BINDINGS_ERROR = error
        raise
    return _BINDINGS


def native_conpty_available() -> bool:
    if os.name != "nt":
        return False
    try:
        bindings = _get_bindings()
    except Exception:
        return False
    required = (
        "CreatePseudoConsole",
        "ResizePseudoConsole",
        "ClosePseudoConsole",
        "InitializeProcThreadAttributeList",
        "UpdateProcThreadAttribute",
        "DeleteProcThreadAttributeList",
        "CreateProcessW",
    )
    return all(getattr(bindings, name, None) is not None for name in required)


def _raise_last_winerror(message: str) -> None:
    raise ctypes.WinError(ctypes.get_last_error(), message)


def _validate_text(value: str, *, field: str) -> None:
    if "\x00" in value:
        raise ValueError(f"{field} must not contain NUL characters")


def _build_environment_block(env: dict[str, str]):
    for key, value in env.items():
        _validate_text(key, field="environment key")
        _validate_text(value, field=f"environment value for {key}")
    payload = "\x00".join(f"{key}={value}" for key, value in env.items()) + "\x00\x00"
    return ctypes.create_unicode_buffer(payload)


def _command_line_buffer(argv: tuple[str, ...]):
    if not argv:
        raise ValueError("argv must not be empty")
    for argument in argv:
        _validate_text(argument, field="argv entry")
    return ctypes.create_unicode_buffer(subprocess.list2cmdline([str(item) for item in argv]))


class _AttributeList:
    def __init__(self, bindings: _Bindings, attribute_count: int) -> None:
        self._bindings = bindings
        self._size = bindings.SIZE_T(0)
        bindings.InitializeProcThreadAttributeList(None, attribute_count, 0, ctypes.byref(self._size))
        self._buffer = ctypes.create_string_buffer(self._size.value)
        self.pointer = ctypes.cast(self._buffer, ctypes.c_void_p)
        if not bindings.InitializeProcThreadAttributeList(
            self.pointer,
            attribute_count,
            0,
            ctypes.byref(self._size),
        ):
            _raise_last_winerror("InitializeProcThreadAttributeList failed")
        self._closed = False

    def update(self, attribute: int, value, size: int) -> None:
        if not self._bindings.UpdateProcThreadAttribute(
            self.pointer,
            0,
            attribute,
            value,
            size,
            None,
            None,
        ):
            _raise_last_winerror(f"UpdateProcThreadAttribute failed for 0x{attribute:08X}")

    def close(self) -> None:
        if self._closed:
            return
        self._bindings.DeleteProcThreadAttributeList(self.pointer)
        self._closed = True


def create_suspended_conpty_process(
    argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    pseudoconsole: int,
    job_handle: object,
) -> tuple[int, int, int, int]:
    bindings = _get_bindings()
    if handle_value(job_handle) == 0:
        raise ValueError("job_handle must be a live handle")
    if int(pseudoconsole) == 0:
        raise ValueError("pseudoconsole must be a live handle")
    cwd = Path(cwd)
    _validate_text(str(cwd), field="cwd")
    command_line = _command_line_buffer(argv)
    environment = _build_environment_block(env)
    attribute_list = _AttributeList(bindings, 2)
    job_array = (bindings.HANDLE * 1)(bindings.HANDLE(handle_value(job_handle)))
    hpc = bindings.HPCON(int(pseudoconsole))
    process_info = bindings.PROCESS_INFORMATION()
    created = False
    try:
        attribute_list.update(
            _PROC_THREAD_ATTRIBUTE_JOB_LIST,
            ctypes.cast(job_array, ctypes.c_void_p),
            ctypes.sizeof(job_array),
        )
        attribute_list.update(
            _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            hpc,
            ctypes.sizeof(hpc),
        )
        startup = bindings.STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(bindings.STARTUPINFOEXW)
        startup.lpAttributeList = attribute_list.pointer
        creation_flags = (
            _CREATE_SUSPENDED
            | _EXTENDED_STARTUPINFO_PRESENT
            | _CREATE_UNICODE_ENVIRONMENT
        )
        if not bindings.CreateProcessW(
            None,
            command_line,
            None,
            None,
            False,
            creation_flags,
            ctypes.cast(environment, ctypes.c_void_p),
            str(cwd),
            ctypes.byref(startup),
            ctypes.byref(process_info),
        ):
            _raise_last_winerror("CreateProcessW failed")
        created = True
    finally:
        try:
            attribute_list.close()
        except Exception as error:
            if created:
                raise WindowsConPTYCreateProcessError(
                    f"CreateProcessW succeeded but attribute cleanup failed: {error}",
                    process_handle=handle_value(process_info.hProcess),
                    thread_handle=handle_value(process_info.hThread),
                    pid=int(process_info.dwProcessId),
                    thread_id=int(process_info.dwThreadId),
                ) from error
            raise
    return (
        handle_value(process_info.hProcess),
        handle_value(process_info.hThread),
        int(process_info.dwProcessId),
        int(process_info.dwThreadId),
    )


class WindowsConPTY:
    def __init__(self, rows: int, cols: int) -> None:
        self._rows = int(rows)
        self._cols = int(cols)
        self._pseudoconsole = 0
        self._input_read = 0
        self._input_write = 0
        self._output_read = 0
        self._output_write = 0
        self._close_thread: threading.Thread | None = None
        self._close_error: BaseException | None = None
        self._close_lock = threading.Lock()

    @property
    def pseudoconsole(self) -> int:
        return int(self._pseudoconsole)

    def open(self) -> None:
        if self.pseudoconsole:
            return
        bindings = _get_bindings()
        security = bindings.SECURITY_ATTRIBUTES()
        security.nLength = ctypes.sizeof(bindings.SECURITY_ATTRIBUTES)
        security.bInheritHandle = 1
        input_read = bindings.HANDLE()
        input_write = bindings.HANDLE()
        output_read = bindings.HANDLE()
        output_write = bindings.HANDLE()
        hpc = bindings.HPCON()
        if not bindings.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write), ctypes.byref(security), 0):
            _raise_last_winerror("CreatePipe failed for ConPTY input")
        self._input_read = handle_value(input_read)
        self._input_write = handle_value(input_write)
        if not bindings.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write), ctypes.byref(security), 0):
            _raise_last_winerror("CreatePipe failed for ConPTY output")
        self._output_read = handle_value(output_read)
        self._output_write = handle_value(output_write)
        if not bindings.SetHandleInformation(bindings.HANDLE(self._input_write), _HANDLE_FLAG_INHERIT, 0):
            _raise_last_winerror("SetHandleInformation failed for ConPTY input writer")
        if not bindings.SetHandleInformation(bindings.HANDLE(self._output_read), _HANDLE_FLAG_INHERIT, 0):
            _raise_last_winerror("SetHandleInformation failed for ConPTY output reader")
        result = bindings.CreatePseudoConsole(
            bindings.COORD(self._cols, self._rows),
            bindings.HANDLE(self._input_read),
            bindings.HANDLE(self._output_write),
            0,
            ctypes.byref(hpc),
        )
        if int(result) < 0:
            raise OSError(int(result), "CreatePseudoConsole failed")
        self._pseudoconsole = handle_value(hpc)

    def close_child_ends(self) -> None:
        error: OSError | None = None
        for attribute in ("_input_read", "_output_write"):
            value = getattr(self, attribute)
            if value == 0:
                continue
            try:
                self._close_handle(value)
            except OSError as close_error:
                if error is None:
                    error = close_error
            else:
                setattr(self, attribute, 0)
        if error is not None:
            raise error

    def read(self, size: int = 65536) -> bytes | None:
        if self._output_read == 0:
            return b""
        bindings = _get_bindings()
        available = bindings.DWORD(0)
        try:
            ok = bindings.PeekNamedPipe(
                bindings.HANDLE(self._output_read),
                None,
                0,
                None,
                ctypes.byref(available),
                None,
            )
        except OSError as error:
            if getattr(error, "winerror", None) in {
                _ERROR_BROKEN_PIPE,
                _ERROR_NO_DATA,
                _ERROR_PIPE_NOT_CONNECTED,
            }:
                return b""
            raise
        if not ok:
            error = ctypes.get_last_error()
            if error in {_ERROR_BROKEN_PIPE, _ERROR_NO_DATA, _ERROR_PIPE_NOT_CONNECTED}:
                return b""
            _raise_last_winerror("PeekNamedPipe failed")
        if available.value == 0:
            return None
        wanted = min(int(size), int(available.value))
        buffer = ctypes.create_string_buffer(wanted)
        read = bindings.DWORD(0)
        if not bindings.ReadFile(
            bindings.HANDLE(self._output_read),
            buffer,
            wanted,
            ctypes.byref(read),
            None,
        ):
            error = ctypes.get_last_error()
            if error in {_ERROR_BROKEN_PIPE, _ERROR_NO_DATA, _ERROR_PIPE_NOT_CONNECTED}:
                return b""
            _raise_last_winerror("ReadFile failed")
        return bytes(buffer.raw[: read.value])

    def write(self, data: bytes) -> None:
        if self._input_write == 0:
            raise OSError("ConPTY input is closed")
        if not data:
            return
        bindings = _get_bindings()
        written = bindings.DWORD(0)
        buffer = ctypes.create_string_buffer(bytes(data))
        if not bindings.WriteFile(
            bindings.HANDLE(self._input_write),
            buffer,
            len(data),
            ctypes.byref(written),
            None,
        ):
            _raise_last_winerror("WriteFile failed")
        if written.value != len(data):
            raise OSError("WriteFile wrote a partial chunk")

    def resize(self, rows: int, cols: int) -> None:
        if self.pseudoconsole == 0:
            raise OSError("ConPTY is not open")
        bindings = _get_bindings()
        result = bindings.ResizePseudoConsole(bindings.HPCON(self.pseudoconsole), bindings.COORD(int(cols), int(rows)))
        if int(result) < 0:
            raise OSError(int(result), "ResizePseudoConsole failed")
        self._rows = int(rows)
        self._cols = int(cols)

    def begin_close(self) -> None:
        if self.pseudoconsole == 0:
            return
        with self._close_lock:
            if self._close_thread is not None and self._close_thread.is_alive():
                return
            self._close_thread = None
            self._close_error = None
            value = self.pseudoconsole
            bindings = _get_bindings()

            def worker() -> None:
                try:
                    bindings.ClosePseudoConsole(bindings.HPCON(value))
                except BaseException as error:
                    self._close_error = error
                else:
                    self._pseudoconsole = 0

            self._close_thread = threading.Thread(target=worker, name="flowgency-conpty-close")
            self._close_thread.start()

    def wait_closed(self, deadline: float) -> bool:
        thread = self._close_thread
        if thread is None:
            return self.pseudoconsole == 0 and self._close_error is None
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(remaining)
        return not thread.is_alive() and self.pseudoconsole == 0 and self._close_error is None

    def close_streams(self) -> None:
        error: OSError | None = None
        for attribute in ("_input_write", "_output_read", "_input_read", "_output_write"):
            value = getattr(self, attribute)
            if value == 0:
                continue
            try:
                self._close_handle(value)
            except OSError as close_error:
                if error is None:
                    error = close_error
            else:
                setattr(self, attribute, 0)
        if error is not None:
            raise error

    def _close_handle(self, value: int) -> None:
        if value == 0:
            return
        bindings = _get_bindings()
        if not bindings.CloseHandle(bindings.HANDLE(value)):
            _raise_last_winerror(f"CloseHandle failed for 0x{value:08X}")