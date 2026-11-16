from types import SimpleNamespace

import pytest

from gentle_stream.desktop_webview import guarded_chrome, require_renderer


def test_fallback_rejected_before_construction():
    with pytest.raises(RuntimeError, match='edgechromium'):
        require_renderer(SimpleNamespace(renderer='mshtml'))
    require_renderer(SimpleNamespace(renderer='edgechromium'))


def test_virtual_deny_handlers_never_call_unsafe_base():
    class Unsafe:
        def on_new_window_request(self,*args):
            raise AssertionError('external browser')
    owner = SimpleNamespace(_fail=lambda: None)
    cls = guarded_chrome(Unsafe, owner, SimpleNamespace(security={}))
    chrome = object.__new__(cls)
    popup = SimpleNamespace(Handled=False)
    chrome.on_new_window_request(None,popup)
    assert popup.Handled is True
    download = SimpleNamespace(Cancel=False)
    chrome.on_download_starting(None,download)
    assert download.Cancel is True
    nav = SimpleNamespace(Uri='https://example.invalid',Cancel=False)
    chrome._pending_document = None
    chrome.guards_armed = True
    chrome.on_navigation_start(None,nav)
    assert nav.Cancel is True
    nav = SimpleNamespace(Uri='about:blank',Cancel=False)
    chrome.on_navigation_start(None,nav)
    assert nav.Cancel is True
    nav = SimpleNamespace(Uri='about:blank',Cancel=False)
    chrome.on_navigation_start(None,nav)
    assert nav.Cancel is True


def test_initialization_navigation_cannot_consume_payload_grant():
    class Base:
        def __init__(self, form, window, cache):
            self.pywebview_window = window
            self.webview = SimpleNamespace(CoreWebView2=SimpleNamespace(
                NavigateToString=lambda html: navigated.append(html)))
            # The pinned base binds NavigationStarting before EnsureCore async.
            self.on_navigation_start(None, early)

        def on_navigation_start(self, sender, args):
            raise AssertionError('unsafe initialization navigation handler')

    navigated = []
    early = SimpleNamespace(Uri='about:blank', Cancel=False, NavigationId=1)
    backend = SimpleNamespace(_closing=lambda *args: None, _start_timer=lambda: None,
                              security={})
    class Event:
        def __iadd__(self, callback):
            return self
    form = SimpleNamespace(FormClosing=Event())
    chrome = guarded_chrome(Base, SimpleNamespace(_fail=lambda: pytest.fail('host failed')), backend)(
        form, SimpleNamespace(html='owned document'), 'unused')
    assert early.Cancel is True
    chrome.guards_armed = True
    chrome.load_html('owned document', '')
    from urllib.parse import quote
    payload = SimpleNamespace(Uri='data:text/html;charset=utf-8,' + quote('owned document'),
                              Cancel=False, NavigationId=2)
    chrome.on_navigation_start(None, payload)
    assert payload.Cancel is False
    assert navigated == ['owned document']
    extra = SimpleNamespace(Uri='about:blank', Cancel=False, NavigationId=3)
    chrome.on_navigation_start(None, extra)
    assert extra.Cancel is True
    assert backend.security['html_stage'] == 'navigate_returned'
    assert backend.security['navigation_category'] == 'about_blank'
    assert 'owned document' not in str(backend.security)


def test_owned_browser_handle_precedes_disposal_and_waits_after_gui():
    from gentle_stream.desktop_webview import NativeBackend

    calls = []
    process = SimpleNamespace(
        Handle=123,
        WaitForExit=lambda timeout: calls.append(('wait', timeout)) or True,
        Dispose=lambda: calls.append(('release', None)))
    backend = NativeBackend(SimpleNamespace())
    backend.native = SimpleNamespace(Chromium=SimpleNamespace(Process=SimpleNamespace(
        GetProcessById=lambda pid: calls.append(('capture', pid)) or process)))
    core = SimpleNamespace(BrowserProcessId=42)
    backend._capture_browser(core)
    cls = guarded_chrome(object, SimpleNamespace(), backend)
    chrome = object.__new__(cls)
    chrome.webview = SimpleNamespace(
        Dispose=lambda: calls.append(('dispose', None)))
    chrome.form = SimpleNamespace(webview=chrome.webview)
    chrome.guards_armed = False
    chrome.clear_user_data()
    assert calls == [('capture', 42), ('dispose', None)]
    assert backend.security['browser_process_captured'] is True
    assert not backend.security.get('browser_exit_observed', False)
    backend._observe_browser_exit()
    assert calls == [('capture', 42), ('dispose', None), ('wait', 3000), ('release', None)]
    assert backend.security['browser_exit_observed'] is True


