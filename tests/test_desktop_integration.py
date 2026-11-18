"""Public session lifecycle and the real StreamApp; no audio/models/daemon."""
import threading
from dataclasses import replace

import pytest
from textual.widgets import Header

from tests import test_ui_toolbar_contracts as toolbar

ui = toolbar.ui


def test_normal_close_waits_for_child_drain_then_ack(tmp_path):
    from gentle_stream.desktop_control import SessionChannel
    from gentle_stream.desktop_session import DesktopSession
    channel = SessionChannel.create(tmp_path)
    log, release, started = [], threading.Event(), threading.Event()
    class PTY:
        def __init__(self, *a, **kw): pass
        def start(self):
            log.append('spawn')
            started.set()
        def resize(self, cols, rows): pass
        def close(self, request_shutdown, timeout=10):
            request_shutdown()
            assert channel.stopping()
            assert release.wait(2)
            log.extend(['music_closed', 'drained', 'exit'])
            return 0
        def snapshot(self): return {'state': 'exited', 'reader_done': True}
    class Window:
        def __init__(self, **kw): self.action = kw['on_action']
        def run(self):
            self.action('ready')
            assert started.wait(2)
            self.action('quit')
            assert 'ACK' not in log
            release.set()
            assert session.done.wait(2)
        def finish_close(self): log.append('ACK')
        def snapshot(self): return {'epoch': 0, 'cols': 80, 'rows': 24, 'state': 'closing'}
    session = DesktopSession(channel, ['python', 'child'], window_factory=Window, pty_factory=PTY)
    assert session.run() == 0
    assert log == ['spawn', 'music_closed', 'drained', 'exit', 'ACK']


@pytest.mark.parametrize('fatal', [False, True])
def test_close_during_startup_is_preserved_and_fatal_returns_promptly(tmp_path, fatal):
    from gentle_stream.desktop_control import SessionChannel
    from gentle_stream.desktop_session import DesktopSession
    channel = SessionChannel.create(tmp_path)
    entered, release = threading.Event(), threading.Event()
    log = []
    class PTY:
        def __init__(self, *a, **kw): pass
        def start(self):
            entered.set()
            assert release.wait(2)
            assert channel.stopping()  # Shutdown existed BEFORE the late spawn.
            log.append('late_spawn_stopped')
        def close(self, request_shutdown, timeout=10):
            request_shutdown()
            log.extend(['drain', 'exit'])
            return 0
    class Window:
        def __init__(self, **kw): self.action = kw['on_action']
        def run(self):
            self.action('ready')
            assert entered.wait(2)
            self.action('host_failed' if fatal else 'quit')
            log.append('callback_returned')
            assert not session.done.is_set()
            release.set()
        def finish_close(self): log.append('ACK')
    session = DesktopSession(channel, ['python', 'child'], window_factory=Window, pty_factory=PTY)
    assert session.run() == (1 if fatal else 0)
    assert log[:2] == ['callback_returned', 'late_spawn_stopped']
    assert log[2:] == (['drain', 'exit'] if fatal else ['drain', 'exit', 'ACK'])


def test_post_start_transport_failure_stops_without_exit_callback(tmp_path, capsys):
    from gentle_stream.desktop_control import SessionChannel
    from gentle_stream.desktop_session import DesktopSession
    from gentle_stream.desktop_transport import TransportError

    channel = SessionChannel.create(tmp_path)
    started, failed = threading.Event(), threading.Event()
    log = []

    class PTY:
        def __init__(self, *args, **kwargs): pass
        def start(self): started.set()
        def snapshot(self):
            return {'state': 'failed' if failed.is_set() else 'running',
                    'reader_done': False, 'exit_status': None}
        def close(self, request_shutdown, timeout=10):
            assert timeout == 10.0
            request_shutdown()
            log.append('shutdown')
            raise TransportError('completion unconfirmed')

    class Window:
        def __init__(self, **kwargs): self.action = kwargs['on_action']
        def snapshot(self):
            return {'epoch': 0, 'cols': 0, 'rows': 0, 'state': 'ready'}
        def run(self):
            self.action('ready')
            assert started.wait(2)
            # No on_exit, resize, output or input callback reports this failure.
            failed.set()
            self.autonomous_cleanup = session.done.wait(1)
            if not self.autonomous_cleanup:
                # Bound the broken implementation's test teardown, not its proof.
                self.action('host_failed')
        def finish_close(self): log.append('ACK')

    session = DesktopSession(channel, ['python', 'child'], window_factory=Window, pty_factory=PTY)
    result = session.run()
    assert session.window.autonomous_cleanup, 'post-start transport failure was not supervised'
    assert result == 1 and session.done.is_set() and session.snapshot()['fatal']
    assert channel.stopping() and session.child_status is None
    assert session.error == 'TransportError' and log == ['shutdown']
    assert capsys.readouterr() == ('', '')


