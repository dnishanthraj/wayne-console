"""
Plans and invitations as life has them: a case comes first and then he's late; calling something off lands softly
once and harder as a habit; the rest go on without him unless it only ever made sense with him there; and the
family ask him along to their things.
"""
import asyncio
import json
from types import SimpleNamespace

from wayne.contacts import directory
from wayne.engine import batman, bonds, engagements, invites, plans


def _console():
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    console._members = lambda: []
    texts, groups, spawned = [], [], []

    async def text(cid, body, reply_to=None):
        texts.append((cid, body))

    async def group_text(gid, line, reply_to=None):
        groups.append((gid, line))

    async def broadcast(event):
        return None
    console.text, console.group_text, console.broadcast = text, group_text, broadcast
    console._spawn = lambda coroutine: spawned.append(coroutine) or coroutine.close()
    return console, texts, groups, spawned


def _plan(clock, who, ahead=3600, **extra):
    plan = plans.add({"what": "dinner at Antonio's", "where": "Antonio's", "at": clock() + ahead,
                      "until": clock() + ahead + 2 * 3600, "who": who, **extra}, made_in="")
    for cid in who:
        plans.answer(plan["id"], cid, "yes")
    row = next(e for e in engagements.all_engagements() if e.get("plan") == plan["id"])
    return plan, row


def _fresh(plan_id):
    return next(p for p in plans.all_plans() if p["id"] == plan_id)


# --- a slow burn ------------------------------------------------------------------------------------------------

def test_letting_them_down_is_a_slow_burn_and_a_reason_takes_the_sting_out(clock):
    start = bonds.state("robin", bonds.ME, clock())
    assert bonds.let_down("robin", "he called off pizza", warmth=-2, tension=1.5) == 1
    once = bonds.state("robin", bonds.ME, clock())
    assert start["warmth"] - once["warmth"] < 1.5 and bonds.standing("robin", bonds.ME, clock())[1] == ""   # once: life
    bonds.let_down("robin", "he called off the arcade", warmth=-2, tension=1.5)
    assert bonds.let_down("robin", "he called off the match", warmth=-2, tension=1.5) == 3
    thrice = bonds.state("robin", bonds.ME, clock())
    assert thrice["tension"] > 3 * once["tension"]                                   # a habit of it is what hurts
    assert "3 times this month" in bonds.memories("robin", bonds.ME, clock())[0]["text"]
    before = bonds.state("batgirl", bonds.ME, clock())["warmth"]
    bonds.let_down("batgirl", "he never showed", warmth=-2, understood=True)
    assert before - bonds.state("batgirl", bonds.ME, clock())["warmth"] < 0.5           # a case, and she knows


# --- the rest go on without him, unless it needs him ------------------------------------------------------------

def test_a_family_dinner_goes_on_without_him_and_a_talk_he_called_does_not(clock):
    family, _ = _plan(clock, ["alfred", "batgirl", "robin"])
    plans.he_cant(family["id"], "work")
    family = _fresh(family["id"])
    assert not plans.off(family)
    tim = directory().get("robin")
    clock.at(family["at"])
    assert plans.block(tim, clock())["doing"] == "dinner at Antonio's"              # still going
    clock.at(family["at"] - 3600)
    strangers, _ = _plan(clock, ["redhood", "batwing"], ahead=5 * 3600)
    plans.he_cant(strangers["id"])
    assert plans.off(_fresh(strangers["id"]))                                       # Jason and Randy, without him: no
    clock.at(strangers["at"])
    assert plans.block(directory().get("redhood"), clock()) is None
    clock.at(strangers["at"] - 5 * 3600)
    close, _ = _plan(clock, ["nightwing", "batgirl"], ahead=7 * 3600)
    plans.he_cant(close["id"])
    assert not plans.off(_fresh(close["id"]))                                       # Dick and Barbara: an evening
    talk, _ = _plan(clock, ["alfred", "batgirl", "robin"], ahead=9 * 3600, needs_him=True)
    plans.he_cant(talk["id"])
    assert plans.off(_fresh(talk["id"]))                                            # he called them together
    party, _ = _plan(clock, ["alfred", "batgirl", "robin"], ahead=11 * 3600, host="me")
    plans.he_cant(party["id"])
    assert plans.off(_fresh(party["id"]))                                           # his party


