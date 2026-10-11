"""
Hearing him on a call: his turns have a thread of their own and go before any live caption; a caption only takes
what he's saying now; and a sound the decoder is sure wasn't speech isn't a turn.
"""
import asyncio
import threading
import time

import numpy as np

import wayne.frontends.web as web
from wayne.audio import stt


class _Request:
    def __init__(self, body, partial=False):
        self._body = body
        self.query_params = {"partial": "1"} if partial else {}

    async def body(self):
        return self._body


def _pcm(seconds):
    return (np.ones(int(seconds * stt.SAMPLE_RATE), dtype=np.float32) * 0.1).tobytes()


def test_a_caption_gives_way_to_his_turn_and_only_hears_the_last_few_seconds(monkeypatch):
    heard = []

    def transcribe(body, hint=None, **kw):
        heard.append(len(body) / 4 / stt.SAMPLE_RATE)
        return "words", 0.9
    monkeypatch.setattr(stt, "transcribe_pcm", transcribe)
    monkeypatch.setattr(web, "_speech_hint", lambda: "")
    web._HEARING["waiting"] = 1                                   # his turn is waiting to be heard
    skipped = asyncio.run(web.transcribe(_Request(_pcm(10), partial=True)))
    assert b'"skipped":true' in skipped.body and heard == []
    web._HEARING["waiting"] = 0
    asyncio.run(web.transcribe(_Request(_pcm(10), partial=True)))
    assert heard == [web._CAPTION_SECONDS]                       # only the last few seconds
    asyncio.run(web.transcribe(_Request(_pcm(10))))
    assert heard[-1] == 10 and web._HEARING["waiting"] == 0       # his turn: all of it


def test_his_turn_never_waits_on_the_shared_pool(monkeypatch):
    """Every shared worker tied up with model calls: hearing him still goes ahead, on its own thread."""
    monkeypatch.setattr(stt, "transcribe_pcm", lambda body, hint=None, **kw: ("heard", 0.9))
    monkeypatch.setattr(web, "_speech_hint", lambda: "")
    release = threading.Event()

    async def main():
        loop = asyncio.get_running_loop()
        blocked = [loop.run_in_executor(None, release.wait) for _ in range(64)]    # the shared pool, saturated
        began = time.monotonic()
        reply = await asyncio.wait_for(web.transcribe(_Request(_pcm(2))), timeout=5)
        took = time.monotonic() - began
        release.set()
        await asyncio.gather(*blocked)
        return reply, took
    reply, took = asyncio.run(main())
    assert b"heard" in reply.body and took < 2


def test_a_sound_the_decoder_is_sure_was_not_speech_is_not_a_turn():
    noise = {"segments": [{"no_speech_prob": 0.85, "avg_logprob": -1.2}]}
    speech = {"segments": [{"no_speech_prob": 0.1, "avg_logprob": -0.3}]}
    assert stt._hallucinated("hand me the wrench", noise, 3.0)
    assert not stt._hallucinated("hand me the wrench", speech, 3.0)


# --- the sound of where they'll be, made ahead -------------------------------------------------------------------

def test_the_places_coming_up_are_made_ahead_within_the_days_allowance(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from wayne import config
    from wayne.audio import ambience
    monkeypatch.setattr(ambience, "available", lambda: True)
    monkeypatch.setattr(ambience, "path", lambda name: tmp_path / f"{name}.mp3")
    made = []

    def clip(name):
        made.append(name)
        (tmp_path / f"{name}.mp3").write_bytes(b"x" * 2000)
        return tmp_path / f"{name}.mp3"
    monkeypatch.setattr(ambience, "clip", clip)
    family = [SimpleNamespace(id="a"), SimpleNamespace(id="a2"), SimpleNamespace(id="b")]
    monkeypatch.setattr(ambience, "scene_for",
                        lambda contact, t=None: "rooftop" if contact.id in ("a", "a2") else "cave")
    monkeypatch.setattr(config, "SFX_WARM_PER_DAY", 100000)
    first = ambience.warm(family, most=2)
    assert first == ["rooftop", ambience._wanted("rooftop")[1]]               # where most of them will be, loop first
    monkeypatch.setattr(config, "SFX_WARM_PER_DAY", 0)
    assert ambience.warm(family) == []                                        # switched off
    monkeypatch.setattr(config, "SFX_WARM_PER_DAY", 1)
    assert ambience.warm(family) == []                                        # nothing fits today's allowance


def test_the_room_is_occasional_and_never_the_same_sound_twice_running():
    import random

    from wayne.audio import ambience
    state = {}
    cues = ambience._lay_out("street_day", 0, 1200, random.Random(3), state, sky="clear")
    assert cues and len(cues) / 20 < 3                                       # a few a minute at most, on a street
    for a, b in zip(cues, cues[1:], strict=False):
        assert b["at"] - a["at"] >= ambience.MIN_GAP
    seen = {}
    for cue in cues:
        assert cue["at"] - seen.get(cue["sound"], -1e9) >= ambience.REPEAT_GAP
        seen[cue["sound"]] = cue["at"]
    more = ambience._lay_out("street_day", 1200, 2400, random.Random(4), state, sky="clear")
    assert not more or more[0]["at"] - cues[-1]["at"] >= ambience.MIN_GAP      # carried on, not piled up


def test_on_a_call_he_is_heard_on_the_cpu_and_its_confidence_is_read(monkeypatch):
    from types import SimpleNamespace
    used = []

    class CPU:
        def transcribe(self, audio, **kw):
            used.append("cpu")
            # A generator, as faster-whisper's is: it decodes as it's read.
            return (s for s in [SimpleNamespace(text=" Where's Dick?", avg_logprob=-1.2, compression_ratio=1.1,
                                                no_speech_prob=0.1)]), None
    monkeypatch.setattr(stt, "_resolve_backend", lambda: ("mlx", "model"))
    monkeypatch.setattr(stt, "_cpu_model", lambda: CPU())
    speech = (np.random.default_rng(1).standard_normal(stt.SAMPLE_RATE * 2) * 0.1).astype(np.float32)
    text, confidence = stt.transcribe_audio(speech, on_cpu=True)
    assert used == ["cpu"] and text == "Where's Dick?"
    assert confidence < 1.0                      # the segments were read for it — not found empty the second time


def test_the_names_to_listen_for_put_the_citys_own_first_and_fit():
    import json

    from wayne import paths
    (paths.DATA_DIR / "lore.json").write_text(json.dumps({"people": [{"id": "x", "name": "Testa Quill", "alias": "TQ"}],
                                                         "places": [{"name": "Quill's Diner", "kind": "diner",
                                                                     "x": 100, "y": 60}]}))
    hint = web._speech_hint()
    assert hint.index("Testa Quill") < hint.index("Alfred")           # a name nobody's model knows, before the cast
    assert "TQ" in hint and "Quill's Diner" in hint and len(hint) <= 600
