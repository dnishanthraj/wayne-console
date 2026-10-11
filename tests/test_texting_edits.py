"""
Texting as people do it now: slips are rare and fixed their own way (edited in place, a '*word', Jim's plain
'Grand. Not Grant.'), a real mix-up now and then put right, shouting that stays shouted — and his own texts his
to edit, with whoever's read them noticing what changed.
"""
import asyncio
import json
import random
from types import SimpleNamespace

from wayne.contacts import directory
from wayne.engine import initiative, tastes
from wayne.memory import groups as group_store
from wayne.memory.texts import TextLog


def _contact(**style):
    return SimpleNamespace(id="nightwing", name="Dick", full_name="Dick Grayson", texting="mostly lowercase",
                           texting_style=style, model="m", options={})


class _Rng(random.Random):
    """A seeded draw that can be told what the next few random() calls say."""
    def __init__(self, seed, first=()):
        super().__init__(seed)
        self.first = list(first)

    def random(self):
        return self.first.pop(0) if self.first else super().random()


# --- slips ---------------------------------------------------------------------------------------------------------

def test_a_slip_is_the_replys_chance_not_each_messages():
    many = ["heading to the docks now", "bringing the good coffee", "dont start without me"]
    careful = _contact(typos=0.0)
    assert initiative._with_slips(careful, list(many), random.Random(1)) == many
    always = _contact(typos=1.0, corrects=0.0)
    out = initiative._with_slips(always, list(many), random.Random(2))
    assert len(out) == 3 and sum(1 for a, b in zip(out, many, strict=True) if a != b) == 1         # one slip, one message


def test_most_who_fix_a_slip_edit_it_in_place():
    editor = _contact(typos=1.0, corrects=1.0, edits=1.0)
    out = initiative._with_slips(editor, ["heading to the docks now"], random.Random(4))
    assert len(out) == 1 and out[0] != "heading to the docks now"
    assert out[0].edit_to == "heading to the docks now"                              # fixed a moment later


def test_jim_fixes_it_plainly_never_with_an_asterisk():
    jim = _contact(typos=1.0, corrects=1.0, edits=0.0, correct_style="plain")
    sent, correction = initiative.slip(jim, "Two units on the corner by nine.", random.Random(3))
    assert sent != "Two units on the corner by nine." and correction.endswith(".") and ". Not " in correction
    assert "*" not in correction


def test_shouting_stays_shouted_for_a_lowercase_texter():
    lower = _contact(lower=1.0)
    assert initiative.styled(lower, "I CANT BELIEVE YOU DID THAT") == "I CANT BELIEVE YOU DID THAT"
    assert initiative.styled(lower, "Wait. WHAT did you just say") == "wait. WHAT did you just say"


# --- a real mix-up -------------------------------------------------------------------------------------------------

def test_a_mix_up_is_rare_and_put_right_their_way(monkeypatch):
    from wayne.engine import model
    text = "dinner at ralli's on thursday at eight, bring tim"
    reply = {"slipped": "dinner at ralli's on tuesday at eight, bring tim", "fix": "wait no, thursday"}
    monkeypatch.setattr(model, "ask", lambda *a, **k: json.dumps(reply))
    never = _contact(misspeaks=0.0)
    assert initiative.misspeak(never, text, "m", {}) is None
    follows = _contact(misspeaks=1.0, edits=0.0)
    slipped, fix = initiative.misspeak(follows, text, "m", {}, _Rng(1, [0.0, 0.99]))
    assert "tuesday" in slipped and fix == "wait no, thursday"
    edits = _contact(misspeaks=1.0, edits=1.0)
    slipped, fix = initiative.misspeak(edits, text, "m", {}, _Rng(1, [0.0, 0.0]))
    assert fix is None and slipped.edit_to == text


