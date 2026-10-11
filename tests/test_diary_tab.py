"""His diary as he manages it: the days, what's on each, writing in, changing, calling off — and who's told."""
import asyncio
import datetime

from wayne.engine import engagements, inbox, plans


def test_the_days_show_whats_on_who_is_coming_and_where_it_came_from(clock):
    today = datetime.date.fromtimestamp(clock())
    plan = plans.add({"what": "dinner at Antonio's", "where": "Antonio's", "at": clock() + 3600,
                      "until": clock() + 3 * 3600, "who": ["nightwing", "robin"]}, made_in="g:fam1")
    plans.answer(plan["id"], "nightwing", "yes")
    plans.answer(plan["id"], "robin", "no")
    mine = engagements.add("a fitting at Hart & Sons", "Fashion District", clock() + 2 * 3600)
    days = engagements.days(today, 2, clock())
    entries = {e["what"]: e for e in days[0]["entries"]}
    assert set(entries) == {"dinner at Antonio's", "a fitting at Hart & Sons"}
    dinner = entries["dinner at Antonio's"]
    assert [(p["name"], p["said"]) for p in dinner["who"]] == [("Dick", "yes"), ("Tim", "no")]
    assert dinner["group"] == "fam1" and dinner["origin"] == "a plan with the family" and dinner["state"] == "upcoming"
    assert entries["a fitting at Hart & Sons"]["origin"] == "you" and mine["source"] == "manual"


def test_he_changes_what_is_still_to_come_and_not_what_is_over(clock):
    row = engagements.add("lunch with Lucius", "Wayne Tower", clock() + 3600)
    moved = engagements.edit(row["id"], at=clock() + 7200, where="The Regency")
    assert moved["at"] == clock() + 7200 and moved["until"] - moved["at"] == row["until"] - row["at"]
    assert moved["where"] == "The Regency"
    engagements._rows().put(row["id"], {**moved, "status": "attended"})
    assert engagements.edit(row["id"], what="anything") is None


def test_calling_off_a_letters_meeting_is_alfreds_apology_in_the_thread(clock, monkeypatch):
    vicki = inbox.deliver("vicki", "Dinner?", "Thursday at eight?", kind="letter", name="Vicki Vale")
    row = engagements.agree("dinner with Vicki Vale", "Le Bistro de la Rue", clock() + 86400, "Vicki Vale",
                            vicki["thread"], message=vicki["id"])
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)

    async def broadcast(event):
        return None
    console.broadcast = broadcast
    done = asyncio.run(console.cancel_engagement(row["id"]))
    assert done["status"] == "cancelled"
    note = inbox.thread(vicki["thread"])[-1]
    assert note["folder"] == "sent" and "sincere apologies" in note["body"] and "Le Bistro" in note["body"]


def test_calling_off_a_family_plan_is_said_where_it_was_made_and_stings_a_little(clock, monkeypatch):
    from wayne.engine import bonds
    plan = plans.add({"what": "dinner at Antonio's", "where": "Antonio's", "at": clock() + 3600,
                      "until": clock() + 3 * 3600, "who": ["nightwing"]}, made_in="g:fam2")
    plans.answer(plan["id"], "nightwing", "yes")
    row = next(e for e in engagements.all_engagements() if e.get("plan") == plan["id"])
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = __import__("wayne.contacts", fromlist=["directory"]).directory()
    said = []

    async def group_text(gid, line, reply_to=None):
        said.append((gid, line))
    console.group_text = group_text
    asyncio.run(console.cancel_engagement(row["id"]))
    assert said == [("fam2", "Something's come up — I can't make dinner at Antonio's. Sorry.")]
    assert "called off dinner at Antonio's" in bonds.memories("nightwing", bonds.ME, clock())[0]["text"]


def test_his_day_is_sketched_around_whats_there_in_real_hours(clock, monkeypatch):
    import json

    from wayne.engine import model
    today = datetime.date.fromtimestamp(clock())
    engagements.add("lunch with Clark", "The Regency", time_at(today, 12, 30), time_at(today, 14, 0))
    sketch = {"day": [{"what": "Board briefing with Lucius", "where": "Wayne Tower", "from": "09:30", "to": "11:00",
                       "with": "Lucius and the board"},
                      {"what": "a clash", "where": "Wayne Tower", "from": "13:00", "to": "13:30"},     # over lunch: no
                      {"what": "a 3am thing", "where": "Wayne Tower", "from": "03:00", "to": "04:00"},  # not his hours
                      {"what": "Foundation visit", "where": "Gotham Public Library", "from": "15:00", "to": "16:30"}]}
    monkeypatch.setattr(model, "ask", lambda *a, **k: json.dumps(sketch))
    made = engagements.write_day("m", {}, today, clock())
    assert [m["what"] for m in made] == ["Board briefing with Lucius", "Foundation visit"]
    assert all(m["source"] == "routine" for m in made) and engagements.has_day(today)


def time_at(day, hh, mm):
    import time as _t
    return _t.mktime((day.year, day.month, day.day, hh, mm, 0, 0, 0, -1))


