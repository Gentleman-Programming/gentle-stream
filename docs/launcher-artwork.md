# Launcher artwork

The [wheel resources](../README.md#launcher-artwork) are opt-in: a native launcher
must explicitly use them. They cannot change the icon of an already-running host
terminal. The [Omarchy desktop installer](omarchy-desktop.md) uses the separate
`docs/assets/rose-wave-logo.png` logo.

The packaged PNG is an unchanged copy of `docs/assets/launcher-icon.png` (Git
blob `4033a2c23ffe265d9534786dc481c109e5154364`). To regenerate the ICO from the
repository root, use FFmpeg 8.1 (with PNG encoding and ICO muxing) and Python.
Neither tool is needed to use the packaged artwork.

```bash
icon=src/gentle_stream/assets/launcher-icon
ffmpeg -hide_banner -loglevel error -nostdin -y -i "$icon.png" \
  -filter_complex '[0:v]split=5[a][b][c][d][e];[a]scale=16:16:flags=lanczos[s16];[b]scale=32:32:flags=lanczos[s32];[c]scale=48:48:flags=lanczos[s48];[d]scale=64:64:flags=lanczos[s64];[e]scale=128:128:flags=lanczos[s128]' \
  -map '[s16]' -map '[s32]' -map '[s48]' -map '[s64]' -map '[s128]' -map 0:v \
  -c:v png -pix_fmt rgba -c:v:5 copy -f ico "$icon.ico" && \
python - "$icon.ico" <<'PY'
from pathlib import Path
import struct
import sys

path = Path(sys.argv[1])
data = bytearray(path.read_bytes())
reserved, kind, count = struct.unpack_from("<HHH", data)
assert (reserved, kind, count) == (0, 1, 6)
last = 6 + 16 * (count - 1)
assert data[last:last + 2] == b"\x00\x00"  # ICO encodes 256 px as zero.
# FFmpeg stream-copies the original PNG and leaves its bit depth unset.
struct.pack_into("<H", data, last + 6, 32)
path.write_bytes(data)
PY
```