def test_each_of_them_decides_whether_theres_any_point_without_him(clock):
    plan, _ = _plan(clock, ["alfred", "batgirl", "robin"])
    clock.at(clock() + 60)
    plans.he_cant(plan["id"], "Gotham's on fire")
    assert [p["id"] for p in plans.reconsidering("robin", clock())] == [plan["id"]]
    note = plans.note(directory().get("robin"), now=clock())
    assert "can't make it" in note and "Gotham's on fire" in note and "[rsvp: no]" in note
    clock.at(clock() + 60)
    plans.answer(plan["id"], "robin", "no", "no point without him")
    plans.answer(plan["id"], "alfred", "no", "I'll stay in")
    assert plans.reconsidering("robin", clock()) == []
    assert plans.off(_fresh(plan["id"]))                                            # one left: it's off
    assert "it's off" in plans.note(directory().get("batgirl"), now=clock())


# --- calling it off, from the diary -------------------------------------------------------------------------------

def test_calling_off_says_his_reason_and_the_diary_shows_who_still_goes(clock):
    console, texts, groups, _ = _console()
    plan, row = _plan(clock, ["alfred", "batgirl", "robin"], ahead=6 * 3600)
    done = asyncio.run(console.cancel_engagement(row["id"], "Board dinner ran late — sorry, go without me"))
    assert done["status"] == "cancelled" and done["cancel_why"].startswith("Board dinner")
    assert sorted(cid for cid, _ in texts) == ["alfred", "batgirl", "robin"]
    assert all(body == "Board dinner ran late — sorry, go without me" for _, body in texts)
    entry = engagements.entry(engagements._rows().get(row["id"]), clock())
    assert entry["after"].startswith("Going ahead without you") and "Tim" in entry["after"]
    assert "called off dinner at Antonio's" in bonds.memories("robin", bonds.ME, clock())[0]["text"]


def test_calling_off_days_ahead_is_just_plans_changing(clock):
    console, texts, _, _ = _console()
    _, row = _plan(clock, ["robin", "nightwing"], ahead=3 * 86400)
    asyncio.run(console.cancel_engagement(row["id"]))
    assert texts and bonds.memories("robin", bonds.ME, clock()) == []                # nothing to remember
    assert bonds.letdowns("robin", t=clock()) == 0


def test_calling_off_their_evening_tells_them_and_its_theirs_still(clock):
    console, texts, _, _ = _console()
    dick = directory().get("nightwing")
    row = engagements.invited("movie night", "Wayne Manor", clock() + 2 * 3600, clock() + 5 * 3600, dick, now=clock())
    engagements.answer_invite(row["id"], True, clock())
    asyncio.run(console.cancel_engagement(row["id"]))
    assert texts == [("nightwing", "Something's come up — I can't make movie night after all. Sorry.")]
    assert "your movie night" in bonds.memories("nightwing", bonds.ME, clock())[0]["text"]


def test_after_the_evening_one_of_them_might_say_so(clock, monkeypatch):
    import random as _random
    console, _, _, spawned = _console()
    plan, row = _plan(clock, ["alfred", "batgirl", "robin"])
    asyncio.run(console.cancel_engagement(row["id"], "Can't — sorry"))
    clock.at(row["until"] + 60)
    sent = []

    async def send(contact, about, why):
        sent.append((contact.id, about, why))
    console._send_unprompted = send
    console._later = lambda delay, coroutine: coroutine
    console._spawn = lambda coroutine: asyncio.run(coroutine)
    monkeypatch.setattr(_random, "random", lambda: 0.0)
    for e in engagements.called_off_over(clock()):
        console._after_called_off(e)
    assert len(sent) == 1 and sent[0][2] == "letdown"
    assert "you went without him" in sent[0][1] and "Can't — sorry" in sent[0][1] and "one evening" in sent[0][1]
    assert engagements.called_off_over(clock()) == []                                # once