def test_invalid_nonce_and_stale_layout_rejected_without_effects(tmp_path):
    from gentle_stream.desktop_control import SessionChannel
    channel = SessionChannel.create(tmp_path)
    before = sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir())
    with pytest.raises(ValueError, match='Invalid desktop session'):
        SessionChannel.open(tmp_path, '0' * 48)
    assert before == sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir())
    channel.request_layout(2, 80, 24)
    channel.publish({'epoch': 1, 'cols': 80, 'rows': 24, 'mode': 'mini',
                     'theme': ['#000000', '#ffffff', '#ff0000'], 'regions': [[0, 0, 80, 20]]})
    assert channel.state() is None
    assert not channel.stopping()


@pytest.mark.parametrize('size', [(40, 8), (60, 12)])
@pytest.mark.parametrize('desktop', [True, False], ids=['desktop', 'terminal'])
@pytest.mark.asyncio
async def test_mini_chrome_and_controls_fit_without_changing_terminal_mode(ui, tmp_path, size, desktop):
    from gentle_stream.desktop_control import SessionChannel

    app, player, _, _, _ = ui
    if desktop:
        app.desktop_control = SessionChannel.create(tmp_path / 'channel')
        app.desktop_control.request_layout(1, *size)
    async with app.run_test(size=size) as pilot:
        root, timer = app.screen, app._ui_timer
        header = root.query_one(Header)
        toggle = root.query_one('#mode-toggle')
        commands = root.query_one('#commands')
        spectrum = root.query_one('#spectrum')
        controls = [root.query_one('#' + name) for name in
                    ('previous', 'play', 'next', 'quieter', 'volume-meter', 'louder')]
        assert header.display and toggle.display
        await pilot.press('r')
        await pilot.pause(0)
        assert app.ui_mode == 'mini'
        assert header.display == (not desktop)
        assert toggle.display == (not desktop)
        assert commands.region.height == (3 if desktop else 1)
        for control in controls:
            assert control.display and control.region.width and control.region.height
            assert commands.region.contains_region(control.region)
        assert len({control.region.y for control in controls}) == 1
        assert spectrum.runtime_height == spectrum.region.height
        if desktop:
            assert controls[2].region.right < controls[3].region.x
            assert controls[3].region.right < controls[4].region.x
            assert controls[4].region.right < controls[5].region.x
            assert controls[0].region.y == commands.region.y + 1
        assert await pilot.click('#play')
        player.toggle.assert_called_once()
        app.set_focus(None)
        await pilot.press('r')
        assert app.ui_mode == 'full' and header.display and toggle.display
        assert app.screen is root and app.player is player and app._ui_timer is timer


