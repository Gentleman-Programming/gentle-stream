#!/usr/bin/env bash
set -eu

checkout=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
if [[ ! -x /usr/bin/python3 ]]; then
    printf '%s\n' 'gentle-stream: install the Arch system Python and python-gobject first.' >&2
    exit 1
fi
exec /usr/bin/python3 -I -B "$checkout/src/gentle_stream/linux_desktop.py" --install "$checkout"
