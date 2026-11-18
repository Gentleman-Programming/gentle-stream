"""One owned DesktopWindow / persistent ConPTY / real StreamApp session."""

from __future__ import annotations

import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .desktop_control import SessionChannel
from .desktop_transport import DesktopPTY, TransportError
from .desktop_window import DesktopTheme, DesktopWindow


class DesktopSession:
    def __init__(self, channel, argv, *, window_factory: Callable[..., Any] = DesktopWindow,
                 pty_factory: Callable[..., Any] = DesktopPTY):
        self.channel = channel
        self.done = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._render_lock = threading.RLock()
        self._thread = None
        self._fatal = False
        self.status = None
        self.error = None
        self.child_status = None
        self._last_frame = None
        self._pty_grid = None
        self.window = window_factory(
            on_input=self._input, on_resize=self._resize, on_action=self._action,
            theme=DesktopTheme('#140a12', '#f6eff3', '#ff76c3'),
            assets_dir=Path(__file__).with_name('desktop_assets'))
        self.pty = pty_factory(argv, cwd=str(Path.cwd()),
                               on_output=self._output, on_exit=self._exited)

    def _input(self, text):
        if not self._stop.is_set():
            try:
                self.pty.write(text)
            except Exception as exc:
                self._fail(exc)

    def _regions(self, snap, regions):
        try:
            self.window.set_drag_regions(snap['epoch'], snap['cols'], snap['rows'], regions)
            return True
        except ValueError as exc:
            if str(exc) != 'stale desktop drag layout':
                raise
            # Concurrent native resize/mode already revoked this exact grant.
            return False

    def _output(self, text):
        # H1 rejects output once user close begins. Continue draining the PTY
        # into a discard sink after that point, never block its exit on GUI ACK.
        while not self._stop.is_set():
            try:
                with self._render_lock:
                    snap = self.window.snapshot()
                    if snap['cols'] and snap['rows'] and not self._regions(snap, []):
                        raise BufferError('desktop layout changed; retry output')
                    self.window.write(text)
                    self._last_frame = None
                return
            except BufferError:
                self._stop.wait(.02)
            except RuntimeError:
                if not self._stop.is_set():
                    self._fail(RuntimeError('Desktop output unavailable'))
                return

    def _exited(self, status):
        self.child_status = status
        self._stop.set()

    def _resize(self, cols, rows):
        try:
            snap = self.window.snapshot()
            self.channel.request_layout(snap['epoch'], cols, rows)
            if hasattr(self.pty, 'snapshot') and self.pty.snapshot()['state'] == 'running':
                self.pty.resize(cols, rows)
                self._pty_grid = (cols, rows)
        except Exception as exc:
            self._fail(exc)

    def _start_supervisor(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._supervise,
                                                name='desktop-session', daemon=True)
                self._thread.start()

    def _action(self, action):
        if action == 'ready':
            self._start_supervisor()
        elif action == 'toggle' and not self._stop.is_set():
            try:
                # Revoke grants before asking the App to change cell ownership.
                snap = self.window.snapshot()
                if snap['cols'] and snap['rows']:
                    self._regions(snap, [])
                self.channel.request_toggle()
            except Exception as exc:
                self._fail(exc)
        elif action in ('quit', 'host_failed'):
            if action == 'host_failed':
                self._fatal = True
            self._stop.set()
            try:
                self.channel.request_shutdown()  # available even BEFORE spawn
            except Exception as exc:
                self.error = type(exc).__name__
                self._fatal = True
            self._start_supervisor()

    def _fail(self, exc):
        self.error = type(exc).__name__  # no command/input/exception payloads
        self._action('host_failed')
        if isinstance(self.window, DesktopWindow) and self.window.snapshot()['state'] != 'failed':
            self.window._fail()

    def _sync(self):
        with self._render_lock:
            self._sync_frame()

    def _sync_frame(self):
        snap = self.window.snapshot()
        cols, rows = snap['cols'], snap['rows']
        if not cols or not rows:
            return
        layout = [snap['epoch'], cols, rows]
        if self.channel.layout() != layout:
            self.channel.request_layout(*layout)
        if self._pty_grid != (cols, rows):
            self.pty.resize(cols, rows)
            self._pty_grid = (cols, rows)
        frame = self.channel.state()
        if frame is None or frame == self._last_frame:
            return
        if frame['mode'] != snap['mode']:
            # Mode invalidates epoch. Wait for the new grid's App frame before
            # authorizing any drag cells; never reuse old coordinates.
            self.window.set_mode(frame['mode'])
            self._last_frame = None
            return
        self.window.set_theme(DesktopTheme(*frame['theme']))
        # A render must reach xterm's actual write ACK before cells are granted.
        if snap.get('buffered_bytes', 0) != 0:
            return
        if self._regions(snap, frame['regions']):
            self._last_frame = frame

    def _supervise(self):
        started = False
        try:
            if not self._stop.is_set():
                # Even a failed/late start needs close's actual completion proof.
                started = True
                self.pty.start()
                while not self._stop.wait(.02):
                    # Native read/write failure need not emit an exit callback.
                    # Reuse fatal shutdown and bounded close, never infer exit.
                    if hasattr(self.pty, 'snapshot') and self.pty.snapshot()['state'] == 'failed':
                        raise TransportError('Desktop transport failed')
                    self._sync()
        except Exception as exc:
            self._fail(exc)
        finally:
            try:
                if started:
                    self.child_status = self.pty.close(self.channel.request_shutdown, timeout=10.0)
                self.status = 0 if not self._fatal and (not started or self.child_status == 0) else 1
                if not self._fatal:
                    # Natural child exit still requires H1's close-request state.
                    if hasattr(self.window, 'snapshot') and self.window.snapshot().get('state') == 'ready':
                        self.window._message({'type': 'action', 'action': 'quit'})
                    self.window.finish_close()
            except Exception as exc:
                self.status = 1
                self._fail(exc)
            finally:
                self.done.set()

    def run(self):
        try:
            self.window.run()
        except Exception as exc:
            self._fail(exc)
        finally:
            if not self.done.is_set() and not self._stop.is_set():
                self._action('host_failed')
            # Startup has its own 5s bound; close has a single 10s deadline.
            # GUI callbacks never wait here. Uncertainty is retained and fails.
            self.done.wait(16)
        backend = getattr(self.window, '_backend', None)
        if backend is not None and not backend.security.get('browser_exit_observed'):
            self._fatal = True
            self.error = 'BrowserExitUnconfirmed'
        return self.status if self.done.is_set() and self.status is not None and not self._fatal else 1

    def snapshot(self):
        return {'status': self.status, 'child_status': self.child_status,
                'done': self.done.is_set(), 'fatal': self._fatal,
                'error': self.error, 'window': self.window.snapshot(),
                'transport': self.pty.snapshot()}


