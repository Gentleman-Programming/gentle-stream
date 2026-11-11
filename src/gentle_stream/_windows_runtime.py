"""Small Win32 boundary: private runtime directories and owned process jobs.

No shell commands, ACL repair, PID searches or global process termination.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from functools import lru_cache
from pathlib import Path
import stat
import threading

k32 = C.WinDLL("kernel32", use_last_error=True)
a32 = C.WinDLL("advapi32", use_last_error=True)


def api(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


CloseHandle = api(k32, "CloseHandle", W.BOOL, W.HANDLE)
LocalFree = api(k32, "LocalFree", W.HLOCAL, W.HLOCAL)
GetCurrentProcess = api(k32, "GetCurrentProcess", W.HANDLE)
OpenProcessToken = api(a32, "OpenProcessToken", W.BOOL, W.HANDLE, W.DWORD, C.POINTER(W.HANDLE))
GetTokenInformation = api(a32, "GetTokenInformation", W.BOOL, W.HANDLE, C.c_int, W.LPVOID,
                          W.DWORD, C.POINTER(W.DWORD))
ConvertSid = api(a32, "ConvertSidToStringSidW", W.BOOL, W.LPVOID, C.POINTER(W.LPWSTR))
ConvertSD = api(a32, "ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
                W.LPCWSTR, W.DWORD, C.POINTER(W.LPVOID), C.POINTER(W.DWORD))
GetSecurity = api(a32, "GetNamedSecurityInfoW", W.DWORD, W.LPWSTR, C.c_int, W.DWORD,
                  C.POINTER(W.LPVOID), W.LPVOID, C.POINTER(W.LPVOID), W.LPVOID, C.POINTER(W.LPVOID))
GetControl = api(a32, "GetSecurityDescriptorControl", W.BOOL, W.LPVOID,
                 C.POINTER(W.WORD), C.POINTER(W.DWORD))
GetAce = api(a32, "GetAce", W.BOOL, W.LPVOID, W.DWORD, C.POINTER(W.LPVOID))


def checked(result):
    if not result:
        raise C.WinError(C.get_last_error())
    return result


def sid_string(sid):
    text = W.LPWSTR()
    checked(ConvertSid(sid, C.byref(text)))
    try:
        return text.value
    finally:
        LocalFree(C.cast(text, W.HLOCAL))


@lru_cache(maxsize=1)
def user_sid():
    token = W.HANDLE()
    checked(OpenProcessToken(GetCurrentProcess(), 8, C.byref(token)))  # TOKEN_QUERY
    try:
        size = W.DWORD()
        GetTokenInformation(token, 1, None, 0, C.byref(size))  # TokenUser
        buffer = C.create_string_buffer(size.value)
        checked(GetTokenInformation(token, 1, buffer, size, C.byref(size)))
        return sid_string(C.cast(buffer, C.POINTER(W.LPVOID))[0])
    finally:
        CloseHandle(token)


class SecurityAttributes(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", W.LPVOID), ("inherit", W.BOOL)]


CreateDirectory = api(k32, "CreateDirectoryW", W.BOOL, W.LPCWSTR, C.POINTER(SecurityAttributes))
CreateFile = api(k32, "CreateFileW", W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD,
                 C.POINTER(SecurityAttributes), W.DWORD, W.DWORD, W.HANDLE)


def create_private_file(path: Path):
    """Exclusively create a user-owned/private file; return a noninheritable fd."""
    import msvcrt
    import os

    no_reparse(path.parent)
    descriptor = W.LPVOID()
    sid = user_sid()
    checked(ConvertSD(f"O:{sid}D:P(A;;FA;;;{sid})", 1, C.byref(descriptor), None))
    try:
        attrs = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
        # GENERIC_WRITE, no sharing, CREATE_NEW, FILE_ATTRIBUTE_NORMAL.
        # The owner and protected DACL apply atomically, before any bytes exist.
        handle = CreateFile(str(path), 0x40000000, 0, C.byref(attrs), 1, 0x80, None)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY | os.O_NOINHERIT)
        except BaseException:
            CloseHandle(handle)
            raise
        return fd  # The CRT fd now owns the handle; os.close closes both.
    finally:
        LocalFree(descriptor)


def no_reparse(path):
    # Checking every existing component rejects junctions as well as symlinks.
    for part in (path, *path.parents):
        if part.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(f"runtime path contains a reparse point: {part}")


def make_private(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    no_reparse(path.parent)
    descriptor = W.LPVOID()
    checked(ConvertSD(f"O:{user_sid()}D:P(A;OICI;FA;;;{user_sid()})", 1,
                      C.byref(descriptor), None))
    try:
        attrs = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
        if not CreateDirectory(str(path), C.byref(attrs)) and C.get_last_error() != 183:
            raise C.WinError(C.get_last_error())
    finally:
        LocalFree(descriptor)
    check_private(path)


class ACL(C.Structure):
    _fields_ = [("revision", W.BYTE), ("reserved", W.BYTE), ("size", W.WORD),
                ("count", W.WORD), ("reserved2", W.WORD)]


def check_private(path: Path, directory=True):
    no_reparse(path)
    if directory and not path.is_dir():
        raise OSError(f"{path} is not a directory")
    owner, dacl, descriptor = W.LPVOID(), W.LPVOID(), W.LPVOID()
    error = GetSecurity(str(path), 1, 5, C.byref(owner), None, C.byref(dacl), None, C.byref(descriptor))
    if error:
        raise C.WinError(error)
    try:
        control, revision = W.WORD(), W.DWORD()
        checked(GetControl(descriptor, C.byref(control), C.byref(revision)))
        if sid_string(owner) != user_sid() or not dacl or (directory and not control.value & 0x1000):
            raise OSError(f"{path} is not private to this user")
        acl = C.cast(dacl, C.POINTER(ACL)).contents
        if not acl.count:
            raise OSError(f"{path} has an empty access list")
        for i in range(acl.count):
            ace = W.LPVOID()
            checked(GetAce(dacl, i, C.byref(ace)))
            # ACCESS_ALLOWED_ACE: 4-byte header, DWORD mask, then SID.
            if C.cast(ace, C.POINTER(W.BYTE))[0] != 0 or sid_string(ace.value + 8) != user_sid():
                raise OSError(f"{path} grants access outside this user")
    finally:
        LocalFree(descriptor)


# A Job Object owns descendants even if the launcher exits. Assignment occurs
# while suspended: no launcher can escape by spawning before assignment.
class BasicLimit(C.Structure):
    _fields_ = [("process_time", C.c_longlong), ("job_time", C.c_longlong), ("flags", W.DWORD),
                ("minimum", C.c_size_t), ("maximum", C.c_size_t), ("active", W.DWORD),
                ("affinity", C.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD)]


class IOCounters(C.Structure):
    _fields_ = [(name, C.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops",
                                                  "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimit(C.Structure):
    _fields_ = [("basic", BasicLimit), ("io", IOCounters), ("process_memory", C.c_size_t),
                ("job_memory", C.c_size_t), ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]


CreateJob = api(k32, "CreateJobObjectW", W.HANDLE, W.LPVOID, W.LPCWSTR)
SetJob = api(k32, "SetInformationJobObject", W.BOOL, W.HANDLE, C.c_int, W.LPVOID, W.DWORD)
AssignJob = api(k32, "AssignProcessToJobObject", W.BOOL, W.HANDLE, W.HANDLE)
TerminateJob = api(k32, "TerminateJobObject", W.BOOL, W.HANDLE, W.UINT)
ResumeProcess = api(C.WinDLL("ntdll"), "NtResumeProcess", C.c_long, W.HANDLE)
OpenProcess = api(k32, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
WaitForSingleObject = api(k32, "WaitForSingleObject", W.DWORD, W.HANDLE, W.DWORD)


class Job:
    def __init__(self):
        self._lock = threading.Lock()
        self.handle = checked(CreateJob(None, None))
        limits = ExtendedLimit()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            checked(SetJob(self.handle, 9, C.byref(limits), C.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def attach_and_resume(self, proc):
        checked(AssignJob(self.handle, int(proc._handle)))
        # CPython closes the primary thread handle in Popen. Resume the owned
        # suspended process by its retained process handle, never by a PID lookup.
        status = ResumeProcess(int(proc._handle))
        if status < 0:
            raise OSError(f"cannot resume owned model process: NTSTATUS {status:#x}")

    def kill(self):
        with self._lock:
            if self.handle:
                checked(TerminateJob(self.handle, 1))

    def close(self, detach=False):
        with self._lock:
            if self.handle:
                if detach:
                    # A ready daemon must outlive its starting client. Until
                    # readiness, the job still owns the entire launcher tree.
                    limits = ExtendedLimit()
                    checked(SetJob(self.handle, 9, C.byref(limits), C.sizeof(limits)))
                CloseHandle(self.handle)
                self.handle = None


def is_alive(pid):
    handle = OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only; never terminate by PID
    if not handle:
        return C.get_last_error() == 5  # access denied is not evidence of death
    try:
        return WaitForSingleObject(handle, 0) == 258
    finally:
        CloseHandle(handle)
