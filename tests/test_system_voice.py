"""The Batcomputer's voice: what it says for each moment, kept as mp3s once."""
import pytest

from wayne import config
from wayne.audio import system_voice
from wayne.contacts import directory


class Voice:
    """A voice engine that counts what it's asked for, and can be told to fail."""

    def __init__(self, fail=()):
        self.asked, self.fail = [], set(fail)

    def synthesize_capped(self, text, voice_id):
        self.asked.append(text)
        if text in self.fail:
            raise RuntimeError("no quota")
        return f"mp3:{text}".encode()


@pytest.fixture(autouse=True)
def voice_set(monkeypatch):
    monkeypatch.setattr(config, "BATCOMPUTER_VOICE_ID", "test-voice")
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")


def test_a_typed_passcode_is_never_called_a_voiceprint():
    for event in ("unlock", "denied", "lockout"):
        for said in system_voice.LINES[event]:
            assert "voice" not in said.lower() and "print" not in said.lower()
    assert "unlock_voice" not in system_voice.LINES


def test_the_family_by_their_masks_on_a_case_and_jim_by_his_title_on_the_line():
    book = directory()
    assert system_voice.name_for("case_scene", book.get("robin")) == "Robin"
    assert system_voice.name_for("call", book.get("robin")) == "Tim Drake"
    assert system_voice.name_for("incoming", book.get("gordon")) == "Commissioner Gordon"
    assert system_voice.line("case_scene") == ""                     # a moment that needs a name, without one


def test_everything_it_could_say_includes_the_new_moments_and_the_new_man():
    lines = {text for _, text in system_voice.every_line(list(directory()))}
    for text in ("Incoming call. Commissioner Gordon.", "Robin is on scene.", "Batwing inbound.",
                 "Nightwing needs backup.", "Passcode accepted. Good evening.", "Destination reached."):
        assert text in lines
    assert "Alfred Pennyworth is on scene." not in lines             # no mask, no case


def test_each_line_is_said_once_and_kept_where_a_person_can_find_it():
    voice = Voice()
    first = system_voice.audio("Batwing inbound.", voice, "jet_summoned")
    again = system_voice.audio("Batwing inbound.", voice, "jet_summoned")
    assert first == again and voice.asked == ["Batwing inbound."]
    path = system_voice._cache_path("Batwing inbound.", "jet_summoned")
    assert path.exists() and path.name == "Batwing inbound.mp3" and path.parent.name == "jet_summoned"


def test_a_clip_kept_under_its_old_name_moves_rather_than_being_paid_for_again():
    old = system_voice._old_path("Line closed.")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old clip")
    voice = Voice()
    assert system_voice.audio("Line closed.", voice, "end") == b"old clip"
    assert voice.asked == [] and not old.exists() and system_voice._cache_path("Line closed.", "end").exists()


def test_priming_tries_past_a_failure_and_stops_on_a_run_of_them(monkeypatch):
    monkeypatch.setattr(system_voice.time, "sleep", lambda s: None)
    monkeypatch.setattr(system_voice, "LINES", {"a": ["One."], "b": ["Two."], "c": ["Three."]})
    voice = Voice(fail={"One."})
    assert system_voice.prime(voice, []) == 2
    monkeypatch.setattr(system_voice, "LINES", {k: [f"New {k}."] for k in "abcdef"})
    voice = Voice(fail={f"New {k}." for k in "abcdef"})
    assert system_voice.prime(voice, []) == 0 and len(voice.asked) == 3