def launch(forwarded):
    if sys.platform != 'win32':
        print('gentle-stream: --desktop requires native Windows; use ordinary gentle-stream on this platform',
              file=sys.stderr)
        return 1
    try:
        # Parent creates the channel before DesktopPTY can possibly spawn.
        # Retain it on uncertain cleanup rather than removing a live child's IPC.
        root = Path(tempfile.mkdtemp(prefix='gentle-stream-session-'))
        channel = SessionChannel.create(root)
        session = DesktopSession(channel, [sys.executable, '-m', 'gentle_stream',
                                          '--desktop-child', str(root), channel.nonce, *forwarded])
        result = session.run()
        if result:
            print('gentle-stream: desktop session failed; child/browser cleanup may be uncertain; '
                  'check gentle-stream[desktop] and Edge WebView2 Runtime', file=sys.stderr)
        return result
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        print(f'gentle-stream: desktop unavailable ({type(exc).__name__}); install gentle-stream[desktop] '
              'and Microsoft Edge WebView2 Runtime on native Windows', file=sys.stderr)
        return 1


def child_main(argv):
    if len(argv) < 2:
        return 2
    channel = SessionChannel.open(argv[0], argv[1])
    if channel.stopping():
        return 0
    from . import main
    return main(argv[2:], desktop_control=channel)
