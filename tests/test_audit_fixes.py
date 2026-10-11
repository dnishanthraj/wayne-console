"""What the October audit found, pinned: each test is a failure that happened, and mustn't again."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from wayne import delivery, paths
from wayne.engine import cases, diary, guards, outcomes, presence


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "contact_dir", lambda cid: tmp_path / cid)
    presence._registry.clear()
    diary.forget()
    yield tmp_path
    presence._registry.clear()
    diary.forget()


@pytest.mark.parametrize("line", ["I see you've been busy.", "It was a good night for us.",
                                  "I'm heading off to the docks, keep comms open.", "See you at the warehouse in ten.",
                                  "I'm tired. Talk to me...", "Good. Now find out who hired him."])
def test_an_ordinary_sentence_doesnt_hang_up_the_call(line):
    assert not guards.user_is_leaving(line)


@pytest.mark.parametrize("line", ["Thanks Alfred, goodnight.", "Okay, bye!", "Night, B.", "I'm tired. Night.",
                                  "Alright, I gotta go."])
def test_a_goodbye_at_the_end_still_does(line):
    assert guards.user_is_leaving(line)


def test_three_following_each_other_stay_put_instead_of_crashing_the_pulse():
    from wayne.contacts import directory
    book = directory()
    dick, tim, cass = (presence.of(book.get(c)) for c in ("nightwing", "robin", "orphan"))
    dick.set_activity("with Tim", "busy", 90, where="Crime Alley", follow="robin")
    tim.set_activity("with Cass", "busy", 90, where="The Bowery", follow="orphan")
    cass.set_activity("with Dick", "busy", 90, where="Burnside", follow="nightwing")
    for p in (dick, tim, cass):
        assert p.whereabouts()[0]           # no RecursionError
        assert p.public()["status"]


def test_ending_what_they_were_doing_leaves_where_they_were():
    """The case closed and Dick was instantly 12 km away on his patrol: the past had been rewritten."""
    from wayne.contacts import directory
    dick = presence.of(directory().get("nightwing"))
    an_hour_ago = time.time() - 3600
    dick.set_activity("working the robbery at Crime Alley", "busy", 180, t=an_hour_ago, where="Crime Alley", owner="case")
    dick.clear_activity()
    assert dick.whereabouts(time.time() - 1800)[0] == "Crime Alley"     # half an hour ago, he was there
    assert dick._activity_at(time.time() + 60) is None                  # and now he isn't doing it


def test_what_they_say_in_passing_doesnt_overwrite_the_case_theyre_on():
    from wayne.contacts import directory
    from wayne.engine import initiative
    tim = directory().get("robin")
    state = presence.of(tim)
    state.set_activity("working the robbery at Crime Alley", "busy", 60, where="Crime Alley", owner="case")
    initiative.apply(SimpleNamespace(contact=tim), {"doing": "handling it", "minutes": 10, "free": True})
    assert state.get("activity")["doing"].startswith("working the robbery")


def test_markers_in_a_retry_are_read_and_never_said():
    from wayne.contacts import directory
    from wayne.engine.session import ContactSession
    session = ContactSession(directory().get("robin"))
    session._join = None
    assert session._read_markers("Heading over. [join: Dick]") == "Heading over."
    assert session._join == "Dick"
    assert delivery.clean("[react: 👍] sure thing [later]") == "sure thing"


def test_someone_added_with_no_backlog_never_knew_what_was_said_before():
    from wayne.memory import groups
    group = groups.create("Family", ["nightwing", "robin"])
    group.add("nightwing", "Patrol tonight — Nightwing takes the Narrows.", at=time.time() - 60)
    group.add_member("catwoman", backlog=0)
    assert not [m for m in group.seen_by("catwoman") if "Narrows" in m["text"]]


def test_an_outsider_never_hears_the_family_is_on_a_case():
    from wayne.contacts import directory
    book = directory()
    report = {"id": "t-x", "kind": "Armed robbery", "severity": 3, "place": "Ralli's Family Restaurant",
              "area": "Otisburg", "x": 49.0, "y": 30.2, "at": time.time() - 60}
    cases.assign(report, "nightwing", by="self", travel=5)
    assert presence.glance(book.get("nightwing"), viewer="catwoman") == ""
    assert "robbery" in presence.glance(book.get("nightwing"), viewer="robin")


@pytest.mark.parametrize("line", ["Don't tell the group about this.", "What did Tim say in the group chat?",
                                  "Tell me what's going on in the chat."])
def test_a_question_about_a_chat_isnt_an_errand_to_post_in_it(line):
    from wayne.engine import groupchat
    assert not (groupchat._ASKS.search(line) and not groupchat._NOT_ASKING.search(line))


def test_naming_the_batwing_isnt_naming_randy():
    from wayne.contacts import directory
    from wayne.engine.session import ContactSession
    session = ContactSession(directory().get("robin"))
    assert "Randy" not in session._named_family("Is the Batwing fuelled?")
    assert "[join: Bruce]" not in session._named_family("Find me everything on the Penguin's shipments.")


@pytest.mark.parametrize("line", ["How was the party last night?", "How is the shoulder doing?",
                                  "Did you see the game last night?"])
def test_small_talk_doesnt_send_anyone_searching(line):
    from wayne.engine.search import subject_of_a_take
    assert subject_of_a_take(line) == ""


def test_a_hunt_with_nobody_named_can_end_well():
    """Homicides with no rogue behind them failed every time — worse than GCPD alone."""
    now = time.time()
    grades = set()
    for i in range(40):
        case = {"id": f"t-hunt-{i}", "kind": "Homicide", "severity": 3, "suspect": "", "crew": 1, "began": now,
                "opened_at": now, "team": ["robin", "batgirl"],
                "members": {"robin": {"joined": now, "travel": 1, "status": "on scene"},
                            "batgirl": {"joined": now, "travel": 1, "status": "on scene"}}}
        grades.add(outcomes.decide(case)["grade"])
    assert "success" in grades


def _console(monkeypatch, book):
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console._adding, console._dropped_adds, console._closing, console._tasks = set(), set(), set(), set()
    console.directory = SimpleNamespace(get=book.get, find=lambda n: None, __iter__=lambda: iter(book.values()))
    console.clients = set()

    async def broadcast(e):
        return None
    console.broadcast = broadcast
    console._presence_changed = lambda c: asyncio.sleep(0)
    console._members = lambda: []
    console._case_comms = lambda case: asyncio.sleep(0)
    return console


def test_nobody_is_sent_to_a_case_thats_over(monkeypatch):
    from wayne.engine import incidents
    tim = SimpleNamespace(id="robin", name="Tim", full_name="Tim Drake", routine=(), shares_status=True,
                          shares_location=True, home="Wayne Manor", texting_pace={}, initiative={}, beat=())
    console = _console(monkeypatch, {"robin": tim})
    report = {"id": "t-done", "kind": "Mugging", "severity": 2, "place": "Crime Alley", "area": "Crime Alley",
              "x": 40.0, "y": 50.0, "at": time.time() - 600, "status": "units responding"}
    monkeypatch.setattr(incidents, "get", lambda rid: dict(report))
    monkeypatch.setattr(cases, "FIELD", ("robin", "nightwing"))
    case = cases.assign(report, "nightwing", by="self", travel=1)
    cases.close(case["id"], "Done.", {"ok": True, "how": "caught", "caught": "", "hurt": {}})
    assert asyncio.run(console.assign_case("t-done", "robin", tell=False)) is None


def test_a_getaway_that_ran_out_ends_as_a_chase_never_as_the_robbery(monkeypatch):
    tim = SimpleNamespace(id="robin", name="Tim", full_name="Tim Drake", routine=(), shares_status=True,
                          shares_location=True, home="Wayne Manor", texting_pace={}, initiative={}, beat=())
    console = _console(monkeypatch, {"robin": tim})
    report = {"id": "t-get", "kind": "Vehicle pursuit", "severity": 3, "place": "Crime Alley", "area": "Crime Alley",
              "x": 40.0, "y": 50.0, "at": time.time() - 600, "was": ["Armed robbery"]}
    case = cases.assign(report, "robin", by="self", travel=30)
    cases.mark(case["id"], was=["Armed robbery"], due=time.time() + 3600)
    console._turned(cases.for_report("t-get"), {**report, "kind": "Armed robbery", "fled": True, "was": []}, time.time())
    after = cases.for_report("t-get")
    assert after["kind"] == "Vehicle pursuit" and after["due"] <= time.time() + 1
