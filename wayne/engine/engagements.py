"""
His engagements — Bruce Wayne's evenings and Batman's meetings — made by mail, and what became of them.

An invitation (engine.inbox) carries the event — what, where, when, the dress, the host — and he accepts or
declines it from the letter, Alfred answering for him; a meeting agreed in a thread ("Thursday at eight, Le
Bistro" — "See you there") goes in the diary too, Batman's from the secure line ("Roof. Midnight." — Jim:
"I'll be there.").

Being there means being there: before it, a reminder and the way to go (see go); during it, wherever he is
on the map is checked against the place (watch). Afterwards it's settled the only way it can be — he came, or
he didn't (and if he was out on a case, that's why) — and it goes on from there: the host's note, Vicki Vale
wanting to know where he was, Jim and the department a little colder for a meeting Batman never came to, or
warmer for one he did; and what happened at the evening itself, written once, which the mail and Alfred
remember.

Kept in the database (the space "engagements", a row an engagement).
"""
import datetime
import time
import uuid

from .. import store

LENGTH = 3 * 3600          # an evening, give or take, when the invitation doesn't say
MEETING = 3600             # a meeting: an hour's grace either side of being on time
PRESENT_RADIUS = 2.5       # this close to the place, he's there (map units; one is ~150 m)
REMIND_BEFORE = 40 * 60    # the reminder, and time enough to get across town
_KEEP_DAYS = 30


def _rows():
    return store.space("engagements")


def all_engagements():
    return sorted(_rows().list(), key=lambda e: e["at"])


def when_from(details, now=None):
    """The invitation's date and time as a moment — None when it doesn't give one that makes sense."""
    try:
        day = datetime.date.fromisoformat(str(details.get("date") or "").strip())
    except ValueError:
        return None
    hh, mm = 20, 0
    raw = str(details.get("time") or "").strip()
    try:
        if ":" in raw:
            hh, mm = (int(x) for x in raw.split(":")[:2])
        elif raw.isdigit():
            hh = int(raw)
    except ValueError:
        pass
    at = time.mktime((day.year, day.month, day.day, max(0, min(23, hh)), max(0, min(59, mm)), 0, 0, 0, -1))
    now = now or time.time()
    return at if now - 86400 < at < now + 120 * 86400 else None


def _venue(where):
    """Where a place is, if it's somewhere on the map: (x, y), or None."""
    from . import places
    found = places.resolve(where or "")
    return (found["x"], found["y"]) if found else None


def answer(message, accept, now=None):
    """He accepts or declines an invitation: in the diary (or not), and the row for it. None if there's no event."""
    details = (message or {}).get("engagement") or {}
    at = when_from(details, now)
    if at is None:
        return None
    rows = _rows()
    found = next((e for e in rows.list() if e.get("message") == message["id"]), None)
    where = str(details.get("where") or "")[:80]
    row = {**(found or {}), "id": (found or {}).get("id") or uuid.uuid4().hex[:10], "message": message["id"],
           "thread": message.get("thread") or message["id"], "what": str(details.get("what") or message["subject"])[:120],
           "where": where, "venue": _venue(where), "host": str(details.get("host") or message["from"])[:80],
           "dress": str(details.get("dress") or "")[:60], "at": at, "until": at + LENGTH, "as": "bruce",
           "source": "invitation", "status": "accepted" if accept else "declined", "answered_at": now or time.time()}
    rows.put(row["id"], row)
    return row


def agree(what, where, at, with_, thread, as_="bruce", message=None, now=None, source="mail", plan=None):
    """
    A meeting agreed in a letter's thread — dinner with Vicki, Batman and Jim on a roof — or a plan he made with
    the family (`plan`: engine.plans), in the diary. One per thread and time: agreeing again is the same meeting.
    Returns the row.
    """
    rows = _rows()
    found = next((e for e in rows.list() if e.get("thread") == thread and abs(e["at"] - at) < 1800), None)
    if found:
        return found
    row = {"id": uuid.uuid4().hex[:10], "message": message, "thread": thread, "what": str(what)[:120],
           "where": str(where)[:80], "venue": _venue(where), "host": str(with_)[:80], "with": str(with_)[:80],
           "dress": "", "at": at, "until": at + (MEETING if as_ == "batman" else LENGTH), "as": as_, "source": source,
           "status": "accepted", "answered_at": now or time.time(), **({"plan": plan} if plan else {})}
    rows.put(row["id"], row)
    return row


