"""Pinned pywebview 6.2.1 adapter. Optional CLR imports stay behind run()."""
import base64
import binascii
import hashlib
import importlib
import json
import queue
import re
import tempfile
import threading
from importlib.metadata import version
from typing import Any
from urllib.parse import unquote_to_bytes

from .desktop_frontend import build_html, data_script


def require_renderer(backend):
    if backend.renderer != 'edgechromium':
        raise RuntimeError('desktop requires edgechromium; fallback is forbidden')


def guarded_chrome(base, owner, backend):
    """Virtual dispatch is established before base starts Core initialization.

    Do not call base Core-ready/navigation-completed: they navigate before app
    guards and inject pywebview's general-purpose JS bridge respectively.
    """
    class GuardedChrome(base):
        # The pinned base supplies a CLR control; teardown releases that proxy.
        webview: Any

        def __init__(self, form, window, cache_dir):
            self._pending_document = None
            self._bootstrap_issued = False
            self._denied_ids = set()
            self.guards_armed = False
            backend.form, backend.chrome = form, self
            super().__init__(form, window, cache_dir)
            form.FormClosing += backend._closing
            backend._start_timer()

        def _fatal(self):
            backend.security.setdefault('failure_stage', 'renderer_event')
            owner._fail()

        def on_webview_ready(self, sender, args):
            stage = 'core_init'
            try:
                if not args.IsSuccess:
                    # SDK metadata only: never stringify an exception or read
                    # Message/StackTrace/Data (which may contain private paths).
                    exc = args.InitializationException
                    name = str(exc.GetType().FullName) if exc is not None else 'unknown'
                    if not re.fullmatch(r'[A-Za-z0-9_.+`]{1,160}', name):
                        name = 'unknown'
                    hresult = int(exc.HResult) if exc is not None else None
                    if 'failure_stage' not in backend.security:
                        backend.security.update(
                            failure_stage=stage, failure_type=name,
                            initialization_exception_present=exc is not None,
                            initialization_hresult=hresult,
                            initialization_hresult_hex=(f'{hresult & 0xffffffff:08X}'
                                                        if hresult is not None else None))
                    self._fatal()
                    return
                core = sender.CoreWebView2
                stage = 'owned_browser_capture'
                backend._capture_browser(core)
                stage = 'native_api_metadata'
                api = importlib.import_module('Microsoft.Web.WebView2.Core')
                # Runtime CLR metadata is the authority for these native APIs.
                names = {m.Name for m in core.GetType().GetMethods()}
                if 'get_Environment' not in names:
                    raise RuntimeError('WebView2 environment API unavailable')
                environment = core.Environment
                if not any(m.Name == 'CreateWebResourceResponse' for m in environment.GetType().GetMethods()):
                    raise RuntimeError('WebView2 deny-response API unavailable')
                self._deny_permission = api.CoreWebView2PermissionState.Deny
                self._resource_context = api.CoreWebView2WebResourceContext.All
                self._cancel_certificate = api.CoreWebView2ServerCertificateErrorAction.Cancel
                stage = 'native_event_guards'
                core.NewWindowRequested += self.on_new_window_request
                core.DownloadStarting += self.on_download_starting
                core.PermissionRequested += self._permission
                core.ServerCertificateErrorDetected += self._certificate
                core.AddWebResourceRequestedFilter('*', self._resource_context)
                core.WebResourceRequested += self.on_web_resource_request
                core.FrameNavigationStarting += self._frame_navigation
                stage = 'native_settings'
                settings = core.Settings
                for name in ('AreBrowserAcceleratorKeysEnabled','AreDefaultContextMenusEnabled',
                             'AreDefaultScriptDialogsEnabled','AreDevToolsEnabled',
                             'IsBuiltInErrorPageEnabled','IsStatusBarEnabled',
                             'IsSwipeNavigationEnabled','IsZoomControlEnabled',
                             'AreHostObjectsAllowed'):
                    setattr(settings, name, False)
                settings.IsScriptEnabled = True
                settings.IsWebMessageEnabled = True
                self.guards_armed = True
                backend.security.update(renderer='edgechromium', guards_before_payload=True,
                                        permission_api=str(self._deny_permission),
                                        resource_response_api=True)
                stage = 'first_html'
                backend.security['html_stage'] = 'argument'
                self.load_html(self.pywebview_window.html, '')
            except Exception as exc:
                backend.security['failure_stage'] = stage
                backend.security['failure_type'] = type(exc).__name__
                self._fatal()

        def load_html(self, content, base_uri):
            backend.security['html_stage'] = 'entered'
            if (not self.guards_armed or self._bootstrap_issued or
                    content != self.pywebview_window.html):
                self._fatal()
                return
            document = content.encode('utf-8')
            if len(document) > 2 * 1024 * 1024:
                self._fatal()
                return
            self.html, self.ishtml = content, True
            # Fingerprint the exact nonce-bearing helper document BEFORE native
            # navigation. Neither an early blank page nor another data URI owns it.
            self._pending_document = (len(document), hashlib.sha256(document).digest())
            self._bootstrap_issued = True
            backend.security['html_stage'] = 'navigate_call'
            self.webview.CoreWebView2.NavigateToString(content)
            backend.security['html_stage'] = 'navigate_returned'

        def load_url(self, url):
            self._fatal()

        def _matches_pending_document(self, uri):
            expected = self._pending_document
            if expected is None or len(uri) > expected[0] * 3 + 128:
                return False
            header, separator, payload = uri.partition(',')
            header = header.lower()
            if not separator or header not in (
                    'data:text/html', 'data:text/html;charset=utf-8',
                    'data:text/html;base64', 'data:text/html;charset=utf-8;base64'):
                return False
            try:
                if header.endswith(';base64'):
                    document = base64.b64decode(payload, validate=True)
                else:
                    if re.search(r'%(?![0-9a-fA-F]{2})', payload):
                        return False
                    document = unquote_to_bytes(payload)
            except (ValueError, binascii.Error):
                return False
            return (len(document) == expected[0] and
                    hashlib.sha256(document).digest() == expected[1])

        def on_navigation_start(self, sender, args):
            uri = str(args.Uri)
            backend.security['navigation_category'] = (
                'about_blank' if uri == 'about:blank' else
                'data' if uri.startswith('data:') else 'other')
            matches = self.guards_armed and self._matches_pending_document(uri)
            backend.security['bootstrap_expected_match'] = bool(matches)
            if matches:
                self._pending_document = None
                backend.security['payload_navigation_admitted'] = True
            else:
                args.Cancel = True
                if not self.guards_armed:
                    backend.security['initialization_navigation_denied'] = True
                navigation_id = getattr(args, 'NavigationId', None)
                if navigation_id is not None:
                    self._denied_ids.add(navigation_id)

        def _frame_navigation(self, sender, args):
            args.Cancel = True

        def on_navigation_completed(self, sender, args):
            if args.NavigationId in self._denied_ids:
                self._denied_ids.remove(args.NavigationId)
                return
            backend.security['payload_navigation_completed'] = bool(args.IsSuccess)
            if not args.IsSuccess:
                backend.security['failure_stage'] = 'navigation_completed'
                backend.security['navigation_status'] = str(args.WebErrorStatus)
                self._fatal()

        def on_new_window_request(self, sender, args):
            args.Handled = True

        def on_download_starting(self, sender, args):
            args.Cancel = True

        def _permission(self, sender, args):
            try:
                args.State = self._deny_permission
                args.Handled = True
            except Exception as exc:
                backend.security['failure_stage'] = 'permission'
                backend.security['failure_type'] = type(exc).__name__
                self._fatal()

        def _certificate(self, sender, args):
            args.Action = self._cancel_certificate

        def on_web_resource_request(self, sender, args):
            try:
                args.Response = self.webview.CoreWebView2.Environment.CreateWebResourceResponse(
                    None, 403, 'Forbidden', 'Content-Type: text/plain\r\n')
            except Exception as exc:
                backend.security['failure_stage'] = 'resource_response'
                backend.security['failure_type'] = type(exc).__name__
                self._fatal()

        def on_script_notify(self, sender, args):
            try:
                raw = str(args.get_WebMessageAsJson())
                if len(raw) > 100000:
                    raise ValueError('oversized desktop message')
                message = json.loads(raw)
                if isinstance(message, dict) and message.get('type') == 'startup':
                    stage, line = message.get('stage'), message.get('line')
                    if stage not in ('bootstrap', 'script_error', 'promise_error', 'csp_blocked'):
                        raise ValueError('invalid startup stage')
                    if type(line) is not int or not 0 <= line <= 100000:
                        raise ValueError('invalid startup line')
                    # Fixed stage and line only: no JS error text, URI or sample.
                    backend.security['document_stage'] = stage
                    backend.security['document_line'] = line
                    if stage == 'csp_blocked':
                        backend.security['csp_blocks'] = backend.security.get('csp_blocks', 0) + 1
                    if stage in ('script_error', 'promise_error'):
                        backend.security['failure_stage'] = stage
                        self._fatal()
                    return
                owner._message(message)
            except Exception as exc:
                backend.security['failure_stage'] = 'private_message'
                backend.security['failure_type'] = type(exc).__name__
                self._fatal()

        def clear_user_data(self):
            # Close the owned controller while its parent HWND and STA are still
            # live. The pinned FormClosed callback repeats this; make it a no-op.
            control = self.webview
            if control is None:
                return
            if self.guards_armed:
                core = control.CoreWebView2
                # Detach exactly the Core handlers we registered, not SDK or
                # foreign handlers. This breaks our delegate/owner reference cycle.
                core.NewWindowRequested -= self.on_new_window_request
                core.DownloadStarting -= self.on_download_starting
                core.PermissionRequested -= self._permission
                core.ServerCertificateErrorDetected -= self._certificate
                core.WebResourceRequested -= self.on_web_resource_request
                core.FrameNavigationStarting -= self._frame_navigation
                backend.security['owned_core_handlers_detached'] = True
            control.Dispose()  # pinned SDK Dispose closes its native controller
            backend.security['owned_control_disposed'] = True
            self.webview = None
            self.form.webview = None  # release the pinned Form's second owned root
            backend.security['owned_control_references_released'] = True

    return GuardedChrome


