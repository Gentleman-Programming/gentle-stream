"""Gentle Shell-style volume feedback, rendered without audio or personal records."""

import pytest

from gentle_stream.volume_meter import render_compact_volume_meter, render_volume_meter


@pytest.mark.parametrize("percent", range(0, 101, 5))
def test_eight_segment_gauge_keeps_five_percent_numeric_steps(percent):
    meter = render_volume_meter(percent, "#f095c8")
    filled = round(percent * 8 / 100)
    assert meter.plain == "▰" * filled + "▱" * (8 - filled) + f" {percent}%"
    assert "\n" not in meter.plain
    assert meter.style == "#f095c8"
    assert render_compact_volume_meter(percent, "#f095c8").plain == meter.plain


def test_reference_example_boundaries_and_accent():
    assert render_volume_meter(83, "#ff00aa").plain == "▰" * 7 + "▱ 83%"
    assert render_volume_meter(-5, "#ff00aa").plain == "▱" * 8 + " 0%"
    assert render_volume_meter(105, "#ff00aa").plain == "▰" * 8 + " 100%"
    assert render_volume_meter(0, "#ff00aa").style == "#ff00aa"
    assert render_volume_meter(100, "#ff00aa").style == "#ff00aa"
