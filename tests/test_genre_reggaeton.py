"""Original reggaeton groove via the public genre and engine interfaces, without audio output."""
import numpy as np

from gentle_stream import genres
from gentle_stream.composer import Composer
from gentle_stream.genres.base import Piece
from gentle_stream.style import Style
from gentle_stream.synth import SR


class RecordingVoices:
    def __getattr__(self, name):
        return lambda *args, **kwargs: (name, args, kwargs)


class RecordingPiece(Piece):
    def __init__(self, style):
        super().__init__(style, np.random.default_rng(12), RecordingVoices(), 45, SR, np.zeros((1, 2)))
        self.events = []

    def add(self, sig, t, pan=0, g=1):
        self.events.append((sig[0], round(t / (60 / self.style.bpm), 3), sig[1], g))


def test_reggaeton_has_an_original_dembow_and_guitar_colour():
    assert 'reggaeton' in genres.NAMES
    genre = genres.get('reggaeton')
    assert 'guitar' in genre.voices['nylon']
    style = Style(genre='reggaeton', bpm=98, chords='nylon', lead='nylon', drums='busy', energy=0.8)
    piece = RecordingPiece(style)
    genre.start(piece)
    composer = Composer(style, 12, 45, genre.chord)
    drum_bars = 0
    for bar in range(24):
        plan = composer.plan(bar)
        piece.events.clear()
        genre.schedule(piece, plan)
        events = piece.events
        if 'drums' in plan.layers:
            drum_bars += 1
            assert [t for name, t, _, _ in events if name == 'snare'] == [1.0, 3.0]
            assert [t for name, t, _, _ in events if name == 'clap'] == [1.0, 3.0]
            kicks = [t for name, t, _, _ in events if name == 'kick']
            assert kicks[0] == 0 and any(t % 1 for t in kicks)
            assert [t for name, t, _, _ in events if name == 'hat']
        else:
            assert not {'kick', 'snare', 'clap', 'hat'} & {name for name, *_ in events}
        if plan.section in ('A', 'B'):
            assert any(name == 'stab' and args[0] == 'nylon' and t % 1 for name, t, args, _ in events)
        assert [args[0] for name, _, args, _ in events if name == 'bass']
    assert drum_bars
    assert genre.master(style).vinyl == 0
