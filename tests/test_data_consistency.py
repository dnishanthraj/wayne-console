"""
One truth, kept in step: a plan moved in the diary moves for the family; an evening removed is called off first;
his own reminders stay his; his day runs with nobody at the console; and a reset leaves nothing behind it shouldn't.
"""
import asyncio

from wayne.contacts import directory
from wayne.engine import engagements, plans


def _console():
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    said = []

    async def broadcast(event):
        return None

    async def text(cid, body, reply_to=None):
        said.append((cid, body))

    async def group_text(gid, body, reply_to=None):
        said.append((f"g:{gid}", body))
    console.broadcast, console.text, console.group_text = broadcast, text, group_text
    console._members = lambda: []
    return console, said


def test_a_plan_moved_in_the_diary_moves_for_the_family(clock):
    plan = plans.add({"what": "dinner at Ralli's", "where": "Ralli's Family Restaurant", "at": clock() + 3600,
                      "until": clock() + 7200, "who": ["robin", "nightwing"]}, made_in="g:fam")
    row = next(e for e in engagements.all_engagements() if e.get("plan") == plan["id"])
    engagements.edit(row["id"], at=clock() + 2 * 3600)
    moved = next(p for p in plans.all_plans() if p["id"] == plan["id"])
    assert moved["at"] == clock() + 2 * 3600 and moved["until"] == clock() + 2 * 3600 + 3600
    console, said = _console()
    asyncio.run(console.plan_moved(engagements._rows().get(row["id"])))
    assert said and said[0][0] == "g:fam" and "Change of plan" in said[0][1]


def test_the_same_plan_asked_again_keeps_the_diary_row_in_step(clock):
    first = plans.add({"what": "dinner", "where": "Ralli's Family Restaurant", "at": clock() + 3600,
                       "until": clock() + 7200, "who": ["robin"]})
    plans.add({"what": "dinner", "where": "Ralli's Family Restaurant", "at": clock() + 3600 + 600,
               "until": clock() + 7200, "who": ["nightwing"]})
    row = next(e for e in engagements.all_engagements() if e.get("plan") == first["id"])
    assert "nightwing" in row["with"] and "robin" in row["with"]


def test_who_an_entry_is_with_can_be_changed(clock):
    row = engagements.add("lunch", "The Regency", clock() + 3600)
    assert engagements.edit(row["id"], with_="Clark Kent")["with"] == "Clark Kent"


def test_his_reminders_are_his_and_not_the_familys_news(clock):
    engagements.remind("call Lucius", clock() + 1800, by="alfred", now=clock())
    assert "call Lucius" not in engagements.known_to("nightwing", clock())


def test_removing_an_evening_someone_expects_calls_it_off_first(clock, monkeypatch):
    import wayne.frontends.web as web
    plan = plans.add({"what": "movie night", "where": "Wayne Manor", "at": clock() + 3600, "until": clock() + 7200,
                      "who": ["robin", "nightwing"]}, made_in="")
    for cid in ("robin", "nightwing"):
        plans.answer(plan["id"], cid, "yes")
    row = next(e for e in engagements.all_engagements() if e.get("plan") == plan["id"])
    called_off = []

    async def cancel(eid, why=""):
        called_off.append(eid)
    monkeypatch.setattr(web.console, "cancel_engagement", cancel)
    asyncio.run(web.diary_delete(row["id"]))
    assert called_off == [row["id"]] and engagements._rows().get(row["id"]) is None


def test_his_day_runs_with_nobody_at_the_console(clock, monkeypatch):
    import wayne.frontends.web as web
    from wayne.engine import batman
    console, _ = _console()
    row = engagements.add("the Mayor's gala", "Gotham Opera House", clock() - 600, clock() + 3 * 3600)
    venue = row["venue"]
    monkeypatch.setattr(batman, "position", lambda t=None: (venue[0], venue[1]))
    monkeypatch.setattr(batman, "state", lambda t=None: {"where": "Gotham Opera House"})
    monkeypatch.setattr(batman, "committed", lambda t=None: "")
    asyncio.run(web.Console._diary_tick(console, clock(), quiet=True))
    assert engagements._rows().get(row["id"])["present"]                       # he was there: never "missed"


def test_a_reset_leaves_nothing_behind_it_should_not(clock):
    from wayne import store
    from wayne.store import reset
    store.save_doc("repute", {"score": 20, "said": ["he stood up the Mayor"]})
    store.space("event_crimes").put("evt-x", {"id": "evt-x"})
    plan = plans.add({"what": "dinner", "where": "Wayne Manor", "at": clock() + 3600, "until": clock() + 7200,
                      "who": ["robin"]})
    mine = engagements.add("a fitting", "Wayne Tower", clock() + 7200)
    reset.run(chats=False, world=True)
    assert store.doc("repute", None) is None and not store.space("event_crimes").list()
    assert not plans.all_plans()                                                # gone with the world, too
    plan = plans.add({"what": "dinner", "where": "Wayne Manor", "at": clock() + 3600, "until": clock() + 7200,
                      "who": ["robin"]})
    mine = engagements.add("a fitting", "Wayne Tower", clock() + 7200)
    reset.run(chats=True, world=False)
    left = engagements.all_engagements()
    assert [e["id"] for e in left] == [mine["id"]]                             # what a conversation made, gone
    assert not any(e.get("plan") == plan["id"] for e in left)
