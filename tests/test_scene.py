"""
A call with company written as one scene: his line answered first, the next lines in one stream voiced as they come,
a line written for him ends the round, cut in on it's kept to what he heard, and with Jim on the line nobody's face
is said — not even in Jim's own lines, though the scene's writer knows them all.
"""
import ollama
import pytest

from wayne.contacts import directory
from wayne.engine import party
from wayne.engine.session import ContactSession


def _stream(*chunks):
    def chat(model=None, messages=None, stream=False, options=None, **kw):
        _stream.seen = {"messages": messages, "options": options}
        return ({"message": {"content": c}} for c in chunks)
    return chat


@pytest.fixture
def call(clock):
    c = party.Call(operator="Bruce Wayne")
    for cid in ("nightwing", "robin"):
        c.join(ContactSession(directory().get(cid)))
    return c


def _said(events):
    return [(e["speaker"], e["text"]) for e in events if e["type"] == "sentence"]


def test_his_line_is_answered_in_one_stream_and_a_line_for_him_ends_it(call, monkeypatch):
    monkeypatch.setattr(ollama, "chat", _stream("Di", "ck: Hey B. Where", " are you?\nTi", "m: On the roof.\n",
                                                "Bruce: I'm on my way.\nDick: never said"))
    out = list(call.turn("Dick, where are you?"))
    assert _said(out) == [("nightwing", "Hey B."), ("nightwing", "Where are you?"), ("robin", "On the roof.")]
    assert out[0] == {"type": "message", "role": "user", "text": "Dick, where are you?"}
    assert ("nightwing", "Hey B. Where are you?") in call.transcript and ("robin", "On the roof.") in call.transcript
    scene = call.writer()
    assert scene.messages[-1]["content"] == "Dick: Hey B. Where are you?\nTim: On the roof."
    assert "(He's talking to Dick.)" in scene.messages[-2]["content"]
    assert "\nBruce:" in _stream.seen["options"]["stop"]


def test_a_sentence_is_voiced_before_its_line_is_finished(call, monkeypatch):
    seen = []
    monkeypatch.setattr(ollama, "chat", _stream("Dick: First thing. Second", " thing.\n"))
    for event in call.turn("Talk to me."):
        if event["type"] == "sentence":
            seen.append(event["text"])
        if event["type"] == "reply_end":
            seen.append("|end")
    assert seen == ["First thing.", "Second thing.", "|end"]


def test_cut_in_on_it_the_scene_keeps_only_what_he_heard(call, monkeypatch):
    monkeypatch.setattr(ollama, "chat", _stream("Dick: One. Two. Three.\n", "Tim: Four.\n"))
    turn = call.turn("Go on.")
    for event in turn:
        if event["type"] == "sentence" and event["text"] == "Two.":
            break
    turn.close()                                   # he talked over it
    scene = call.writer()
    scene.heard_only(1)                            # and heard only the first sentence begin
    assert scene.messages[-1]["content"] == "Dick: One. —"
    assert not any(who == "robin" for who, _ in call.transcript)


def test_with_jim_on_the_line_nobody_says_a_face_not_even_jim(clock, monkeypatch):
    c = party.Call(operator="Bruce Wayne")
    for cid in ("gordon", "nightwing", "robin"):
        c.join(ContactSession(directory().get(cid)))
    scene = c.writer()
    assert scene.label(c.members[1]) == "Nightwing" and scene.him() == "Batman"
    monkeypatch.setattr(ollama, "chat", _stream("Jim: Where's Dick Grayson tonight?\n",
                                                "Nightwing: Thanks, Tim.\n"))
    said = " ".join(t for _, t in _said(c.turn("Report.")))
    assert said == "Where's Nightwing tonight? Thanks."        # Jim's line masked; "Tim", said to him, left out


def test_a_pause_can_go_unfilled_and_markers_are_theirs_to_act_on(call, monkeypatch):
    monkeypatch.setattr(ollama, "chat", _stream("(nothing)"))
    assert _said(call.lull(None, 1)) == []
    assert call.writer().messages[-1]["content"] == "(nothing)"
    monkeypatch.setattr(ollama, "chat", _stream("Tim: Gotta go. [hang up]\n"))
    said = _said(call.lull(None, 2))
    tim = call.members[1]
    assert said == [("robin", "Gotta go.")] and tim._hanging_up


def test_the_scene_failing_falls_back_to_whoever_he_spoke_to(call, monkeypatch):
    def broken(**kw):
        raise ConnectionError("model gone")
    monkeypatch.setattr(ollama, "chat", broken)
    tim = call.members[1]

    def ask(prompt, **kw):
        from wayne import events
        yield events.sentence(0, "Still here.")
        yield events.reply_end("Still here.")
    monkeypatch.setattr(tim, "ask", ask)
    assert ("robin", "Still here.") in [(e.get("speaker"), e["text"]) for e in call.turn("Tim?")
                                        if e["type"] == "sentence"]


def test_ollamas_own_parts_are_read_not_just_dicts(call, monkeypatch):
    class Part:                      # what the real client streams: dict-like, not a dict
        def __init__(self, text):
            self.text = text

        def get(self, key, default=None):
            return {"content": self.text} if key == "message" else default

    def chat(**kw):
        return (Part(c) for c in ("Dick: Still here, B.\n",))
    monkeypatch.setattr(ollama, "chat", chat)
    assert _said(call.turn("You there?")) == [("nightwing", "Still here, B.")]
