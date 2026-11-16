"""Optional, owned Windows terminal host. No player or transport dependencies."""
import re
import threading
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_OUTPUT = 1024 * 1024
MAX_INPUT = 65536


@dataclass(frozen=True)
class DesktopTheme:
    background: str
    foreground: str
    accent: str

    def __post_init__(self):
        for value in asdict(self).values():
            if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
                raise ValueError('Desktop theme colors must be #RRGGBB')


def _backend_factory(owner):
    from .desktop_webview import NativeBackend
    return NativeBackend(owner)


class DesktopWindow:
    """Controller seam. Callback threads must not block waiting for the GUI.

    Output capacity includes the in-flight chunk until xterm's write callback
    acknowledges it. BufferError means the caller must pause its output reader;
    the rejected write has no effects. No terminal payload enters snapshots.
    """

    def __init__(self, *, on_input, on_resize, on_action, theme, assets_dir,
                 saved_mini_position=None):
        if not isinstance(theme, DesktopTheme):
            raise ValueError('theme must be DesktopTheme')
        if saved_mini_position is not None and (
                not isinstance(saved_mini_position, (tuple, list)) or
                len(saved_mini_position) != 2 or
                any(type(n) is not int for n in saved_mini_position)):
            raise ValueError('mini position must contain two integers')
        self.on_input, self.on_resize, self.on_action = on_input, on_resize, on_action
        self.theme, self.assets_dir = theme, Path(assets_dir)
        self.mini_position = tuple(saved_mini_position) if saved_mini_position else None
        self._lock = threading.RLock()
        self._state, self._mode = 'new', 'full'
        self._epoch, self._cols, self._rows = 0, 0, 0
        self._regions = []
        self._queue, self._pending = deque(), None
        self._bytes, self._seq = 0, 0
        self._backend = None
        self._quit = False

    def run(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('DesktopWindow.run requires the main thread')
        with self._lock:
            if self._state != 'new':
                raise RuntimeError('DesktopWindow can run only once')
            self._state = 'starting'
        try:
            self._backend = _backend_factory(self)
            self._backend.run()
        except Exception:
            self._fail()
            raise

    def _ready(self):
        with self._lock:
            if self._state != 'starting':
                return
            self._state = 'ready'
        self.on_action('ready')
        self._pump()

    def write(self, text):
        if not isinstance(text, str):
            raise ValueError('terminal output must be text')
        size = len(text.encode('utf-8'))
        with self._lock:
            if self._state in ('closed', 'failed', 'closing'):
                raise RuntimeError('desktop host is closing or unavailable')
            if size + self._bytes > MAX_OUTPUT:
                raise BufferError('desktop terminal output backpressure')
            if not text:
                return
            self._seq += 1
            self._queue.append((self._seq, text, size))
            self._bytes += size
        self._pump()

    def _pump(self):
        with self._lock:
            if self._state != 'ready' or self._pending or not self._queue or not self._backend:
                return
            self._pending = self._queue.popleft()
            seq, text, _ = self._pending
            self._backend.send('output', [seq, text])

    def _invalidate(self):
        self._epoch += 1
        self._regions = []

    def set_mode(self, mode):
        if mode not in ('full', 'mini'):
            raise ValueError('mode must be full or mini')
        with self._lock:
            if mode == self._mode:
                return
            if self._backend:
                self._backend.send('mode', mode)
            self._mode = mode
            self._invalidate()

    def set_theme(self, theme):
        if not isinstance(theme, DesktopTheme):
            raise ValueError('theme must be DesktopTheme')
        with self._lock:
            self.theme = theme
            if self._backend:
                self._backend.send('theme', asdict(theme))

    def set_drag_regions(self, epoch, cols, rows, regions):
        """Publish ONLY allowed background/Spectrum cells, never controls/modal.

        The controller must publish an empty list before showing a modal or
        changing cell ownership. Rectangles are (column, row, width, height).
        A resize/mode transition revokes every previous grant.
        """
        with self._lock:
            if (type(epoch) is not int or epoch != self._epoch or
                    (cols, rows) != (self._cols, self._rows)):
                raise ValueError('stale desktop drag layout')
            if not isinstance(regions, (list, tuple)) or len(regions) > 64:
                raise ValueError('invalid desktop drag regions')
            checked = []
            for rect in regions:
                if (not isinstance(rect, (tuple, list)) or len(rect) != 4 or
                        any(type(n) is not int for n in rect)):
                    raise ValueError('invalid desktop drag rectangle')
                x, y, w, h = rect
                if min(x, y) < 0 or min(w, h) <= 0 or x+w > cols or y+h > rows:
                    raise ValueError('desktop drag rectangle outside terminal')
                checked.append(list(rect))
            self._regions = checked
            if self._backend:
                self._backend.send('regions', [epoch, cols, rows, checked])

    def _request_close(self):
        with self._lock:
            if self._quit or self._state in ('closed', 'failed'):
                return
            self._quit = True
            self._state = 'closing'
            self._invalidate()
        self.on_action('quit')

    def finish_close(self):
        """Call only after the future controller has stopped/drained its child."""
        with self._lock:
            if not self._quit:
                raise RuntimeError('close requires controller stop acknowledgement')
            if self._state == 'closed':
                return
            self._state = 'closed'
        if self._backend:
            self._backend.close()

    def _fail(self):
        with self._lock:
            if self._state in ('closed', 'failed'):
                return
            self._state = 'failed'
            self._invalidate()
        try:
            self.on_action('host_failed')
        finally:
            if self._backend:
                self._backend.close()

    def _message(self, message):
        try:
            if not isinstance(message, dict):
                raise ValueError('invalid host message')
            kind = message.get('type')
            if kind == 'ready':
                self._ready()
            elif kind == 'ack':
                with self._lock:
                    if not self._pending or message.get('seq') != self._pending[0]:
                        raise ValueError('invalid output acknowledgement')
                    self._bytes -= self._pending[2]
                    self._pending = None
                self._pump()
            elif kind == 'input':
                text = message.get('text')
                if not isinstance(text, str) or len(text) > MAX_INPUT:
                    raise ValueError('invalid terminal input')
                self.on_input(text)
            elif kind == 'resize':
                c, r = message.get('cols'), message.get('rows')
                if type(c) is not int or type(r) is not int or not (1 <= c <= 1000 and 1 <= r <= 1000):
                    raise ValueError('invalid terminal size')
                with self._lock:
                    self._cols, self._rows = c, r
                    self._invalidate()
                self.on_resize(c, r)
            elif kind == 'action' and message.get('action') in ('toggle', 'quit'):
                if message['action'] == 'quit':
                    self._request_close()
                else:
                    self.on_action('toggle')
            elif kind == 'drag':
                epoch, col, row = message.get('epoch'), message.get('col'), message.get('row')
                header = message.get('header') is True
                with self._lock:
                    allowed = (epoch == self._epoch and type(col) is int and type(row) is int and
                               any(x <= col < x+w and y <= row < y+h for x,y,w,h in self._regions))
                    backend = self._backend
                    if backend is not None and self._state == 'ready' and (header or allowed):
                        backend.send('drag', None)
            elif kind == 'grip' and message.get('edge') in ('e','s','se','w','n','nw','ne','sw'):
                with self._lock:
                    backend = self._backend
                    if backend is not None and self._state == 'ready' and self._mode == 'full':
                        backend.send('grip', message['edge'])
            else:
                raise ValueError('unknown host message')
        except Exception:
            self._fail()

    def snapshot(self):
        with self._lock:
            result = {'state': self._state, 'mode': self._mode, 'epoch': self._epoch,
                      'cols': self._cols, 'rows': self._rows,
                      'mini_position': self.mini_position,
                      'drag_regions': [r[:] for r in self._regions],
                      'buffered_bytes': self._bytes}
        if self._backend:
            result.update(self._backend.snapshot())
        return result
