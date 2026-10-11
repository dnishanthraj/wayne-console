"""
Cases: reports off the scanner that someone in the family has taken on.

A report becomes a case when he puts someone on it — from the map, or by just
telling them ("Tim, take the robbery in the Diamond District") — or when
someone on patrol nearby takes it themselves. From then on it's theirs: they
head there (the map follows), it's part of what they know when he talks to
them, and they may well tell him about it — that they've got it, how it went.

More than one of them can work a case: he can put a second on it, someone on
patrol nearby joins in, and a case gone wrong pulls the family in as backup.
Each comes from wherever they are and is on scene when they get there; the
first on it leads, and writes up how it ended.

A case closes the way a real one does: they say it's handled, in a call or a
text (the pass after every conversation listens for that), or it runs its
course and they close it themselves. Either way it gets a line of how it
ended, in their words, and it stays on the board for a while after.

Only the people who'd work a scene take cases: Dick, Tim, Barbara, Cass,
Jason, Randy — not Alfred, not Lucius, not Selina. Jason and Randy can be
asked; they mostly do their own thing.
"""
import json
import re
import threading
import time

from .. import store
from ..store import events

FIELD = ("nightwing", "robin", "batgirl", "orphan", "redhood", "batwing")
# How long after closing a case stays on the board (and in their mind).
KEEP_CLOSED = 6 * 3600

_lock = threading.Lock()


# The board is the space "cases", a row per case, in the order they were opened (see wayne.store.kv). Asked
# about hundreds of times a pulse — who's on what, for every map dot and every prompt — it's read from a view
# parsed once per change, and a case handed out is a copy of its own; a change writes that case's row and
# nothing else. How each went is kept for good as an event (case.closed): who was on it, where, who was behind it.

def _board():
    return store.space("cases")


def everything():
    """Every case on the board, in the order they were opened — copies, to change as you like."""
    return _board().list()


def peek():
    """
    The board as it stands, shared and not to be changed — for readers that only
    look, many times a pulse (the scanner, the patrols). Hand on a copy of anything.
    """
    return _board().view()


def _copy(case):
    return json.loads(json.dumps(case)) if case is not None else None


def _get(case_id):
    return _board().get(case_id)


def _put(case):
    _board().put(case["id"], case)


def _prune(now=None):
    """Off the board three days after it closed (the record keeps it), and never more than 200 on it."""
    now = now or time.time()
    board_ = _board()
    gone = [c["id"] for c in board_.view() if c["status"] == "closed" and now - c.get("closed_at", now) >= 3 * 86400]
    if gone:
        board_.drop(*gone)
    board_.trim(200)


def team(case):
    """Everyone on a case, the lead first."""
    return case.get("team") or ([case["assignee"]] if case.get("assignee") else [])


def known(case):
    """
    Whether he'd know about a case: he gave it to one of them, or someone on it
    lets him see where they are. Jason working something on his own is Jason's business.
    """
    if case.get("by") == "him" or any(m.get("by") == "him" for m in (case.get("members") or {}).values()):
        return True
    from ..contacts import directory
    book = directory()
    return any(book.get(cid) is None or getattr(book.get(cid), "shares_location", True) for cid in team(case))


def board(now=None):
    """Open cases he knows of, and the ones closed in the last few hours, newest first."""
    now = now or time.time()
    return sorted((_copy(c) for c in _board().view() if (c["status"] != "closed"
                   or now - c.get("closed_at", 0) < KEEP_CLOSED) and known(c)), key=lambda c: -c["opened_at"])


def active(contact_id):
    """The case they're on right now, or None."""
    return _copy(next((c for c in _board().view() if c["status"] != "closed" and contact_id in team(c)), None))


def recent(contact_id, now=None):
    """The last case they closed, if it was tonight."""
    now = now or time.time()
    closed = [c for c in _board().view() if c["status"] == "closed" and now - c.get("closed_at", 0) < KEEP_CLOSED
              and contact_id in team(c)]
    return _copy(max(closed, key=lambda c: c["closed_at"])) if closed else None