def test_a_mix_up_never_brings_in_the_masks(monkeypatch):
    from wayne.engine import model
    text = "see you at the manor on friday, bruce says hi"
    leak = {"slipped": "see you at the cave on friday, batman says hi", "fix": "*manor"}
    monkeypatch.setattr(model, "ask", lambda *a, **k: json.dumps(leak))
    assert initiative.misspeak(_contact(misspeaks=1.0), text, "m", {}, _Rng(1, [0.0])) is None
    rewritten = {"slipped": "totally different words about nothing at all here", "fix": "oops"}
    monkeypatch.setattr(model, "ask", lambda *a, **k: json.dumps(rewritten))
    assert initiative.misspeak(_contact(misspeaks=1.0), "meet at the docks at nine tonight ok", "m", {},
                               _Rng(1, [0.0])) is None


# --- habits in the prompt ------------------------------------------------------------------------------------------

def test_how_they_send_them_and_a_few_habits_go_in_each_time():
    dick = directory().get("nightwing")
    said = tastes.texting_habits(dick.texting_style, random.Random(1))
    assert said.startswith("How you send them:") and "Habits of yours" in said
    assert said.count(";") >= 2                                                           # three of them, not all
    other = tastes.texting_habits(dick.texting_style, random.Random(2))
    assert other != said                                                                  # a different few


# --- edits, stored -------------------------------------------------------------------------------------------------

def test_only_whoever_sent_it_can_edit_it_and_the_first_words_are_kept():
    log = TextLog("nightwing")
    mine = log.add("me", "meet at the docks at 10")
    theirs = log.add("them", "omw")
    edited = log.edit(mine["id"], "meet at the docks at 11", "me")
    assert edited["text"] == "meet at the docks at 11" and edited["was"] == "meet at the docks at 10"
    assert edited["edited_at"]
    assert log.edit(theirs["id"], "rewritten", "me") is None                            # not his to change
    again = log.edit(mine["id"], "meet at the docks at 11:30", "me")
    assert again["was"] == "meet at the docks at 10"                                      # the first words, kept


def test_a_group_message_is_edited_by_its_sender_only():
    group = group_store.create("Family", ["nightwing", "robin"])
    message = group.add("robin", "pizza at 8?")
    assert group.edit(message["id"], "me", "hijacked") is None
    assert group.edit(message["id"], "robin", "pizza at 9?")["text"] == "pizza at 9?"


# --- edits, live ---------------------------------------------------------------------------------------------------

def _console():
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    console._pending_texts = {}
    events, started = [], []

    async def broadcast(event):
        events.append(event)
    console.broadcast = broadcast
    console._start_answering = lambda contact: started.append(contact.id)
    return console, events, started


def test_his_edit_before_theyve_read_it_is_what_they_read():
    console, events, started = _console()
    log = TextLog("nightwing")
    message = log.add("me", "dinner thursday?")
    console._pending_texts["nightwing"] = [dict(message)]
    asyncio.run(console.text_edit("nightwing", message["id"], "dinner friday?"))
    assert console._pending_texts["nightwing"][0]["text"] == "dinner friday?"
    assert events[-1]["type"] == "text_edited" and events[-1]["message"]["text"] == "dinner friday?"
    assert started == []


def test_his_edit_after_theyve_read_it_is_noticed_unless_its_a_typo():
    console, events, started = _console()
    log = TextLog("nightwing")
    message = log.add("me", "meet at the docks at ten")
    log.mark_read([message["id"]])
    asyncio.run(console.text_edit("nightwing", message["id"], "meet at the docks at tne"))
    assert started == [] and not console._pending_texts.get("nightwing")                  # a typo: just fixed
    asyncio.run(console.text_edit("nightwing", message["id"], "actually, meet me at Ace Chemicals instead"))
    assert started == ["nightwing"]
    noted = console._pending_texts["nightwing"][-1]["text"]
    assert "edited a text" in noted and "Ace Chemicals" in noted


