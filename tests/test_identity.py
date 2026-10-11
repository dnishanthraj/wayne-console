"""Who knows what about the masks: what each is told, who they hear, what they may say — and Jim Gordon."""
from types import SimpleNamespace

import pytest

from wayne import identity, operator
from wayne.contacts import directory
from wayne.engine import party


@pytest.fixture
def book():
    return directory()


# --- levels ----------------------------------------------------------------------------------

def test_how_each_of_them_knows_him(book):
    assert identity.level("robin") == "all" and identity.level("alfred") == "all"
    assert identity.level("catwoman") == "his" and identity.level("gordon") == "masks"
    assert identity.knows_him("catwoman") and not identity.knows_faces("catwoman")
    assert not identity.knows_him("gordon") and identity.outside("gordon")
    assert identity.insiders(book) >= {"alfred", "robin", "batgirl", "lucius"}


def test_the_story_can_change_and_everything_follows(book):
    assert operator.name("gordon") == "Batman"
    identity.set_level("gordon", "all")                         # Jim found out
    assert operator.name("gordon") == "Bruce" and "Under the masks" in operator.briefing("gordon")
    assert identity.label("batgirl", "gordon") == "Barbara"
    identity.set_level("gordon", None)                          # back to what the profile says
    assert identity.level("gordon") == "masks" and operator.name("gordon") == "Batman"


# --- what Jim is told -----------------------------------------------------------------------------

def test_jim_is_told_about_batman_and_never_about_bruce(book):
    told = operator.briefing("gordon")
    assert "He is Batman — to you, only Batman" in told
    for leak in ("He is Bruce Wayne", "cave", "Under the masks", "Randy Wayne is", "is Batgirl", "Nightwing is",
                 "Who doesn't know"):
        assert leak not in told
    assert "Barbara" in told and "nothing to do with any of this" in told


def test_those_who_know_are_told_who_doesnt(book):
    told = operator.briefing("robin")
    assert "Jim Gordon knows Batman and the masks" in told and "Selina Kyle knows he's Batman" in told
    assert "Who doesn't know" not in operator.briefing("catwoman")


def test_jims_own_bio_holds_nothing_he_cant_know(book):
    bio = book.get("gordon").bio
    assert not any(w in bio for w in ("Bruce", "Alfred", "Barbara is", "one of us", "Wayne"))


# --- who hears whom as who -------------------------------------------------------------------------

def test_jim_hears_everyone_by_their_mask(book):
    assert identity.label("me", "gordon") == "Batman"
    assert identity.label("batgirl", "gordon") == "Batgirl"
    assert identity.label("nightwing", "gordon") == "Nightwing"
    assert identity.label("alfred", "gordon") == "a British voice on Batman's line"
    assert identity.label("catwoman", "gordon") == "Catwoman"
    assert identity.label("nightwing", "catwoman") == "Dick"           # Selina knows the faces
    assert identity.label("gordon", "batgirl") == "Jim"


class Member:
    def __init__(self, contact):
        self.contact, self.heard, self.call = contact, [], None
        self._hanging_up = False

    def mark_call_start(self):
        pass

    def call_messages(self):
        return []

    def keep_heard(self):
        pass


def test_on_a_call_jim_hears_batgirl_and_batman_and_she_hears_her_father(book):
    gordon, barbara = Member(book.get("gordon")), Member(book.get("batgirl"))
    call = party.Call()
    call.join(gordon)
    call.join(barbara)
    call._heard_by_all(barbara, "Two shooters on the roof.")
    call.transcript.append(("me", "Hold the perimeter."))
    for member in (gordon, barbara):
        member.heard.append(call.heard_as("me", "Hold the perimeter.", member))
    assert gordon.heard == ["Batgirl: Two shooters on the roof.", "Batman: Hold the perimeter."]
    assert barbara.heard[-1].startswith("Bruce Wayne: ")
    note = call.note_for(barbara)
    assert "Jim Gordon is on this call" in note and "never 'Dad'" in note
    assert "you, Batman, Batgirl" in call.note_for(gordon)
    assert "Jim Gordon is on this call" not in call.note_for(gordon)


def test_a_line_about_someone_is_said_as_each_listener_knows_them(book):
    line = f"{identity.mention('nightwing')} is on the call now"
    assert identity.render(line, "gordon") == "Nightwing is on the call now"
    assert identity.render(line, "robin") == "Dick is on the call now"


# --- what they may say with Jim listening ------------------------------------------------------------

