"""Invitations he answers: Alfred keeps the diary, and an evening missed for a case is an evening missed."""
import datetime
import time

from wayne.engine import cases, engagements, inbox


def _invite(clock, days=2, hour="20:00"):
    day = (datetime.date.fromtimestamp(clock()) + datetime.timedelta(days=days)).isoformat()
    return inbox.deliver("Mayor's Office", "The Mayor's Fundraiser", "Do come.", kind="invitation",
                         name="Office of the Mayor", address="events@gotham.gov",
                         extra={"engagement": {"what": "the Mayor's fundraiser", "where": "Gotham Opera House",
                                               "date": day, "time": hour, "dress": "black tie", "host": "Mayor Nakano"}})


def test_an_evening_has_a_when_or_it_isnt_one(clock):
    assert engagements.when_from({"date": "2026-10-16", "time": "19:30"}, clock())
    assert engagements.when_from({"date": "someday"}, clock()) is None
    assert engagements.when_from({"date": "2030-01-01", "time": "20:00"}, clock()) is None     # years off: no


def test_accepting_puts_it_in_the_diary_and_alfred_answers_for_him(clock):
    invite = _invite(clock)
    row, note = inbox.rsvp(invite["id"], True)
    assert row["status"] == "accepted" and row["where"] == "Gotham Opera House"
    assert note["from"].startswith("Alfred Pennyworth") and "accepts with pleasure" in note["body"]
    assert [m["id"] for m in inbox.thread(invite["thread"])] == [invite["id"], note["id"]]
    assert inbox._mail().get(invite["id"])["rsvp"] == "accepted"
    assert "Mayor's fundraiser" in engagements.diary_note(clock())
    declined = _invite(clock, days=3)
    row, note = inbox.rsvp(declined["id"], False)
    assert row["status"] == "declined" and "regrets" in note["body"]
    assert "fundraiser" in engagements.diary_note(clock()) and engagements.diary_note(clock()).count("fundraiser") == 1


def test_an_evening_over_is_went_or_missed_for_a_case(clock):
    invite = _invite(clock, days=0, hour=time.strftime("%H:%M", time.localtime(clock() - 5 * 3600)))
    row, _ = inbox.rsvp(invite["id"], True)
    report = {"id": "r-gala", "kind": "Armed robbery", "severity": 3, "place": "Ace Chemicals", "area": "Ironworks",
              "x": 2.4, "y": 85.1, "at": row["at"]}
    cases.assign(report, "bruce", by="him", travel=5)
    case = cases.for_report("r-gala")
    cases.mark(case["id"], members={"bruce": {"joined": row["at"] + 600, "status": "on scene"}})
    settled = engagements.settle(clock())
    assert settled[0]["status"] == "missed" and "Armed robbery" in settled[0]["kept_away_by"]
    waiting = inbox._take_event(clock())
    assert waiting["kind"] == "engagement" and waiting["status"] == "missed"


def test_the_morning_after_the_host_writes_and_vicki_wonders(clock, monkeypatch):
    asked = []
    monkeypatch.setattr(inbox, "_ask", lambda model, options, instruction, purpose:
                        asked.append(instruction) or {"subject": "Last night", "body": "We missed you."})
    monkeypatch.setattr(inbox, "_draw", lambda *parts: 0.0)
    invite = _invite(clock)
    inbox.rsvp(invite["id"], True)
    made = inbox._engagement_letter("m", {}, {"kind": "engagement", "id": "e1", "status": "missed",
                                              "what": "the Mayor's fundraiser", "where": "Gotham Opera House",
                                              "host": "Mayor Nakano", "thread": invite["thread"]}, clock())
    assert made is not None and "chair empty" in asked[0] and "Vicki Vale" in asked[1]
    froms = [m["from"] for m in inbox.mail("inbox")]
    assert "Mayor Nakano" in froms and "Vicki Vale" in froms


def test_alfred_knows_whats_in_the_diary(clock, monkeypatch):
    from wayne.contacts import directory
    from wayne.engine.session import ContactSession
    invite = _invite(clock)
    inbox.rsvp(invite["id"], True)
    alfred = ContactSession.__new__(ContactSession)
    alfred.contact = directory().get("alfred")
    alfred._said = ""
    assert "You keep his diary" in alfred._background() and "Gotham Opera House" in alfred._background()


def _at(clock, minutes):
    return clock() + minutes * 60