def test_their_slip_is_edited_a_moment_later_and_the_page_hears(monkeypatch):
    console, events, _ = _console()
    dick = directory().get("nightwing")

    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    log = TextLog("nightwing")
    sent = log.add("them", "heading to the dcoks now")
    asyncio.run(console._edit_later(dick, sent["id"], "heading to the docks now"))
    assert log.thread.get(sent["id"])["text"] == "heading to the docks now"
    assert events[-1]["type"] == "text_edited"


# --- the voice, on a call ------------------------------------------------------------------------------------------

def test_each_of_them_has_their_own_range_on_a_call(monkeypatch):
    from wayne import delivery
    from wayne.engine import prompting
    monkeypatch.setattr(delivery, "supported", lambda model=None: True)
    system = prompting.build_payload(directory().get("alfred"), [], "x")[0]["content"]
    jason = prompting.build_payload(directory().get("redhood"), [], "x")[0]["content"]
    assert "[quietly]" in system and "never shout" in system                      # in the cached prompt, theirs
    assert "[angry]" in jason and "[shouting]" in jason
    monkeypatch.setattr(delivery, "supported", lambda model=None: False)
    assert "When something lands" not in prompting.build_payload(directory().get("alfred"), [], "x")[0]["content"]
    for cue in ("angry", "softly", "quietly", "worried", "excited", "frustrated", "tired", "chuckles"):
        assert delivery._performable(cue, "eleven_v4_turbo") == cue                   # every one is performed


def test_on_a_call_they_talk_rather_than_write(monkeypatch):
    from wayne import delivery
    from wayne.engine import prompting
    from wayne.engine.session import ContactSession
    monkeypatch.setattr(delivery, "supported", lambda model=None: False)
    alfred, dick = directory().get("alfred"), directory().get("nightwing")
    said = prompting.build_payload(alfred, [], "x")[0]["content"]
    assert "How you talk on the phone" in said and "never 'um'" in said                # his, not everyone's
    assert "'dude'" in prompting.build_payload(dick, [], "x")[0]["content"]
    assert "How you talk on the phone" not in prompting.build_payload(dick, [], "x", texting=True)[0]["content"]
    assert not any("How you talk" in n for n in ContactSession(dick)._voice_notes())     # not re-read every turn
    assert prompting.SPEECH_REALISM in prompting.standing_directives(dick)                # on a call
    assert prompting.SPEECH_REALISM not in prompting.standing_directives(dick, texting=True)
    for contact in directory():
        fresh = contact.boot_prompts.get("fresh", "")
        # A pick-up, the way they answer — not a crafted opening line.
        assert "one line" not in fresh.lower() and "one sentence" not in fresh.lower() and "'" in fresh, contact.id


def test_stage_directions_never_reach_the_subtitles_or_get_read_out():
    from wayne import delivery
    v4 = "eleven_v4_turbo"
    for line, shown, voiced in (
            ("[Sighs.] Fine.", "Fine.", "[sighs] Fine."),                       # its full stop in
            ("[He sighs.] Fine.", "Fine.", "[sighs] Fine."),                    # someone doing it
            ("[laughs softly, shaking my head] No way.", "No way.", "[laughs] No way."),
            ("[Long pause.] Okay.", "Okay.", "Okay."),
            ("*rolls eyes* fine", "fine", "fine"),                               # silent: nowhere
            ("[nods] okay", "okay", "okay"),
            ("[laughs!] no", "no", "[laughs] no")):
        assert delivery.clean(line) == shown, line
        assert delivery.voiced(line, v4) == voiced, line
    assert delivery.clean("I *really* mean it") == "I really mean it"            # emphasis, not a direction
    assert delivery.clean("[Rust, if you want rigour,] but it's worth it") == "Rust, if you want rigour, but it's worth it"


def test_a_pick_up_is_a_word_or_two():
    from wayne.engine import guards
    assert guards.pickup("Hey! Just catching my breath, Haley's eating everything. What's up?") == "Hey!"
    assert guards.pickup("Hey, B. What's up?") == "Hey, B. What's up?"
    assert guards.pickup("Ah — hello, Mr. Wayne. To what do I owe the pleasure?") == "Ah — hello, Mr. Wayne."