def for_report(report_id):
    return _get(report_id)


def assign(report, contact_id, by="him", travel=0):
    """
    Put someone on a report — the first on it leads; anyone after joins them.
    Returns the case. `travel` is how many minutes it takes them to get there
    from wherever they are.
    """
    now = time.time()
    with _lock:
        case = _get(report["id"])
        if case is not None and case["status"] == "closed":
            return case
        new = case is None
        if new:
            case = {"id": report["id"], "kind": report["kind"], "severity": report["severity"],
                    "place": report["place"], "area": report["area"], "x": report["x"], "y": report["y"],
                    "dispatch": report.get("dispatch", ""), "suspect": report.get("suspect", ""),
                    "gang": report.get("gang", ""), "crew": report.get("crew", 1), "was": report.get("was", []),
                    "began": report.get("at", now), "opened_at": now, "team": [], "members": {}}
        case.setdefault("team", team(case))
        case.setdefault("members", {})
        if contact_id in case["team"]:
            return case
        first = not case["team"]
        case["team"].append(contact_id)
        case["members"][contact_id] = {"by": by, "travel": round(travel, 1), "joined": now, "status": "assigned"}
        from . import outcomes
        if first:
            # How long it takes is who's on it — and how it's going to go: a clean sweep's over early, a case
            # that's slipping away can be lost fast, a close one drags (outcomes.course).
            way, pace = outcomes.course(case, [contact_id])
            case.update({"assignee": contact_id, "by": by, "status": "assigned", "updated_at": now, "course": way,
                         "travel": round(travel, 1),
                         "due": now + (travel + outcomes.work(case, [contact_id]) * pace) * 60})
        else:
            # Another pair of hands — a better one, faster: the work left is as long as it takes this
            # team, and no sooner than they can get there. The odds move with them, and so does its course.
            way, pace = outcomes.course(case, case["team"])
            case["course"] = way
            began = case.get("updated_at", now) if case["status"] == "on scene" else now
            left = outcomes.work(case, case["team"]) * pace - (now - began) / 60
            if case["status"] == "on scene":
                # Under way already: help can only bring the end closer. Pushed back for Cass's twenty minutes
                # out, a case wrapping up went back to "gone wrong" and its bar ran backwards.
                case["due"] = min(case.get("due", now), max(now + (travel + 4) * 60, now + max(3, left) * 60))
            else:
                case["due"] = max(now + (travel + 4) * 60, min(case.get("due", now), now + max(3, left) * 60))
        _put(case)
        if new:
            _prune(now)
    return case


def leave(case_id, contact_id):
    """
    They've moved on to something else: off this case — and the case gone with them if they were all it had. The
    lead gone, whoever's next leads it, on their own terms: whose word put them on it, how far they had to come —
    the lead Bruce had sent kept a case Jason was left working alone on his board — and how it runs now with
    fewer hands (outcomes.course; when it's due stays put, so the bar never runs backwards).
    """
    with _lock:
        case = _get(case_id)
        if case is None or case["status"] == "closed":
            return                                      # a closed case's team is the record of who worked it
        members = team(case)
        if contact_id not in members:
            return
        rest = [m for m in members if m != contact_id]
        if not rest:
            _board().drop(case_id)
            return
        case["team"] = rest
        crew = case.setdefault("members", {})
        crew.pop(contact_id, None)
        if case.get("assignee") == contact_id:
            lead = crew.get(rest[0]) or {}
            case.update({"assignee": rest[0], "by": lead.get("by", case.get("by")),
                         "travel": lead.get("travel", case.get("travel"))})
        from . import outcomes
        case["course"] = outcomes.course(case, rest)[0]
        _put(case)


