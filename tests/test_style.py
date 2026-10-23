import numpy as np

from gentle_stream.style import DEFAULT, PROGRESSIONS, Style


def test_from_dict_accepts_a_full_valid_answer():
    s = Style.from_dict({"name": "Desert Dawn", "mood": "hope", "key": "f#", "mode": "Lydian", "bpm": 96,
                         "progression": [1, 2, 5, 1], "chords": "rhodes", "lead": "none", "drums": "soft",
                         "energy": 0.7, "brightness": 0.8, "swing": 0.2, "vinyl": 0.1, "space": 0.9,
                         "lead_density": 0.3})
    assert (s.name, s.key, s.mode, s.bpm, s.progression) == ("Desert Dawn", "F#", "lydian", 96.0, (1, 2, 5, 1))
    assert (s.chords, s.lead, s.drums, s.energy, s.space) == ("rhodes", "none", "soft", 0.7, 0.9)


def test_from_dict_clamps_and_falls_back_on_invalid_values():
    base = Style(key="A", chords="nylon")
    s = Style.from_dict({"bpm": 400, "energy": -3, "key": "H", "chords": "trumpet", "progression": [9, "x"],
                         "swing": float("nan"), "name": "  "}, base)
    assert s.bpm == 140 and s.energy == 0
    assert (s.key, s.chords, s.progression, s.swing, s.name) == ("A", "nylon", base.progression, base.swing, base.name)


def test_from_dict_ignores_non_objects():
    assert Style.from_dict(["not", "a", "dict"]) == DEFAULT


def test_variation_keeps_the_feeling():
    s = Style(mode="phrygian", energy=0.9, brightness=0.1, vinyl=0.8, space=0.2, drums="busy", mood="storm")
    v = s.variation(np.random.default_rng(3))
    assert (v.mode, v.energy, v.brightness, v.vinyl, v.space, v.drums, v.mood) == \
           ("phrygian", 0.9, 0.1, 0.8, 0.2, "busy", "storm")
    assert v.progression in PROGRESSIONS["phrygian"] and 50 <= v.bpm <= 140


def test_variation_with_no_lead_stays_without_lead():
    v = Style(lead="none").variation(np.random.default_rng(1))
    assert v.lead == "none"


def test_to_dict_round_trips():
    s = Style(name="X", progression=(2, 5, 1))
    assert Style.from_dict(s.to_dict()) == s
