"""
Things they can do on the line, not just say — and show him.

Asked "where's that?", "who's she?", "what's tomorrow look like?", a person with the
right thing to hand doesn't describe it: they send it. So a contact can put a card in
front of him — a place on the map, a Codex file, a case on the board, a day of his
diary, the page they found, the share price — with [show: …]; and the ones who keep
his diary (Alfred; Lucius for the company's days) can write in it — [remind: when |
what] for a reminder that comes up at the time, [diary: when | what | where] for
something he's doing. Each is theirs only if their profile says so (`tools`): Alfred
won't be a calendar app, but he does keep the diary, and always has.

They're markers the reply carries, like [take: …] — read off and acted on by the
console after the turn (see Console._use_tools), never spoken or shown as words.
What comes back is a card: a small dict the page renders in the call or in the
thread, with what it needs to act on it (fly the map there, open the file, open
the day).
"""
import datetime
import re
import time

# What each kind of thing can be, when they show it.
KINDS = ("place", "person", "rogue", "case", "diary", "link", "market")

TOOLS = ("show", "remind", "diary")


def may(contact, tool):
    """Whether this one can do this: their profile's `tools`, or none."""
    return tool in (getattr(contact, "tools", ()) or ())


def note(contact):
    """
    What they can do on the line, for the cached prompt: '' if nothing. Said once, plainly, with the marker — the
    console reads it and does it; the words around it are theirs.
    """
    can = [t for t in TOOLS if may(contact, t)]
    if not can:
        return ""
    lines = []
    if "show" in can:
        lines.append("[show: what] puts it in front of him instead of describing it — a place by its name, "
                     "someone's file by their name, the case, a day of his diary ('diary: tomorrow'), the page you "
                     "found, the share price ('market') — only when showing beats saying, and say it your way too")
    if "remind" in can:
        lines.append("[remind: when | what] sets him a reminder that comes up at the time — 'when' as a date and "
                     "24-hour time (2026-10-12 18:00) or 'in 20 minutes' — when he asks for one, or you'd set it "
                     "for him anyway")
    if "diary" in can:
        lines.append("[diary: when | what | where] writes something he's doing into his diary, when he asks you to "
                     "put it in")
    return ("Things you can do, with a marker the console acts on (never read out): " + "; ".join(lines)
            + ". At most one of each in a reply, and never make up that you've done one you haven't.")


# --- when ----------------------------------------------------------------------------------------------------------

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_PARTS = {"morning": 9, "noon": 12, "midday": 12, "lunchtime": 13, "afternoon": 15, "evening": 19,
          "tonight": 20, "night": 21, "midnight": 0}


def when(text, now=None):
    """
    A moment as it's written in a marker — '2026-10-12 18:00', '18:00', '6pm', 'in 20 minutes', 'tomorrow at
    9', 'friday 8pm', 'tonight' — as a timestamp, always ahead of now; None if it can't be read.
    """
    now = now or time.time()
    raw = (text or "").strip().lower()
    if not raw:
        return None
    base = datetime.datetime.fromtimestamp(now)
    iso = re.match(r"(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2}):(\d{2})", raw)
    if iso:
        try:
            moment = datetime.datetime(*map(int, iso.groups()))
        except ValueError:
            return None
        return moment.timestamp() if moment.timestamp() > now - 60 else None
    rel = re.search(r"\bin\s+(an?|half an|\d+(?:\.\d+)?)\s*(minute|min|hour|hr)s?\b", raw)
    if rel:
        amount = {"a": 1, "an": 1, "half an": 0.5}.get(rel.group(1))
        amount = amount if amount is not None else float(rel.group(1))
        return now + amount * (3600 if rel.group(2).startswith(("hour", "hr")) else 60)
    day = base.date()
    named_day = False
    if "tomorrow" in raw:
        day, named_day = day + datetime.timedelta(days=1), True
    else:
        for i, name in enumerate(_DAYS):
            if re.search(rf"\b{name[:3]}(?:{name[3:]})?\b", raw):
                ahead = (i - day.weekday()) % 7 or 7
                day, named_day = day + datetime.timedelta(days=ahead), True
                break
    clock = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.sub(r"\d{4}-\d{2}-\d{2}", "", raw))
    hour = minute = None
    if clock and (clock.group(2) or clock.group(3) or re.search(r"\bat\s+\d", raw) or named_day
                  or re.fullmatch(r"\s*\d{1,2}(:\d{2})?\s*(am|pm)?\s*", raw)):
        hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
        if clock.group(3) == "pm" and hour < 12:
            hour += 12
        elif clock.group(3) == "am" and hour == 12:
            hour = 0
        elif not clock.group(3) and 1 <= hour <= 8 and not re.search(r"\bmorning\b", raw):
            hour += 12                       # "at 6", "friday 8": the evening, as people mean it
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
    else:
        part = next((h for word, h in _PARTS.items() if re.search(rf"\b{word}\b", raw)), None)
        if part is None:
            if not named_day:
                return None
            part = 9
        hour, minute = part, 0
    moment = datetime.datetime.combine(day, datetime.time(hour, minute))
    if moment.timestamp() <= now and not named_day:
        moment += datetime.timedelta(days=1)           # "6pm" said at seven is tomorrow's
    return moment.timestamp() if moment.timestamp() > now - 60 else None


# --- cards ---------------------------------------------------------------------------------------------------------

