from pathlib import Path

import pytest

from gentle_stream.desktop_frontend import build_html, data_script
from gentle_stream.desktop_window import DesktopTheme

ASSETS = Path('.validation/windows-desktop/assets')


def test_embedded_offline_terminal_and_safe_data():
    html = build_html(DesktopTheme('#101010','#eeeeee','#abcdef'), ASSETS)
    assert 'Content-Security-Policy' in html
    assert "connect-src 'none'" in html and "default-src 'none'" in html
    assert '<script src=' not in html and '<link ' not in html
    assert 'terminal.write(data[1]' in html
    assert 'innerHTML' not in html
    assert 'WebLinksAddon' not in html
    assert 'aria-label="Close gentle-stream"' in html
    assert 'aria-label="Toggle mini window"' in html
    assert 'attachCustomKeyEventHandler' in html
    assert "'unsafe-eval'" not in html
    assert html.index("report('bootstrap')") < html.index('!function(e,t)')
    assert "setTimeout(()=>{resized();terminal.focus();post({type:'ready'});},0)" in html
    script = data_script('output', [1,'</script>\n日本語'])
    assert '\\u003c/script\\u003e' in script
    assert script.startswith('window.gentleReceive(')


def test_missing_assets_is_explicit(tmp_path):
    with pytest.raises((FileNotFoundError,ValueError)):
        build_html(DesktopTheme('#000000','#ffffff','#123456'), tmp_path)