def test_being_there_is_being_there_on_the_map(clock, monkeypatch):
    from wayne.engine import batman
    row = engagements.agree("dinner with Vicki Vale", "Gotham Opera House", _at(clock, 30), "Vicki Vale", "t1")
    assert engagements.agree("dinner with Vicki Vale", "Gotham Opera House", _at(clock, 40), "Vicki Vale", "t1")["id"] == row["id"]
    venue = row["venue"]
    monkeypatch.setattr(batman, "position", lambda t=None: (1.0, 10.0))             # at the Manor
    clock.at(row["at"] + 600)
    engagements.watch(clock())
    assert not engagements._rows().get(row["id"]).get("present")
    monkeypatch.setattr(batman, "position", lambda t=None: (venue[0] + 0.5, venue[1]))  # there
    engagements.watch(clock())
    assert engagements._rows().get(row["id"])["present"]
    clock.at(row["until"] + 60)
    assert engagements.settle(clock())[0]["status"] == "attended"


def test_a_reminder_before_once_and_go_sets_him_off_as_the_right_man(clock, monkeypatch):
    from wayne.engine import batman
    gala = engagements.agree("the Mayor's gala", "Gotham Opera House", _at(clock, 30), "Mayor Nakano", "t2")
    roof = engagements.agree("meeting Jim on the GCPD roof", "GCPD Central", _at(clock, 35), "Jim Gordon", "t3",
                             as_="batman")
    assert {e["id"] for e in engagements.reminders(clock())} == {gala["id"], roof["id"]}
    assert engagements.reminders(clock()) == []                                    # once
    went = []
    monkeypatch.setattr(batman, "go", lambda where, x=None, y=None, case="", t=None, suit=None: went.append((where, suit)) or {})
    engagements.go(gala["id"])
    engagements.go(roof["id"])
    assert went == [("Gotham Opera House", False), ("GCPD Central", True)]          # Bruce Wayne; Batman


def test_standing_jim_up_costs_batman_and_keeping_it_earns(clock, monkeypatch):
    from wayne.engine import batman, bonds
    monkeypatch.setattr(batman, "position", lambda t=None: (1.0, 10.0))
    roof = engagements.agree("meeting Jim on the GCPD roof", "GCPD Central", _at(clock, 10), "Jim Gordon", "t4",
                             as_="batman")
    before = bonds.state("gordon", bonds.ME)["trust"]
    clock.at(roof["until"] + 60)
    settled = engagements.settle(clock())
    assert settled[0]["status"] == "missed"
    assert bonds.state("gordon", bonds.ME)["trust"] < before
    assert any("never showed" in m["text"] for m in bonds.memories("gordon", bonds.ME))


def test_a_meeting_agreed_in_a_thread_goes_in_the_diary_and_the_letter_says_so(clock, monkeypatch):
    day = (datetime.date.fromtimestamp(clock()) + datetime.timedelta(days=2)).isoformat()
    monkeypatch.setattr(inbox, "_ask", lambda model, options, instruction, purpose: {
        "meeting": {"what": "dinner with Vicki Vale", "where": "Gotham Opera House", "date": day, "time": "20:00"}})
    vicki = inbox.deliver("vicki", "Dinner?", "Thursday?", kind="letter", name="Vicki Vale")
    row = inbox._agreed("m", {}, vicki, "Bruce Wayne", False, clock())
    assert row["as"] == "bruce" and row["with"] == "Vicki Vale"
    marked = inbox._mail().get(vicki["id"])
    assert marked["rsvp"] == "accepted" and marked["engagement"]["id"] == row["id"]
    jim = inbox.deliver("gordon", "Roof", "Midnight. I'll be there.", kind="gordon")
    assert inbox._agreed("m", {}, jim, "Batman", True, clock())["as"] == "batman"


def test_after_an_evening_something_happened_and_the_mail_remembers_it(clock, monkeypatch):
    replies = iter([{"story": "Simon Saint cornered him about the drone contracts"},
                    {"subject": "Thank you", "body": "So glad you came."}])
    asked = []
    monkeypatch.setattr(inbox, "_ask", lambda model, options, instruction, purpose: asked.append(instruction) or next(replies))
    row = engagements.agree("the Mayor's gala", "Gotham Opera House", _at(clock, -200), "Mayor Nakano", "t5")
    engagements._rows().put(row["id"], {**row, "status": "attended", "until": _at(clock, -10)})
    inbox._engagement_letter("m", {}, {"kind": "engagement", "id": row["id"], "status": "attended",
                                       "what": row["what"], "where": row["where"], "host": "Mayor Nakano",
                                       "thread": "t5", "as": "bruce"}, clock())
    assert "drone contracts" in asked[1]                                           # the thank-you touches on it
    assert "drone contracts" in engagements.lately(clock()) and "drone contracts" in inbox._context(clock())
    assert "drone contracts" not in inbox._context(clock(), secure=True)          # nothing of Bruce on Jim's line
