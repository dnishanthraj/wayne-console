"""The sound of where they are: the catalogue, the weather over it, what happens when — and what they hear of it."""
import random
from types import SimpleNamespace

import pytest

from wayne import config
from wayne.audio import ambience
from wayne.engine import cases, presence

NOW = 1_791_700_000.0


@pytest.fixture(autouse=True)
def fresh_plans():
    ambience._plans.clear()
    yield
    ambience._plans.clear()


def test_the_catalogue_holds_together_and_keeps_the_clips_already_made():
    for name, spec in ambience.CATALOGUE_SCENES.items():
        for sound, every, distances in spec.get("sounds", []):
            assert sound in ambience.CATALOGUE_SOUNDS, (name, sound)
            assert every > 0
            for d in distances.split(","):
                assert d in ambience.CATALOGUE_SOUNDS[sound]["variants"], (name, sound, d)
        for layer, _ in spec.get("layers", []):
            assert layer in ambience.CATALOGUE_SCENES
    # Made before distances existed, kept under the same names and words: nothing paid for twice.
    assert ambience.ONE_SHOTS["siren"] == "A police siren passing at a distance on a city street at night, fading away."
    assert ambience.SCENES["street_day"].startswith("A busy city street by day")
    assert ambience._variant_file("horn", "mid") == "horn" and ambience._variant_file("horn", "near") == "horn-near"
    assert len(ambience.CATALOGUE_SCENES) >= 100 and len(ambience.every_clip()) >= 290


def test_rain_comes_down_on_whatever_is_out_in_it_and_on_the_windows_of_what_isnt():
    assert ambience.beds_for("street_day", "rain") == [("street_day", 1.0), ("chatter", 0.55), ("rain_layer", 0.85)]
    assert ("rain_roof", 0.85) in ambience.beds_for("rooftop", "rain")
    assert ("car_rain", 0.8) in ambience.beds_for("car", "storm")
    assert ("window_rain", 0.45) in ambience.beds_for("office", "rain")
    assert ambience.beds_for("cave", "rain") == [("cave", 1.0)]                 # nothing reaches the cave
    assert ambience.beds_for("street_night", "") == [("street_night", 1.0)]


def _somewhere(monkeypatch, scene):
    monkeypatch.setattr(ambience, "scene_for", lambda contact, t=None: scene)
    monkeypatch.setattr(ambience, "_sky", lambda: "")
    return SimpleNamespace(id="nightwing")


def test_the_next_couple_of_minutes_are_laid_out_spaced_and_kept_when_asked_again(monkeypatch):
    dick = _somewhere(monkeypatch, "street_rush")
    first = ambience.plan(dick, NOW, rng=random.Random(1))
    cues = first["cues"]
    assert cues and all(NOW <= c["at"] <= NOW + ambience.HORIZON for c in cues)
    assert all(b["at"] - a["at"] >= ambience.MIN_GAP for a, b in zip(cues, cues[1:], strict=False))
    assert {b["name"] for b in first["beds"]} == {"street_rush", "chatter"}
    passing = [c for c in cues if c["distance"] == "pass"]
    assert all(c.get("sweep") for c in passing)                               # a car going past, swept across
    again = ambience.plan(dick, NOW + 60, rng=random.Random(2))
    kept = {c["id"] for c in cues if c["at"] >= NOW + 60}
    assert kept <= {c["id"] for c in again["cues"]}                           # what was promised still happens


def test_a_training_hall_has_bursts_and_rests(monkeypatch):
    dick = _somewhere(monkeypatch, "acrobatics")
    plan = ambience.plan(dick, NOW, rng=random.Random(3))
    windows = ambience._windows((20, 60, 8, 30), NOW, NOW + 150, random.Random(3), {})
    assert plan["cuts"] and windows
    assert plan["level"] < 1                                                  # a big quiet hall, mostly


def test_what_they_hear_is_what_played_and_the_others_hear_it_down_the_line(monkeypatch):
    dick = _somewhere(monkeypatch, "street_day")
    ambience.plan(dick, NOW, rng=random.Random(4))
    cue = ambience._plans["nightwing"]["cues"][0]
    later = cue["at"] + 3
    mine = ambience.heard("nightwing", later)
    said = ambience.CATALOGUE_SOUNDS[cue["sound"]]["say"]
    assert "traffic" in mine and said in mine and "just now" in mine
    assert ambience.heard("nightwing", NOW + 900) == ""                     # the page stopped asking: no call
    theirs = ambience.heard("robin", later, others=[("nightwing", "Dick")])
    if cue["distance"] in ("near", "pass") or cue["sound"] in ("gunshots", "siren", "thunder"):
        assert "Down Dick's phone" in theirs


def test_the_months_allowance_is_kept(monkeypatch):
    asked = []
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(config, "AMBIENCE", True)
    monkeypatch.setattr(config, "SFX_CREDITS", 100)                          # less than one loop
    monkeypatch.setattr(ambience.requests, "post", lambda *a, **k: asked.append(a))
    assert ambience.clip("hangar") is None and asked == []
    assert ambience.clip("nothing-at-all") is None


def test_an_office_is_the_buildings_kind_of_office(monkeypatch):
    fake = SimpleNamespace(now=lambda t=None: {"status": presence.BUSY, "doing": "working late in your office"},
                           whereabouts=lambda t=None: ("GCPD Central", []), get=lambda k, d=None: d)
    monkeypatch.setattr(presence, "of", lambda contact: fake)
    monkeypatch.setattr(cases, "active", lambda cid: None)
    jim = SimpleNamespace(id="gordon", home="Home, Burnley")
    assert ambience.scene_for(jim, NOW) == "police_office"
    fake.now = lambda t=None: {"status": presence.BUSY, "doing": "at GCPD Central — meetings, reports, the press"}
    assert ambience.scene_for(jim, NOW) == "police"
