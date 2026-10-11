"""On a call the model is the call's: background passes wait for it, and one already writing gives way."""
import asyncio
import threading
import time

import pytest

from wayne.engine import model as llm
from wayne.frontends.web import Console, log

log.handlers.clear()
log.disabled = True


@pytest.fixture(autouse=True)
def off_air():
    llm.on_air(False)
    yield
    llm.on_air(False)


def test_a_pass_waits_for_the_call_to_end(monkeypatch):
    asked = []
    monkeypatch.setattr(llm.ollama, "chat", lambda **kw: asked.append(time.monotonic()) or
                        {"message": {"content": "a day plan"}})
    llm.on_air(True)
    ended = time.monotonic() + 1.2
    threading.Timer(1.2, llm.on_air, args=(False,)).start()
    assert llm.ask("m", [{"role": "user", "content": "plan"}]) == "a day plan"
    assert asked and asked[0] >= ended - 0.05            # not asked until he'd hung up


def test_one_already_writing_stops_when_a_call_starts_and_starts_again_after(monkeypatch):
    runs, closed = [], []

    class Stream:
        def __init__(self):
            runs.append(self)

        def __iter__(self):
            for i, word in enumerate(["the ", "morning ", "papers"]):
                if i == 1 and len(runs) == 1:
                    llm.on_air(True)                      # he picks up mid-sentence
                    threading.Timer(0.5, llm.on_air, args=(False,)).start()
                yield {"message": {"content": word}}

        def close(self):
            closed.append(self)

    monkeypatch.setattr(llm.ollama, "chat", lambda **kw: Stream())
    assert llm.ask("m", [{"role": "user", "content": "x"}], purpose="the gazette") == "the morning papers"
    assert len(runs) == 2 and runs[0] in closed           # the first was dropped, not run to the end


def test_what_he_asked_for_himself_never_waits(monkeypatch):
    monkeypatch.setattr(llm.ollama, "chat", lambda **kw: {"message": {"content": '{"plan": false}'}})
    llm.on_air(True)
    began = time.monotonic()
    assert llm.ask("m", [], background=False) == '{"plan": false}'
    assert time.monotonic() - began < 0.5


def test_being_on_a_call_is_who_is_on_the_line():
    console = Console.__new__(Console)
    console.current_id = "alfred"
    assert llm.is_on_air()
    console.current_id = None
    assert not llm.is_on_air()


def test_the_pulse_starts_nothing_that_writes_while_he_is_on_a_call():
    console = Console.__new__(Console)
    console.directory = []
    console.clients = {object()}
    ran = []

    def step(name):
        async def go(*_args):
            ran.append(name)
        return go
    for name in ("_write_status_lines", "_keep_up", "_write_day_plans", "_write_gazette", "_write_mail",
                 "_write_dispatches", "_maybe_reach_out", "_calls_in", "_announce", "_group_tick", "_case_tick",
                 "_comms_tick", "_advance_cases"):
        setattr(console, name, step(name))
    console._observe_all = lambda now: None

    async def tick(on):
        ran.clear()
        console.current_id = on
        await console._tick()
        return set(ran)
    calling = asyncio.run(tick("alfred"))
    assert calling == {"_calls_in", "_announce", "_case_tick"}      # the night goes on; nobody writes
    free = asyncio.run(tick(None))
    assert {"_write_day_plans", "_write_gazette", "_group_tick", "_maybe_reach_out"} <= free


def test_cutting_in_stops_a_reply_still_reading_its_prompt(monkeypatch):
    """No word has come yet — the model's reading thousands of tokens — and he talks: it stops now, not then."""
    from types import SimpleNamespace

    from wayne.engine import session as sess

    class Line:
        def __init__(self):
            self.cut = threading.Event()
            self._client = SimpleNamespace(close=self.cut.set)

        def chat(self, **kw):
            if not self.cut.wait(10):           # reading the prompt, a long while
                yield {"message": {"content": "too late"}}
            raise ConnectionError("closed")

    monkeypatch.setattr(sess.ollama, "Client", Line)
    them = sess.ContactSession.__new__(sess.ContactSession)
    them.contact = SimpleNamespace(model="m", id="orphan", options={}, think=None)
    monkeypatch.setattr(them, "_options", lambda **kw: {}, raising=False)
    monkeypatch.setattr(them, "_extra", lambda: {}, raising=False)
    threading.Timer(0.3, them.cancel).start()
    began = time.monotonic()
    assert them._chat_once([{"role": "user", "content": "x"}]) == ""
    assert time.monotonic() - began < 2