def watch(now=None):
    """
    While it's on — from a little before the start to the end — whether he's where it is: on the map, near enough
    to count. Once there is enough. Without a place on the map, being out on a case is all that keeps him away.
    """
    from . import batman
    now = now or time.time()
    here = None
    for e in _rows().list():
        if e["status"] != "accepted" or e.get("present") or not (e["at"] - 900 <= now <= e["until"]):
            continue
        if not e.get("venue"):
            continue
        here = here or batman.position(now)
        if here and ((here[0] - e["venue"][0]) ** 2 + (here[1] - e["venue"][1]) ** 2) ** 0.5 <= PRESENT_RADIUS:
            _rows().put(e["id"], {**e, "present": True, "present_at": now})


def reminders(now=None):
    """What's about to start that he hasn't been reminded of — marked reminded. [rows]."""
    now = now or time.time()
    due = []
    for e in _rows().list():
        if e["status"] == "accepted" and not e.get("reminded") and e["at"] - REMIND_BEFORE <= now < e["at"]:
            e = {**e, "reminded": True}
            _rows().put(e["id"], e)
            due.append(e)
    return due


def go(engagement_id, now=None):
    """He sets off for it: as Bruce Wayne in the car to an evening, as Batman to a meeting (batman.go)."""
    from . import batman
    e = _rows().get(engagement_id)
    if not e or e["status"] != "accepted":
        return None
    if e.get("venue"):
        return batman.go(e["where"] or "there", e["venue"][0], e["venue"][1], t=now, suit=e.get("as") == "batman")
    return batman.go(e["where"], t=now, suit=e.get("as") == "batman")


def upcoming(now=None, days=8):
    """What's in his diary from now on: accepted, not yet over."""
    now = now or time.time()
    return [e for e in all_engagements() if e["status"] == "accepted" and e["until"] > now and e["at"] < now + days * 86400]


def diary_note(now=None):
    """For Alfred, who keeps the diary: what's coming, and what became of the last few — '' when there's nothing."""
    now = now or time.time()
    items = upcoming(now)
    past = recent(now, days=2)
    if not items and not past:
        return ""
    gone = "; ".join(
        f"{'went to' if e['status'] == 'attended' else 'never made it to'} {e['what']}"
        + (f" — out on the {e['kept_away_by'].lower()} instead" if e.get("kept_away_by") else "")
        for e in past[-3:])
    if not items:
        return f"His diary, lately: he {gone}."

    def day(at):
        d = datetime.date.fromtimestamp(at)
        today = datetime.date.fromtimestamp(now)
        name = "today" if d == today else "tomorrow" if d == today + datetime.timedelta(days=1) else d.strftime("%A %-d %B")
        return f"{name} at {time.strftime('%-I:%M %p', time.localtime(at)).lower()}"
    lines = [("Batman: " if e.get("as") == "batman" else "") + f"{e['what']}" + (f", {e['where']}" if e["where"] else "")
             + f" — {day(e['at'])}" + (f" ({e['dress']})" if e["dress"] else "") for e in items[:4]]
    return ("You keep his diary — what he's agreed to; the invitations, you answered for him: " + "; ".join(lines)
            + ". Remind him, or not, the way you would." + (f" Lately he {gone}." if gone else ""))


def _was_out(at, until):
    """Whether he was out as Batman when he should have been there: on a case for any of the evening."""
    from . import cases
    for c in cases.board(until + 3600):
        if "bruce" not in cases.team(c):
            continue
        joined = ((c.get("members") or {}).get("bruce") or {}).get("joined", c.get("opened_at", 0))
        ended = c.get("closed_at") or time.time()
        if joined < until and ended > at:
            return c
    return None


def _gordon(e):
    return e.get("as") == "batman" and "gordon" in (e.get("with") or e.get("host") or "").lower() or \
        e.get("as") == "batman" and (e.get("with") or "").lower() in ("jim", "james gordon", "jim gordon", "g")


