"""Shared DSP building blocks for the engine and the genres: filters, pitch, band-limited oscillators, envelopes
and a waveshaper. Everything is pure numpy/scipy and deterministic.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy.signal import butter, sosfilt

SR = 44100


@lru_cache(maxsize=256)
def _sos(kind: str, f1: int, f2: int = 0, sr: int = SR):
    return butter(2, [f1, f2] if kind == "bandpass" else f1, kind, fs=sr, output="sos")


def lp(x, f, sr=SR):
    return sosfilt(_sos("lowpass", int(f), 0, sr), x)


def hp(x, f, sr=SR):
    return sosfilt(_sos("highpass", int(f), 0, sr), x)


def bp(x, a, b, sr=SR):
    return sosfilt(_sos("bandpass", int(a), int(b), sr), x)


def mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


class StatefulFilter:
    """Second-order filter applied block by block along axis 0, carrying its state across blocks."""

    def __init__(self, sos, channels: int):
        self.sos = sos
        self.zi = np.zeros((sos.shape[0], 2, channels))

    def __call__(self, x):
        y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
        return y


# ---------------------------------------------------------------- oscillators
def _blep(t: np.ndarray, dt: np.ndarray) -> np.ndarray:
    """PolyBLEP residual for a unit-phase oscillator: smooths the jump at phase 0 over one sample each side."""
    out = np.zeros_like(t)
    a = t < dt
    x = t[a] / dt[a]
    out[a] = x + x - x * x - 1
    b = t > 1 - dt
    x = (t[b] - 1) / dt[b]
    out[b] = x * x + x + x + 1
    return out


def osc(shape: str, freq, n: int, sr: int = SR, phase: float = 0.0, duty: float = 0.5) -> np.ndarray:
    """`n` samples of a band-limited oscillator in [-1, 1] (pulse: DC removed, so up to 1 + |2 duty - 1|).

    `shape`: sine, saw, square, pulse (width `duty`) or triangle. `freq` is Hz, a number or an array of `n`
    values (vibrato, slides). `phase` is the start phase in cycles. Saw, square and pulse use polyBLEP.
    """
    dt = np.broadcast_to(np.asarray(freq, dtype=float) / sr, (n,)).astype(float)
    t = (phase + np.concatenate([[0.0], np.cumsum(dt[:-1])])) % 1.0
    if shape == "sine":
        return np.sin(2 * np.pi * t)
    if shape == "triangle":           # harmonics fall as 1/k^2: the naive shape barely aliases
        return 2 * np.abs(2 * t - 1) - 1
    if shape == "saw":
        return 2 * t - 1 - _blep(t, dt)
    if shape in ("square", "pulse"):
        d = 0.5 if shape == "square" else float(np.clip(duty, 0.05, 0.95))
        x = np.where(t < d, 1.0, -1.0) + _blep(t, dt) - _blep((t - d) % 1.0, dt)
        return x - (2 * d - 1)
    raise ValueError(f"unknown oscillator shape: {shape}")


# ---------------------------------------------------------------- envelopes and shaping
def adsr(n: int, sr: int = SR, attack: float = 0.005, decay: float = 0.1, sustain: float = 0.7,
         release: float = 0.2, hold: float | None = None) -> np.ndarray:
    """Linear attack/decay to `sustain`, held until `hold` seconds (default: release at the end), then a linear
    release to zero. Always `n` samples in [0, 1]."""
    t = np.arange(n) / sr
    attack, release = max(attack, 1 / sr), max(release, 1 / sr)
    hold = n / sr - release if hold is None else hold
    env = np.interp(t, [0, attack, attack + max(decay, 1 / sr)], [0, 1, sustain])
    at_hold = float(np.interp(hold, [0, attack, attack + max(decay, 1 / sr)], [0, 1, sustain]))
    rel = t >= hold
    env[rel] = at_hold * np.clip(1 - (t[rel] - hold) / release, 0, 1)
    return env


def distort(x: np.ndarray, drive: float) -> np.ndarray:
    """Smooth, monotonic waveshaper bounded to [-1, 1]; higher `drive` means more gain and more harmonics."""
    return np.tanh(np.asarray(x) * drive)
