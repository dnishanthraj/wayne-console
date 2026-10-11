"""How long a case runs: who's on it, and how it's going to go — over early either way, or dragging on."""
import time

from wayne.engine import cases, outcomes


def _report(i, kind="Armed robbery", severity=3, suspect=""):
    return {"id": f"c-{kind}-{suspect}-{i}", "kind": kind, "severity": severity, "place": "Ace Chemicals",
            "area": "The Narrows", "x": 10.0, "y": 10.0, "suspect": suspect, "crew": 3, "at": time.time()}


def _case(i, team, kind="Armed robbery", severity=3, suspect=""):
    now = time.time()
    r = _report(i, kind, severity, suspect)
    return {**r, "team": list(team), "assignee": team[0], "opened_at": now, "began": now - 60, "travel": 5,
            "members": {m: {"joined": now, "travel": 5, "by": "him", "status": "on scene"} for m in team}}


def test_the_way_it_runs_is_the_way_it_ends():
    # Read off the same odds and roll as the ending: a clean sweep is a success; a collapse never is.
    seen = set()
    for i in range(300):
        team = [("bruce",), ("robin",), ("nightwing", "batgirl"), ("orphan",)][i % 4]
        case = _case(i, team, kind=("Armed robbery", "Hostage situation", "Riddle left at a crime scene")[i % 3],
                     severity=2 + i % 3, suspect=("", "The Riddler", "Killer Croc")[i % 3])
        way, pace = outcomes.course(case)
        ending = outcomes.decide(case)
        seen.add(way)
        if way == "clean":
            assert ending["ok"] and 0.55 <= pace <= 0.75
        elif way == "collapse":
            assert not ending["ok"] and 0.5 <= pace <= 0.75
        elif way == "drag":
            assert 1.15 <= pace <= 1.3
    assert {"clean", "collapse", "even"} <= seen


def test_the_right_people_finish_sooner_and_the_wrong_ones_lose_it_more():
    def tally(team, suspect, severity):
        ways = [outcomes.course(_case(i, team, suspect=suspect, severity=severity))[0] for i in range(200)]
        return ways.count("clean"), ways.count("collapse")
    strong_clean, strong_lost = tally(("bruce", "nightwing"), "", 2)
    weak_clean, weak_lost = tally(("robin",), "Bane", 5)
    assert strong_clean > weak_clean and weak_lost > strong_lost


def test_its_course_sets_when_it_ends_and_how_it_reads_on_the_way(clock):
    for i in range(200):
        case = cases.assign(_report(i, severity=4), "robin", travel=0)
        if case["course"] == "collapse":
            break
    assert case["course"] == "collapse"
    plain = outcomes.work(case, ["robin"])
    span = (case["due"] - case["updated_at"]) / 60
    assert span < plain * 0.8                                              # lost, and fast
    case = {**case, "status": "on scene"}
    clock.at(case["updated_at"] + 0.5 * (case["due"] - case["updated_at"]))
    key, how = cases.phase(case, clock())
    assert key == "gone wrong" and "slipping away" in how                   # a moment where help could turn it
    clock.at(case["updated_at"] + 0.9 * (case["due"] - case["updated_at"]))
    assert "not the way you wanted" in cases.phase(case, clock())[1]


def test_help_arriving_rereads_the_odds(clock):
    for i in range(300):
        report = _report(1000 + i, severity=4)
        case = cases.assign(report, "robin", travel=0)
        if case["course"] == "collapse":
            break
    assert case["course"] == "collapse"
    joined = cases.assign(report, "bruce", travel=2)
    assert joined["team"] == ["robin", "bruce"]
    assert joined["course"] == outcomes.course(joined, ["robin", "bruce"])[0]   # the odds move with who's there


def test_the_log_tells_the_same_story_as_the_case_and_the_comms(clock):
    from wayne.engine import incidents
    from wayne.memory import groups as group_store
    for i in range(300):
        report = _report(2000 + i, severity=3)
        case = cases.assign(report, "bruce", travel=0)
        if case["course"] == "clean":
            break
    assert case["course"] == "clean"
    cases.mark(case["id"], status="on scene")
    comms = group_store.create("Comms", ["nightwing"])
    comms.update_meta(lambda m: m.update({"case": case["id"], "comms": True}))
    comms.add("me", "Three down. Last one's running for the alley.")
    case = cases.for_report(case["id"])
    clock.at(case["updated_at"] + 0.45 * (case["due"] - case["updated_at"]))
    view, key = incidents._case_view(report, clock())
    assert "the Bat himself" in view and "going their way" in view
    assert "Batman: Three down" in view and "never quotes it" in view
    assert ":in it:" in key                                              # the log grows as the case moves on
    clock.at(case["updated_at"] + 0.95 * (case["due"] - case["updated_at"]))
    assert ":wrapping up:" in incidents._case_view(report, clock())[1]