def test_with_jim_listening_his_name_and_theirs_are_caught(book):
    caught = identity.guard_vocatives("robin", ["gordon", "nightwing"])
    assert "Bruce" in caught and "Dick" in caught and "B" in caught
    assert identity.guard_vocatives("gordon", ["robin"]) == ()
    assert "Nightwing" in identity.guard_vocatives("robin", ["catwoman", "nightwing"])
    assert "Bruce" not in identity.guard_vocatives("robin", ["catwoman"])     # Selina knows him


def test_his_name_mid_sentence_becomes_batman_and_the_billionaire_stays(book):
    said = identity.scrub("Bruce is right — Bruce Wayne's gala can wait.", "robin", ["gordon"])
    assert said == "Batman is right — Bruce Wayne's gala can wait."
    assert identity.scrub("Bruce is right.", "robin", ["catwoman"]) == "Bruce is right."


def test_with_jim_listening_their_names_and_nicknames_become_masks(book):
    # Dick, in a chat with Jim and Barbara, as he actually wrote it before this was caught.
    said = identity.scrub("@barbara babs check those feeds. thanks Babs", "nightwing", ["gordon", "batgirl"])
    assert said == "@batgirl check those feeds. thanks Batgirl"
    assert identity.scrub("stay safe babs Barbara", "nightwing", ["gordon"]) == "stay safe batgirl"
    assert identity.scrub("Tim and Barbara Gordon are on it", "robin", ["gordon"]) == "Robin and Batgirl are on it"
    assert identity.scrub("Dick here.", "nightwing", ["gordon"]) == "Nightwing here."
    assert identity.scrub("he ran off, the dick", "redhood", ["gordon"]) == "he ran off, the dick"   # just words
    assert identity.scrub("thanks Babs", "nightwing", ["catwoman"]) == "thanks Babs"     # Selina knows the faces
    assert identity.scrub("my Barbara's home", "gordon", ["nightwing"]) == "my Barbara's home"   # his to say


def test_a_chat_with_jim_in_it_tags_everyone_by_their_mask(book):
    from wayne.engine import groupchat
    with_jim = SimpleNamespace(members=["gordon", "batgirl", "nightwing"])
    without = SimpleNamespace(members=["batgirl", "nightwing"])
    assert groupchat.handles(with_jim, book, "nightwing") == {"me": "Batman", "gordon": "Jim", "batgirl": "Batgirl"}
    assert groupchat.handles(without, book, "nightwing") == {"me": "Bruce", "batgirl": "Barbara"}
    assert groupchat.addressed("@Batgirl you seeing this?", book.get("batgirl"))
    assert groupchat.addressed("babs, you there", book.get("batgirl"))
    assert groupchat.tagged("@Batgirl feeds?", book.get("batgirl"))


def test_what_has_to_be_kept_depends_on_who_listens():
    assert identity.guards(["gordon"]) == {"him", "faces"}               # the work, by the masks
    assert identity.guards(["catwoman"]) == {"faces", "work"}            # none of the work
    assert identity.guards(["alfred"]) == set()


def test_a_chat_with_jim_in_it_warns_the_family(book):
    from wayne.engine import groupchat
    group = SimpleNamespace(members=["robin", "gordon"])
    assert "Jim Gordon is in this chat" in groupchat.secrets_note(group, "robin")
    assert groupchat.secrets_note(group, "gordon") == ""
    name = groupchat.names(book, "gordon")
    assert name("robin") == "Robin" and name("me") == "Batman"


# --- news and memory ---------------------------------------------------------------------------------

def test_the_familys_work_never_reaches_jim_as_hearsay():
    from wayne.engine import grapevine
    assert grapevine._crosses_secret("Bruce asked Tim to stake out the docks", "gordon")
    assert not grapevine._crosses_secret("Bruce is selling the Duesenberg", "gordon")
    assert not grapevine._crosses_secret("Bruce asked Tim to stake out the docks", "robin")


def test_jim_remembers_his_own_nights(book, clock):
    from wayne.store import events, recall
    events.add("diary.doing", "at GCPD Central — up all night on the Joker's breakout", at=clock() - 2 * 86400 + 10 * 3600,
               actor="gordon", audience=["gordon"])
    assert "Joker's breakout" in recall.block(book.get("gordon"), "What were you doing on Monday night?")


# --- Jim's desk ---------------------------------------------------------------------------------------

