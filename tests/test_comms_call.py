"""A case's comms as a live call: whoever's on it, no ring; the case down the line as it moves; one story with the channel."""
import asyncio
import time
from types import SimpleNamespace

from wayne.contacts import directory
from wayne.engine import cases, party
from wayne.memory import groups as group_store


def _case(team, place="Waterloo Docks", area="Blüdhaven", kind="Child abduction"):
    report = {"id": f"r-{place[:4]}-{len(team)}", "kind": kind, "severity": 3, "place": place, "area": area,
              "x": 117.1, "y": 0.7, "at": time.time() - 300}
    for member in team:
        cases.assign(report, member, by="him", travel=5)
    return cases.for_report(report["id"])


def _console(monkeypatch):
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    console.sessions, console._tasks, console.clients = {}, set(), set()
    console.turn_lock, console.turn_epoch = asyncio.Lock(), 0
    console.current_id, console.call, console._incoming = None, None, None
    console._group_ring = None
    sent, said = [], []

    async def broadcast(event):
        sent.append(event)
    console.broadcast = broadcast

    def session_for(cid):
        if cid not in console.sessions:
            console.sessions[cid] = SimpleNamespace(contact=directory().get(cid), heard=[], call=None,
                                                    _hanging_up=False, mark_call_start=lambda: None,
                                                    call_messages=lambda: [], keep_heard=lambda: None)
        return console.sessions[cid]
    console.session_for = session_for

    async def say_on_call(cid, instruction):
        said.append((cid, instruction))
    console._say_on_call = say_on_call
    return console, sent, said


def test_on_comms_is_whoever_is_on_the_case_and_the_screens_if_theyre_at_them(monkeypatch):
    console, _, _ = _console(monkeypatch)
    case = _case(["nightwing"])
    monkeypatch.setattr(console, "_at_their_screens", lambda cid: cid == "alfred")
    assert console._live_on_comms("nightwing", case)
    assert console._live_on_comms("alfred", case)
    assert not console._live_on_comms("batgirl", case)
    assert not console._live_on_comms("robin", case)                    # not on it: he'd ask them in


def test_joining_comms_is_no_ring_and_the_one_there_says_where_its_at(monkeypatch):
    console, sent, said = _console(monkeypatch)
    case = _case(["nightwing"])
    group = group_store.create("Comms · Child abduction · Waterloo Docks", ["nightwing", "alfred", "batgirl"])
    group.update_meta(lambda m: m.update({"case": case["id"], "comms": True}))
    monkeypatch.setattr(console, "_at_their_screens", lambda cid: cid == "alfred")
    asyncio.run(console.join_comms(group.id))
    kinds = [e["type"] for e in sent]
    assert kinds[0] == "comms_joined" and sent[0]["members"] == ["nightwing", "alfred"]   # Barbara's not at hers
    assert "ringing" not in " ".join(kinds) and kinds.count("picked_up") == 2
    assert console.call.comms == {"group": group.id, "case": case["id"]} and console.current_id == "nightwing"
    assert said and said[0][0] == "nightwing" and "where it's at" in said[0][1]
    assert any(m.get("text") == "You came onto comms, live" for m in group.messages())


def test_nobody_on_it_is_no_call_and_says_so(monkeypatch):
    console, sent, said = _console(monkeypatch)
    case = _case(["nightwing"])
    group = group_store.create("Comms · x", ["batgirl", "alfred"])
    group.update_meta(lambda m: m.update({"case": case["id"], "comms": True}))
    monkeypatch.setattr(console, "_at_their_screens", lambda cid: False)
    asyncio.run(console.join_comms(group.id))
    assert sent[0] == {"type": "comms_joined", "group": group.id, "members": []} and console.call is None
    assert any(e["type"] == "notice" for e in sent) and not said


def _live(console, case, group, members):
    console.call = party.Call()
    console.call.comms = {"group": group.id, "case": case["id"]}
    for cid in members:
        console.call.join(console.session_for(cid))
    console.current_id = members[0]
    console._members = lambda: [m.contact.id for m in console.call.members]


