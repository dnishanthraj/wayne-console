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
    # There, and then gone well before the end — somewhere else he chose, or a case: leaving early, and why.
    for e in _rows().list():
        if (e["status"] != "accepted" or not e.get("present") or e.get("left_at") or not e.get("venue")
                or not (e["at"] <= now < e["until"] - 600)):
            continue
        here = here or batman.position(now)
        if here and ((here[0] - e["venue"][0]) ** 2 + (here[1] - e["venue"][1]) ** 2) ** 0.5 > PRESENT_RADIUS * 2:
            from . import cases
            case = cases.active("bruce")
            where = batman.state(now).get("where") or ""
            _rows().put(e["id"], {**e, "left_at": now, "left_for": f"{case['kind']} at {case['place']}" if case
                                  else (where if where != e.get("where") else "")})


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
            elif e["status"] == "left":
                bonds.nudge(cid, bonds.ME, warmth=0.5, tension=0.8, weight=1,
                            why=f"he came to {e['what']} — and slipped out early")
            else:
                bonds.nudge(cid, bonds.ME, warmth=-2.5, trust=-2, tension=1.5, weight=2,
                            why=f"he never showed for {e['what']} — you went, and waited")
        return
    if not _gordon(e):
        return
    if e["status"] in ("attended", "left"):
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
        # Gone before most of it was done: left early (the case that took him, or wherever he went).
        early = came and e.get("left_at") and (e["left_at"] - e["at"]) < 0.6 * (e["until"] - e["at"])
        e = {**e, "status": "left" if early else "attended" if came else "missed", "settled_at": now,
             **({"kept_away_by": f"{case['kind']} at {case['place']}"} if case and not came else {})}
        rows.put(e["id"], e)
        try:
            _consequences(e)
        except Exception:
            pass
        if e.get("source") != "routine":
            inbox.queue({"kind": "engagement", "id": e["id"], "status": e["status"], "what": e["what"],
                         "where": e["where"], "host": e["host"], "thread": e["thread"], "as": e.get("as", "bruce"),
                         "with": e.get("with", ""), "kept_away_by": e.get("kept_away_by", ""),
                         "left_for": e.get("left_for", "")})
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
        mine = [entry(e, now) for e in rows if lo <= e["at"] < hi]
        theirs = [f for f in at_his(day, now) if not any(abs(f["at"] - m["at"]) < 1800 for m in mine)]
        out.append({"date": day.isoformat(), "entries": sorted(mine + theirs, key=lambda x: x["at"])})
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


# --- his own day, and the map following it ------------------------------------------------------------------
# Bruce Wayne's day is a chairman's: the board and the briefings at Wayne Tower, lunches where the deals are, the
# Foundation's causes, a fitting, a photograph, and now and then a gala — written once a day by the model, from
# what Gotham's doing and what's already in the diary, in places that are on the map. Nights are Batman's, and
# his. The map follows the diary on its own: to each thing in time (go), home when the day's done — unless he's
# somewhere else he chose, which is leaving, and noted (watch).

AUTO_LEAD = 25 * 60        # setting off this long before
_HOME_AFTER = 20 * 60      # after the day's last thing, home — if nothing else is soon


def has_day(day):
    """Whether his own day is already written for `day` (a date)."""
    lo = time.mktime((day.year, day.month, day.day, 0, 0, 0, 0, 0, -1))
    return any(e.get("source") == "routine" and lo <= e["at"] < lo + 86400 for e in _rows().list())


