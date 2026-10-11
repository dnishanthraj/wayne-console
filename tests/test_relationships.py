"""Relationships, further: being turned away, plans kept and broken, a romance's turns, a group chat read, the hunt."""
import json
from types import SimpleNamespace

from wayne.contacts import directory
from wayne.engine import bonds, engagements, initiative, model, outcomes, plans

DAY = 86400.0


def test_a_romance_crossing_a_line_is_remembered_and_a_breakup_is_one(clock):
    t = clock()
    start = bonds.state("catwoman", bonds.ME, t)["romance"]
    for i in range(12):
        bonds.nudge("catwoman", bonds.ME, romance=4, t=t + i * 60)
    lines = [e["text"] for e in bonds.memories("catwoman", bonds.ME, t + 3600, most=5)]
    assert start < 70 and any("together now" in line for line in lines)
    for i in range(14):                                       # from the top, all the way down past 40, hurt
        bonds.nudge("catwoman", bonds.ME, romance=-5, tension=1.5, t=t + DAY + i * 60)
    lines = [e["text"] for e in bonds.memories("catwoman", bonds.ME, t + DAY + 3600, most=5)]
    assert any("broke up" in line for line in lines)


def test_turned_away_once_is_nothing_much_and_again_is_remembered(clock):
    tim = directory().get("robin")
    bonds.turned_away(tim, "declined", 1, clock())
    assert bonds.memories("robin", bonds.ME, clock()) == []
    bonds.turned_away(tim, "declined", 3, clock())
    assert "declined your call — 3 times today" in bonds.memories("robin", bonds.ME, clock())[0]["text"]


def test_a_plan_he_makes_is_in_his_diary_and_showing_up_is_what_counts(clock, monkeypatch):
    from wayne.engine import batman
    plan = plans.add({"what": "dinner at Ralli's", "where": "Ralli's Family Restaurant", "at": clock() + 3600,
                      "until": clock() + 3 * 3600, "who": ["nightwing", "robin"]})
    plans.answer(plan["id"], "nightwing", "yes")
    plans.answer(plan["id"], "robin", "no")
    row = next(e for e in engagements.all_engagements() if e.get("plan") == plan["id"])
    monkeypatch.setattr(batman, "position", lambda t=None: (1.0, 10.0))       # he stays at the Manor
    clock.at(row["until"] + 60)
    engagements.settle(clock())
    said = [e["text"] for e in bonds.memories("nightwing", bonds.ME, clock())]
    assert any("never showed for dinner at Ralli's" in line for line in said)
    assert bonds.memories("robin", bonds.ME, clock()) == []                      # he wasn't going anyway


def test_a_group_chat_stretch_moves_what_happened_in_it(clock, monkeypatch):
    reply = {"felt": [{"who": "Tim", "toward": "Bruce", "warmth": -2, "sore": 2,
                       "remember": "he made fun of you in front of everyone"},
                      {"who": "Jason", "toward": "Tim", "warmth": -2, "sore": 1, "remember": "Tim took Bruce's side"},
                      {"who": "Nobody", "toward": "Bruce", "warmth": 3, "sore": 0, "remember": "x"}]}
    monkeypatch.setattr(model, "ask", lambda *a, **k: json.dumps(reply))
    group = SimpleNamespace(members=["robin", "redhood"])
    msgs = [{"from": "me", "text": "Tim, that plan was a joke."}, {"from": "robin", "text": "wow ok"},
            {"from": "redhood", "text": "replacement crying again"}, {"from": "robin", "text": "shut up jason"}]
    moved = initiative.group_afterthought(group, msgs, directory(), "m", {})
    assert {(w, t) for w, t, *_ in moved} == {("robin", bonds.ME), ("redhood", "robin")}
    assert "made fun of you" in bonds.memories("robin", bonds.ME, clock())[0]["text"]


def test_with_the_department_against_the_masks_batmans_odds_are_worse(clock, monkeypatch):
    case = {"id": "c1", "kind": "Armed robbery", "severity": 3, "place": "Ace Chemicals", "area": "Ironworks",
            "team": ["bruce", "robin"], "members": {}, "crew": 2, "suspect": ""}
    from wayne.engine import cases
    monkeypatch.setattr(cases, "team", lambda c: c["team"])
    calm = outcomes.chance(case)
    monkeypatch.setattr(outcomes, "hunted", lambda t=None: True)
    assert outcomes.chance(case) < calm or calm <= outcomes.baseline(case)