def test_the_case_comes_down_the_line_as_it_moves_and_ends_said_there(monkeypatch):
    console, sent, said = _console(monkeypatch)
    case = _case(["nightwing"])
    group = group_store.create("Comms · y", ["nightwing", "alfred"])
    group.update_meta(lambda m: m.update({"case": case["id"], "comms": True}))
    _live(console, case, group, ["nightwing", "alfred"])
    console._him_at = 0
    now = time.time()
    cases.mark(case["id"], status="on scene")
    asyncio.run(console._comms_live(group, cases.for_report(case["id"]), now, group.meta()))
    assert said and said[-1][0] in ("nightwing", "alfred")
    n = len(said)
    asyncio.run(console._comms_live(group, cases.for_report(case["id"]), now + 5, group.meta()))
    assert len(said) == n                                               # nothing new, and not due: quiet
    console._him_at = time.time()
    cases.mark(case["id"], status="closed", outcome="the girl's safe; two in cuffs")
    asyncio.run(console._comms_live(group, cases.for_report(case["id"]), now + 10, group.meta()))
    assert "It's over: the girl's safe" in said[-1][1] and "[hang up]" in said[-1][1]
    asyncio.run(console._comms_live(group, cases.for_report(case["id"]), now + 20, group.meta()))
    assert sum("It's over" in s[1] for s in said) == 1                  # signed off once


def test_on_live_comms_what_theyd_text_about_the_case_is_said_on_the_call(monkeypatch):
    console, sent, said = _console(monkeypatch)
    case = _case(["nightwing"])
    group = group_store.create("Comms · z", ["nightwing"])
    group.update_meta(lambda m: m.update({"case": case["id"], "comms": True}))
    _live(console, case, group, ["nightwing"])
    asyncio.run(console._send_unprompted(directory().get("nightwing"), "the abduction at Waterloo Docks", "case_trouble"))
    assert said and said[0][0] == "nightwing" and "gone bad" in said[0][1]


def test_whats_said_on_the_call_is_in_the_channel_as_said(monkeypatch):
    console, sent, said = _console(monkeypatch)
    case = _case(["nightwing"])
    group = group_store.create("Comms · w", ["nightwing"])
    group.update_meta(lambda m: m.update({"case": case["id"], "comms": True}))
    _live(console, case, group, ["nightwing"])

    async def record():
        console._comms_record("me", "Dick, status?")
        console._comms_record("nightwing", "Van's moving. Following.")
        await asyncio.sleep(0)
    asyncio.run(record())
    lines = [(m["from"], m["text"], m.get("origin")) for m in group.messages() if m.get("kind") != "system"]
    assert lines == [("me", "Dick, status?", "call"), ("nightwing", "Van's moving. Following.", "call")]
    assert group.read_upto("nightwing") >= group.messages()[-1]["at"]      # heard it: not unread


def test_comms_pauses_are_for_the_op_and_everyone_knows_the_op(monkeypatch):
    from wayne.engine.session import ContactSession
    case = _case(["nightwing"])
    call = party.Call()
    call.comms = {"group": "g", "case": case["id"]}
    dick = ContactSession.__new__(ContactSession)
    dick.contact, dick.heard, dick.call, dick._hanging_up = directory().get("nightwing"), [], None, False
    dick.mark_call_start, dick.keep_heard, dick.call_messages = (lambda: None), (lambda: None), (lambda: [])
    alfred = ContactSession.__new__(ContactSession)
    alfred.contact, alfred.heard, alfred.call, alfred._hanging_up = directory().get("alfred"), [], None, False
    alfred.mark_call_start, alfred.keep_heard, alfred.call_messages = (lambda: None), (lambda: None), (lambda: [])
    call.join(dick)
    call.join(alfred)
    assert "live comms call" in call.note_for(alfred) and "in the cave" in call.note_for(alfred)
    assert "If nothing's changed, reply with exactly SKIP" in call._lull_instruction(dick, 1)
