"""
Word passed on — "tell Tim to text me when he's free" — and a call that starts, for whoever's patched in, when
they pick up: not "let me call Dick", said before Dick's phone even rang.
"""
from types import SimpleNamespace

from wayne.contacts import directory
from wayne.engine import grapevine, initiative, model, party, presence


class _Sure:
    """Chance that always comes up, and the soonest of every wait."""
    def random(self):
        return 0.0

    def uniform(self, a, b):
        return a


def _member(cid):
    return SimpleNamespace(contact=directory().get(cid), heard=[], call=None, _hanging_up=False,
                           mark_call_start=lambda: None, call_messages=lambda: [], keep_heard=lambda: None)


def _console(members):
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    console._members = lambda: list(members)
    return console


def test_let_me_call_dick_is_his_to_do_and_alfred_get_dick_is_asking():
    console = _console(["alfred"])
    for his_own in ("Let me call Dick.", "I'll ring Dick after this", "ok, I'm gonna get Dick on the line",
                    "let's get Dick on", "Thanks Alfred, let me call Dick"):
        assert console._asked_to_add(his_own) is None, his_own
    for asked in ("Alfred, call Dick", "Can you get Dick on the line?", "I need you to patch Dick in",
                  "I'm tired. Get Dick on the call"):
        assert console._asked_to_add(asked) == "nightwing", asked


def test_whoever_is_patched_in_hears_the_call_from_when_they_pick_up():
    call = party.Call()
    alfred, dick, tim = _member("alfred"), _member("nightwing"), _member("robin")
    call.join(alfred)
    call.transcript += [("me", "Let me call Dick, he'll know."), ("alfred", "Very good, sir. Master Dick will be thrilled."),
                        (None, "(Barbara didn't pick up when he tried to add them)")]
    call.join(dick)
    assert not any("Let me call Dick" in h or "thrilled" in h for h in dick.heard)
    assert any("didn't pick up" in h for h in dick.heard)            # what his phone shows him, he knows
    call.join(tim, overheard=True)                                    # in the room from the start: he heard it
    assert any("Let me call Dick" in h for h in tim.heard)


def test_the_rest_of_a_sentence_still_being_said_as_they_pick_up_is_heard():
    call = party.Call()
    alfred, dick, jim = _member("alfred"), _member("nightwing"), _member("gordon")
    call.join(alfred)
    call.join(dick)
    dick.heard.append("Alfred: Good evening, Master Dick.")          # said after he'd picked up
    console = _console(["alfred", "nightwing"])
    console.call, console.sessions = call, {"alfred": alfred, "nightwing": dick, "gordon": jim}
    console.caught("nightwing", [{"speaker": "alfred", "text": "…the car's been seen at the docks."}])
    assert dick.heard[0].startswith("(as you picked up)") and "docks" in dick.heard[0]
    assert dick.heard[1] == "Alfred: Good evening, Master Dick."
    call.join(jim)
    console._members = lambda: ["alfred", "nightwing", "gordon"]
    console.caught("gordon", [{"speaker": "alfred", "text": "…shall I tell Master Bruce you rang?"},
                              {"speaker": "nightwing", "text": "…on the roof in five."}])
    assert not any("Bruce" in h for h in jim.heard)                   # said before Jim was on: never his to hear
    assert any("roof" in h for h in jim.heard)


def test_tell_tim_to_text_me_reaches_tim_and_he_means_to(clock, monkeypatch):
    monkeypatch.setattr(grapevine, "seen_at", lambda contact, t, rng=None: t + 60)
    dick, tim = directory().get("nightwing"), directory().get("robin")
    sent = grapevine.relay(dick, [{"to": "Tim", "message": "he wants you to text him when you're free",
                                   "wants": "text"}], rng=_Sure())
    assert [cid for cid, _ in sent] == ["robin"]
    reaches = sent[0][1]
    assert grapevine.relayed("robin", reaches - 1) == []               # Dick hasn't got round to it yet
    told = grapevine.relayed("robin", reaches + 5)[0]
    assert told["from"] == "Dick" and "text him when you're free" in told["text"]
    intent = presence.of(tim).intents()[0]
    assert intent["origin"] == "relayed" and intent["action"] == "text" and intent["due"] > reaches
    assert "Dick told you" in intent["about"] and intent["arrives"] == reaches


