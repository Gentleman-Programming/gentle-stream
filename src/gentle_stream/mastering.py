"""Bus processing for the master chain: convolution reverb, compression and the final soft clip.

Everything here is stateful and block-size independent: processing a signal bar by bar gives the same samples as
processing it in one go, so the stream joins seamlessly. The reverb's impulse response is generated procedurally
(no third-party recording is used or shipped).
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, fftconvolve, lfilter, sosfilt


def procedural_ir(space: float, sr: int, seed: int = 0) -> np.ndarray:
    """Stereo impulse response, (n, 2): decorrelated noise whose highs die faster than its lows.

    Length runs from 1.5 s (space 0) to 2.5 s (space 1); each channel is normalised to unit energy.
    """
    space = float(np.clip(space, 0, 1))
    length = 1.5 + 1.0 * space
    n = int(length * sr)
    t = np.arange(n) / sr
    rt60 = 0.75 * length                          # -60 dB well before the end, so the cut is inaudible
    rng = np.random.default_rng(seed)
    low_sos = butter(2, 1500, "lowpass", fs=sr, output="sos")
    high_sos = butter(2, 1500, "highpass", fs=sr, output="sos")
    out = np.empty((n, 2))
    for c in (0, 1):
        low = sosfilt(low_sos, rng.standard_normal(n)) * np.exp(-6.91 * t / rt60)
        high = sosfilt(high_sos, rng.standard_normal(n)) * np.exp(-6.91 * t / (0.4 * rt60)) * 0.6
        out[:, c] = low + high
    pre = int(0.012 * sr)                         # pre-delay, then a short fade-in
    out = np.concatenate([np.zeros((pre, 2)), out[:n - pre]])
    out[pre:pre + int(0.004 * sr)] *= np.linspace(0, 1, int(0.004 * sr))[:, None]
    out[-int(0.05 * sr):] *= np.linspace(1, 0, int(0.05 * sr))[:, None]
    return out / np.sqrt(np.sum(out ** 2, axis=0))


class ConvolutionReverb:
    """FFT convolution with overlap-add: the part of each block's response that falls past the block is kept as
    a tail and added to the following blocks."""

    def __init__(self, ir: np.ndarray):
        self.ir = ir
        self._tail = np.zeros((0, 2))

    def set_ir(self, ir: np.ndarray) -> None:
        """Use `ir` for new input; the tail already ringing keeps the old response."""
        self.ir = ir

    def __call__(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        y = fftconvolve(x, self.ir, axes=0)
        m = max(len(y), len(self._tail))
        acc = np.zeros((m, 2))
        acc[:len(y)] += y
        acc[:len(self._tail)] += self._tail
        self._tail = acc[n:]
        return acc[:n]


class Compressor:
    """Feed-forward bus compressor: RMS detector, soft-knee gain computer, attack/release smoothing.

    The gain is computed on a control grid every `step` samples of absolute time and ramped linearly towards
    each point (one step behind), so the result does not depend on how the signal is cut into blocks.
    """

    def __init__(self, sr: int, threshold_db: float = -20.0, ratio: float = 2.5, knee_db: float = 6.0,
                 attack: float = 0.015, release: float = 0.25, rms_window: float = 0.03, makeup_db: float = 3.0,
                 step: int = 64):
        self.threshold, self.ratio, self.knee, self.makeup = threshold_db, ratio, knee_db, makeup_db
        self.step = step
        self._a_att = np.exp(-step / (attack * sr))
        self._a_rel = np.exp(-step / (release * sr))
        a = 1 - np.exp(-1 / (rms_window * sr))
        self._det_b, self._det_a = [a], [1, -(1 - a)]
        self._det_zi = np.zeros(1)
        self._pos = 0
        self._gr = 0.0                                       # smoothed gain reduction (dB) at the last point
        self._points = [(-2 * step, 0.0), (-step, 0.0)]      # (absolute sample, gain dB), last two points

    def _target(self, level_db: np.ndarray) -> np.ndarray:
        over = level_db - self.threshold
        slope = 1 - 1 / self.ratio
        k = self.knee / 2
        return np.where(over <= -k, 0.0,
                        np.where(over >= k, over * slope, slope * (over + k) ** 2 / (2 * self.knee)))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        n, s = len(x), self.step
        power, self._det_zi = lfilter(self._det_b, self._det_a, np.mean(x ** 2, axis=1), zi=self._det_zi)
        first = -(-self._pos // s) * s                       # first grid point at or after this block
        idx = np.arange(first, self._pos + n, s)
        target = self._target(10 * np.log10(power[idx - self._pos] + 1e-12))
        gr, gains = self._gr, np.empty(len(idx))
        for i, g in enumerate(target):
            a = self._a_att if g > gr else self._a_rel
            gr = a * gr + (1 - a) * g
            gains[i] = gr
        self._gr = gr
        xp = np.concatenate([[p for p, _ in self._points], idx + s])
        fp = np.concatenate([[g for _, g in self._points], -gains])
        self._points = list(zip(xp[-2:], fp[-2:]))
        gain_db = np.interp(self._pos + np.arange(n), xp, fp) + self.makeup
        self._pos += n
        return x * (10 ** (gain_db / 20))[:, None]


def soft_clip(x: np.ndarray, drive: float, ceiling: float) -> np.ndarray:
    """Tape-style saturation that never exceeds `ceiling`."""
    return np.tanh(x * drive) * ceiling
