"""Their day as it happened: the diary, what they know of who just left them, and going to join one another."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from wayne import paths
from wayne.engine import cases, diary, outcomes, presence


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "contact_dir", lambda cid: tmp_path / cid)
    presence._registry.clear()
    diary.forget()
    yield tmp_path
    presence._registry.clear()
    diary.forget()


def _person(cid, name, home="Wayne Manor"):
    return SimpleNamespace(id=cid, name=name, full_name=name, routine=(), shares_status=True, shares_location=True,
                           home=home, texting_pace={}, initiative={"takes_orders": 1.0}, beat=())


def test_the_diary_writes_what_changed_and_nothing_twice():
    from wayne.contacts import directory
    tim = presence.of(directory().get("robin"))
    tim.set_activity("on patrol", "busy", 120, where="Crime Alley")
    first = diary.observe(tim)
    assert first and "on patrol" in first[0]["text"]
    assert diary.observe(tim) == []                                     # nothing new: nothing written
    tim.set_activity("checking the docks", "busy", 60, where="Gotham Docks")
    assert any("checking the docks" in e["text"] for e in diary.observe(tim) + diary.entries("robin"))
    assert diary.observe(tim, time.time() - 3600) == []                 # a look back writes nothing


def test_tim_knows_dick_just_left_him_and_where_he_went(monkeypatch):
    """
    Out as a pair, Dick peeled off to a shots fired two streets over. Asked "is Dick
    with you?", Tim said "just me" and then made up the docks: nothing he'd been told
    said Dick had been there, or where he'd gone. He'd know — he was there.
    """
    from wayne.contacts import directory
    book = directory()
    tim, dick = presence.of(book.get("robin")), presence.of(book.get("nightwing"))
    out_since = time.time() - 3600              # out together for the last hour
    tim.set_activity("on patrol", "busy", 180, t=out_since, where="Crime Alley", company=["nightwing"])
    dick.set_activity("on patrol", "busy", 180, t=out_since, where="Crime Alley", company=["robin"])
    diary.observe(tim)
    assert any(e["kind"] == "with" and "Dick" in e["text"] for e in diary.entries("robin"))
    dick.set_activity("on the way to the shots fired at the Bowery", "busy", 60, where="The Bowery")
    diary.observe(tim)
    assert diary.parted("robin") and diary.parted("robin")[0][0] == "nightwing"
    told = tim.note()
    assert "Dick was with you till" in told and "shots fired" in told
    assert "as it happened" in told and "Dick went off" in told


def test_what_their_day_says_reads_as_a_sentence():
    assert presence._you_are("patrol") == "you're on patrol"
    assert presence._you_are("watching the docks") == "you're watching the docks"
    assert presence._you_are("Late night snacks and checking in with Babs").startswith("you've got late night snacks")
    assert presence._you_are("Nightwing patrol and keeping the peace") == "you've got Nightwing patrol and keeping the peace"
    assert presence.how_far(0.5) == "right by you" and "km" in presence.how_far(20)


def test_a_plan_made_at_midnight_leaves_the_evening_as_it_was():
    """Dick's day, sketched at 23:54, had him home with snacks from eleven — while he was on a roof with Tim."""
    night = SimpleNamespace(id="nightwing", name="Dick", full_name="Dick Grayson", shares_status=True,
                            shares_location=True, home="Home, Blüdhaven", texting_pace={},
                            routine=({"from": 21, "to": 3, "doing": "on patrol", "status": "online", "drift": 0},))
    state = presence.of(night)
    eleven = time.mktime(time.strptime("2026-10-10 23:10", "%Y-%m-%d %H:%M"))
    late = time.mktime(time.strptime("2026-10-10 23:54", "%Y-%m-%d %H:%M"))
    assert state.now(eleven)["doing"] == "on patrol"
    state.set_plan([{"from": 23.0, "to": 2.0, "doing": "late night snacks", "status": "online", "where": "Home, Blüdhaven"}],
                   t=late)
    assert state.now(eleven)["doing"] == "on patrol"                    # what happened, happened
    assert state.now(late + 60)["doing"] == "late night snacks"         # from when it was made, it holds


def test_whoever_he_names_comes_with_where_they_are_and_how_to_go_to_them(monkeypatch):
    from wayne.contacts import directory
    from wayne.engine.session import ContactSession
    book = directory()
    tim, dick = book.get("robin"), book.get("nightwing")
    presence.of(dick).set_activity("on patrol", "busy", 180, t=time.time() - 3600, where="Crime Alley")
    presence.of(tim).set_activity("on patrol", "busy", 180, t=time.time() - 3600, where="The Bowery")
    session = ContactSession(tim)
    for said in ("Go attach to Dick.", "help dick out", "Is Dick with you?"):
        told = session._named_family(said)
        assert "Dick, on the family app:" in told and "[join: Dick]" in told, said
    assert session._named_family("Go to bed.") == ""                    # nobody named, nothing added
    alfred = ContactSession(book.get("alfred"))
    told = alfred._named_family("Where's Dick?")
    assert "Dick, on the family app:" in told and "[join:" not in told  # Alfred sees; he doesn't go


def test_someone_in_the_middle_of_a_case_cant_walk_off_it_to_join_anyone(monkeypatch):
    from wayne.contacts import directory
    from wayne.engine.session import ContactSession
    book = directory()
    report = {"id": "t-scene", "kind": "Armed robbery", "severity": 3, "place": "Ralli's Family Restaurant",
              "area": "Otisburg", "x": 49.0, "y": 30.2, "at": time.time() - 600}
    case = cases.assign(report, "robin", by="him", travel=1)
    cases.arrive(case["id"], "robin") if hasattr(cases, "arrive") else cases.mark(
        case["id"], status="on scene", members={"robin": {**case["members"]["robin"], "status": "on scene"}})
    told = ContactSession(book.get("robin"))._named_family("go help Dick")
    assert "can't walk away" in told and "[join:" not in told