def test_jims_desk_knows_the_masks_on_scene_and_never_their_faces(book, clock, monkeypatch):
    from wayne.engine import ContactSession, cases, incidents
    report = {"id": "r-desk", "kind": "Armed robbery", "severity": 3, "place": "Ralli's Family Restaurant",
              "area": "Otisburg", "x": 49.0, "y": 30.0, "at": clock() - 900, "status": "units responding"}
    monkeypatch.setattr(incidents, "at", lambda t=None: [report])
    cases.assign(report, "robin", travel=0.1)
    cases.assign(report, "nightwing", travel=0.1)
    clock.at(clock() + 120)
    cases.advance()
    desk = ContactSession(book.get("gordon"))._desk("Anything on the scanner tonight?")
    assert "Robin and Nightwing at the armed robbery at Ralli's" in desk or "Nightwing and Robin" in desk
    assert "Tim" not in desk and "Dick" not in desk
    assert "armed robbery — Ralli's Family Restaurant" in desk


def test_a_case_brief_with_jim_listening_names_the_team_by_mask(book, clock):
    from wayne.engine import cases
    report = {"id": "r-brief", "kind": "Hostage situation", "severity": 4, "place": "Gotham Zoo",
              "area": "Fashion District", "x": 40.0, "y": 50.0, "at": clock()}
    cases.assign(report, "robin", travel=5)
    cases.assign(report, "batgirl", travel=5)
    told = cases.brief("robin", masks=True)
    assert "Batgirl" in told and "Barbara" not in told and "Wayne Manor" not in told


# --- Jim calling ---------------------------------------------------------------------------------------

def test_jim_rings_when_something_big_lands_and_nobodys_on_it(book, clock, monkeypatch):
    import asyncio

    import wayne.frontends.web as web
    from wayne.engine import incidents
    report = {"id": "r-big", "kind": "Hostage situation", "severity": 4, "place": "Gotham Zoo",
              "area": "Fashion District", "at": clock() - 300, "status": "contained", "toll": {"dead": 2}}
    monkeypatch.setattr(incidents, "at", lambda t=None: [report])
    monkeypatch.setattr(web.random, "random", lambda: 0.0)
    console = web.Console.__new__(web.Console)
    console.directory, console.current_id, console._incoming, console._ringing_out = book, None, None, None
    rung = []

    async def ring(contact, about):
        rung.append((contact.id, about))
    console._ring = ring
    asyncio.run(console._calls_in(clock()))
    assert rung and rung[0][0] == "gordon" and "Gotham Zoo" in rung[0][1]
    rung.clear()
    asyncio.run(console._calls_in(clock() + 60))
    assert rung == []                                          # once per call, and not again inside his gap


def test_the_codex_puts_jim_in_his_phone(book):
    from wayne.engine import codex
    jim = next(p for p in codex.people(book) if p["id"] == "gordon")
    assert jim["callable"] and jim["contact"] == "gordon"


# --- picking up, like anyone ---------------------------------------------------------------------

def _rate(contact, state, n=400):
    import random as rnd

    from wayne.engine import presence
    return sum(presence.answers(contact, state, rng=rnd.Random(i)) is None for i in range(n)) / n


def test_busy_with_the_roses_alfred_still_picks_up(book):
    roses = {"status": "busy", "doing": "tending the roses"}
    meeting = {"status": "busy", "doing": "in a board meeting"}
    assert _rate(book.get("alfred"), roses) > 0.85
    assert _rate(book.get("lucius"), meeting) < 0.4


def test_on_the_way_to_a_scene_they_answer_and_in_the_fight_they_dont(book, clock):
    from wayne.engine import cases
    report = {"id": "r-ans", "kind": "Armed robbery", "severity": 3, "place": "Ralli's Family Restaurant",
              "area": "Otisburg", "x": 49.0, "y": 30.0, "at": clock()}
    cases.assign(report, "nightwing", travel=20)
    working = {"status": "busy", "doing": "on the way to the armed robbery at Ralli's"}
    assert _rate(book.get("nightwing"), working) > 0.8
    case = cases.for_report("r-ans")
    clock.at(clock() + 21 * 60)
    cases.advance()
    case = cases.for_report("r-ans")
    clock.at(case["updated_at"] + 0.4 * (case["due"] - case["updated_at"]))      # in the thick of it
    assert cases.phase(case)[0] in ("in it", "gone wrong")
    assert _rate(book.get("nightwing"), {"status": "busy", "doing": "working the armed robbery"}) < 0.25


def test_the_commissioner_at_the_podium_lets_it_ring(book):
    assert _rate(book.get("gordon"), {"status": "busy", "doing": "in a press conference"}) < 0.2
    assert _rate(book.get("gordon"), {"status": "online", "doing": "working late in your office"}) > 0.9