# --- a case first, then late --------------------------------------------------------------------------------------

def test_a_case_first_then_he_goes_late_on_his_own(clock, monkeypatch):
    import wayne.frontends.web as web  # noqa: F401
    console, _, _, _ = _console()
    went = []
    monkeypatch.setattr(engagements, "go", lambda eid, now=None: went.append(eid) or {"to": "there"})
    monkeypatch.setattr(batman, "state", lambda t=None: {"where": "Ace Chemicals"})
    monkeypatch.setattr(batman, "committed", lambda t=None: "in the middle of the hostage situation")
    row = engagements.add("the Mayor's gala", "Gotham Opera House", clock() + 10 * 60, clock() + 3 * 3600)
    asyncio.run(console._diary_tick(clock()))
    assert went == []                                                                # he doesn't walk out on it
    clock.at(row["at"] + 40 * 60)
    monkeypatch.setattr(batman, "committed", lambda t=None: "")
    asyncio.run(console._diary_tick(clock()))
    assert went == [row["id"]]                                                       # done: off he goes, late
    asyncio.run(console._diary_tick(clock()))
    assert went == [row["id"]]                                                       # once


def test_go_waits_for_the_case(clock, monkeypatch):
    row = engagements.add("lunch with Clark", "The Regency", clock() + 600, clock() + 3600)
    monkeypatch.setattr(batman, "committed", lambda t=None: "in the middle of it")
    assert engagements.go(row["id"], clock()) is None


def test_turning_up_late_from_a_case_is_noted_and_forgiven_by_those_who_know(clock, monkeypatch):
    plan, row = _plan(clock, ["nightwing", "batgirl", "robin"], ahead=600)
    venue = row["venue"]
    monkeypatch.setattr(batman, "position", lambda t=None: (venue[0], venue[1]))
    monkeypatch.setattr(engagements, "_was_out", lambda at, until: {"kind": "Hostage situation", "place": "Waterloo Docks"})
    clock.at(row["at"] + 45 * 60)
    engagements.watch(clock())
    kept = engagements._rows().get(row["id"])
    assert kept["late"] == 45 and kept["held_by"] == "Hostage situation at Waterloo Docks"
    assert engagements.late_words(kept) == "45 minutes late"
    clock.at(row["until"] + 60)
    monkeypatch.setattr(engagements, "_was_out", lambda at, until: None)
    assert engagements.settle(clock())[0]["status"] == "attended"
    memory = bonds.memories("robin", bonds.ME, clock())[0]["text"]
    assert "45 minutes late" in memory and "straight from the hostage situation at Waterloo Docks" in memory
    entry = engagements.entry(engagements._rows().get(row["id"]), clock())
    assert entry["late"] == "45 minutes late" and entry["held_by"].startswith("Hostage")


def test_where_he_is_says_what_for_and_as_which_of_him(clock, monkeypatch):
    engagements.add("the Mayor's gala", "Gotham Opera House", clock() - 20 * 60, clock() + 3 * 3600)
    on_the_way = {"where": "Gotham Opera House", "suit": False, "route": {"end": clock() + 600, "by": "by car"}}
    monkeypatch.setattr(batman, "state", lambda t=None: on_the_way)
    assert "on his way to Gotham Opera House for the Mayor's gala, as Bruce Wayne, running late" in batman.note(clock())
    monkeypatch.setattr(batman, "state", lambda t=None: {"where": "Gotham Opera House", "suit": False})
    assert batman.note(clock()) == "Bruce: at Gotham Opera House for the Mayor's gala, as Bruce Wayne"
    engagements.add("a word with Jim", "GCPD Headquarters", clock() + 600, clock() + 3600, as_="batman")
    monkeypatch.setattr(batman, "state", lambda t=None: {"where": "GCPD Headquarters", "suit": True})
    assert batman.note(clock()) == "Bruce: at GCPD Headquarters for a word with Jim, as Batman — suited up"


