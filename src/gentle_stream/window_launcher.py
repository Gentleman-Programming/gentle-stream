"""Kitty launcher for Hyprland 0.52's classic dispatch exec rule API."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys


class LaunchError(RuntimeError):
    """A launcher failure that must be shown before creating audio."""


def parse_window_size(value: str) -> tuple[int, int]:
    if not re.fullmatch(r'[0-9]{1,5}x[0-9]{1,5}', value):
        raise ValueError('window size must be WIDTHxHEIGHT pixels (each 1..16384)')
    width, height = map(int, value.split('x'))
    if not (1 <= width <= 16384 and 1 <= height <= 16384):
        raise ValueError('window size must be WIDTHxHEIGHT pixels (each 1..16384)')
    return width, height


def launch_window(mode: str, size: tuple[int, int] | None, terminal: str, forwarded: list[str]) -> None:
    if mode not in ('window', 'floating') or terminal != 'kitty':
        raise LaunchError('supported modes: window/floating; supported terminal: kitty')
    if size is not None and (len(size) != 2 or any(type(n) is not int or not 1 <= n <= 16384 for n in size)):
        raise LaunchError('window size must be WIDTHxHEIGHT pixels (each 1..16384)')
    if not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        raise LaunchError('window launcher requires a running Hyprland session')
    hyprctl, kitty = shutil.which('hyprctl'), shutil.which('kitty')
    if not hyprctl or not kitty:
        raise LaunchError('window launcher requires hyprctl and kitty on PATH')
    title = 'gentle-dot' if mode == 'floating' else 'gentle-stream'
    command = [kitty, '--class', title, '--title', title, sys.executable, '-m', 'gentle_stream']
    if mode == 'floating':
        command.append('--compact')
    command.extend(forwarded)
    rules = ['float'] if mode == 'floating' else []
    if size:
        rules.append(f'size {size[0]} {size[1]}')
    serialized = shlex.join(command)
    if rules:
        serialized = '[' + '; '.join(rules) + '] ' + serialized
    try:
        result = subprocess.run([hyprctl, 'dispatch', 'exec', serialized], capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LaunchError(f'Hyprland classic dispatch exec failed: {error}') from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'
        raise LaunchError(f'Hyprland classic dispatch exec failed: {detail}')