def _consequences(e):
    """
    What it does between them: Batman's meeting with Jim kept, or not — Jim, and the department he answers to,
    remember either way (engine.bonds). Bruce Wayne's evenings answer in letters (see inbox).
    """
    from . import bonds
    if e.get("plan"):
        # A plan he made with them: whoever came, came for him — an evening together warms; waiting for a man who
        # never shows stays with them.
        from . import plans
        plan = next((p for p in plans.all_plans() if p["id"] == e["plan"]), None)
        for cid in (plan or {}).get("who") or []:
            if not plans.going(plan, cid):
                continue
            if e["status"] == "attended":
                bonds.nudge(cid, bonds.ME, warmth=2.5, trust=1, why=f"an evening with him: {e['what']}", weight=1)
            else:
                bonds.nudge(cid, bonds.ME, warmth=-2.5, trust=-2, tension=1.5, weight=2,
                            why=f"he never showed for {e['what']} — you went, and waited")
        return
    if not _gordon(e):
        return
    if e["status"] == "attended":
        bonds.nudge("gordon", bonds.ME, warmth=2, trust=4, why=f"Batman came to {e['what']}, as he said he would",
                    weight=1)
        bonds.nudge(bonds.GCPD, bonds.ME, warmth=1, trust=1)
    else:
        bonds.nudge("gordon", bonds.ME, warmth=-4, trust=-5, tension=1.5,
                    why=f"Batman never showed for {e['what']}" + (f" at {e['where']}" if e.get("where") else ""),
                    weight=2)
        bonds.nudge(bonds.GCPD, bonds.ME, warmth=-2, trust=-2, why="Batman stood the commissioner up", weight=1)


def settle(now=None):
    """
    What's over: he was there, or he wasn't (out on a case, if that's why) — what it does between them, and the
    letters to come (inbox queues them). Returns the engagements settled now.
    """
    from . import inbox
    now = now or time.time()
    settled = []
    rows = _rows()
    for e in rows.list():
        if e["status"] != "accepted" or e["until"] > now:
            continue
        case = _was_out(e["at"], e["until"])
        came = e.get("present") if e.get("venue") else not case
        e = {**e, "status": "attended" if came else "missed", "settled_at": now,
             **({"kept_away_by": f"{case['kind']} at {case['place']}"} if case and not came else {})}
        rows.put(e["id"], e)
        try:
            _consequences(e)
        except Exception:
            pass
        inbox.queue({"kind": "engagement", "id": e["id"], "status": e["status"], "what": e["what"], "where": e["where"],
                     "host": e["host"], "thread": e["thread"], "as": e.get("as", "bruce"), "with": e.get("with", ""),
                     "kept_away_by": e.get("kept_away_by", "")})
        settled.append(e)
    for e in rows.list():
        if e.get("until", 0) < now - _KEEP_DAYS * 86400:
            rows.drop(e["id"])
    return settled


def recent(now=None, days=3):
    """What became of his last few evenings — for Alfred, and anyone who'd have heard."""
    now = now or time.time()
    return [e for e in all_engagements() if e["status"] in ("attended", "missed") and now - e["until"] < days * 86400]


def remember(engagement_id, story):
    """What happened at an evening he went to, in a line or two — the mail and Alfred carry it on."""
    e = _rows().get(engagement_id)
    if e and story:
        _rows().put(engagement_id, {**e, "story": str(story).strip()[:400]})


def lately(now=None):
    """His last few evenings, as anyone writing to him would know them — '' when there were none."""
    items = [e for e in recent(now, days=4) if e.get("as") != "batman"]
    if not items:
        return ""
    lines = []
    for e in items[-3:]:
        lines.append(f"he {'was at' if e['status'] == 'attended' else 'accepted and never came to'} {e['what']}"
                     + (f" ({e['story']})" if e.get("story") and e["status"] == "attended" else ""))
    return "Lately: " + "; ".join(lines) + ". "


# --- the diary, as he manages it ---------------------------------------------------------------------------

_STATUS_WORDS = {"accepted": "upcoming", "attended": "went", "missed": "missed", "left": "left early",
                 "cancelled": "cancelled", "declined": "declined"}


def _who(e):
    """Who's coming, as far as anyone knows: the family's answers to a plan, the person a meeting's with, the host."""
    from ..contacts import directory
    from . import plans
    book = directory()
    if e.get("plan"):
        plan = next((p for p in plans.all_plans() if p["id"] == e["plan"]), None)
        if plan:
            out = []
            for cid in plan.get("who") or []:
                said = (plan.get("answers") or {}).get(cid, {}).get("said") or "asked"
                found = book.get(cid)
                out.append({"id": cid, "name": found.name if found else cid, "said": said})
            return out
    names = [n.strip() for n in str(e.get("with") or "").split(",") if n.strip()]
    out = []
    for name in names:
        found = book.find(name)
        out.append({"id": found.id if found else "", "name": found.name if found else name, "said": "yes"})
    return out