def write_day(model_name, options, day, now=None):
    """
    His day, sketched: a few things between nine and seven (lighter at weekends), around whatever's already in the
    diary, in real places. Background (engine.model). Returns the rows written, or [].
    """
    import json

    from . import gazette, inbox
    from . import model as llm
    now = now or time.time()
    lo = time.mktime((day.year, day.month, day.day, 0, 0, 0, 0, 0, -1))
    already = [e for e in _rows().list() if lo <= e["at"] < lo + 86400 and e["status"] == "accepted"]
    busy = "; ".join(f"{time.strftime('%H:%M', time.localtime(e['at']))}–{time.strftime('%H:%M', time.localtime(e['until']))} "
                     f"{e['what']}" for e in sorted(already, key=lambda e: e["at"])) or "nothing yet"
    papers = "; ".join(f"“{i['headline']}”" for i in (gazette.today(day) or [])[:3])
    weekend = day.weekday() >= 5
    instruction = (
        f"It's {day.strftime('%A %-d %B %Y')}. Bruce Wayne is chairman of Wayne Enterprises (Lucius Fox runs it day to "
        "day) and the face of the Wayne Foundation — to Gotham, a charming, not very serious billionaire. Sketch his "
        f"day: {'two or three' if weekend else 'three to five'} things between 09:00 and 19:00 — the board, a briefing "
        "with Lucius at Wayne Tower, a lunch where a deal or a favour is, a Foundation visit, a fitting, a photograph, a "
        "charity's committee — specific, with who it's with. Already in his diary that day (keep clear of it): "
        f"{busy}." + (f" This morning's papers: {papers}." if papers else "") + " "
        + inbox._real_places() + 'JSON only: {"day": [{"what": "...", "where": "a place from the list, by its exact '
        'name", "from": "HH:MM", "to": "HH:MM", "with": "who, in a few words", "notes": "a line, or empty"}]}')
    try:
        reply = llm.ask(model_name, [{"role": "user", "content": instruction}],
                        {**options, "temperature": 0.7, "num_predict": 420}, fmt="json", purpose="Bruce's day")
        sketch = json.loads(reply).get("day") or []
    except Exception:
        return []
    made = []
    for item in sketch[:5]:
        if not isinstance(item, dict) or not str(item.get("what") or "").strip():
            continue
        try:
            hh, mm = (int(x) for x in str(item.get("from") or "").split(":")[:2])
            eh, em = (int(x) for x in str(item.get("to") or "").split(":")[:2])
        except ValueError:
            continue
        at = time.mktime((day.year, day.month, day.day, hh, mm, 0, 0, 0, -1))
        until = time.mktime((day.year, day.month, day.day, eh, em, 0, 0, 0, -1))
        if until <= at or not (8 <= hh <= 20) or any(e["at"] < until and at < e["until"] for e in already + made):
            continue
        made.append(add(item["what"], item.get("where", ""), at, until, "bruce", item.get("with", ""),
                        item.get("notes", ""), now=now, source="routine"))
    return made


def due_to_go(now=None):
    """
    What he should be setting off for now, on his own: the next thing in the diary, a little before it starts, not
    already gone for and not over. Marked as gone for. [rows]
    """
    now = now or time.time()
    out = []
    for e in sorted(_rows().list(), key=lambda e: e["at"]):
        if (e["status"] != "accepted" or e.get("auto_went") or e.get("present") or not e.get("venue")
                or not (e["at"] - AUTO_LEAD <= now < e["until"] - 600)):
            continue
        e = {**e, "auto_went": now}
        _rows().put(e["id"], e)
        out.append(e)
    return out[:1]


def day_done(now=None):
    """Whether his day's over for now: the last thing ended a while ago and nothing's coming soon."""
    now = now or time.time()
    rows = [e for e in _rows().list() if e["status"] in ("accepted", "attended", "left") and e.get("source") != "plan"]
    ended = [e for e in rows if e["until"] <= now]
    soon = [e for e in rows if e["status"] == "accepted" and now < e["until"] and e["at"] < now + 90 * 60]
    return bool(ended) and not soon and now - max(e["until"] for e in ended) > _HOME_AFTER \
        and now - max(e["until"] for e in ended) < _HOME_AFTER + 600


# --- what others know of his diary ---------------------------------------------------------------------------

def known_to(contact_id, now=None):
    """
    What someone would know of his diary — Alfred keeps the family in the loop: the ones who know everything know
    his evenings, the meetings he's made, Batman's too, and how the last few went (so they can call him on them);
    Lucius knows the company's days as well; Selina, who knows who he is but not the family's business, hears of the
    public evenings. '' for anyone else, or when there's nothing to know. (Alfred has the whole diary: diary_note.)
    """
    from .. import identity
    now = now or time.time()
    if contact_id == "alfred":
        return ""
    level = identity.level(contact_id)
    if level not in ("all", "his"):
        return ""
    soon = [e for e in upcoming(now, days=7) if not e.get("plan")]
    if level == "his":
        soon = [e for e in soon if e.get("source") == "invitation"]
    elif contact_id != "lucius":
        soon = [e for e in soon if e.get("source") != "routine"]
    else:
        soon = [e for e in soon if e.get("as") != "batman"]

    def when(at):
        import datetime as _dt
        d = _dt.date.fromtimestamp(at)
        today = _dt.date.fromtimestamp(now)
        name = "today" if d == today else "tomorrow" if d == today + _dt.timedelta(days=1) else d.strftime("%A")
        return f"{name} at {time.strftime('%-I:%M %p', time.localtime(at)).lower()}"
    lines = [("Batman: " if e.get("as") == "batman" else "") + e["what"] + (f" at {e['where']}" if e.get("where") else "")
             + f", {when(e['at'])}" for e in soon[:4]]
    past = [] if level == "his" else [
        f"he {'went to' if e['status'] == 'attended' else 'left early from' if e['status'] == 'left' else 'never made it to'} "
        f"{e['what']}" for e in recent(now, days=3)[-2:] if e.get("source") != "routine"]
    if not lines and not past:
        return ""
    return ("What you know of his diary (Alfred keeps the family in the loop): " + "; ".join(lines or ["nothing much"])
            + "." + (f" Lately {'; '.join(past)}." if past else "")
            + " Bring it up only if it's natural — asking, teasing, reminding, calling him on it.")