@pytest.mark.parametrize('exception', [None, 'sdk'])
def test_sdk_initialization_failure_preserves_metadata_and_closes_before_guards(monkeypatch, tmp_path, exception):
    from gentle_stream import desktop_window as host
    from gentle_stream.desktop_webview import NativeBackend

    class SDKException:
        HResult = -2147024891

        def GetType(self):
            return SimpleNamespace(FullName='System.UnauthorizedAccessException')

        def __str__(self):
            raise AssertionError('SDK exception text must never be read')

    actions, closed = [], []
    window = host.DesktopWindow(on_input=lambda text: None, on_resize=lambda c, r: None,
        on_action=actions.append, theme=host.DesktopTheme('#101010', '#eeeeee', '#abcdef'),
        assets_dir=tmp_path)
    backend = NativeBackend(window)
    backend.form = SimpleNamespace(Close=lambda: closed.append('form'))
    backend._timer = SimpleNamespace(Stop=lambda: closed.append('timer'))
    chrome = object.__new__(guarded_chrome(object, window, backend))
    chrome.guards_armed = False
    chrome.form = backend.form
    chrome.webview = SimpleNamespace(Dispose=lambda: closed.append('webview'))
    backend.chrome = chrome
    args = SimpleNamespace(IsSuccess=False,
        InitializationException=SDKException() if exception else None)
    backend.run = lambda: chrome.on_webview_ready(None, args)
    monkeypatch.setattr(host, '_backend_factory', lambda owner: backend)
    backend.send('output', [1, 'must stay queued'])
    window.run()
    assert actions == ['host_failed']
    assert window.snapshot()['state'] == 'failed'
    assert backend.security == {
        'failure_stage': 'core_init',
        'failure_type': 'System.UnauthorizedAccessException' if exception else 'unknown',
        'initialization_exception_present': bool(exception),
        'initialization_hresult': -2147024891 if exception else None,
        'initialization_hresult_hex': '80070005' if exception else None,
    }
    assert closed == []
    backend._tick(None, None)
    assert closed == ['timer', 'webview', 'form']
    assert backend._operations.get_nowait() == ('output', [1, 'must stay queued'])
    assert not chrome.guards_armed


@pytest.mark.parametrize('encoding', ['percent', 'base64'])
def test_only_exact_pending_document_is_admitted_once(encoding):
    import base64
    from urllib.parse import quote

    document = '<!doctype html><script nonce="session-123">/*日本語*/</script>'
    def uri(text):
        if encoding == 'base64':
            return 'data:text/html;charset=utf-8;base64,' + base64.b64encode(text.encode()).decode()
        return 'data:text/html;charset=utf-8,' + quote(text)

    class Event:
        def __iadd__(self, callback):
            return self

    class Base:
        def __init__(self, form, window, cache):
            self.pywebview_window = window
            self.webview = SimpleNamespace(CoreWebView2=SimpleNamespace(
                NavigateToString=lambda html: sent.append(html)))

    sent, failed = [], []
    backend = SimpleNamespace(security={}, _closing=lambda *args: None, _start_timer=lambda: None)
    chrome = guarded_chrome(Base, SimpleNamespace(_fail=lambda: failed.append(True)), backend)(
        SimpleNamespace(FormClosing=Event()), SimpleNamespace(html=document), 'unused')
    chrome.guards_armed = True
    chrome.load_html(document, '')
    assert sent == [document]
    invalid = [uri(document.replace('session-123', 'session-456')),
               'data:text/html;charset=utf-8,' + 'x' * (len(document.encode()) * 3 + 129),
               'data:text/html;charset=utf-8,%GG',
               'data:text/html;charset=utf-8;base64,!!!!',
               'data:text/plain;charset=utf-8,' + quote(document),
               'data:text/html;charset=utf-16,' + quote(document),
               'about:blank', 'https://example.invalid/']
    for navigation_id, candidate in enumerate(invalid, 1):
        args = SimpleNamespace(Uri=candidate, Cancel=False, NavigationId=navigation_id)
        chrome.on_navigation_start(None, args)
        assert args.Cancel is True
        assert 'payload_navigation_admitted' not in backend.security
    frame = SimpleNamespace(Uri=uri(document), Cancel=False)
    chrome._frame_navigation(None, frame)
    assert frame.Cancel is True
    owned = SimpleNamespace(Uri=uri(document), Cancel=False, NavigationId=20)
    chrome.on_navigation_start(None, owned)
    assert owned.Cancel is False
    assert backend.security['payload_navigation_admitted'] is True
    accepted = dict(backend.security)
    replay = SimpleNamespace(Uri=uri(document), Cancel=False, NavigationId=21)
    chrome.on_navigation_start(None, replay)
    assert replay.Cancel is True
    assert backend.security['payload_navigation_admitted'] == accepted['payload_navigation_admitted']
    assert document not in str(backend.security) and uri(document) not in str(backend.security)
    assert failed == []
    chrome.load_html(document, '')
    assert failed == [True] and sent == [document]
    replay.Cancel = False
    chrome.on_navigation_start(None, replay)
    assert replay.Cancel is True


