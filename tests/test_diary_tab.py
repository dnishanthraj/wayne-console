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