def test_word_for_jim_comes_only_from_a_mask_and_never_names_him(clock, monkeypatch):
    monkeypatch.setattr(grapevine, "seen_at", lambda contact, t, rng=None: t + 60)
    book = directory()
    ask = [{"to": "Jim", "message": "he wants you to call him tonight", "wants": "call"}]
    assert grapevine.relay(book.get("alfred"), ask, rng=_Sure()) == []      # Alfred is nobody Jim knows
    assert grapevine.relay(book.get("batgirl"), [{"to": "Jim", "message": "Bruce wants a word", "wants": "call"}],
                           rng=_Sure()) == []
    sent = grapevine.relay(book.get("batgirl"), ask, rng=_Sure())
    told = grapevine.relayed("gordon", sent[0][1] + 1)[0]
    assert told["from"] == "Batgirl" and told["text"].startswith("Batman asked Batgirl")


def test_nothing_of_the_work_goes_to_selina_and_nobody_on_the_call_needs_telling(clock, monkeypatch):
    monkeypatch.setattr(grapevine, "seen_at", lambda contact, t, rng=None: t + 60)
    dick = directory().get("nightwing")
    assert grapevine.relay(dick, [{"to": "Selina", "message": "the patrol's moved to midnight"}], rng=_Sure()) == []
    assert grapevine.relay(dick, [{"to": "Tim", "message": "he wants you to text him", "wants": "text"}],
                           present=("robin",), rng=_Sure()) == []


def test_once_theyve_talked_to_him_word_that_had_reached_them_is_said_and_done(clock):
    tim = presence.of(directory().get("robin"))
    tim.intend("text", "Dick told you…", clock() + 600, origin="relayed", arrives=clock() - 60)
    tim.intend("call", "about the docks", clock() + 900, origin="promise")
    tim.drop_reached("relayed")
    assert [i["origin"] for i in tim.intents()] == ["promise"]
    tim.intend("text", "Barbara told you…", clock() + 600, origin="relayed", arrives=clock() + 300)
    tim.drop_reached("relayed")
    assert "relayed" in [i["origin"] for i in tim.intents()]        # it hasn't reached him yet: it stays


def test_the_afterthought_asks_and_sends_word_on_and_news_travels_unless_hushed(clock, monkeypatch):
    seen = {}

    def ask(model_name, messages, options, **kw):
        seen["prompt"] = messages[0]["content"]
        return '{"pass_on": [{"to": "Tim", "message": "text him", "wants": "text"}], "news": "Bruce is off to Paris"}'
    monkeypatch.setattr(model, "ask", ask)
    relayed, said = [], []
    monkeypatch.setattr(grapevine, "relay", lambda source, asks, present=(): relayed.append((source.id, present)))
    monkeypatch.setattr(grapevine, "note_said", lambda source, item, contacts, present=(): said.append(item))
    session = SimpleNamespace(contact=directory().get("nightwing"))
    initiative.afterthought(session, [{"role": "user", "content": "I'm off to Paris friday. tell Tim to text me"},
                                      {"role": "assistant", "content": "will do"}], "text")
    assert "pass something on" in seen["prompt"] and '"pass_on"' in seen["prompt"] and '"news"' in seen["prompt"]
    assert relayed == [("nightwing", ())] and said == ["Bruce is off to Paris"]
    initiative.apply(session, {"news": "Bruce is seeing someone"}, by="text",
                     exchanges=[{"role": "user", "content": "don't tell anyone but I'm seeing someone"}])
    assert said == ["Bruce is off to Paris"]                           # asked to keep it quiet: it stays
    initiative.apply(session, {"news": "Bruce is tired"}, by="call", exchanges=[])
    assert len(said) == 1                                               # a call's news goes by grapevine.note_call


def test_news_he_texts_reaches_those_close_to_them_in_their_own_time(clock, monkeypatch):
    monkeypatch.setattr(grapevine.random, "random", lambda: 0.0)
    dick = directory().get("nightwing")
    grapevine.note_said(dick, "Bruce is off to Paris on Friday", list(directory()))
    reached = [c.id for c in directory() if any("Paris" in h["text"] for h in grapevine.heard(c.id, clock() + 15 * 3600))]
    assert "batgirl" in reached and "nightwing" not in reached
    assert not any("Paris" in h["text"] for h in grapevine.heard("batgirl", clock() + 60))   # not the moment he said it
