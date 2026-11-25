"""Check the launcher artwork through the public package-resource API."""

from importlib.resources import files
import struct
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def _asset(name: str) -> bytes:
    return files("gentle_stream").joinpath("assets", name).read_bytes()


def _assert_rgba_png(data: bytes, size: int) -> None:
    assert data.startswith(PNG_SIGNATURE)
    assert struct.unpack_from(">I4sIIBBBBB", data, 8) == (
        13, b"IHDR", size, size, 8, 6, 0, 0, 0
    )

    offset = len(PNG_SIGNATURE)
    pixels = bytearray()
    while offset < len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 8 + length
        assert end + 4 <= len(data)
        assert zlib.crc32(data[offset + 4:end]) == struct.unpack_from(">I", data, end)[0]
        if kind == b"IDAT":
            pixels.extend(data[offset + 8:end])
        offset = end + 4
        if kind == b"IEND":
            assert length == 0 and offset == len(data)
            assert len(zlib.decompress(pixels)) == size * (1 + 4 * size)
            return
    raise AssertionError("missing PNG end chunk")


def test_launcher_png_is_a_256_pixel_rgba_package_resource():
    _assert_rgba_png(_asset("launcher-icon.png"), 256)


def test_launcher_ico_contains_the_documented_sizes_and_original_artwork():
    data = _asset("launcher-icon.ico")
    assert struct.unpack_from("<HHH", data) == (0, 1, len(ICO_SIZES))
    end = 6 + 16 * len(ICO_SIZES)
    for index, size in enumerate(ICO_SIZES):
        width, height, colors, reserved, planes, depth, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index
        )
        assert (width or 256, height or 256) == (size, size)
        assert (colors, reserved, planes, depth) == (0, 0, 1, 32)
        assert end == offset and 0 < length <= len(data) - offset
        end = offset + length
        frame = data[offset:end]
        _assert_rgba_png(frame, size)
        if size == 256:
            assert frame == _asset("launcher-icon.png")
    assert end == len(data)