@pytest.mark.asyncio
async def test_real_app_same_player_timer_draft_theme_drag_and_shutdown(ui, tmp_path, monkeypatch):
    from gentle_stream.desktop_control import SessionChannel
    app, player, shell, store, library = ui
    channel = SessionChannel.create(tmp_path / 'channel')
    app.desktop_control = channel
    frames = []
    publish = channel.publish

    def observe(frame):
        publish(frame)
        frames.append(frame)

    monkeypatch.setattr(channel, 'publish', observe)
    async with app.run_test(size=(80, 24)) as pilot:
        timer, root, spectrum = app._ui_timer, app.screen, app.query_one('#spectrum')

        def geometry():
            return {name: tuple(root.query_one('#' + name).region) for name in
                    ('panel', 'spectrum', 'mode-toggle', 'commands', 'transport',
                     'quieter', 'volume-meter', 'louder')}

        async def ready(predicate):
            # Bounded readiness, not a longer fixed sleep accepting empty mini.
            for _ in range(50):
                state = channel.state()
                if predicate(state):
                    return state
                await pilot.pause(.02)
            pytest.fail(f'desktop readiness: mode={app.ui_mode}, size={tuple(app.size)}, '
                        f'geometry={geometry()}, state={channel.state()}')

        def mini(state):
            return state is not None and state['mode'] == 'mini' and bool(state['regions'])

        def assert_grants(state):
            info = geometry()
            assert mini(state), info
            cols, rows = state['cols'], state['rows']
            cells = set()
            for x, y, w, h in state['regions']:
                assert 0 <= x < x + w <= cols, info
                assert 0 <= y < y + h <= rows, info
                cells.update((cx, cy) for cx in range(x, x + w) for cy in range(y, y + h))
            panel = root.query_one('#panel').region
            toggle = root.query_one('#mode-toggle').region
            strip = root.query_one('#commands').region
            expected = {(x, y) for x in range(max(0, panel.x), min(cols, panel.right))
                        for y in range(max(0, panel.y), min(rows, panel.bottom, strip.y))
                        if not toggle.contains(x, y)}
            assert cells == expected and cells, info  # Actual eligible background.
            assert any(spectrum.region.contains(x, y) for x, y in cells), info
            for name in ('mode-toggle', 'commands', 'transport', 'previous', 'play',
                         'next', 'quieter', 'volume-meter', 'louder'):
                region = root.query_one('#' + name).region
                assert not any(region.contains(x, y) for x, y in cells), (name, info)

        async def stable_grants():
            assert_grants(await ready(mini))
            first, polls = len(frames), player.spectrum.call_count
            await ready(lambda state: mini(state) and player.spectrum.call_count >= polls + 3)
            settled = frames[first:]
            assert len(settled) >= 3, geometry()
            for frame in settled:
                assert_grants(frame)  # No empty overwrite between ordinary ticks.

        channel.request_layout(1, 80, 24)
        channel.request_toggle()
        await stable_grants()
        assert app.ui_mode == 'mini' and app._ui_timer is timer
        await pilot.press('f2')
        await ready(lambda state: state is not None and state['regions'] == [])
        first, polls = len(frames), player.spectrum.call_count
        app.screen.query_one('#pref-background').value = '#123456'
        await ready(lambda state: state is not None and player.spectrum.call_count >= polls + 3)
        assert frames[first:] and all(frame['regions'] == [] for frame in frames[first:])
        state = channel.state()
        assert state is not None and state['theme'][0] != '#123456'
        await pilot.press('escape')
        await stable_grants()  # Dismissal restores eligibility without a mode toggle.
        applied = replace(app.preferences, theme='gentle', background='#123456')
        app.apply_preferences(applied)
        await ready(lambda state: mini(state) and state['theme'][0] == '#123456')
        await stable_grants()
        saved_timer = app._ui_timer
        await pilot.resize_terminal(60, 12)
        channel.request_layout(2, 60, 12)
        await ready(lambda state: mini(state) and state['epoch'] == 2 and
                    (state['cols'], state['rows']) == (60, 12))
        await stable_grants()
        app.set_focus(None)
        await pilot.press('r')
        await ready(lambda state: state is not None and state['mode'] == 'full' and state['regions'] == [])
        assert app.ui_mode == 'full'
        assert app.player is player and app.screen is root and app.query_one('#spectrum') is spectrum
        assert saved_timer is app._ui_timer
        player.start.assert_called_once()
        player.close.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        channel.request_shutdown()
        await pilot.pause(.1)
    player.close.assert_called_once()
    shell.cancel.assert_called_once()
    assert not store.path.exists() and not library.path.exists()