def _console(monkeypatch, book):
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console._adding, console._dropped_adds, console._closing, console._tasks = set(), set(), set(), set()

    class Directory:
        def get(self, cid):
            return book.get(cid)

        def find(self, name):
            said = name.strip().lower()
            return next((c for c in book.values() if said in (c.name.lower(), c.id)), None)

        def __iter__(self):
            return iter(book.values())
    console.directory = Directory()
    console.clients = set()

    async def broadcast(e):
        return None
    console.broadcast = broadcast
    console._presence_changed = lambda c: asyncio.sleep(0)
    console._members = lambda: []
    console._case_comms = lambda case: asyncio.sleep(0)
    return console


def test_joining_dick_on_a_case_puts_tim_on_it(monkeypatch):
    """"Go help Dick" while Dick's on a call: Tim's on the case with him — heading for the scene, counted when he's there."""
    from wayne.engine import incidents
    tim, dick = _person("robin", "Tim", "Houseboat, Gotham Marina"), _person("nightwing", "Dick", "Home, Blüdhaven")
    console = _console(monkeypatch, {"robin": tim, "nightwing": dick})
    report = {"id": "t-shots", "kind": "Shots fired", "severity": 3, "place": "Ralli's Family Restaurant",
              "area": "Otisburg", "x": 49.0, "y": 30.2, "at": time.time() - 120, "status": "units responding"}
    monkeypatch.setattr(incidents, "get", lambda rid: dict(report) if rid == "t-shots" else None)
    monkeypatch.setattr(cases, "FIELD", ("robin", "nightwing"))
    asyncio.run(console.assign_case("t-shots", "nightwing", by="self", tell=False))
    asyncio.run(console._join_by_name(tim, "Dick"))
    case = cases.for_report("t-shots")
    assert set(cases.team(case)) == {"nightwing", "robin"}
    assert "shots fired" in presence.of(tim).now()["doing"]
    assert any("joined Dick" in e["text"] for e in diary.entries("robin"))


def test_joining_dick_on_patrol_goes_to_him_and_stays_with_him(monkeypatch):
    tim, dick = _person("robin", "Tim", "Houseboat, Gotham Marina"), _person("nightwing", "Dick", "Home, Blüdhaven")
    console = _console(monkeypatch, {"robin": tim, "nightwing": dick})
    monkeypatch.setattr(cases, "FIELD", ("robin", "nightwing"))
    presence.of(dick).set_activity("on patrol", "busy", 180, t=time.time() - 3600, where="Crime Alley")
    done = asyncio.run(console._join_by_name(tim, "Dick"))
    assert done and done["joining"] == "Dick"
    activity = presence.of(tim).get("activity")
    assert activity["follow"] == "nightwing" and presence.of(tim).whereabouts()[0] == presence.of(dick).whereabouts()[0]
    later = presence.of(tim).get("trip")["end"] + 5
    assert "nightwing" in presence.of(tim).whereabouts(later)[1]          # there, and with him


def test_turning_up_late_is_never_worse_than_gcpd_alone():
    """Officer down, everyone half an hour out: sending someone read 2% while GCPD alone was 34%."""
    now = time.time()
    case = {"id": "t-late", "kind": "Officer down", "severity": 4, "suspect": "", "crew": 1, "began": now - 20 * 60,
            "opened_at": now, "team": ["robin"], "members": {"robin": {"joined": now, "travel": 33, "status": "assigned"}},
            "x": 0, "y": 0, "status": "assigned"}
    alone = outcomes.baseline(case)
    assert outcomes.too_late(case)
    assert outcomes.estimate(case, now, projected=True) == alone
    ending = outcomes.decide(case)
    theirs = outcomes.gcpd(case)
    assert ending["how"] == "too late" and ending["grade"] == theirs["grade"]
    for kind in ("Hostage situation", "Armed robbery", "Kidnapping"):
        trial = {**case, "kind": kind, "suspect": "The Joker", "id": f"t-{kind}"}
        assert outcomes.chance(trial, gone_wrong=True, late=True) >= outcomes.baseline(trial)


def test_nobody_walks_off_a_scene_theyre_in_the_middle_of(monkeypatch):
    from wayne.engine import incidents
    tim = _person("robin", "Tim", "Houseboat, Gotham Marina")
    console = _console(monkeypatch, {"robin": tim})
    monkeypatch.setattr(cases, "FIELD", ("robin",))
    first = {"id": "t-one", "kind": "Armed robbery", "severity": 3, "place": "Ralli's Family Restaurant",
             "area": "Otisburg", "x": 49.0, "y": 30.2, "at": time.time() - 120, "status": "units responding"}
    second = {**first, "id": "t-two", "kind": "Mugging", "place": "Crime Alley"}
    monkeypatch.setattr(incidents, "get", lambda rid: dict({"t-one": first, "t-two": second}.get(rid) or {}) or None)
    asyncio.run(console.assign_case("t-one", "robin", tell=False))
    case = cases.for_report("t-one")
    cases.mark(case["id"], status="on scene", members={"robin": {**case["members"]["robin"], "status": "on scene"}})
    assert asyncio.run(console.assign_case("t-two", "robin", tell=False)) is None
    assert cases.active("robin")["id"] == "t-one"