def test_initial_full_geometry_is_clamped_before_mode_baseline(monkeypatch, tmp_path):
    from gentle_stream import desktop_webview as adapter
    from gentle_stream import desktop_window as host

    def size(w, h):
        return SimpleNamespace(Width=w, Height=h)

    class Form:
        Left, Top, Width, Height = 104, 104, 1100, 700
        TopMost, FormBorderStyle, _scale = False, 'None', 1
        Handle = SimpleNamespace(ToInt64=lambda: 123)
        MinimumSize, MaximumSize = size(280, 140), size(0, 0)

        @property
        def Bounds(self):
            return (self.Left, self.Top, self.Width, self.Height)

        @Bounds.setter
        def Bounds(self, rect):
            self.Left, self.Top, self.Width, self.Height = rect

        @property
        def Size(self):
            return size(self.Width, self.Height)

        @Size.setter
        def Size(self, value):
            self.Width, self.Height = value.Width, value.Height

        @property
        def Location(self):
            return (self.Left, self.Top)

        @Location.setter
        def Location(self, value):
            self.Left, self.Top = value.X, value.Y

    original_import = adapter.importlib.import_module
    drawing = SimpleNamespace(Size=size, Point=lambda x, y: SimpleNamespace(X=x, Y=y))
    monkeypatch.setattr(adapter.importlib, 'import_module',
        lambda name: drawing if name == 'System.Drawing' else original_import(name))
    window = host.DesktopWindow(on_input=lambda text: None, on_resize=lambda c, r: None,
        on_action=lambda action: None, theme=host.DesktopTheme('#101010', '#eeeeee', '#abcdef'),
        assets_dir=tmp_path)
    backend = adapter.NativeBackend(window)
    backend.form = Form()
    area = SimpleNamespace(Left=0, Top=0, Right=1366, Bottom=720)
    backend.native = SimpleNamespace(WinForms=SimpleNamespace(Screen=SimpleNamespace(
        FromControl=lambda form: SimpleNamespace(WorkingArea=area))))
    backend.chrome = SimpleNamespace(guards_armed=True, webview=SimpleNamespace(
        CoreWebView2=SimpleNamespace(ExecuteScriptAsync=lambda script: None)))
    backend.run = window._ready
    monkeypatch.setattr(host, '_backend_factory', lambda owner: backend)
    window.run()
    backend._tick(None, None)
    # The initial native size fits, but the default cascade origin does not.
    assert backend.form.Bounds == (104, 20, 1100, 700)
    initial = backend.snapshot()
    window.set_mode('mini')
    backend._tick(None, None)
    mini = backend.snapshot()
    assert mini['geometry'][2:] == [420, 220]
    assert mini['identity'] == initial['identity'] and mini['topmost']
    assert backend.form.MinimumSize == backend.form.MaximumSize
    backend.form.Left, backend.form.Top = 150, 60
    window.set_mode('full')
    backend._tick(None, None)
    assert backend.form.Bounds == tuple(initial['geometry'])
    assert backend.snapshot()['identity'] == initial['identity']
    assert backend.form.TopMost is False
    assert window.mini_position == (150, 60)
    assert backend.form.MinimumSize == size(280, 140)
    assert backend.form.MaximumSize == size(0, 0)


def test_acknowledged_close_disposes_and_releases_owned_control_before_form(monkeypatch, tmp_path):
    from gentle_stream import desktop_window as host
    from gentle_stream.desktop_webview import NativeBackend

    calls = []
    class Event:
        def __init__(self, name):
            self.name = name
        def __isub__(self, handler):
            calls.append('detach:' + self.name)
            return self
    actions = []
    window = host.DesktopWindow(on_input=lambda text: None, on_resize=lambda c, r: None,
        on_action=actions.append, theme=host.DesktopTheme('#101010', '#eeeeee', '#abcdef'),
        assets_dir=tmp_path)
    backend = NativeBackend(window)
    names = ('NewWindowRequested', 'DownloadStarting', 'PermissionRequested',
             'ServerCertificateErrorDetected', 'WebResourceRequested', 'FrameNavigationStarting')
    core = SimpleNamespace(**{name: Event(name) for name in names})
    control = SimpleNamespace(CoreWebView2=core, Dispose=lambda: calls.append('control'))
    form = SimpleNamespace(webview=control, Close=lambda: calls.append('form'))
    chrome = object.__new__(guarded_chrome(object, window, backend))
    chrome.form, chrome.webview, chrome.guards_armed = form, control, True
    backend.chrome, backend.form = chrome, form
    backend._timer = SimpleNamespace(Stop=lambda: calls.append('timer'))
    backend.run = window._ready
    monkeypatch.setattr(host, '_backend_factory', lambda owner: backend)
    window.run()
    closing = SimpleNamespace(Cancel=False)
    backend._closing(None, closing)
    assert closing.Cancel is True and actions == ['ready', 'quit'] and calls == []
    window.finish_close()
    backend._tick(None, None)
    assert calls == ['timer'] + ['detach:' + name for name in names] + ['control', 'form']
    assert chrome.webview is None and form.webview is None
    assert backend.security['owned_core_handlers_detached'] is True
    assert backend.security['owned_control_disposed'] is True
    assert backend.security['owned_control_references_released'] is True
    assert backend._browser_process is None  # no fabricated browser/process in this unit test
    chrome.clear_user_data()  # upstream FormClosed repeats this callback
    assert calls[-2:] == ['control', 'form'] and calls.count('control') == 1