@pytest.mark.parametrize('change', [
    {'epoch': True}, {'cols': 501}, {'rows': 0}, {'mode': 'arbitrary'},
    {'theme': ['red', '#ffffff', '#000000']},
    {'regions': [[0, 0, 81, 1]]}, {'regions': [[True, 0, 1, 1]]},
    {'regions': [[0, 0, 1, 1]] * 65}, {'extra': 'not a control API'},
])
def test_bad_frame_is_atomic_and_keeps_counters_and_shutdown(tmp_path, change):
    from gentle_stream.desktop_control import SessionChannel
    channel = SessionChannel.create(tmp_path)
    channel.request_layout(1, 80, 24)
    frame = {'epoch': 1, 'cols': 80, 'rows': 24, 'mode': 'full',
             'theme': ['#000000', '#ffffff', '#ff0000'], 'regions': []}
    channel.publish(frame)
    channel.request_toggle()
    before = sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir())
    with pytest.raises(ValueError, match='Invalid desktop session'):
        channel.publish({**frame, **change})
    assert before == sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir())
    assert channel.state() == frame and not channel.stopping()
    assert channel.take_toggle() and not channel.take_toggle()


def test_pre_ready_close_prevents_spawn(tmp_path):
    from gentle_stream.desktop_control import SessionChannel
    from gentle_stream.desktop_session import DesktopSession
    channel = SessionChannel.create(tmp_path)
    log = []
    class PTY:
        def __init__(self, *args, **kwargs): pass
        def start(self): pytest.fail('closed session must not spawn')
    class Window:
        def __init__(self, **kwargs): self.action = kwargs['on_action']
        def run(self):
            self.action('quit')
            assert channel.stopping()
        def finish_close(self): log.append('ACK')
    session = DesktopSession(channel, ['python', 'child'], window_factory=Window, pty_factory=PTY)
    assert session.run() == 0 and session.done.is_set()
    assert log == ['ACK']


def test_cleanup_uncertainty_never_normal_ack(tmp_path):
    from gentle_stream.desktop_control import SessionChannel
    from gentle_stream.desktop_session import DesktopSession
    channel = SessionChannel.create(tmp_path)
    started = threading.Event()
    log = []
    class PTY:
        def __init__(self, *args, **kwargs): pass
        def start(self): started.set()
        def close(self, request_shutdown, timeout=10):
            request_shutdown()
            raise TimeoutError('unconfirmed child exit/drain')
    class Window:
        def __init__(self, **kwargs): self.action = kwargs['on_action']
        def run(self):
            self.action('ready')
            assert started.wait(2)
            self.action('quit')
        def finish_close(self): log.append('ACK')
    session = DesktopSession(channel, ['python', 'child'], window_factory=Window, pty_factory=PTY)
    assert session.run() == 1 and session.done.is_set()
    assert session.child_status is None and session.error == 'TimeoutError'
    assert log == [] and channel.stopping()


def test_stale_layout_output_retry_is_lossless_and_output_is_not_control(tmp_path):
    from gentle_stream.desktop_control import SessionChannel
    from gentle_stream.desktop_session import DesktopSession
    channel = SessionChannel.create(tmp_path)
    delivered = threading.Event()
    payload = '{"type":"action","action":"quit"}'
    writes = []
    class PTY:
        def __init__(self, *args, **kwargs): self.output = kwargs['on_output']
        def start(self):
            self.output(payload)
            delivered.set()
        def resize(self, cols, rows): pass
        def close(self, request_shutdown, timeout=10):
            request_shutdown()
            return 0
    class Window:
        def __init__(self, **kwargs):
            self.action = kwargs['on_action']
            self.first, self.state = True, 'ready'
        def run(self):
            self.action('ready')
            assert delivered.wait(2)
            assert not channel.stopping()  # JSON-looking VT was only display data.
            self.state = 'closing'
            self.action('quit')
        def snapshot(self):
            return {'epoch': 1, 'cols': 80, 'rows': 24, 'state': self.state}
        def set_drag_regions(self, *args):
            if self.first:
                self.first = False
                raise ValueError('stale desktop drag layout')
        def write(self, text): writes.append(text)
        def finish_close(self): pass
    session = DesktopSession(channel, ['python', 'child'], window_factory=Window, pty_factory=PTY)
    assert session.run() == 0
    assert writes == [payload] and channel.stopping()
