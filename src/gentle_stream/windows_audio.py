"""Opt-in WSL -> native Windows output. No synthesis, devices or downloads here."""
from __future__ import annotations

import os
from pathlib import Path
import select
import shutil
import struct
import subprocess
import sys
import threading
import time

import numpy as np


def _executable(variable: str, candidates) -> Path:
    explicit = os.environ.get(variable)
    for candidate in ([explicit] if explicit else candidates):
        if not candidate:
            continue
        path = Path(candidate).absolute()
        if not explicit and 'WindowsApps' in path.parts:
            continue  # Never launch the Microsoft Store Python alias.
        try:
            with path.open('rb') as file:
                if path.suffix.lower() == '.exe' and file.read(2) == b'MZ':
                    return path
        except OSError:
            pass
    raise OSError(f'Set {variable} to the WSL path of the installed Windows executable')


def _executables():
    if sys.platform != 'linux' or not os.environ.get('WSL_DISTRO_NAME'):
        raise OSError('Windows audio output requires WSL with Windows interop enabled')
    python = _executable('GENTLE_STREAM_WINDOWS_PYTHON', [shutil.which('python.exe')])
    ffplay = _executable('GENTLE_STREAM_WINDOWS_FFPLAY', [shutil.which('ffplay.exe')])
    converted = subprocess.run(['wslpath', '-w', str(ffplay)], check=True,
                               capture_output=True, text=True, timeout=5)
    return str(python), converted.stdout.strip()