def _norm(text):
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def _person(thing, viewer):
    """A Codex file by name (or alias): the family's and Gotham's people, and the rogues."""
    from .. import identity
    from ..contacts import directory
    from . import codex
    wanted = _norm(thing)
    if not wanted:
        return None
    outside = identity.outside(viewer.id) if viewer is not None else True
    for person in codex.people(directory()):
        names = [_norm(person.get("name")), _norm(person.get("alias")), _norm((person.get("name") or "").split()[0])]
        if wanted in [n for n in names if n]:
            alias = person.get("alias") or ""
            if outside and person.get("group") == "Family":
                alias = ""                         # someone outside the secret never shows a mask's name
            return {"kind": "person", "id": person["id"], "name": person["name"], "alias": alias,
                    "role": person.get("occupation", ""), "portrait": person.get("portrait", ""),
                    "group": person.get("group", ""), "line": (person.get("bio") or "").split("\n")[0][:160]}
    for rogue in codex.rogues():
        names = [_norm(rogue.get("name")), _norm(rogue.get("real"))]
        if wanted in [n for n in names if n] or (len(wanted) > 4 and any(wanted in n for n in names if n)):
            return {"kind": "rogue", "id": rogue["id"], "name": rogue["name"], "alias": rogue.get("real", ""),
                    "role": rogue.get("status", ""), "portrait": rogue.get("portrait", ""),
                    "line": (rogue.get("bio") or "").split("\n")[0][:160],
                    **({"x": rogue["x"], "y": rogue["y"]} if rogue.get("x") is not None else {})}
    return None


def _place(thing):
    from . import places
    spot = places.resolve(thing)
    if not spot:
        return None
    return {"kind": "place", "name": spot["name"], "area": spot.get("area", ""), "x": spot["x"], "y": spot["y"],
            "icon": spot.get("icon", ""), "line": (spot.get("bio") or "")[:160]}


def _case(thing, viewer, now):
    from . import cases
    wanted = _norm(thing)
    board = cases.board(now)
    if not board:
        return None
    pick = None
    if re.search(r"\b(the )?case\b|\bthis one\b|\bit\b", wanted) and viewer is not None:
        pick = cases.active(viewer.id)
    if pick is None:
        pick = next((c for c in board if wanted and (wanted in _norm(c["place"]) or _norm(c["place"]) in wanted
                                                     or wanted in _norm(c["kind"]))), None)
    if pick is None:
        return None
    from ..contacts import directory
    book = directory()
    team = [book.get(m).name if book.get(m) else ("Bruce" if m == "bruce" else m) for m in cases.team(pick)]
    return {"kind": "case", "id": pick["id"], "name": pick["kind"], "place": pick["place"],
            "area": pick.get("area", ""), "status": pick["status"], "team": team,
            "x": pick.get("x"), "y": pick.get("y"), "line": pick.get("outcome") or pick.get("dispatch", "")[:160]}


def _diary(thing, now):
    from . import engagements
    raw = re.sub(r"^\s*(my |his |the )?diary\s*[:,-]?\s*", "", thing or "", flags=re.I).strip() or "today"
    day = datetime.date.fromtimestamp(now)
    if raw.lower() in ("today", "tonight"):
        pass
    else:
        at = when(raw if re.search(r"\d|tomorrow|day", raw.lower()) else f"{raw} 9am", now)
        if at:
            day = datetime.date.fromtimestamp(at)
    entries = engagements.days(day, 1, now)[0]["entries"]
    return {"kind": "diary", "date": day.isoformat(), "name": day.strftime("%A %-d %B"),
            "entries": [{"what": e["what"], "at": e["at"], "where": e.get("where", ""), "as": e.get("as", "bruce"),
                         "state": e.get("state", "")} for e in entries][:8]}


def _market(now):
    from . import markets
    today = datetime.date.fromtimestamp(now)
    days = []
    d = today
    while len(days) < 30:
        if markets.trading(d):
            days.append(d)
        d -= datetime.timedelta(days=1)
    days.reverse()
    series = [round(markets.close("WYNE", d), 2) for d in days]
    row = next(r for r in markets.board(today) if r["ticker"] == "WYNE")
    return {"kind": "market", "name": "Wayne Enterprises", "ticker": "WYNE", "close": row["close"],
            "pct": row["pct"], "series": series, "from": days[0].isoformat(), "to": days[-1].isoformat()}


def _link(thing, sources):
    wanted = _norm(thing)
    for item in sources or []:
        title = _norm(item.get("title"))
        if wanted and (wanted in title or title in wanted or wanted in _norm(item.get("url"))):
            return {"kind": "link", "name": item.get("title", "")[:120], "url": item.get("url", ""),
                    "line": (item.get("snippet") or "")[:200]}
    if sources and re.search(r"\b(it|that|this|the (page|article|link|result))\b", wanted):
        item = sources[0]
        return {"kind": "link", "name": item.get("title", "")[:120], "url": item.get("url", ""),
                "line": (item.get("snippet") or "")[:200]}
    return None


def card(thing, viewer=None, now=None, sources=()):
    """
    What [show: thing] puts in front of him, as a card — a day of his diary, the share price, the page they found,
    the case, someone's file, a place — or None when it isn't anything they could show.
    """
    now = now or time.time()
    thing = (thing or "").strip().strip("\"'")
    if not thing:
        return None
    low = thing.lower()
    if re.match(r"^(my |his |the )?diary\b", low) or low in ("today", "tomorrow", "tonight"):
        return _diary(thing, now)
    if re.search(r"\b(market|share price|stock|shares|wyne)\b", low):
        return _market(now)
    return (_link(thing, sources) or _case(thing, viewer, now) or _person(thing, viewer) or _place(thing))
