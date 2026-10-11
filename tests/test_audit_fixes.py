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


def test_catching_a_rogue_doesnt_redraw_the_night():
    """One capture rewrote sixteen of the night's reports — the case just won, marked on a different crime."""
    from wayne.engine import codex, incidents
    t = time.time()
    before = {r["id"]: (r["kind"], r["place"]) for r in incidents.at(t) + incidents.ended(t)}
    for g in incidents.rogues()[:6]:
        codex.capture(g["name"], "caught on a test")
    after = {r["id"]: (r["kind"], r["place"]) for r in incidents.at(t) + incidents.ended(t)}
    assert before and before == {k: v for k, v in after.items() if k in before}


def test_help_on_the_way_never_pushes_a_case_back():
    report = {"id": "t-back", "kind": "Armed robbery", "severity": 3, "place": "Crime Alley", "area": "Crime Alley",
              "x": 40.0, "y": 50.0, "at": time.time() - 900}
    case = cases.assign(report, "nightwing", by="self", travel=1)
    now = time.time()
    cases.mark(case["id"], status="on scene", updated_at=now - 1200, due=now + 120)
    after = cases.assign(report, "orphan", by="self", travel=20)
    assert after["due"] <= now + 121


def test_only_whoever_got_there_is_graded_or_hurt():
    now = time.time()
    case = {"id": "t-present", "kind": "Armed robbery", "severity": 3, "team": ["nightwing", "robin"],
            "members": {"nightwing": {"joined": now - 1800, "travel": 2, "status": "on scene"},
                        "robin": {"joined": now - 60, "travel": 30, "status": "assigned"}}, "due": now}
    assert outcomes.present(case) == ["nightwing"]


def test_a_run_that_started_today_isnt_hot_yesterday():
    from wayne.engine import arcs
    case = {"id": "t-run", "suspect": "The Joker", "kind": "Laughing-gas attack", "place": "Ace Chemicals",
            "severity": 4}
    arcs.record(case, {"how": "got away", "ok": False})
    assert arcs.heat("The Joker") > 0 and arcs.heat("The Joker", time.time() - 86400) == 0.0


def test_mail_isnt_lost_when_the_model_fails_to_write_it(monkeypatch):
    from wayne.engine import inbox
    inbox.queue({"kind": "case", "id": "t-mail", "how": "caught", "caught": "", "suspect": "", "place": "Crime Alley",
                 "what": "mugging", "severity": 2, "chase": False})
    monkeypatch.setattr(inbox, "_from_event", lambda *a, **k: None)
    inbox.write_one("m", {})
    assert inbox._take_event() is not None              # kept for another go
    inbox.write_one("m", {})
    assert inbox._take_event() is None                  # tried twice: let go


def test_saying_more_is_never_cut_tighter_than_saying_a_little():
    import random as _random

    from wayne.contacts import directory
    from wayne.engine import prompting
    alfred = directory().get("alfred")
    caps = {}
    for seed in range(200):
        hint, cap = prompting.spoken_length(alfred, "My father would have known what to do. I don't. I really don't "
                                            "know anymore, Alfred, and it's been weighing on me for weeks now.",
                                            rng=_random.Random(seed))
        caps[hint] = cap
    long_cap = [c for h, c in caps.items() if "longer" in h]
    assert long_cap and min(long_cap) > max(c for h, c in caps.items() if "longer" not in h)


@pytest.mark.parametrize("line,urgent", [("Hey, what are you doing right now?", False), ("Where are you now?", False),
                                         ("Get here now.", True), ("Find it. Now.", True), ("This is urgent.", True)])
def test_urgent_is_an_order_not_the_word_now(line, urgent):
    assert guards.is_urgent(line) is urgent


def test_a_reminder_about_their_own_night_isnt_filed_as_a_fact():
    from wayne.engine.session import _is_instruction
    assert not _is_instruction("don't forget your mask tonight, tim")
    assert _is_instruction("remember that i'm allergic to shellfish")


def test_a_silence_is_remembered_as_heard_never_as_mm():
    from wayne.memory import History
    history = History("robin")
    history.record_exchange("Bruce: You there?", "Here.")
    history.record_heard("Dick: Tim's being quiet.")
    history.record_exchange("Bruce: Tim?", "Still here.")
    shown = history.for_model()
    assert not any(m["content"] in ("Mm.", "…") for m in shown if m["role"] == "assistant")
    assert any("Tim's being quiet" in m["content"] and "Tim?" in m["content"] for m in shown if m["role"] == "user")


def test_a_line_written_whole_stops_when_hes_cut_in(monkeypatch):
    """A pause-filler read a whole prompt and wrote a whole line while his own turn waited behind it."""
    import wayne.engine.session as sess
    from wayne.contacts import directory
    session = sess.ContactSession(directory().get("robin"))

    def chunks(**kw):
        yield {"message": {"content": "First words"}}
        session.cancel()                    # he cuts in here
        yield {"message": {"content": " and the rest nobody hears"}}
    monkeypatch.setattr(sess.ollama, "chat", chunks)
    assert session._chat_once([{"role": "user", "content": "x"}]) == ""


def test_cut_off_theyre_remembered_as_saying_what_he_heard():
    import wayne.frontends.web as web
    from wayne.contacts import directory
    from wayne.engine.session import ContactSession
    tim = directory().get("robin")
    console = web.Console.__new__(web.Console)
    session = ContactSession(tim)
    session.history.messages.clear()
    console.sessions, console.call = {"robin": session}, None
    started = time.time()
    session.history.record_exchange("Where are you?", "On a roof. Two streets from Dick. He took the shots fired.")
    console._heard_upto = 11
    console._remember_heard(tim, {10: "On a roof.", 11: "Two streets from Dick.", 12: "He took the shots fired."},
                            started - 1)
    assert session.history.messages[-1]["content"] == "On a roof. Two streets from Dick. —"


def test_a_voice_nobody_wants_any_more_never_takes_a_slot(monkeypatch):
    import threading

    from wayne.audio import tts
    engine = tts.AlfredVoiceService.__new__(tts.AlfredVoiceService)
    engine._slots = threading.BoundedSemaphore(1)
    called = []
    monkeypatch.setattr(engine, "_timed", lambda text, voice: called.append(text) or (b"x", []), raising=False)
    with pytest.raises(tts.SynthesisCancelled):
        engine.synthesize_timed("Hello.", "v", cancelled=lambda: True)
    assert not called


def test_someone_on_a_speaker_joins_without_a_ring():
    from wayne import events
    assert events.party(["nightwing", "robin"], added="robin", room=True)["room"] is True
    assert events.party(["nightwing"], added="robin")["room"] is False


def test_concern_for_someone_hurt_or_for_someone_else_isnt_fussing():
    assert not guards.takes_his_temperature("You okay?", "I got stabbed on the docks.")
    assert not guards.takes_his_temperature("Dick, you good?", "Dick took a hit.")
    assert guards.takes_his_temperature("You okay?", "Hey.")


def test_asking_whos_about_isnt_making_a_plan():
    from wayne.engine import plans
    assert not plans.maybe_proposal("Anyone seen Dick tonight?")
    assert plans.maybe_proposal("Dinner tonight at 8, everyone?")


def test_on_a_group_call_tims_line_isnt_hearsay_about_bruce():
    from wayne.contacts import directory
    from wayne.engine import grapevine
    session = SimpleNamespace(contact=directory().get("nightwing"))
    line = grapevine._line(session, {"role": "user", "content": "Tim Drake: I'm quitting the team."})
    assert line.startswith("Tim Drake:") and "Bruce" not in line
