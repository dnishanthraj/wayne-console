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
