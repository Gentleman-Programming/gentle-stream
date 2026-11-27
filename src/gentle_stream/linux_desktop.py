"""GTK/VTE host and user-local installer; run this file with system Python."""
import ctypes
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import NamedTemporaryFile

APP_ID = 'io.github.GentlemanProgramming.GentleStream'
TITLE = 'Gentle Stream (Preview)'
SPAWN_ERROR = 'Could not start the preview. Re-run the installer.'
CHILD_ERROR = 'The preview stopped with an error. Check your audio output and try again.'
PACKAGES = 'Install gtk3, vte3, python-gobject, portaudio and uv first.'


def bindings():
    import gi

    gi.require_version('Gtk', '3.0')
    gi.require_version('Vte', '2.91')
    from gi.repository import Gio, GLib, Gtk, Vte
    return Gio, GLib, Gtk, Vte


class Preview:
    def __init__(self, window, terminal, glib, root):
        self.window, self.terminal, self.glib, self.root = window, terminal, glib, root
        self.fd = self.timer = None
        self.closing = self.finished = False

    def start(self, pty_flags=0):
        argv = [str(self.root / 'venv/bin/python'), '-I', '-B', '-m', 'gentle_stream.linux_desktop_child']
        try:
            # Native VTE owns the PTY and child watch. Keep the terminal alive through
            # the bounded spawn callback, even if the window is closed meanwhile.
            # PyGObject requires child_setup_data even when omitted from the docstring.
            self.terminal.spawn_async(pty_flags, str(self.root), argv, None, 0,
                                      None, None, 5000, None, self.spawned, None)
        except (TypeError, RuntimeError):
            self.spawned(self.terminal, -1, True, None)

    def spawned(self, terminal, pid, error, _data):
        if self.finished:
            return
        if error is not None or pid <= 0:
            self.finish(SPAWN_ERROR)
            return
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return  # Already reaped; VTE still delivers child-exited.
        try:
            # Verify it is still our child, without stealing VTE's wait status.
            os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            os.close(fd)  # VTE reaped it and the numeric PID may already have been reused.
            return
        self.fd = fd
        if self.closing:
            self.terminate()

    def close(self, *_):
        if not self.closing:
            self.closing = True
            self.terminal.set_input_enabled(False)
            if self.fd is not None:
                self.terminate()
            elif self.finished:
                self.window.destroy()
        return True  # VTE must reap its own child before the window is destroyed.

    def send(self, sig):
        try:
            signal.pidfd_send_signal(self.fd, sig)
        except ProcessLookupError:
            pass  # child-exited still arrives

    def terminate(self):
        self.send(signal.SIGTERM)
        self.timer = self.glib.timeout_add(8000, self.force_stop)

    def force_stop(self):
        self.timer = None
        self.send(signal.SIGKILL)
        return False

    def exited(self, _terminal, status):
        self.finish(CHILD_ERROR if status else None)

    def finish(self, error):
        self.finished = True
        if self.timer is not None:
            self.glib.source_remove(self.timer)
            self.timer = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if error and not self.closing:
            self.terminal.set_input_enabled(False)
            self.terminal.feed((error + '\r\n').encode())
        else:
            self.window.destroy()


def launch(root):
    Gio, GLib, Gtk, Vte = bindings()
    app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    try:
        Gtk.Window.set_default_icon_from_file(str(root / f'{APP_ID}.png'))
    except GLib.Error:
        pass  # A missing/unreadable icon must not prevent playback.
    preview = None

    def activate(application):
        nonlocal preview
        if preview is not None:
            preview.window.present()
            return
        window = Gtk.ApplicationWindow(application=application, title=TITLE)
        window.set_default_size(1000, 650)
        terminal = Vte.Terminal()
        window.add(terminal)
        preview = Preview(window, terminal, GLib, root)
        terminal.connect('child-exited', preview.exited)
        window.connect('delete-event', preview.close)
        window.show_all()
        terminal.grab_focus()
        preview.start(Vte.PtyFlags.DEFAULT)

    def terminate():
        if preview is None:
            app.quit()
        else:
            preview.close()
        return GLib.SOURCE_CONTINUE

    app.connect('activate', activate)
    source = GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, terminate)
    try:
        return app.run([])
    finally:
        GLib.source_remove(source)


def require_dependencies():
    uv = shutil.which('uv')
    try:
        bindings()
        ctypes.CDLL('libportaudio.so.2')
    except (ImportError, ValueError, OSError) as exc:
        raise RuntimeError(PACKAGES) from exc
    if not uv:
        raise RuntimeError(PACKAGES)
    return str(Path(uv).absolute())


def replace_file(path, data):
    # Write siblings so an interrupted reinstall cannot leave a partial menu entry.
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        name = Path(temp.name)
        try:
            temp.write(data)
            temp.close()
            name.replace(path)
        finally:
            name.unlink(missing_ok=True)


def desktop_entry(host, icon):
    def value(text):
        if any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise RuntimeError('The install path must not contain control characters.')
        return text.replace('\\', '\\\\')

    # The Exec tokenizer runs after the general desktop-entry string unescaping.
    command = ''.join('\\' + c if c in '\\"$`' else '%%' if c == '%' else c for c in str(host))
    return (f'[Desktop Entry]\nType=Application\nName={TITLE}\n'
            f'Exec=/usr/bin/python3 -I -B "{value(command)}"\nIcon={value(str(icon))}\n'
            'Terminal=false\nCategories=AudioVideo;Audio;\n').encode()


def install(checkout):
    try:
        uv = require_dependencies()
        data = Path(os.environ.get('XDG_DATA_HOME', ''))
        if not data.is_absolute():
            data = Path.home() / '.local/share'
        root = data / 'gentle-stream/omarchy'
        host, icon = root / 'linux_desktop.py', root / f'{APP_ID}.png'
        entry = desktop_entry(host, icon)
        source = (checkout / 'src/gentle_stream/linux_desktop.py').read_bytes()
        logo = (checkout / 'docs/assets/rose-wave-logo.png').read_bytes()
        env = {key: val for key, val in os.environ.items() if not key.startswith('UV_')}
        env.update(UV_PROJECT_ENVIRONMENT=str(root / 'venv'), UV_CACHE_DIR=str(root / 'uv-cache'))
        subprocess.run([uv, '--no-config', 'sync', '--locked', '--no-dev', '--no-editable',
                        '--python', '3.13', '--no-python-downloads'], cwd=checkout, env=env,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        replace_file(host, source)
        replace_file(icon, logo)
        replace_file(data / 'applications' / f'{APP_ID}.desktop', entry)
    except RuntimeError as error:
        print(f'gentle-stream: {error}', file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        print('gentle-stream: could not install dependencies; check Python 3.13 and network access.', file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print('gentle-stream: could not read the checkout or write the user install.', file=sys.stderr)
        return 1
    print(f'Installed {TITLE}.')
    return 0


if __name__ == '__main__':
    if sys.argv[1:2] == ['--install']:
        sys.exit(install(Path(sys.argv[2])))
    try:
        sys.exit(launch(Path(__file__).resolve().parent))
    except (ImportError, ValueError, OSError, RuntimeError):
        sys.exit(f'gentle-stream: {SPAWN_ERROR}')
