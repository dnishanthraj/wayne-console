"""
What each of them did today, as it happened: a diary the console keeps for them.

Set off and got there; on patrol, where, and with whom; who joined them, and
who left and where to; the cases they took, joined, reached and finished, and
how; their calls with him. Written as things change, never worked out again
afterwards: a day plan sketched near midnight put Dick at home with snacks for
the hour he'd spent on a roof with Tim, and anything that looked back by
recomputing believed it. What's written here stays written.

It's what they'd remember of their own night — "what were you just doing?",
"where did Dick go?" — so it goes into what they're told (see Presence.note),
and Alfred and Barbara, who watch the screens, can read anyone's.

Cheap by design: `observe` runs every pulse and before anyone's prompt is put
together, and writes only when something differs from the last thing it saw;
everything else is a one-line append where it happens — a row in the events
table (see wayne.store.events), kept long after the day it was written for
recall to find: "what were you doing Tuesday night?".
"""
import threading
import time

from .. import paths, store
from ..store import events

KEEP = 36 * 3600         # tonight, and last night for the morning after: what's kept to hand
MOST = 240

_lock = threading.Lock()
_books = {}              # (data dir, contact id) -> {"entries": [...], "last": what observe last saw}


def _book(contact_id):
    """
    Their diary as kept to hand: the last KEEP hours of it, read once from the
    record — every line ever written is an event (kind diary.<what>), kept long
    after it's dropped from here, for recall — and what observe last saw.
    """
    key = (str(paths.DATA_DIR), contact_id)
    if key not in _books:
        lines = [{"at": e["at"], "kind": e["kind"][len("diary."):], "text": e["text"], **e["data"]}
                 for e in events.of(contact_id, "diary.", since=time.time() - KEEP, limit=MOST)]
        _books[key] = {"entries": lines, "last": store.space("diary_last").get(contact_id, {})}
    return _books[key]


def _record(contact_id, entry):
    """The line, kept for good: who it's about, and who else may read it (Alfred and Barbara, at their screens)."""
    from ..contacts import directory
    from .grapevine import _THE_WORK
    contact = directory().get(contact_id)
    extra = {k: v for k, v in entry.items() if k not in ("at", "kind", "text")}
    who = extra.get("who")
    who = [who] if isinstance(who, str) else list(who or [])
    shown = getattr(contact, "shares_location", True) if contact else False
    events.add(f"diary.{entry['kind']}", entry["text"], at=entry["at"], actor=contact_id, data=extra or None,
               links=[("contact", contact_id)] + [("contact", c) for c in who], audience=[contact_id],
               scope=events.WATCHERS if shown else events.PRIVATE,
               secret=entry["kind"] == "case" or bool(_THE_WORK.search(entry["text"])))


def write(contact_id, kind, text, at=None, **extra):
    """
    One line in their diary — `kind` (trip, arrived, doing, moved, with, left,
    case, call, status) and what happened, in a few words. The same line twice
    running is one line. Returns the entry, or None.
    """
    text = " ".join((text or "").split())
    if not contact_id or not text:
        return None
    at = at or time.time()
    with _lock:
        book = _book(contact_id)
        entries = book["entries"]
        if entries and entries[-1]["kind"] == kind and entries[-1]["text"] == text and at - entries[-1]["at"] < 3600:
            return None
        entry = {"at": int(at * 10) / 10, "kind": kind, "text": text[:220], **extra}     # never later than it was
        entries.append(entry)
        entries.sort(key=lambda e: e["at"])
        cutoff = time.time() - KEEP
        book["entries"] = [e for e in entries if e.get("at", 0) >= cutoff][-MOST:]
        _record(contact_id, entry)
        return entry


def entries(contact_id, since=None, until=None):
    """Their diary since `since` (default: all that's kept), oldest first."""
    since = since if since is not None else time.time() - KEEP
    with _lock:
        return [dict(e) for e in _book(contact_id)["entries"]
                if e["at"] >= since and (until is None or e["at"] <= until + 0.5)]


def forget(contact_id=None):
    """Drop what's cached — for a reset, or a test that swapped the data directory."""
    with _lock:
        for key in [k for k in _books if contact_id is None or k[1] == contact_id]:
            _books.pop(key, None)


# --- what observing writes -------------------------------------------------------

def _name(contact_id):
    from ..contacts import directory
    found = directory().get(contact_id)
    return found.name if found else contact_id


def _snapshot(whereabouts, t):
    """How things stand for them at t, in the terms the diary compares: doing, where, who with, going where."""
    block, _company, _firm = whereabouts._situation(t)
    where, company = whereabouts.whereabouts(t)
    if where:
        # The journey there, worked out as of now — then who they're with: nobody, till they've arrived.
        whereabouts.trip(where, whereabouts.spot(where, t), t)
        where, company = whereabouts.whereabouts(t)
    trip = whereabouts.get("trip") or {}
    moving = bool(trip and trip.get("to") == where and len(trip.get("pts") or []) > 1
                  and trip.get("left", trip.get("start", 0)) <= t < trip.get("end", 0))
    return {"doing": (block.get("doing") or "").strip(), "where": where or "", "with": sorted(company),
            "trip": where if moving else "", "from": (trip.get("from") or "") if moving else "",
            "by": (trip.get("by") or "") if moving else "",
            "asleep": block.get("status") == "offline"}