def advance(now=None, arrived=None):
    """
    Each of them on scene once they've had time to get there (`arrived(case,
    who)` is told); the case is on scene with the first. Returns cases that
    have run their course.
    """
    now = now or time.time()
    due, landed = [], []
    with _lock:
        for c in [_copy(c) for c in _board().view() if c["status"] != "closed"]:
            changed = False
            members = c.get("members") or {c["assignee"]: {"joined": c["updated_at"], "travel": c.get("travel", 9),
                                                            "status": "on scene" if c["status"] == "on scene" else "assigned"}}
            for cid, m in members.items():
                if m.get("status") == "assigned" and now - m.get("joined", now) > m.get("travel", 9) * 60:
                    m["status"] = "on scene"
                    landed.append((c, cid))
                    changed = True
                    if c["status"] == "assigned":
                        c["status"], c["updated_at"] = "on scene", now
            if "members" in c:
                c["members"] = members
            if changed:
                _put(c)
            if c["status"] in ("assigned", "on scene") and now > c.get("due", now + 1):
                due.append(c)
    for c, cid in landed:
        if arrived:
            arrived(c, cid)
    return due


# How a case ends with someone behind bars, in the words people write it.
_CAUGHT = re.compile(r"(?i)\b(custody|arrest\w*|cuffed|caught|locked (him|her|them) up|took (him|her|them) down|"
                     r"handed (him|her|them) (over|to)|gcpd ha(s|ve) (him|her|them)|in a cell|bagged|apprehended|"
                     r"back in arkham|back to arkham|off to blackgate)\b")


def close(case_id, outcome, result=None):
    """
    It's over: how it ended, in a line — and, decided by the odds (see
    outcomes.decide), whether it went well. If one of the rogues was caught,
    they're in GCPD custody now (see codex.capture).
    """
    now = time.time()
    with _lock:
        case = _get(case_id)
        if case is None or case["status"] == "closed":
            return None
        case.update({"status": "closed", "closed_at": now, "updated_at": now, "outcome": outcome.strip()[:240]})
        if result and result.get("story"):
            case["story"] = result["story"][:700]
        if result:
            case["result"] = {k: result[k] for k in ("ok", "grade", "lives", "culprit", "how", "caught", "hurt")
                              if k in result}
        _put(case)
        _prune(now)
    _remember(case)
    if result is not None:
        if result.get("caught"):
            from . import codex
            codex.capture(result["caught"], f"caught on the {case['kind'].lower()} at {case['place']}")
        return case
    if case.get("suspect") and _CAUGHT.search(outcome or ""):
        from . import codex
        codex.capture(case["suspect"], f"caught on the {case['kind'].lower()} at {case['place']}")
    return case


def _remember(case):
    """
    How it went, kept for good — the board forgets a case after three days — for
    whoever was on it, and for Alfred and Barbara at the screens if he'd know of it.
    """
    from ..contacts import directory
    book = directory()
    crew = team(case)
    names = [book.get(c).name if book.get(c) else "Bruce" if c == "bruce" else c for c in crew]
    result = case.get("result") or {}
    text = (f"the {case['kind'].lower()} at {case['place']} ({case['area']}), "
            + (f"{' and '.join(names)} on it" if names else "nobody on it")
            + (f" — {result['grade']}" if result.get("grade") else "")
            + f": {case.get('story') or case.get('outcome') or 'handled'}")
    events.add("case.closed", text[:900], at=case.get("closed_at"), actor="",
               data={"case": case["id"], "kind": case["kind"], "place": case["place"], "area": case["area"],
                     "team": crew, "suspect": case.get("suspect", ""), "outcome": case.get("outcome", ""),
                     "grade": result.get("grade", ""), "opened_at": case.get("opened_at")},
               links=[("case", case["id"]), ("place", case["place"]), ("rogue", case.get("suspect", ""))]
               + [("contact", c) for c in crew],
               audience=[c for c in crew if c != "bruce"],
               scope=events.WATCHERS if known(case) else events.PRIVATE, secret=True)


