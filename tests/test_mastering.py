import numpy as np

from gentle_stream.mastering import Compressor, ConvolutionReverb, procedural_ir, soft_clip

SR = 44100


def test_impulse_response_is_stereo_decorrelated_decaying_and_scaled_by_space():
    short, long = procedural_ir(0.0, SR, seed=1), procedural_ir(1.0, SR, seed=1)
    assert short.shape[1] == 2 and 1.4 * SR <= len(short) < len(long) <= 2.6 * SR
    assert np.corrcoef(long[:, 0], long[:, 1])[0, 1] < 0.2
    q = len(long) // 4
    assert np.sum(long[-q:] ** 2) < 0.01 * np.sum(long[:q] ** 2)
    assert np.array_equal(long, procedural_ir(1.0, SR, seed=1))
    assert np.allclose(np.sum(long ** 2, axis=0), 1.0)


def test_reverb_tail_continues_across_blocks_exactly():
    rng = np.random.default_rng(0)
    ir = procedural_ir(0.5, SR, seed=2)
    x = np.zeros((3 * SR, 2)); x[:SR] = rng.standard_normal((SR, 2)) * 0.1
    whole = ConvolutionReverb(ir)(x)
    rev = ConvolutionReverb(ir)
    parts = np.concatenate([rev(x[a:b]) for a, b in ((0, 50000), (50000, 50001), (50001, 120000), (120000, 3 * SR))])
    assert np.allclose(parts, whole, atol=1e-12)
    ref = np.convolve(x[:, 0], ir[:, 0])[:3 * SR]
    assert np.allclose(whole[:, 0], ref, atol=1e-9)
    assert np.sqrt(np.mean(whole[SR + SR // 4:SR + SR // 2] ** 2)) > 1e-3   # the tail rings after the input stops


def test_reverb_keeps_its_tail_when_the_impulse_response_changes():
    rev = ConvolutionReverb(procedural_ir(0.0, SR, seed=3))
    x = np.zeros((SR, 2)); x[0] = 1.0
    rev(x)
    rev.set_ir(procedural_ir(1.0, SR, seed=3))
    after = rev(np.zeros((SR, 2)))
    assert np.max(np.abs(after[:100])) > 0


def _tone(amp, n):
    return np.repeat((amp * np.sin(2 * np.pi * 220 * np.arange(n) / SR))[:, None], 2, 1)


def test_compressor_reduces_dynamic_range():
    soft, loud = _tone(0.05, SR), _tone(0.8, SR)
    y = Compressor(SR)(np.concatenate([soft, loud]))
    rms = lambda a: np.sqrt(np.mean(a ** 2))
    in_db = 20 * np.log10(rms(loud) / rms(soft))
    out_db = 20 * np.log10(rms(y[SR + SR // 2:]) / rms(y[SR // 2:SR]))
    assert out_db < in_db - 6


def test_compressor_state_carries_across_blocks():
    x = np.concatenate([_tone(0.05, SR), _tone(0.8, SR), _tone(0.2, SR)])
    whole = Compressor(SR)(x)
    comp = Compressor(SR)
    parts = np.concatenate([comp(x[a:b]) for a, b in ((0, 1000), (1000, 70001), (70001, 100000), (100000, len(x)))])
    assert np.allclose(parts, whole, atol=1e-12)


def test_soft_clip_keeps_peaks_below_the_ceiling():
    y = soft_clip(np.linspace(-50, 50, 1001)[:, None] * np.ones(2), drive=1.6, ceiling=0.9)
    assert np.max(np.abs(y)) <= 0.9 < 1.0