# --- their evenings at his ---------------------------------------------------------------------------------------

def at_his(day, now=None):
    """
    Their habits that are at his — dinner at the Manor with whoever's coming this week — as diary rows for `day`,
    read-only: theirs, kept with him. [entry-shaped dicts]
    """
    from ..contacts import directory
    from . import week
    import re as _re
    noon = time.mktime((day.year, day.month, day.day, 12, 0, 0, 0, 0, -1))
    social = _re.compile(r"(?i)dinner|lunch|brunch|family|party|movie|film|game|poker|chess|birthday|sunday")
    found = {}
    for contact in directory():
        if (getattr(contact, "home", "") or "") == "Wayne Manor":
            continue            # Alfred's roses are his house's day, not an evening at his
        for habit in week.on(contact, noon):
            if (habit.get("where") or "") != "Wayne Manor" or not (habit.get("together") or social.search(habit.get("doing", ""))):
                continue
            start, end = week._hours(habit.get("from", "0")), week._hours(habit.get("to", "0"))
            key = (habit.get("together") or habit.get("doing", "")[:24], start)
            row = found.setdefault(key, {"what": habit.get("doing", "at the Manor"), "start": start, "end": end, "who": []})
            row["who"].append({"id": contact.id, "name": contact.name, "said": "yes"})
    out = []
    for (tag, _start), row in found.items():
        at = noon - 12 * 3600 + row["start"] * 3600
        until = noon - 12 * 3600 + (row["end"] if row["end"] > row["start"] else row["end"] + 24) * 3600
        out.append({"id": f"family:{day.isoformat()}:{abs(hash(tag)) % 10**6}", "what": row["what"][0].upper() + row["what"][1:],
                    "where": "Wayne Manor", "venue": _venue("Wayne Manor"), "at": at, "until": until, "as": "bruce",
                    "source": "family", "origin": "their week — at yours", "status": "accepted",
                    "state": "on now" if at <= (now or time.time()) <= until else
                    ("went" if until < (now or time.time()) else "upcoming"),
                    "who": row["who"], "host": "", "dress": "", "notes": "", "story": "", "kept_away_by": "",
                    "left_for": "", "present": False, "thread": "", "group": "", "readonly": True})
    return out


# --- a crime at the evening ---------------------------------------------------------------------------------------
# Rarely — more where the city's rougher, at a gala with the rich in one room, with someone loose who'd go for it —
# trouble comes to where he is: a report on the scanner at the place, like any other (incidents.at takes it), his to
# slip out of the evening for as Batman (and leaving early is noted), or to leave to GCPD and the family.

_CRIME_ODDS = 0.02


def crime_at(e, now=None, rng=None):
    """Whether trouble comes to this evening — rolled once, while he's there. The report, or None."""
    import random as _random

    from . import incidents, places
    rng = rng or _random
    now = now or time.time()
    if e.get("crime_rolled") or not e.get("present") or not e.get("venue") or e.get("source") == "routine":
        return None
    _rows().put(e["id"], {**e, "crime_rolled": now})
    spot = places.resolve(e.get("where") or "") or {}
    rough = incidents._crime_by_area().get(spot.get("area", ""), 0.4)
    gala = any(w in (e.get("what") or "").lower() for w in ("gala", "ball", "fundraiser", "auction", "party", "opening"))
    odds = _CRIME_ODDS * (1 + rough) * (2.0 if gala else 1.0)
    if rng.random() >= odds:
        return None
    kind = rng.choices(["Armed robbery", "Kidnapping", "Hostage situation"], weights=[6, 2, 1])[0]
    report = {"id": f"evt-{e['id']}", "kind": kind, "severity": 4 if kind == "Hostage situation" else 3,
              "place": spot.get("name") or e["where"], "area": spot.get("area", ""), "x": e["venue"][0],
              "y": e["venue"][1], "at": int(now), "ends": int(now + 75 * 60), "crew": rng.choice([2, 3, 4]),
              "at_his": e["id"], "dispatch": f"Reports of a {kind.lower()} under way at {e['where']} — during "
                                             f"{e['what']}."}
    from .. import store
    store.space("event_crimes").put(report["id"], report)
    return report