def retime(case_id, contact_id, minutes):
    """Their way there changed — a lift in the Batwing — and with it when they'll be on scene."""
    with _lock:
        case = _get(case_id)
        mine = (case or {}).get("members", {}).get(contact_id)
        if not mine or mine.get("status") != "assigned":
            return None
        mine["joined"], mine["travel"] = time.time(), round(minutes, 1)
        if case["assignee"] == contact_id and case["status"] == "assigned":
            case["updated_at"], case["travel"] = time.time(), round(minutes, 1)
        _put(case)
    return case


def mark(case_id, **fields):
    """Note something on a case — that they called for help, say."""
    with _lock:
        case = _get(case_id)
        if case is None:
            return None
        case.update(fields)
        _put(case)
    return case


def drop(case_id):
    with _lock:
        _board().drop(case_id)


# The kinds where being on scene means a fight; the rest are searched, asked about, pieced together.
_VIOLENT = ("robbery", "assault", "hostage", "gang", "shots", "shooting", "riot", "smash", "kidnap", "stabbing",
            "fight", "carjack", "home invasion", "attack", "explosion", "arson")


def _roll(case, what):
    import hashlib
    return int(hashlib.sha1(f"{case['id']}:{what}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def goes_wrong(case):
    """Whether this one goes sideways — more of them than dispatch said, someone hurt: the serious ones, sometimes."""
    return case["severity"] >= 3 and _roll(case, "trouble") < 0.18 + 0.1 * (case["severity"] - 3)


def phase(case, now=None):
    """
    Where they've got to with it, from the clock: (key, how it is) — on the
    way; just there, sizing it up; in the thick of it; wrapping up. Worked out,
    not stored, so a call and the map agree on it at every moment.
    """
    now = now or time.time()
    if case["status"] == "closed":
        return "closed", "it's over"
    if case["status"] == "assigned":
        left = max(1, round((case["updated_at"] + case.get("travel", 9) * 60 - now) / 60))
        return "en route", f"on your way there, about {left} minute{'s' if left != 1 else ''} out"
    span = max(60.0, case.get("due", now) - case["updated_at"])
    f = (now - case["updated_at"]) / span
    violent = any(word in case["kind"].lower() for word in _VIOLENT)
    way = case.get("course", "even")
    if f < 0.18:
        return "arriving", "just got there — taking it in, quiet, before anyone knows you're there"
    if way == "collapse" and f < 0.85:
        # Losing it, and fast: there's still a moment where help could turn it (cases.assign re-reads the odds).
        if f >= 0.3:
            return "gone wrong", ("it's slipping away from you — they're bolting, or it's turning bad fast; you're "
                                  "losing it and could use help")
    if f < (0.6 if way == "clean" else 0.68):
        if goes_wrong(case) and way != "clean":
            return "gone wrong", ("it's gone bad — more of them than dispatch said, or someone's hurt; you're in it "
                                  "and could use help")
        if way == "clean" and f >= 0.3:
            return "in it", ("in the thick of it — and on top of it: it's going your way" if violent else
                             "in the thick of it — and it's coming together fast")
        return ("in it", "in the thick of it — fighting" if violent else
                "in the thick of it — searching, asking, piecing it together")
    if way == "collapse":
        return "wrapping up", "it's over, and not the way you wanted — what's left to do is pick up the pieces"
    return "wrapping up", "it's settling — cuffs on or the scene secured, GCPD on the way, catching your breath"


def phase_of_him(case, now=None):
    """Where it's got to, said of him rather than to them: 'on his way there', 'catching his breath'."""
    _key, how = phase(case, now)
    for mine, his in (("you're", "he's"), ("your", "his"), ("you", "him")):
        how = re.sub(rf"\b{mine}\b", his, how)
    return how


def brief(contact_id, now=None, masks=False):
    """
    What they're working, or just finished, as a line for the model. `masks`: with someone listening who
    knows only the masks (Jim, on the line) — everyone on it by their mask, and nothing of where he is.
    """
    now = now or time.time()
    case = active(contact_id)
    if case:
        mine = (case.get("members") or {}).get(contact_id) or {"by": case.get("by"), "joined": case["opened_at"]}
        minutes = int((now - mine.get("joined", case["opened_at"])) / 60)
        who = "He put you on it" if mine.get("by") == "him" else "You took it yourself"
        _, how = phase(case, now)
        if mine.get("status") == "assigned" and case["status"] == "on scene":
            left = max(1, round((mine["joined"] + mine.get("travel", 9) * 60 - now) / 60))
            how = f"on your way to join them, about {left} minute{'s' if left != 1 else ''} out"
        from ..contacts import directory
        book = directory()
        from .. import identity, operator
        if masks:
            others = [operator.mask_name() if c == "bruce" else identity.mask_of(book.get(c))
                      for c in team(case) if c != contact_id]
        else:
            others = [book.get(c).name if book.get(c) else "Bruce — Batman himself" if c == "bruce" else c
                      for c in team(case) if c != contact_id]
        from . import batman, outcomes
        bring = " and ".join(outcomes.strengths(contact_id, case["kind"]))
        lacking = outcomes.weakest(contact_id, case["kind"])
        if "bruce" in team(case):
            him = ""
        elif masks:
            him = " He isn't on this one."
        else:
            # Never told, they took it he was out there with them: "you're late", "get out of there".
            bruce = batman.state(now)
            where = f"on his way to {bruce['where']}" if bruce.get("route") else f"at {bruce['where']}"
            him = f" He isn't on this one — he's {where}, not on the scene."
        from . import arcs
        run = arcs.note(case["suspect"]) if case.get("suspect") else ""
        return (f"You're working a case: {case['kind'].lower()} at {case['place']} ({case['area']})"
                + (f" — dispatch said: \"{case['dispatch']}\"" if case.get("dispatch") else "")
                + f". {who} {minutes} minutes ago" + (f", with {' and '.join(others)} on it too" if others else "")
                + f". Right now you're {how}.{him} On this one you'd play to what you're good at — {bring}"
                + (f"; {lacking} is where you'd lean on someone" if lacking and others else "")
                + (f" {run}" if run else "")
                + ". It's yours to talk about, your way — what you've found, what you think, how it's going.")
    done = recent(contact_id, now)
    if done:
        return (f"Earlier tonight you closed a case: {done['kind'].lower()} at {done['place']} — "
                f"{done.get('outcome') or 'handled'}.")
    return ""


def missions(contact_id, now=None, days=7):
    """The cases they've worked lately and seen through, newest first — the stories they'd tell."""
    now = now or time.time()
    done = [_copy(c) for c in _board().view() if c["status"] == "closed" and now - c.get("closed_at", 0) < days * 86400
            and contact_id in team(c)]
    return sorted(done, key=lambda c: -c["closed_at"])


def missions_note(contact_id, names, now=None, limit=4):
    """
    Their recent missions as they'd tell them — the same story everyone who was
    there remembers — for when the talk turns to cases, or a few of them get
    together and swap them. '' if they've worked none lately.
    """
    now = now or time.time()
    told = []
    for c in missions(contact_id, now)[:limit]:
        when = time.strftime("%A night", time.localtime(c["closed_at"]))
        others = [names.get(m, m) for m in team(c) if m != contact_id]
        story = (c.get("story") or c.get("outcome") or "handled")[:320]
        told.append(f"{when}: the {c['kind'].lower()} at {c['place']}" + (f", with {' and '.join(others)}" if others else "")
                    + f" — {story}")
    if not told:
        return ""
    return ("Missions you've worked lately — tell them the way they happened, in your own words; whoever else was "
            "there remembers the same, and can tell their side: " + " | ".join(told))


def board_note(names, now=None):
    """The case board, for whoever watches it (Alfred, Barbara)."""
    lines = []
    for c in board(now)[:6]:
        who = " and ".join(names.get(cid, cid) for cid in team(c))
        state = f"closed — {c.get('outcome', '')}" if c["status"] == "closed" else c["status"]
        lines.append(f"{c['kind'].lower()} at {c['place']}: {who}, {state}")
    return ("The case board: " + "; ".join(lines) + ".") if lines else ""
