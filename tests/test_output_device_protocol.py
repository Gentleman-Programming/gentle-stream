import json

import pytest

from gentle_stream import daemon
from gentle_stream.client import PlayerClient, PlayerError
from tests.test_audio_diagnostics import Connection, base_state
from tests.test_continuous_playback import LocalConnection
from tests.test_player import output_session


def test_devices_round_trip_and_rejected_choices_leave_output_untouched(output_session):
    from gentle_stream.audio_devices import Device

    player, factory = output_session
    client = PlayerClient(LocalConnection(player))
    assert client.devices() == [Device(1, "Built-in Speakers", True), Device(4, "USB Speakers", False)]
    assert daemon.handle(player, {"cmd": "devices"})["state"]["devices"] == [
        {"index": 1, "name": "Built-in Speakers", "default": True},
        {"index": 4, "name": "USB Speakers", "default": False},
    ]
    before = client.request("state")
    for choice in (True, [], 1.25):
        with pytest.raises(PlayerError, match="^bad request: device must be a name, an index or null$"):
            client.set_device(choice)
    with pytest.raises(PlayerError, match="^no audio output device matches “missing”$"):
        client.set_device("missing")
    assert client.request("state") == before and len(factory.streams) == 1
    client.set_device("USB")
    assert client.device == "USB Speakers" and factory.streams[-1].kw["device"] == 4
    assert PlayerClient(LocalConnection(player)).device == client.device
    client.set_device(None)
    assert client.device is None and factory.streams[-1].kw.get("device") is None


def test_client_refreshes_stopped_state_when_both_outputs_fail(output_session):
    player, factory = output_session
    client = PlayerClient(LocalConnection(player))
    client.set_device(4)
    factory.fail = {1: "start", 4: "open"}
    with pytest.raises(PlayerError, match="^Cannot switch audio output: start failed for 1; previous output could not reopen: open failed for 4$"):
        client.set_device(1)
    assert not client.playing and client.device is None
    assert client.audio_output_error == "Cannot switch audio output: start failed for 1; previous output could not reopen: open failed for 4"
    assert all(not stream.active for stream in factory.streams)
    factory.fail.clear()
    client.set_device(None)
    assert (client.device, client.playing, client.audio_output_error) == (None, False, None)
    client.play()
    assert client.playing


def test_windows_bridge_refuses_listing_and_switching_without_querying(output_session, monkeypatch):
    player, factory = output_session
    old = factory.streams[0]
    old.audio_output = 'windows'
    old.pause = old.abort
    old.resume = lambda: setattr(old, 'active', True)
    monkeypatch.setattr("gentle_stream.audio_devices.output_devices", lambda: pytest.fail("unexpected host query"))
    client = PlayerClient(LocalConnection(player))
    for action in (client.devices, lambda: client.set_device(4), lambda: client.set_device(None)):
        with pytest.raises(PlayerError, match="^Audio output selection is not supported by the Windows FFplay bridge$"):
            action()
    assert len(factory.streams) == 1 and old.active and client.device is None
    client.pause()
    assert not client.playing and not old.active
    client.play()
    assert client.playing and old.active


def test_legacy_daemon_state_and_unsupported_commands_remain_usable():
    class LegacyConnection(Connection):
        def recv_bytes(self):
            cmd = self.requests[-1]["cmd"]
            if cmd in ("devices", "set_device"):
                return json.dumps({"ok": False, "error": f"bad request: unknown command {cmd!r}"}).encode()
            return super().recv_bytes()

    connection = LegacyConnection(base_state())
    client = PlayerClient(connection)
    assert (client.device, client.device_missing) == (None, None)
    with pytest.raises(PlayerError, match="unknown command 'devices'"):
        client.devices()
    with pytest.raises(PlayerError, match="unknown command 'set_device'"):
        client.set_device(None)
    client.set_volume(.42)
    assert connection.requests[-1] == {"cmd": "set_volume", "volume": .42}
    assert client.playing and not client.gone
