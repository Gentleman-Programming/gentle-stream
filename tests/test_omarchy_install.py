import os
from unittest.mock import Mock

import pytest

from gentle_stream import linux_desktop as desktop


@pytest.fixture
def install(tmp_path, monkeypatch):
    checkout = tmp_path / 'preview checkout'
    for relative, contents in [('docs/assets/rose-wave-logo.png', b'rose'),
                               ('src/gentle_stream/linux_desktop.py', b'host')]:
        path = checkout / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(contents)
    data = tmp_path / 'data 100% complete'
    monkeypatch.setenv('XDG_DATA_HOME', str(data))
    monkeypatch.setenv('UV_PROJECT_ENVIRONMENT', str(tmp_path / 'untouched'))
    monkeypatch.setattr(desktop, 'require_dependencies', Mock(return_value='/usr/bin/uv'))
    monkeypatch.setattr(desktop.subprocess, 'run', Mock())
    return checkout, data, tmp_path


def test_install_registers_fixed_app_and_is_repeatable(install, capsys):
    checkout, data, scratch = install
    assert desktop.install(checkout) == 0
    root = data / 'gentle-stream/omarchy'
    launchers = data / 'applications'
    entry, = launchers.iterdir()
    assert entry.name == f'{desktop.APP_ID}.desktop'
    icon = root / f'{desktop.APP_ID}.png'
    host = root / 'linux_desktop.py'
    assert icon.read_bytes() == b'rose'
    assert host.read_bytes() == b'host'
    original = entry.read_bytes()
    path = os.fsdecode(host).replace('\\', '\\\\\\\\').replace('%', '%%')
    assert entry.read_text() == (
        '[Desktop Entry]\nType=Application\nName=Gentle Stream (Preview)\n'
        f'Exec=/usr/bin/python3 -I -B "{path}"\n'
        f'Icon={str(icon).replace(chr(92), chr(92) * 2)}\nTerminal=false\nCategories=AudioVideo;Audio;\n'
    )
    command = desktop.subprocess.run.call_args_list[0]
    assert command.args[0] == ['/usr/bin/uv', '--no-config', 'sync', '--locked', '--no-dev',
                               '--no-editable', '--python', '3.13', '--no-python-downloads']
    assert command.kwargs['cwd'] == checkout
    assert command.kwargs['env']['UV_PROJECT_ENVIRONMENT'] == str(root / 'venv')
    assert not (scratch / 'untouched').exists()
    assert desktop.install(checkout) == 0
    assert list(launchers.iterdir()) == [entry]
    assert entry.read_bytes() == original
    assert capsys.readouterr() == ('Installed Gentle Stream (Preview).\n' * 2, '')


def test_missing_prerequisites_leave_install_paths_untouched(install, capsys):
    checkout, data, _ = install
    desktop.require_dependencies.side_effect = RuntimeError('Install gtk3, vte3, python-gobject, portaudio and uv first.')
    assert desktop.install(checkout) == 1
    assert not data.exists()
    desktop.subprocess.run.assert_not_called()
    assert capsys.readouterr() == ('', 'gentle-stream: Install gtk3, vte3, python-gobject, portaudio and uv first.\n')