def test_the_map_follows_the_diary_once_and_leaving_early_is_noted(clock, monkeypatch):
    from wayne.engine import batman, cases
    row = engagements.add("the Mayor's gala", "Gotham Opera House", clock() + 15 * 60, clock() + 3 * 3600)
    assert [e["id"] for e in engagements.due_to_go(clock())] == [row["id"]]
    assert engagements.due_to_go(clock()) == []                                   # once
    venue = row["venue"]
    monkeypatch.setattr(batman, "position", lambda t=None: (venue[0], venue[1]))
    clock.at(row["at"] + 600)
    engagements.watch(clock())
    assert engagements._rows().get(row["id"])["present"]
    monkeypatch.setattr(batman, "position", lambda t=None: (venue[0] + 30, venue[1]))   # gone, early
    monkeypatch.setattr(cases, "active", lambda who: {"kind": "Armed robbery", "place": "Ace Chemicals",
                                                     "status": "on scene", "members": {}})
    clock.at(row["at"] + 1800)
    engagements.watch(clock())
    assert engagements._rows().get(row["id"])["left_for"] == "Armed robbery at Ace Chemicals"
    clock.at(row["until"] + 60)
    assert engagements.settle(clock())[0]["status"] == "left"


def test_the_console_takes_him_there_unless_hes_in_the_middle_of_something(clock, monkeypatch):
    import wayne.frontends.web as web
    from wayne.engine import batman
    console = web.Console.__new__(web.Console)
    console.directory = __import__("wayne.contacts", fromlist=["directory"]).directory()
    console._members = lambda: []
    console._spawn = lambda coroutine: coroutine.close()        # Alfred's reminder text: not this test's business
    went = []

    async def broadcast(event):
        return None
    console.broadcast = broadcast
    monkeypatch.setattr(engagements, "go", lambda eid, now=None: went.append(eid) or {})
    monkeypatch.setattr(batman, "state", lambda t=None: {"where": "Wayne Manor"})
    monkeypatch.setattr(batman, "committed", lambda t=None: "")
    row = engagements.add("lunch with Clark", "The Regency", clock() + 10 * 60, clock() + 3600)
    asyncio.run(console._diary_tick(clock()))
    assert went == [row["id"]]
    monkeypatch.setattr(batman, "committed", lambda t=None: "in the middle of a case")
    engagements.add("a fitting", "Hart & Sons Tailors", clock() + 12 * 60, clock() + 3600)
    asyncio.run(console._diary_tick(clock()))
    assert went == [row["id"]]                                                      # on a case: it waits for him


def test_who_knows_what_of_his_diary(clock):
    engagements.add("dinner with Vicki Vale", "Le Bistro de la Rue", clock() + 86400)
    gala = engagements.agree("the Mayor's gala", "Gotham Opera House", clock() + 2 * 86400, "Mayor Nakano", "t-g")
    engagements._rows().put(gala["id"], {**gala, "source": "invitation"})
    engagements.add("Board briefing", "Wayne Tower", clock() + 3600, source="routine")
    dick, selina, lucius = (engagements.known_to(c, clock()) for c in ("nightwing", "catwoman", "lucius"))
    assert "Vicki Vale" in dick and "Board briefing" not in dick
    assert "Mayor's gala" in selina and "Vicki" not in selina                  # only what's public
    assert "Board briefing" in lucius
    assert engagements.known_to("gordon", clock()) == "" and engagements.known_to("alfred", clock()) == ""


def test_their_evenings_at_his_are_in_his_diary_and_not_his_to_change(clock, monkeypatch):
    from types import SimpleNamespace

    from wayne.engine import week
    dick = SimpleNamespace(id="nightwing", name="Dick")
    tim = SimpleNamespace(id="robin", name="Tim")
    habit = {"doing": "family dinner at the Manor", "where": "Wayne Manor", "from": "18:00", "to": "20:00",
             "together": "manor-dinner"}
    monkeypatch.setattr(week, "on", lambda contact, t=None: [habit] if contact.id in ("nightwing", "robin") else [])
    import wayne.contacts as contacts
    monkeypatch.setattr(contacts, "directory", lambda: [dick, tim])
    day = datetime.date.fromtimestamp(clock())
    rows = engagements.at_his(day, clock())
    assert len(rows) == 1 and rows[0]["readonly"] and {p["name"] for p in rows[0]["who"]} == {"Dick", "Tim"}
    assert rows[0]["what"] == "Family dinner at the Manor"


def test_trouble_comes_to_an_evening_rarely_and_lands_on_the_scanner(clock):
    from wayne.engine import incidents

    class Sure:
        def random(self):
            return 0.0

        def choices(self, seq, weights=None):
            return [seq[0]]

        def choice(self, seq):
            return seq[0]
    row = engagements.add("the Mayor's gala", "Gotham Opera House", clock() - 600, clock() + 3 * 3600)
    row = {**engagements._rows().get(row["id"]), "present": True}
    engagements._rows().put(row["id"], row)
    report = engagements.crime_at(row, clock(), rng=Sure())
    assert report["kind"] == "Armed robbery" and report["place"] == "Gotham Opera House"
    assert engagements.crime_at(engagements._rows().get(row["id"]), clock(), rng=Sure()) is None   # rolled once
    assert any(r["id"] == report["id"] for r in incidents.at(clock() + 60))


def test_hosting_asks_them_in_a_chat_of_its_own_and_its_in_the_diary(clock, monkeypatch):
    import wayne.frontends.web as web
    from wayne.contacts import directory
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    said = []

    async def broadcast(event):
        return None

    async def group_text(gid, line, reply_to=None):
        said.append((gid, line))
    console.broadcast, console.group_text = broadcast, group_text
    console.group_payload = lambda g: {}
    row = asyncio.run(console.host("A party at the Manor", "Wayne Manor", clock() + 86400, None,
                                   ["nightwing", "batgirl"]))
    assert row["source"] == "plan" and row["what"] == "A party at the Manor"
    assert said and "I'm hosting. You're all invited." in said[0][1]
    assert [p["name"] for p in engagements.entry(row)["who"]] == ["Dick", "Barbara"]
