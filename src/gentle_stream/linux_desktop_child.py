"""The desktop PTY's sole Python child; never attach to a CLI player."""
import asyncio
import signal
import sys


def install_signals(app):
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, app.exit)


async def run():
    from textual.binding import Binding
    from .app import StreamApp
    from .bridge import GentleShell
    from .engine import Engine
    from .player import Player

    shell = GentleShell()
    player = Player(Engine())
    cleaned = False

    def cleanup():
        nonlocal cleaned
        if not cleaned:
            cleaned = True
            try:
                shell.cancel()
            finally:
                player.close()

    class DesktopApp(StreamApp):
        BINDINGS = [Binding('b', 'quit', 'Back: stop music')]

        def on_unmount(self, event):
            event.prevent_default()  # The CLI unmount handler supports detach; this child never does.
            cleanup()

        def _handle_exception(self, error):
            # Textual normally renders a traceback, potentially including provider/config data.
            self.exit(return_code=1)

    try:
        app = DesktopApp(player, shell)
        install_signals(app)
        await app.run_async()
        return app.return_code or 0
    finally:
        cleanup()


def main():
    try:
        status = asyncio.run(run())
    except Exception:
        status = 1
    if status:
        print('gentle-stream: desktop preview failed.', file=sys.stderr)
    return status


if __name__ == '__main__':
    sys.exit(main())