class NativeBackend:
    def __init__(self, owner):
        self.owner = owner
        # These objects are runtime CLR proxies; importing them eagerly breaks
        # the ordinary CLI/Linux dependency boundary.
        self.form: Any = None
        self.chrome: Any = None
        self.native: Any = None
        self._operations = queue.Queue(maxsize=256)
        self._allow_close = False
        self._timer: Any = None
        self._full = None
        self._snapshot: dict[str, Any] = {}
        self._snapshot_lock = threading.Lock()
        self._profile_root: str | None = None
        self._browser_process: Any = None
        self.security = {}

    def run(self):
        if version('pywebview') != '6.2.1':
            raise RuntimeError('desktop adapter requires pinned pywebview 6.2.1')
        webview = importlib.import_module('webview')
        if webview.windows:
            raise RuntimeError('desktop helper requires its own process')
        guilib = importlib.import_module('webview.guilib')
        backend = guilib.initialize('edgechromium')
        require_renderer(backend)  # BEFORE create_window, including any Form.
        self.native = backend
        html = build_html(self.owner.theme, self.owner.assets_dir)
        original = backend.Chromium.EdgeChrome
        backend.Chromium.EdgeChrome = guarded_chrome(original, self.owner, self)
        # The finite probe supplies a project-owned evidence directory. Normal
        # callers retain a private temporary profile, never a user browser profile.
        try:
            # Retain profiles, especially failures: cleanup is parent-owned and
            # must never race an uncertain browser descendant.
            profile = tempfile.mkdtemp(prefix='gentle-stream-webview-', dir=self._profile_root)
            webview.settings['ALLOW_DOWNLOADS'] = False
            webview.settings['ALLOW_FILE_URLS'] = False
            webview.settings['REMOTE_DEBUGGING_PORT'] = None
            webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
            self.window = webview.create_window(
                'gentle-stream', html=html, width=1100, height=700,
                min_size=(280,140), frameless=True, easy_drag=False,
                shadow=False, resizable=True, background_color=self.owner.theme.background)
            webview.start(gui='edgechromium', debug=False, private_mode=True,
                          storage_path=profile, http_server=False)
            self._observe_browser_exit()
        finally:
            backend.Chromium.EdgeChrome = original
            self._timer = None

    def _capture_browser(self, core):
        # The owned Core is the sole identity authority. Force the native handle
        # open while it is live, so a later PID reuse cannot change this identity.
        process = self.native.Chromium.Process.GetProcessById(int(core.BrowserProcessId))
        try:
            _ = process.Handle
        except Exception:
            process.Dispose()
            raise
        self._browser_process = process
        self.security['browser_process_captured'] = True

    def _observe_browser_exit(self):
        process = self._browser_process
        if process is not None:
            try:
                self.security['browser_exit_observed'] = bool(process.WaitForExit(3000))
            finally:
                process.Dispose()
                self._browser_process = None

    def _start_timer(self):
        # Constructor and timer callbacks both execute on the one STA GUI thread.
        self._timer = self.native.WinForms.Timer()
        self._timer.Interval = 15
        self._timer.Tick += self._tick
        self._timer.Start()

    def send(self, kind, value):
        try:
            self._operations.put_nowait((kind, value))
        except queue.Full:
            raise BufferError('desktop GUI operation backpressure') from None

    def close(self):
        # Reserved flag cannot be starved by a full output/control queue.
        self._allow_close = True

    def _closing(self, sender, args):
        if not self._allow_close:
            args.Cancel = True
            self.owner._request_close()

    def _tick(self, sender, args):
        try:
            # Fatal initialization must close even before guards are armed.
            # Never put the readiness/guard gate ahead of this reserved flag.
            if self._allow_close:
                self._timer.Stop()
                self.chrome.clear_user_data()
                self.form.Close()
                return
            if not self._snapshot:
                # Show may choose a cascade origin outside the work area. Apply
                # the same safety clamp BEFORE any baseline or mode capture.
                self._clamp()
                self._refresh()
            if not self.chrome.guards_armed:
                return
            if self.owner.snapshot()['state'] != 'ready':
                self._refresh()
                return
            for _ in range(16):
                try:
                    kind, value = self._operations.get_nowait()
                except queue.Empty:
                    break
                if kind == 'mode':
                    self._mode(value)
                elif kind in ('drag', 'grip'):
                    self._move_loop(kind, value)
                    continue
                self.chrome.webview.CoreWebView2.ExecuteScriptAsync(data_script(kind, value))
            self._refresh()
        except Exception as exc:
            self.security['failure_stage'] = 'gui_tick'
            self.security['failure_type'] = type(exc).__name__
            self.owner._fail()

    def _mode(self, mode):
        f = self.form
        drawing = importlib.import_module('System.Drawing')
        scale = f._scale
        if mode == 'mini':
            if self._full is None:
                self._full = (f.Bounds, bool(f.TopMost))
            f.MinimumSize = drawing.Size(0, 0)
            f.MaximumSize = drawing.Size(0, 0)
            f.Size = drawing.Size(round(420*scale),round(220*scale))
            position = self.owner.mini_position
            if position:
                f.Location = drawing.Point(round(position[0]*scale),round(position[1]*scale))
            self._clamp()
            f.MinimumSize = f.Size
            f.MaximumSize = f.Size
            f.TopMost = True
        elif self._full:
            self._remember_mini()
            bounds, topmost = self._full
            f.MaximumSize = drawing.Size(0, 0)
            f.MinimumSize = drawing.Size(round(280*scale),round(140*scale))
            f.Bounds, f.TopMost = bounds, topmost
            self._full = None
            self._clamp()
        self._refresh()

    def _clamp(self):
        f = self.form
        area = self.native.WinForms.Screen.FromControl(f).WorkingArea
        f.Left = max(area.Left, min(f.Left, area.Right-f.Width))
        f.Top = max(area.Top, min(f.Top, area.Bottom-f.Height))

    def _remember_mini(self):
        self.owner.mini_position = (round(self.form.Left/self.form._scale),
                                    round(self.form.Top/self.form._scale))

    def _move_loop(self, kind, edge):
        # Native nonclient move/size loop operates ONLY on our captured Form.
        # Ignore synthetic or released clicks: no programmatic OS-drag proof.
        forms = self.native.WinForms
        if forms.Control.MouseButtons != forms.MouseButtons.Left:
            return
        import ctypes
        user32 = ctypes.windll.user32
        user32.ReleaseCapture()
        hit = 2 if kind == 'drag' else {'w':10,'e':11,'n':12,'nw':13,
                                      'ne':14,'s':15,'sw':16,'se':17}[edge]
        user32.SendMessageW.argtypes = [ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.SendMessageW(int(self.form.Handle.ToInt64()),0xA1,hit,0)
        self._clamp()
        if self._full:
            self._remember_mini()

    def _refresh(self):
        f = self.form
        if self._full:
            self._remember_mini()
        result = {'identity':[id(f),id(self.chrome.webview),int(f.Handle.ToInt64())],
                  'geometry':[f.Left,f.Top,f.Width,f.Height], 'topmost':bool(f.TopMost),
                  'frameless':str(f.FormBorderStyle)=='None',
                  'security':dict(self.security)}
        with self._snapshot_lock:
            self._snapshot = result

    def snapshot(self):
        with self._snapshot_lock:
            return dict(self._snapshot)