def _their_now(contact_id, t, viewer=None):
    """
    What someone who's just gone is doing now, in a few words: 'on the way to the
    shots fired at …' — or nothing, for a viewer outside the secret when it's the work.
    """
    from ..contacts import directory
    from . import groupchat, presence
    contact = directory().get(contact_id)
    if contact is None:
        return ""
    if viewer and groupchat.outside(viewer) and presence._masked(contact_id, t):
        return ""
    them = presence.of(contact)
    block, _company, _firm = them._situation(t)
    doing = (block.get("doing") or "").strip()
    where, _ = them.whereabouts(t)
    if not getattr(contact, "shares_location", True):
        return doing
    place = them.label(where)
    if doing and place and place.split(",")[0].lower() not in doing.lower():
        return f"{doing} ({place})"
    return doing or (f"at {place}" if place else "")


def observe(whereabouts, t=None):
    """
    Write down whatever's changed for them since last time: set off, got there,
    something new on, moved on, someone with them, someone gone (and where to).
    Only ever about now — a look back at an earlier t writes nothing.
    """
    now = time.time()
    t = t or now
    if abs(t - now) > 90:
        return []
    cid = whereabouts.contact.id
    snap = _snapshot(whereabouts, t)
    with _lock:
        last = dict(_book(cid)["last"])
    made = []

    def put(kind, text, **extra):
        entry = write(cid, kind, text, t, **extra)
        if entry:
            made.append(entry)

    spoken = whereabouts._spoken_place

    def at(doing, where):
        """'working the robbery at Ralli's' — and where, unless that already says it."""
        place = spoken(where)
        if not place or place.split(" (")[0].split(",")[0].lower() in doing.lower():
            return doing or place or ""          # nothing to say is nothing written (it wrote "about")
        return f"{doing} — {place}" if doing else place

    if not last:
        put("doing", at(snap["doing"], snap["where"]) if not snap["trip"] else
            (f"{snap['doing']} — " if snap["doing"] else "") + f"on the way to {spoken(snap['trip'])}")
        if snap["with"]:
            put("with", "with " + " and ".join(_name(c) for c in snap["with"]), who=list(snap["with"]))
    else:
        started = snap["trip"] and snap["trip"] != last.get("trip")
        ended = last.get("trip") and not snap["trip"]
        changed = snap["doing"].lower() != (last.get("doing") or "").lower()
        # What it's for, once — "set off for … — working the shots fired", not a second line saying so.
        why = "" if not changed or not snap["doing"] or snap["doing"].lower().startswith(("on the way", "heading")) \
            else f" — {snap['doing']}"
        if started:
            put("trip", f"set off for {spoken(snap['trip'])}"
                + (f" from {spoken(snap['from'])}" if snap["from"] else "") + (f", {snap['by']}" if snap["by"] else "")
                + why)
        elif ended and snap["where"] == last.get("trip"):
            put("arrived", f"got to {spoken(snap['where'])}" + why)
        elif changed:
            put("doing", (snap["doing"] or "nothing on") if snap["trip"] else at(snap["doing"] or "nothing on",
                                                                                   snap["where"]))
        elif snap["where"] != last.get("where") and not ended and not snap["trip"]:
            put("moved", f"moved on to {spoken(snap['where'])}")
        if snap["asleep"] != bool(last.get("asleep")):
            put("status", "phone off — asleep, or out of reach" if snap["asleep"] else "back on their phone")
        joined = [c for c in snap["with"] if c not in (last.get("with") or [])]
        gone = [c for c in (last.get("with") or []) if c not in snap["with"]]
        if joined:
            put("with", "with " + " and ".join(_name(c) for c in joined), who=joined)
        for other in gone:
            theirs = _their_now(other, t, viewer=cid)
            if started or snap["doing"].lower() != (last.get("doing") or "").lower():
                text = f"headed off from {_name(other)}" + (f", who's now {theirs}" if theirs else "")
            else:
                text = f"{_name(other)} went off" + (f" — {theirs}" if theirs else "")
            put("left", text, who=other)
    if snap != last:
        with _lock:
            _book(cid)["last"] = snap
            store.space("diary_last").put(cid, snap)
    return made


# --- reading it back --------------------------------------------------------------

def parted(contact_id, t=None, within=45 * 60):
    """
    Whoever was with them lately and isn't now — [(contact id, when they parted)],
    latest first: Dick, gone to the shots fired a minute ago.
    """
    t = t or time.time()
    gone = {}
    for e in entries(contact_id, t - within, t):
        if e["kind"] == "left" and e.get("who"):
            gone[e["who"]] = e["at"]
        elif e["kind"] == "with":
            for c in e.get("who") or []:
                gone.pop(c, None)
    return sorted(gone.items(), key=lambda kv: -kv[1])


def summary(contact_id, t=None, hours=3.0, most=6):
    """
    Their last few hours, a line per moment something happened — what happened in
    the same minute said together: '22:44 set off for …; 23:13 got to …, with Dick'.
    """
    t = t or time.time()
    moments = []
    for e in entries(contact_id, t - hours * 3600, t)[-most * 3:]:
        stamp = time.strftime("%H:%M", time.localtime(e["at"]))
        if moments and moments[-1][0] == stamp:
            moments[-1][1].append(e["text"])
        else:
            moments.append((stamp, [e["text"]]))
    return "; ".join(f"{stamp} {', '.join(texts)}" for stamp, texts in moments[-most:])
