"""
What they do on the line, not just say: show him a place, a file, his day, the page, the share price; set him a
reminder that comes up at its moment; write in his diary — only what's theirs to do, and never at a reply's cost.
"""
import asyncio
import datetime
import time
from types import SimpleNamespace

from wayne.contacts import directory
from wayne.engine import engagements, show
from wayne.engine.session import ContactSession
from wayne.memory.texts import TextLog

WED_5PM = time.mktime((2026, 10, 14, 17, 0, 0, 0, 0, -1))


def _at(t):
    return datetime.datetime.fromtimestamp(t).strftime("%a %H:%M") if t else None


def test_a_moment_is_read_the_way_people_write_it():
    for said, meant in (("2026-10-14 18:00", "Wed 18:00"), ("18:00", "Wed 18:00"), ("6pm", "Wed 18:00"),
                        ("in 20 minutes", "Wed 17:20"), ("in an hour", "Wed 18:00"), ("tomorrow at 9", "Thu 09:00"),
                        ("friday 8pm", "Fri 20:00"), ("friday 8", "Fri 20:00"), ("tonight", "Wed 20:00"),
                        ("at 6", "Wed 18:00"), ("tomorrow morning", "Thu 09:00"), ("sunday", "Sun 09:00")):
        assert _at(show.when(said, WED_5PM)) == meant, said
    assert show.when("whenever", WED_5PM) is None
    assert show.when("2026-10-13 09:00", WED_5PM) is None                       # never in the past


def test_what_they_show_is_the_thing_itself(clock):
    alfred = directory().get("alfred")
    assert show.card("Ace Chemicals", alfred)["kind"] == "place"
    two_face = show.card("Two-Face", alfred)
    assert two_face["kind"] == "rogue" and two_face["alias"] == "Harvey Dent"
    assert show.card("Vicki Vale", alfred)["kind"] == "person"
    market = show.card("the share price", alfred)
    assert market["kind"] == "market" and len(market["series"]) == 30
    engagements.add("lunch with Clark", "The Regency", clock() + 86400)
    day = show.card("diary: tomorrow", alfred)
    assert day["kind"] == "diary" and day["entries"][0]["what"] == "lunch with Clark"
    found = [{"title": "Gotham Gazette — Narrows audit", "url": "https://gazette.example/audit", "snippet": "…"}]
    assert show.card("that page", alfred, sources=found)["url"] == "https://gazette.example/audit"
    assert show.card("nothing anyone could show", alfred) is None


def test_someone_outside_the_secret_never_shows_a_masks_name():
    lucius_view = show.card("Dick Grayson", directory().get("gordon"))
    assert lucius_view["kind"] == "person" and lucius_view["alias"] == ""        # Jim doesn't know Dick is Nightwing


def test_only_what_is_theirs_to_do_is_read_off_a_reply():
    alfred, jason = ContactSession(directory().get("alfred")), ContactSession(directory().get("redhood"))
    for session in (alfred, jason):
        session._shows, session._reminds, session._diaries = [], [], []
    said = alfred._take_tools("Done, sir. [remind: in 20 minutes | call Lucius] [show: diary: today]")
    assert said.strip() == "Done, sir." and alfred._reminds == [("in 20 minutes", "call Lucius")]
    assert alfred._shows == ["diary: today"]
    jason._take_tools("sure [remind: 6pm | buy milk]")
    assert jason._reminds == []                                                  # Jason doesn't keep his diary


def _console():
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    events = []

    async def broadcast(event):
        events.append(event)
    console.broadcast = broadcast
    return console, events


def test_a_reminder_goes_in_the_diary_and_comes_up_at_its_moment(clock):
    console, events = _console()
    alfred = ContactSession(directory().get("alfred"))
    alfred._reminds, alfred._shows, alfred._diaries = [("in 20 minutes", "call Lucius")], [], []
    asyncio.run(console._use_tools(alfred, "call"))
    row = next(e for e in engagements.all_engagements() if e.get("reminder"))
    assert row["what"] == "call Lucius" and row["by"] == "alfred" and abs(row["at"] - (clock() + 1200)) < 2
    shown = [e for e in events if e["type"] == "shown"]
    assert shown and shown[0]["card"]["kind"] == "reminder" and any(e["type"] == "diary" for e in events)
    assert engagements.reminders(clock() + 600) == []                            # not forty minutes early
    clock.at(row["at"] + 30)
    assert [e["id"] for e in engagements.reminders(clock())] == [row["id"]]      # at its moment
    clock.at(row["until"] + 60)
    engagements.settle(clock())
    assert engagements._rows().get(row["id"])["status"] == "done"               # over, never "missed"
    assert engagements.entry(engagements._rows().get(row["id"]), clock())["origin"] == "a reminder from Alfred"


def test_a_card_in_a_text_is_a_message_of_its_own(clock):
    console, events = _console()
    tim = ContactSession(directory().get("robin"))
    tim._shows, tim._reminds, tim._diaries = ["Ace Chemicals"], [], []
    asyncio.run(console._use_tools(tim, "text"))
    sent = TextLog("robin").last()
    assert sent["card"]["kind"] == "place" and sent["card"]["name"] == "Ace Chemicals" and sent["text"] == ""
    assert events[-1]["type"] == "text_reply"


def test_a_tool_that_fails_never_costs_the_reply():
    console, _ = _console()
    asyncio.run(console._use_tools(SimpleNamespace(), "text"))                  # no contact: nothing, quietly
    broken = ContactSession(directory().get("alfred"))
    broken._reminds, broken._shows, broken._diaries = [("tomorrow", None)], [], []
    asyncio.run(console._use_tools(broken, "call"))                              # bad data: logged, not raised
