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

    def transcribe(body, hint=None):
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
    monkeypatch.setattr(stt, "transcribe_pcm", lambda body, hint=None: ("heard", 0.9))
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
