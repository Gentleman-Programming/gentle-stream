"""Public H1 lifecycle contract, without optional GUI dependencies."""
import pytest

from gentle_stream import desktop_window as host


class Backend:
    def __init__(self, owner):
        self.owner = owner
        self.calls = []
    def run(self):
        self.owner._ready()
    def send(self, kind, value):
        self.calls.append((kind, value))
    def close(self):
        self.calls.append(('close', None))
    def snapshot(self):
        return {'identity': [1, 2, 3], 'geometry': [0, 0, 1100, 700]}


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(host, '_backend_factory', Backend)
    actions, inputs, sizes = [], [], []
    w = host.DesktopWindow(on_input=inputs.append, on_resize=lambda c,r: sizes.append((c,r)),
        on_action=actions.append, theme=host.DesktopTheme('#101010','#eeeeee','#abcdef'), assets_dir=tmp_path)
    return w, actions, inputs, sizes


@pytest.mark.parametrize('color', ['red', '#fff', '#000000;', '', None])
def test_reject_theme(color):
    with pytest.raises(ValueError):
        host.DesktopTheme(color, '#ffffff', '#123456')


def test_lifecycle_ack_and_output(window):
    w, actions, inputs, sizes = window
    w.write('before ✓')
    w.run()
    assert actions == ['ready']
    assert w._backend.calls == [('output', [1, 'before ✓'])]
    w._message({'type':'ack', 'seq':1})
    w._message({'type':'input', 'text':'\t\x1b[Z日本語'})
    w._message({'type':'resize', 'cols':80, 'rows':24})
    assert inputs == ['\t\x1b[Z日本語'] and sizes == [(80,24)]
    w._request_close()
    w._request_close()
    assert actions == ['ready','quit']
    assert not any(c[0] == 'close' for c in w._backend.calls)
    w.finish_close()
    assert w._backend.calls[-1] == ('close',None)
    assert w.snapshot()['state'] == 'closed'


def test_backpressure_no_silent_drop(window):
    w, *_ = window
    text = 'x' * host.MAX_OUTPUT
    w.write(text)
    before = w.snapshot()
    with pytest.raises(BufferError):
        w.write('x')
    assert w.snapshot() == before
    w.run()
    w._message({'type':'ack', 'seq':1})
    w.write('next')
    assert w._backend.calls[-1] == ('output',[2,'next'])


def test_mode_theme_and_epoch_validation(window):
    w, *_ = window
    w.run()
    w.set_mode('mini')
    w.set_mode('full')
    theme = host.DesktopTheme('#000000','#ffffff','#123456')
    w.set_theme(theme)
    w._message({'type':'resize','cols':40,'rows':10})
    epoch = w.snapshot()['epoch']
    w.set_drag_regions(epoch,40,10,[(0,0,20,5)])
    assert w.snapshot()['drag_regions'] == [[0,0,20,5]]
    for args in [(epoch-1,40,10,[]),(epoch,40,10,[(0,0,41,5)]), (epoch,40,10,[{'control':True}])]:
        with pytest.raises(ValueError):
            w.set_drag_regions(*args)
    w.set_mode('mini')
    assert w.snapshot()['drag_regions'] == []
    with pytest.raises(ValueError):
        w.set_mode('other')


def test_bad_messages_fail_closed_without_payload(window):
    w, actions, *_ = window
    w.run()
    w._message({'type':'eval','text':'private payload'})
    assert actions == ['ready','host_failed']
    assert 'private payload' not in str(w.snapshot())
    assert w._backend.calls[-1] == ('close',None)


@pytest.mark.parametrize('message', [
    {'type': 'drag', 'header': True},
    {'type': 'grip', 'edge': 'se'},
])
def test_startup_mouse_messages_do_not_require_a_backend(window, monkeypatch, message):
    w, actions, *_ = window
    class EarlyBackend(Backend):
        def __init__(self, owner):
            super().__init__(owner)
            owner._message(message)
    monkeypatch.setattr(host, '_backend_factory', EarlyBackend)
    w.run()
    assert actions == ['ready']
    assert w.snapshot()['state'] == 'ready'
    assert w._backend.calls == []


def test_mode_queue_rejection_is_atomic_and_explicit_retry_reaches_gui(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from gentle_stream.desktop_frontend import data_script
    from gentle_stream.desktop_webview import NativeBackend

    w = host.DesktopWindow(on_input=lambda text: None, on_resize=lambda c, r: None,
        on_action=lambda action: None, theme=host.DesktopTheme('#101010', '#eeeeee', '#abcdef'),
        assets_dir=tmp_path)
    backend = NativeBackend(w)
    scripts, native_modes = [], []
    backend.chrome = SimpleNamespace(guards_armed=True, webview=SimpleNamespace(
        CoreWebView2=SimpleNamespace(ExecuteScriptAsync=scripts.append)))
    backend._snapshot = {'native_mode': 'full'}
    backend._refresh = lambda: None
    def apply_mode(mode):
        native_modes.append(mode)
        backend._snapshot['native_mode'] = mode
    backend._mode = apply_mode
    backend.run = w._ready
    monkeypatch.setattr(host, '_backend_factory', lambda owner: backend)
    w.run()
    theme = host.DesktopTheme('#000000', '#ffffff', '#123456')
    for _ in range(256):
        w.set_theme(theme)
    before = w.snapshot()
    with pytest.raises(BufferError, match='^desktop GUI operation backpressure$'):
        w.set_mode('mini')
    assert w.snapshot() == before
    for _ in range(16):
        backend._tick(None, None)
    assert scripts == [data_script('theme', {'background': '#000000',
        'foreground': '#ffffff', 'accent': '#123456'})] * 256
    assert native_modes == [] and w.snapshot()['native_mode'] == 'full'
    w.set_mode('mini')
    backend._tick(None, None)
    assert native_modes == ['mini'] and w.snapshot()['native_mode'] == 'mini'
    assert scripts[-1] == data_script('mode', 'mini')
    accepted = w.snapshot()
    w.set_mode('mini')
    backend._tick(None, None)
    assert w.snapshot() == accepted and native_modes == ['mini']


def test_pre_run_mode_and_repeat_keep_existing_behavior(window):
    w, *_ = window
    w.set_mode('mini')
    before = w.snapshot()
    assert before['mode'] == 'mini' and before['epoch'] == 1
    w.set_mode('mini')
    assert w.snapshot() == before
    w.set_mode('full')
    assert w.snapshot()['mode'] == 'full' and w.snapshot()['epoch'] == 2
    w.run()
    assert w._backend.calls == []