def test_a_plan_read_from_a_chat_knows_if_it_needs_him_and_which_of_him_goes(clock, monkeypatch):
    from wayne.engine import model
    reply = {"plan": True, "what": "a talk on the roof", "where": "GCPD Headquarters", "date": "2026-10-14",
             "time": "23:00", "hours": 1, "who": ["Jason", "Randy"], "needs_him": True, "as": "batman"}
    monkeypatch.setattr(model, "ask", lambda *a, **k: json.dumps(reply))
    read = plans.read("Jason, Randy — roof at eleven. We need to talk.", [("redhood", "Jason"), ("batwing", "Randy")],
                      "m", {}, clock())
    assert read["needs_him"] and read["as"] == "batman" and read["who"] == ["redhood", "batwing"]
    plan = plans.add(read)
    row = next(e for e in engagements.all_engagements() if e.get("plan") == plan["id"])
    assert row["as"] == "batman" and row["until"] - row["at"] == 3600


# --- the family asking him along ----------------------------------------------------------------------------------

class _Rng:
    def __init__(self, pick):
        self.pick = pick

    def choices(self, seq, weights=None):
        return [next(c for c in seq if c.id == self.pick)]

    def random(self):
        return 0.99                                       # not their regular evening: something of their own


def test_one_of_them_asks_him_along_and_goes_whether_he_comes_or_not(clock, monkeypatch):
    from wayne.engine import model
    found = {"what": "Cass's recital", "where": "Gotham Opera House", "date": "2026-10-16", "time": "19:30",
             "hours": 2, "why": "she won't say it, but she wants you there"}
    monkeypatch.setattr(model, "ask", lambda *a, **k: json.dumps(found))
    made = invites.make(directory(), "m", {}, now=clock(), rng=_Rng("orphan"))
    assert made is not None
    contact, row, about = made
    assert contact.id == "orphan" and row["status"] == "invited" and row["one_off"]
    assert "Cass's recital at Gotham Opera House" in about and "Friday" in about
    assert engagements.latest_invite("orphan")["id"] == row["id"]
    assert invites.make(directory(), "m", {}, now=clock() + 60, rng=_Rng("orphan")) is None   # one at a time
    entry = engagements.entry(row, clock())
    assert entry["state"] == "invited" and entry["origin"] == "Cass's invitation" and entry["inviter"] == "orphan"
    cass = SimpleNamespace(id="orphan")
    block = engagements.their_block(cass, row["at"] - 60)
    assert block and block["doing"] == "Cass's recital"                              # she's there, him or not


def test_answering_from_the_diary_is_said_to_them(clock):
    console, texts, _, _ = _console()
    lucius = directory().get("lucius")
    row = engagements.invited("a jazz night", "The Regency", clock() + 86400, None, lucius, now=clock())
    done = asyncio.run(console.answer_invite(row["id"], False, ""))
    assert done["status"] == "declined" and texts == [("lucius", "I can't make a jazz night — sorry.")]
    assert asyncio.run(console.answer_invite(row["id"], True, "")) is None              # already answered


def test_an_invitation_never_answered_is_noticed(clock):
    tim = directory().get("robin")
    row = engagements.invited("games night", "Wayne Manor", clock() + 3600, clock() + 7200, tim, now=clock())
    clock.at(row["until"] + 60)
    engagements.settle(clock())
    assert engagements._rows().get(row["id"])["status"] == "unanswered"
    assert "never answered about your games night" in bonds.memories("robin", bonds.ME, clock())[0]["text"]
