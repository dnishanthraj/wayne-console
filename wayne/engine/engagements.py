"""
Bruce Wayne's engagements: the galas, dinners and boxes he's invited to by mail, and what became of them.

An invitation (engine.inbox) carries the event — what, where, when, the dress, the host. He accepts or declines
from the letter; Alfred, who keeps his diary, answers for him, and knows what's coming. When the evening's
over it's settled the only way it can be: he went — or he was out as Batman when he should have been there,
and the host's note says so, kindly or not, and the Gazette's Vicki Vale may have noticed.

Kept in the database (the space "engagements", a row an engagement).
"""
import datetime
import time
import uuid

from .. import store

LENGTH = 3 * 3600          # an evening, give or take, when the invitation doesn't say
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


def answer(message, accept, now=None):
    """He accepts or declines an invitation: in the diary (or not), and the row for it. None if there's no event."""
    details = (message or {}).get("engagement") or {}
    at = when_from(details, now)
    if at is None:
        return None
    rows = _rows()
    found = next((e for e in rows.list() if e.get("message") == message["id"]), None)
    row = {**(found or {}), "id": (found or {}).get("id") or uuid.uuid4().hex[:10], "message": message["id"],
           "thread": message.get("thread") or message["id"], "what": str(details.get("what") or message["subject"])[:120],
           "where": str(details.get("where") or "")[:80], "host": str(details.get("host") or message["from"])[:80],
           "dress": str(details.get("dress") or "")[:60], "at": at, "until": at + LENGTH,
           "status": "accepted" if accept else "declined", "answered_at": now or time.time()}
    rows.put(row["id"], row)
    return row


def upcoming(now=None, days=8):
    """What's in his diary from now on: accepted, not yet over."""
    now = now or time.time()
    return [e for e in all_engagements() if e["status"] == "accepted" and e["until"] > now and e["at"] < now + days * 86400]


def diary_note(now=None):
    """For Alfred, who keeps the diary: what's coming — '' when nothing is."""
    now = now or time.time()
    items = upcoming(now)
    if not items:
        return ""

    def day(at):
        d = datetime.date.fromtimestamp(at)
        today = datetime.date.fromtimestamp(now)
        name = "today" if d == today else "tomorrow" if d == today + datetime.timedelta(days=1) else d.strftime("%A %-d %B")
        return f"{name} at {time.strftime('%-I:%M %p', time.localtime(at)).lower()}"
    lines = [f"{e['what']}" + (f", {e['where']}" if e["where"] else "") + f" — {day(e['at'])}"
             + (f" ({e['dress']})" if e["dress"] else "") for e in items[:4]]
    return ("You keep his diary — what he's accepted, and you answered for him: " + "; ".join(lines)
            + ". Remind him, or not, the way you would.")


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


def settle(now=None):
    """
    Evenings that are over: he went, or he didn't — and the host's note to come (inbox queues it). Returns the
    engagements settled now.
    """
    from . import inbox
    now = now or time.time()
    settled = []
    rows = _rows()
    for e in rows.list():
        if e["status"] != "accepted" or e["until"] > now:
            continue
        case = _was_out(e["at"], e["until"])
        e = {**e, "status": "missed" if case else "attended", "settled_at": now,
             **({"kept_away_by": f"{case['kind']} at {case['place']}"} if case else {})}
        rows.put(e["id"], e)
        inbox.queue({"kind": "engagement", "id": e["id"], "status": e["status"], "what": e["what"], "where": e["where"],
                     "host": e["host"], "thread": e["thread"]})
        settled.append(e)
    for e in rows.list():
        if e.get("until", 0) < now - _KEEP_DAYS * 86400:
            rows.drop(e["id"])
    return settled


def recent(now=None, days=3):
    """What became of his last few evenings — for Alfred, and anyone who'd have heard."""
    now = now or time.time()
    return [e for e in all_engagements() if e["status"] in ("attended", "missed") and now - e["until"] < days * 86400]
