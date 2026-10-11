"""
A case's crew, its channel and its rides kept in step: the lead gone, the next leads on their own terms; off a case,
off its comms; taken off comms by him, kept off; and a lift he goes back on leaves nobody flying alone.
"""
import asyncio

from wayne.contacts import directory
from wayne.engine import cases, jet, presence
from wayne.memory import groups as group_store

REPORT = {"id": "r-drift", "kind": "Armed robbery", "severity": 3, "place": "Ralli's Family Restaurant",
          "area": "Otisburg", "x": 49.0, "y": 30.2}


def _console():
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    console._group_readers, console._group_writing, console._tasks = {}, set(), set()
    console.call = None
    sent = []

    async def broadcast(event):
        sent.append(event)

    async def presence_changed(contact):
        return None
    console.broadcast, console._presence_changed = broadcast, presence_changed
    console._members = lambda: []
    console.group_payload = lambda group: {"id": group.id}
    console._at_their_screens = lambda cid: False
    return console, sent


def test_the_lead_gone_the_next_leads_on_their_own_terms(clock):
    cases.assign(REPORT, "robin", by="him", travel=6)
    cases.assign(REPORT, "redhood", by="self", travel=11)
    assert cases.known(cases.for_report(REPORT["id"]))
    cases.leave(REPORT["id"], "robin")
    case = cases.for_report(REPORT["id"])
    assert case["assignee"] == "redhood" and case["by"] == "self" and case["travel"] == 11
    assert not cases.known(case)                         # Jason working it alone is Jason's business again


def test_a_closed_case_keeps_the_record_of_who_worked_it(clock):
    cases.assign(REPORT, "robin", by="him", travel=6)
    cases.close(REPORT["id"], "Handled.")
    cases.leave(REPORT["id"], "robin")
    assert cases.team(cases.for_report(REPORT["id"])) == ["robin"]


def test_off_a_case_off_its_comms_and_taken_off_by_him_kept_off(clock, monkeypatch):
    import wayne.frontends.web as web
    console, sent = _console()
    monkeypatch.setattr(web.random, "random", lambda: 0.99)                      # nobody says anything about it
    cases.assign(REPORT, "robin", by="him", travel=6)
    cases.assign(REPORT, "nightwing", by="him", travel=8)
    asyncio.run(console._case_comms(cases.for_report(REPORT["id"])))
    comms = next(g for g in group_store.all_groups() if (g.meta() or {}).get("case") == REPORT["id"])
    assert set(comms.members) == {"robin", "nightwing"}
    cases.assign(REPORT, "orphan", by="him", travel=9)
    asyncio.run(console._case_comms(cases.for_report(REPORT["id"])))
    assert "orphan" in group_store.of(comms.id).members
    asyncio.run(console.unassign_case(REPORT["id"], "orphan"))
    assert "orphan" not in group_store.of(comms.id).members                      # pulled off: off the channel
    assert any(e.get("message", {}).get("text", "").endswith("went off comms") for e in sent if e["type"] == "group_message")
    group_store.of(comms.id).remove_member("nightwing", by_bruce=True)
    asyncio.run(console._case_comms(cases.for_report(REPORT["id"])))
    assert "nightwing" not in group_store.of(comms.id).members                   # his call: not put straight back
    group_store.of(comms.id).add_member("nightwing")
    assert "nightwing" not in (group_store.of(comms.id).meta().get("removed") or {})   # he brought them back


def test_a_lift_he_goes_back_on_leaves_nobody_flying_alone(clock, monkeypatch):
    import wayne.frontends.web as web
    console, _ = _console()
    t = clock.at(clock() + 11 * 3600)                    # eleven at night: the Batwing's out
    home = jet.home()
    me, tim_at, drop = (home[0] + 10, home[1] + 10), (home[0] + 14, home[1] + 16), (40.0, 90.0)
    his, theirs = jet.lift("robin", me, tim_at, drop, t)
    tim = presence.of(directory().get("robin"))
    tim.set_activity("getting a lift from him in the Batwing, to Crime Alley", presence.BUSY, 60, where="Crime Alley",
                     xy=drop, owner="lift")
    tim.set_trip("Crime Alley", {**theirs, "left": t})
    assert jet.riding("robin", t)                         # waiting on the pickup still to come
    asyncio.run(console._lifts_tick(t))
    assert tim.get("trip")["by"].startswith("on the Batwing")                    # still his ride: left alone
    clock.at(theirs["start"] + 30)
    monkeypatch.setattr(web.random, "random", lambda: 0.99)                      # nothing said about it, this time
    jet.let_go(clock())                                   # he changed course mid-flight, Tim aboard
    assert not jet.riding("robin", clock())
    asyncio.run(console._lifts_tick(clock()))
    onward = tim.get("trip")
    assert not onward["by"].startswith("on the Batwing") and onward["pts"][-1] == list(drop)
    assert (tim.get("activity") or {}).get("owner") != "lift"