def entry(e, now=None):
    """One row of the diary as the page shows it: when, what, where, who's coming, how it went, where it came from."""
    from . import plans
    now = now or time.time()
    on_now = e["status"] == "accepted" and e["at"] <= now <= e["until"]
    origin = {"invitation": "an invitation", "mail": f"a letter with {e.get('with') or e.get('host') or 'someone'}",
              "plan": "a plan with the family", "manual": "you", "routine": "your day", "dm": "a text",
              "call": "a call"}.get(e.get("source") or "", "")
    plan = next((p for p in plans.all_plans() if p["id"] == e.get("plan")), None) if e.get("plan") else None
    return {"id": e["id"], "what": e["what"], "where": e.get("where", ""), "venue": e.get("venue"),
            "at": e["at"], "until": e["until"], "as": e.get("as", "bruce"), "source": e.get("source", ""),
            "origin": origin, "status": e["status"], "state": "on now" if on_now else _STATUS_WORDS.get(e["status"], ""),
            "who": _who(e), "host": e.get("host", ""), "dress": e.get("dress", ""), "notes": e.get("notes", ""),
            "story": e.get("story", ""), "kept_away_by": e.get("kept_away_by", ""), "left_for": e.get("left_for", ""),
            "present": bool(e.get("present")), "thread": e.get("thread") if e.get("source") in ("invitation", "mail") else "",
            "group": (plan or {}).get("made_in", "")[2:] if str((plan or {}).get("made_in", "")).startswith("g:") else ""}


def days(start, count=8, now=None):
    """The diary from `start` (a date) for `count` days: [{"date", "entries": [entry, ...]}], soonest first."""
    import datetime as _dt
    now = now or time.time()
    out = []
    rows = all_engagements()
    for i in range(count):
        day = start + _dt.timedelta(days=i)
        lo = time.mktime((day.year, day.month, day.day, 0, 0, 0, 0, 0, -1))
        hi = lo + 86400
        out.append({"date": day.isoformat(),
                    "entries": [entry(e, now) for e in rows if lo <= e["at"] < hi]})
    return out


def add(what, where, at, until=None, as_="bruce", with_="", notes="", now=None, source="manual"):
    """He writes something in himself — or his day does (`source` "routine"). Returns the row."""
    row = {"id": uuid.uuid4().hex[:10], "what": str(what).strip()[:120] or "something", "where": str(where).strip()[:80],
           "venue": _venue(where), "host": "", "with": str(with_)[:200], "notes": str(notes)[:300], "dress": "",
           "at": at, "until": until if until and until > at else at + (MEETING if as_ == "batman" else LENGTH),
           "as": "batman" if as_ == "batman" else "bruce", "source": source, "status": "accepted",
           "thread": "", "answered_at": now or time.time()}
    _rows().put(row["id"], row)
    return row


def edit(engagement_id, what=None, where=None, at=None, until=None, notes=None, as_=None):
    """He changes it: the time, the place, what it is. Only what's still to come. Returns the row, or None."""
    e = _rows().get(engagement_id)
    if not e or e["status"] != "accepted":
        return None
    length = e["until"] - e["at"]
    if what is not None and str(what).strip():
        e["what"] = str(what).strip()[:120]
    if where is not None:
        e["where"], e["venue"] = str(where).strip()[:80], _venue(where)
    if at is not None:
        e["at"] = at
        e["until"] = until if until and until > at else at + length
        e.pop("reminded", None)
    elif until is not None and until > e["at"]:
        e["until"] = until
    if notes is not None:
        e["notes"] = str(notes)[:300]
    if as_ in ("bruce", "batman"):
        e["as"] = as_
    _rows().put(e["id"], e)
    return e


def cancel(engagement_id, now=None):
    """
    He calls it off. Someone else's evening, a meeting, a plan with the family: they're told (inbox and the
    console see to that — see Console.cancel_engagement); his own entry just goes. Returns the row, or None.
    """
    e = _rows().get(engagement_id)
    if not e or e["status"] != "accepted":
        return None
    e = {**e, "status": "cancelled", "cancelled_at": now or time.time()}
    _rows().put(e["id"], e)
    return e


def delete(engagement_id):
    _rows().drop(engagement_id)
