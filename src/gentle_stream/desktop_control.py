"""Private, bounded per-session files; terminal output is never control input.

Each record has one writer: host writes layout/toggle/stop, App writes frame.
Atomic publication keeps readers from observing partial JSON. This is not RPC.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
from pathlib import Path

_LIMIT = 16384


def _open_read(path):
    if os.name != 'nt':
        return path.open('rb')
    # CRT/Python file opens do not share DELETE on Windows. A channel reader
    # must allow atomic replacement while retaining its own complete snapshot.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        return os.fdopen(descriptor, 'rb')  # stream owns descriptor and handle
    except BaseException:
        os.close(descriptor)
        raise


def _replace(source, target):
    if os.name != 'nt':
        os.replace(source, target)
        return
    # MoveFileExW (os.replace) cannot replace an open destination even when
    # its readers share DELETE. POSIX rename semantics keep those readers on
    # the old file while atomically publishing the new name (Windows 10+).
    # No fallback/retry: unsupported filesystems or access denials must escape.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    rename = kernel.SetFileInformationByHandle
    rename.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    rename.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    name = str(target).encode('utf-16-le')
    # SetFileInformationByHandle's Win32 path conversion needs a terminated
    # WCHAR string as well as FileNameLength. Structure alignment padding is
    # not a terminator: without one the published name can include heap bytes.
    terminated_name = name + b'\x00\x00'

    class RenameInfo(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD), ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD),
                    ('FileName', ctypes.c_byte * len(terminated_name))]

    info = RenameInfo()
    info.Flags = 0x1 | 0x2  # REPLACE_IF_EXISTS | POSIX_SEMANTICS
    info.FileNameLength = len(name)  # Bytes, excluding the UTF-16 terminator.
    ctypes.memmove(ctypes.addressof(info) + RenameInfo.FileName.offset,
                   terminated_name, len(terminated_name))
    handle = create(str(source), 0x00010000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not rename(handle, 22, ctypes.byref(info), ctypes.sizeof(info)):  # FileRenameInfoEx
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


class SessionChannel:
    def __init__(self, root, nonce):
        self.root, self.nonce = Path(root), nonce
        self._lock = threading.RLock()
        self._seen_toggle = 0

    @classmethod
    def create(cls, root):
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or any(root.iterdir()):
            raise ValueError('Invalid desktop session')
        channel = cls(root.resolve(), secrets.token_hex(24))
        channel._write('identity', {})
        return channel

    @classmethod
    def open(cls, root, nonce):
        if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{48}', nonce):
            raise ValueError('Invalid desktop session')
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            raise ValueError('Invalid desktop session')
        channel = cls(root.resolve(), nonce)
        if channel._read('identity') != {}:
            raise ValueError('Invalid desktop session')
        return channel

    def _read(self, name):
        path = self.root / (name + '.json')
        try:
            if path.is_symlink() or path.stat().st_size > _LIMIT:
                raise ValueError('Invalid desktop session')
            with _open_read(path) as stream:
                raw = stream.read(_LIMIT + 1)
            packet = json.loads(raw)
            if (len(raw) > _LIMIT or not isinstance(packet, dict) or
                    set(packet) != {'nonce', 'value'} or
                    not isinstance(packet['nonce'], str) or
                    not hmac.compare_digest(packet['nonce'], self.nonce)):
                raise ValueError('Invalid desktop session')
            return packet['value']
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError('Invalid desktop session') from exc

    def _write(self, name, value):
        raw = json.dumps({'nonce': self.nonce, 'value': value}, separators=(',', ':')).encode()
        if len(raw) > _LIMIT:
            raise ValueError('Invalid desktop session')
        # One writer per record, serialized even when callbacks race.
        with self._lock:
            target = self.root / (name + '.json')
            pending = self.root / (name + '.pending')
            if target.is_symlink() or pending.is_symlink():
                raise ValueError('Invalid desktop session')
            with pending.open('wb') as stream:
                stream.write(raw)
            _replace(pending, target)

    def request_shutdown(self):
        self._write('stop', True)

    def stopping(self):
        value = self._read('stop')
        if value is not None and value is not True:
            raise ValueError('Invalid desktop session')
        return value is True

    def request_toggle(self):
        with self._lock:
            value = self._read('toggle')
            if value is not None and not _integer(value, 0, 2**31 - 2):
                raise ValueError('Invalid desktop session')
            self._write('toggle', (value or 0) + 1)

    def take_toggle(self):
        value = self._read('toggle')
        if value is None:
            return False
        if not _integer(value, self._seen_toggle, 2**31 - 1):
            raise ValueError('Invalid desktop session')
        if self._seen_toggle < value:
            self._seen_toggle += 1
            return True
        return False

    def request_layout(self, epoch, cols, rows):
        self._check_layout([epoch, cols, rows])
        self._write('layout', [epoch, cols, rows])

    @staticmethod
    def _check_layout(layout):
        if (not isinstance(layout, list) or len(layout) != 3 or
                not _integer(layout[0], 0, 2**31 - 1) or
                any(not _integer(n, 1, 500) for n in layout[1:])):
            raise ValueError('Invalid desktop session')

    def layout(self):
        value = self._read('layout')
        if value is not None:
            self._check_layout(value)
        return value

    @staticmethod
    def _check_frame(frame):
        if (not isinstance(frame, dict) or set(frame) !=
                {'epoch', 'cols', 'rows', 'mode', 'theme', 'regions'}):
            raise ValueError('Invalid desktop session')
        SessionChannel._check_layout([frame['epoch'], frame['cols'], frame['rows']])
        if frame['mode'] not in ('full', 'mini'):
            raise ValueError('Invalid desktop session')
        theme, regions = frame['theme'], frame['regions']
        if (not isinstance(theme, list) or len(theme) != 3 or
                any(not isinstance(c, str) or not re.fullmatch('#[0-9a-fA-F]{6}', c) for c in theme) or
                not isinstance(regions, list) or len(regions) > 64):
            raise ValueError('Invalid desktop session')
        for rect in regions:
            if (not isinstance(rect, list) or len(rect) != 4 or
                    any(type(n) is not int for n in rect)):
                raise ValueError('Invalid desktop session')
            x, y, w, h = rect
            if min(x, y) < 0 or min(w, h) < 1 or x+w > frame['cols'] or y+h > frame['rows']:
                raise ValueError('Invalid desktop session')

    def publish(self, frame):
        self._check_frame(frame)
        self._write('frame', frame)

    def state(self):
        frame = self._read('frame')
        if frame is None:
            return None
        self._check_frame(frame)
        if [frame['epoch'], frame['cols'], frame['rows']] != self.layout():
            return None
        return frame