class WindowsPCMStream:
    """The Player's stream interface, paced at wall time with bounded pipe writes.

    Native Windows Python supervises FFplay and cleans it up on input EOF/failure.
    Hardware underflows cannot be observed through this transport.
    """
    audio_output = 'windows'
    reports_output_underflows = False

    def __init__(self, *, samplerate, channels, dtype, blocksize, callback, latency=None):
        if channels != 2 or dtype != 'float32' or not 100 <= samplerate <= 192000 or blocksize <= 0:
            raise ValueError('Windows output requires float32 stereo PCM and a positive block size')
        self._python, self._ffplay = _executables()
        self._rate, self._frames, self._callback = samplerate, blocksize, callback
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._controls = threading.Lock()
        self._send = threading.Lock()
        self._receive = threading.Lock()
        self._sequence = 0
        self._generation = 0
        self._acks = bytearray()
        self._stderr_tail = bytearray()
        self._stderr_lock = threading.Lock()
        self._stderr_stop = threading.Event()
        self._stderr_thread = None
        self._process = None
        self._thread = None
        self.error = None

    def start(self):
        if self._thread is not None:
            return
        host = Path(__file__).with_name('_windows_output_host.py').read_text(encoding='utf-8')
        self._process = subprocess.Popen(
            [self._python, '-I', '-B', '-u', '-c', host, self._ffplay, str(self._rate)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        try:
            os.set_blocking(self._process.stdin.fileno(), False)
            os.set_blocking(self._process.stdout.fileno(), False)
            if getattr(self._process, 'stderr', None) is not None:
                reader = threading.Thread(target=self._collect_stderr, daemon=True,
                                          name='gentle-stream-windows-errors')
                reader.start()
                self._stderr_thread = reader
            self._thread = threading.Thread(target=self._run, daemon=True, name='gentle-stream-windows')
            self._thread.start()
        except Exception:
            self._thread = None
            self._process.stdin.close()
            try:
                self._process.wait(timeout=6)
            finally:
                self._process.stdout.close()
                if self._stderr_thread is not None:
                    self._stderr_stop.set()
                    self._stderr_thread.join(timeout=.2)
                elif getattr(self._process, 'stderr', None) is not None:
                    self._process.stderr.close()
            raise

    def _collect_stderr(self):
        pipe = self._process.stderr
        try:
            fd = pipe.fileno()
            os.set_blocking(fd, False)
            while not self._stderr_stop.is_set():
                if not select.select([fd], [], [], .05)[0]:
                    continue
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                with self._stderr_lock:
                    self._stderr_tail.extend(chunk)
                    del self._stderr_tail[:-512]
        except (OSError, ValueError):
            pass  # The supervisor exit code remains available when stderr is unreadable.
        finally:
            pipe.close()

    def _diagnostic(self, message):
        code = self._process.poll()
        if code is not None and self._stderr_thread is not None:
            self._stderr_thread.join(timeout=.2)
        with self._stderr_lock:
            native = ' '.join(self._stderr_tail.decode('utf-8', errors='replace').split())[-512:]
        if native:
            # The control path may have captured an earlier tail; the finalizer
            # must report the latest native failure without duplicating it.
            message = message.partition('; native stderr: ')[0]
        if code is not None and 'supervisor exit status ' not in message:
            message += f'; supervisor exit status {code}'
        if native:
            message += f'; native stderr: {native}'
        return message

    def _write(self, data):
        fd = self._process.stdin.fileno()
        view = memoryview(data)
        deadline = time.monotonic() + 2
        while view:
            if self._stop.is_set():
                raise InterruptedError('Windows output stopped')
            if self._process.poll() is not None:
                raise BrokenPipeError(self._diagnostic('Windows audio supervisor exited'))
            if time.monotonic() >= deadline:
                raise TimeoutError('Windows audio write timed out')
            if not select.select([], [fd], [], .02)[1]:
                continue
            try:
                count = os.write(fd, view[:select.PIPE_BUF])
            except BlockingIOError:
                continue
            if not count:
                raise BrokenPipeError('Windows audio input closed')
            view = view[count:]

    def _packet(self, op, data=b''):
        # Caller holds _send. A bounded frame is never interleaved with a control.
        self._sequence += 1
        self._write(struct.pack('!cII', op, self._sequence, len(data)) + data)
        return self._sequence

    def _ack(self, op, sequence, generation=None):
        deadline = time.monotonic() + 3
        with self._receive:
            fd = self._process.stdout.fileno()
            while not self._stop.is_set():
                if op == b'D' and (self._paused.is_set() or generation != self._generation):
                    return  # Yield the ACK reader to the pause controller.
                if len(self._acks) == 5:
                    got_op, got_sequence = struct.unpack('!cI', self._acks)
                    self._acks.clear()
                    if got_op == b'E':
                        raise OSError('Windows native output write failed')
                    if got_op not in (b'D', b'P', b'R') or got_sequence > sequence:
                        raise OSError('Invalid Windows output acknowledgment')
                    if got_sequence == sequence:
                        if got_op != op:
                            raise OSError('Mismatched Windows output acknowledgment')
                        return
                    continue  # A data ACK overtaken by a pause is obsolete.
                if time.monotonic() >= deadline:
                    raise TimeoutError('Windows output acknowledgment timed out')
                if select.select([fd], [], [], .02)[0]:
                    part = os.read(fd, 5 - len(self._acks))
                    if not part:
                        if self._process.poll() is None:
                            try:
                                self._process.wait(timeout=.2)
                            except subprocess.TimeoutExpired:
                                pass
                        raise BrokenPipeError(self._diagnostic('Windows audio acknowledgment pipe closed'))
                    self._acks.extend(part)
            raise InterruptedError('Windows output stopped')

    def _control(self, op):
        if self._process is None:
            return
        if self.error or self._stop.is_set():
            raise ValueError(self.error or 'Windows output is closed')
        try:
            with self._send:
                sequence = self._packet(op)
            self._ack(op, sequence)
        except Exception as error:
            self.error = self._diagnostic(str(error))
            self._stop.set()
            # Existing public handlers return ValueError messages to clients;
            # keep their connection alive rather than implying the daemon vanished.
            raise ValueError(f'Windows output control failed: {self.error}') from error

    def pause(self):
        with self._controls:
            if self._paused.is_set():
                if self.error:
                    raise ValueError(self.error)
                return
            self._paused.set()  # Stop callbacks before waiting on the native cut.
            self._generation += 1
            self._control(b'P')

    def resume(self):
        with self._controls:
            if not self._paused.is_set():
                if self.error:
                    raise ValueError(self.error)
                return
            self._control(b'R')
            self._paused.clear()

    def _run(self):
        # Split large callback blocks into bounded packets; only one data ACK can
        # be outstanding, so a blocked sink cannot bury P behind a PCM backlog.
        audio = np.empty((self._frames, 2), dtype=np.float32)
        period, deadline = self._frames / self._rate, time.monotonic()
        try:
            while not self._stop.wait(max(0., deadline - time.monotonic())):
                if self._paused.is_set():
                    self._stop.wait(.01)
                    deadline = time.monotonic()
                    continue
                with self._send:
                    if self._paused.is_set():
                        continue
                    self._callback(audio, self._frames, None, None)
                    generation = self._generation
                data = audio.astype('<f4', copy=False).tobytes()
                for offset in range(0, len(data), 8192):
                    with self._send:
                        if self._paused.is_set() or self._stop.is_set() or generation != self._generation:
                            break
                        sequence = self._packet(b'D', data[offset:offset + 8192])
                    self._ack(b'D', sequence, generation)
                deadline = max(deadline + period, time.monotonic())
        except InterruptedError as error:
            if not self._stop.is_set():
                self.error = str(error)
        except Exception as error:
            self.error = str(error)
        finally:
            self._stop.set()
            # Controllers cannot write a partial frame while EOF is delivered.
            with self._send:
                self._process.stdin.close()
            try:
                code = self._process.wait(timeout=6)
                if code:
                    self.error = self._diagnostic(self.error or 'Windows audio supervisor exited')
            except subprocess.TimeoutExpired:
                self.error = 'Windows audio supervisor did not exit after EOF; inspect player.log'
            self._process.stdout.close()

    def stop(self):
        self._stop.set()
        try:
            if self._thread is not None:
                self._thread.join(timeout=7)
                if self._thread.is_alive() or self._process.poll() is None:
                    raise RuntimeError('Windows audio supervisor did not stop; inspect player.log')
        finally:
            self._stderr_stop.set()
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=.2)
                if self._stderr_thread.is_alive():
                    raise RuntimeError('Windows audio stderr reader did not stop; inspect player.log')

    def close(self):
        self.stop()
