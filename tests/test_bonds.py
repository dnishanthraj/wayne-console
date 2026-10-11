"""Where they stand with him and each other, how it moves, and what it changes — true to who they are."""
from types import SimpleNamespace

from wayne.contacts import directory
from wayne.engine import bonds, presence

DAY = 86400.0


def test_everyone_starts_where_they_are_and_nothing_is_said_about_it(clock):
    assert bonds.standing("alfred", "me", clock())[0] == "devoted"
    assert bonds.standing("batwing", "me", clock())[0] == "distant"
    assert bonds.standing("catwoman", "me", clock())[2]              # Selina: there's something
    assert bonds.note(directory().get("robin"), clock()) == ""        # as it always is: nothing to say


def test_alfred_stays_whatever_he_does_and_jason_cools_fast(clock):
    t = clock()
    for i in range(12):
        bonds.nudge("alfred", "me", warmth=-20, trust=-20, t=t + i * DAY)
        bonds.nudge("redhood", "me", warmth=-6, t=t + i * DAY)
    alfred = bonds.state("alfred", "me", t + 12 * DAY)
    assert alfred["warmth"] >= 70 and alfred["trust"] >= 60          # his floor: loyal, whatever
    assert bonds.state("redhood", "me", t + 12 * DAY)["warmth"] < -20


def test_one_bad_afternoon_cant_end_it_and_a_hurt_fades(clock):
    t = clock()
    bonds.nudge("robin", "me", warmth=-60, tension=6, t=t)
    now = bonds.state("robin", "me", t)
    assert now["warmth"] >= 65 - bonds.DAILY_CAP - 0.01               # a day can only move it so far
    assert bonds.state("robin", "me", t + 12 * DAY)["tension"] < now["tension"] / 4


def test_what_stays_with_them_is_what_happened_and_small_things_fade(clock):
    t = clock()
    bonds.nudge("batgirl", "me", tension=3, why="he kept texting you while you were working", weight=2, t=t)
    bonds.nudge("batgirl", "me", warmth=-1, why="he forgot to say thanks", weight=1, t=t)
    said = bonds.note(directory().get("batgirl"), t + 3600)
    assert "he kept texting you while you were working" in said and "argue" in said
    assert "bring up nothing else" in said
    later = [e["text"] for e in bonds.memories("batgirl", "me", t + 9 * DAY)]
    assert "he kept texting you while you were working" in later and "he forgot to say thanks" not in later


def test_waking_dick_three_mornings_running_grates(clock):
    dick = directory().get("nightwing")
    t = clock()
    for day in range(3):
        _, times = bonds.woken(dick, t + day * DAY)
    assert times == 3
    said = bonds.note(dick, t + 2 * DAY + 600)
    assert "3 times this week" in said


def test_how_hurt_shows_is_theirs(clock):
    t = clock()
    for cid in ("batwing", "alfred", "robin"):
        bonds.nudge(cid, "me", tension=8, warmth=-10, t=t)
    randy, alfred, tim = (bonds.effects(directory().get(c), t) for c in ("batwing", "alfred", "robin"))
    assert randy["answer"] < 0.6 and randy["quiet"] > tim["quiet"]   # Randy ghosts
    assert alfred["answer"] >= 0.85                                  # Alfred always picks up
    assert tim["hides"] and not randy["hides"]                       # Tim goes dark
    shown = presence.of(directory().get("robin")).public(t)
    assert shown["status"] == "unknown" and "spot" not in shown


def test_jason_turns_his_location_on_once_he_trusts_him(clock):
    t = clock()
    jason = directory().get("redhood")
    assert not bonds.effects(jason, t)["shares"]
    for day in range(8):
        bonds.nudge("redhood", "me", trust=12, warmth=8, t=t + day * DAY)
    assert not bonds.effects(jason, t + 8 * DAY)["shares"]           # he warms slowly: a week isn't enough
    for day in range(8, 24):
        bonds.nudge("redhood", "me", trust=12, warmth=8, t=t + day * DAY)
    assert bonds.effects(jason, t + 24 * DAY)["shares"]


def test_the_pass_after_a_call_moves_it_only_when_something_happened(clock):
    from wayne.engine import initiative
    session = SimpleNamespace(contact=directory().get("orphan"))
    initiative._felt(session, {"felt": {"warmth": 0, "trust": 0, "sore": 0, "remember": None}})
    assert bonds.memories("orphan", "me") == []
    initiative._felt(session, {"felt": {"warmth": 2, "trust": 1, "sore": 0, "remember": "he asked how you were and meant it"},
                               "about_others": [{"who": "Tim", "warmth": 2, "why": "Tim covered for you"}]})
    assert bonds.memories("orphan", "me")[0]["text"] == "he asked how you were and meant it"
    assert bonds.state("orphan", "robin")["warmth"] > 50


def test_a_case_he_came_to_and_one_nobody_came_to(monkeypatch):
    import wayne.frontends.web as web
    from wayne.engine import cases
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    monkeypatch.setattr(cases, "known", lambda case: True)
    came = {"id": "c1", "kind": "Armed robbery", "place": "Ace Chemicals", "team": ["robin", "bruce"], "severity": 3}
    monkeypatch.setattr(cases, "team", lambda case: case["team"])
    console._case_bonds(came, {"grade": "success", "hurt": {}})
    assert "he came himself" in bonds.memories("robin", "me")[0]["text"]
    alone = {"id": "c2", "kind": "Hostage situation", "place": "Gotham Zoo", "team": ["batgirl"], "severity": 4}
    console._case_bonds(alone, {"grade": "failure", "hurt": {"batgirl": "a cracked rib"}})
    assert "nobody came" in bonds.memories("batgirl", "me")[0]["text"]
    assert bonds.memories(bonds.GCPD, "me")                            # and the department kept count


def test_someone_who_wont_talk_to_him_is_covered_for_or_not(monkeypatch):
    from wayne.engine import party
    call = party.Call()
    dick = SimpleNamespace(contact=directory().get("nightwing"), heard=[], call=None, _hanging_up=False,
                           mark_call_start=lambda: None, call_messages=lambda: [], keep_heard=lambda: None)
    call.join(dick)
    call.avoiding["robin"] = ("nightwing", "he wasn't there when the docks went bad")
    note = call.note_for(dick)
    assert "doesn't want to talk to him" in note and "docks" in note and "don't make up anything else" in note
