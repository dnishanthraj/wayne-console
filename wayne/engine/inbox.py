"""
Bruce's mail: his Wayne Enterprises inbox, and the cave's secure line.

What lands in it is what his life would send him, from what's actually
happened:

- the Batcomputer's night report at six — every case, who was on it and how it
  ended, the scanner's worst, the rogues caught, loose or on a run, who got
  hurt, the patrols — put together from the night itself, not written up;
- the morning's papers, as a briefing;
- Gordon, on the secure line, when there's something to say — a rogue in a
  cell, a bad night with names in it;
- Wayne Enterprises: Lucius, the board, PR, legal — the company's week;
- invitations — the galas, the box at the Knightsdome, Vicki Vale;
- what a night leaves behind: the insurer after a chase through Burnley, a
  thank-you to the Foundation from a family who don't know who to thank,
  Arkham's notice to its benefactors that someone's out;
- and spam, in its own folder.

Written by the model, a few at a time while it's loaded and idle (the night
report is assembled, never written). Kept in the database: a row a message (the
space "mail"), the keys that stop the same thing arriving twice ("mail_keys"),
and what's waiting to be written about ("mail_events").
"""
import datetime
import hashlib
import json
import random
import threading
import time
import uuid

from .. import store

FOLDERS = ("inbox", "secure", "spam", "archive", "sent")
# Mail nobody answers: the machine's report, the papers, the markets, the junk.
_UNANSWERED = {"report", "briefing", "markets", "spam", "sent"}
# How long each takes to write back, in minutes: Oracle at once, Lucius between meetings, Jim after a shift,
# the board's office in its own time — and anyone else in an hour or a day.
_ANSWERS_IN = {"oracle": (4, 30), "lucius": (15, 90), "pr": (30, 240), "foundation": (60, 600), "gordon": (60, 360),
               "board": (120, 1440), "legal": (120, 1440)}
_ANSWERS_DEFAULT = (60, 1200)
KEEP = 400
_lock = threading.Lock()

# Who writes to him, and how they sound.
SENDERS = {
    "batcomputer": ("Batcomputer", "night-report@cave.local", "secure"),
    "gordon": ("James Gordon", "jg@secure.gcpd.local", "secure"),
    "oracle": ("Oracle", "o@clocktower.local", "secure"),
    "lucius": ("Lucius Fox", "lucius.fox@wayne-enterprises.com", "inbox"),
    "board": ("Wayne Enterprises Board Office", "board@wayne-enterprises.com", "inbox"),
    "pr": ("WE Communications", "press@wayne-enterprises.com", "inbox"),
    "legal": ("WE Legal", "legal@wayne-enterprises.com", "inbox"),
    "foundation": ("Wayne Foundation", "office@waynefoundation.org", "inbox"),
    "briefing": ("Morning Briefing", "briefing@wayne-enterprises.com", "inbox"),
}


def _mail():
    return store.space("mail")


def mail(folder=None):
    """His mail, newest first — one folder's, or all of it."""
    found = sorted(_mail().list(), key=lambda m: -m["at"])
    return [m for m in found if folder is None or m["folder"] == folder]


def unread():
    return sum(1 for m in _mail().list() if not m.get("read") and m["folder"] in ("inbox", "secure"))


def _delivered(key):
    return bool(key) and key in store.space("mail_keys")


def deliver(sender, subject, body, folder=None, at=None, name=None, address=None, kind="", key=None,
            thread=None, reply_to=None, extra=None):
    """
    A message arrives. `sender` is one of SENDERS or free text; `key` keeps the
    same thing from arriving twice (the night report for a date, a case's thanks).
    `thread`: the conversation it belongs to, when it answers one of his (see reply).
    """
    known = SENDERS.get(sender)
    with _lock:
        if _delivered(key):
            return None
        message = {"id": uuid.uuid4().hex[:12], "at": at or time.time(),
                   "from": name or (known[0] if known else sender), "address": address or (known[1] if known else ""),
                   "subject": subject.strip()[:160], "body": body.strip()[:6000],
                   "folder": folder or (known[2] if known else "inbox"), "read": False, "kind": kind,
                   "sender": sender}
        message["thread"] = thread or message["id"]
        if reply_to:
            message["reply_to"] = reply_to
        if extra:
            message.update(extra)
        box = _mail()
        with store.get().tx():
            box.put(message["id"], message)
            if len(box) > KEEP:
                # The oldest go — by when they arrived, not when they were filed.
                box.drop(*[m["id"] for m in sorted(box.list(), key=lambda m: m["at"])[:len(box) - KEEP]])
            if key:
                keys = store.space("mail_keys")
                keys.put(key, time.time())
                keys.trim(600)
    return message


def update(message_id, **fields):
    """Read it, file it, bin it — or note how he answered an invitation."""
    with _lock:
        m = _mail().get(message_id)
        if m is None:
            return None
        if fields.get("folder") in FOLDERS:
            m["folder"] = fields["folder"]
        if "read" in fields:
            m["read"] = bool(fields["read"])
        if fields.get("rsvp") in ("accepted", "declined"):
            m["rsvp"] = fields["rsvp"]
        _mail().put(message_id, m)
        return m


def rsvp(message_id, accept, now=None):
    """
    He accepts an invitation, or declines it — and Alfred, who keeps his diary, answers for him in the thread;
    the host writes back in their own time. Returns (the engagement, Alfred's note), or None if it isn't one.
    """
    from . import engagements
    now = now or time.time()
    message = _mail().get(message_id)
    if message is None or not message.get("engagement"):
        return None
    row = engagements.answer(message, accept, now)
    if row is None:
        return None
    update(message_id, rsvp=row["status"])
    with _lock:
        m = _mail().get(message_id)
        if m is not None and m.get("engagement"):
            m["engagement"]["id"] = row["id"]           # the letter knows its evening: Go, from the letter
            _mail().put(message_id, m)
    when_ = time.strftime("%A %-d %B at %-I:%M %p", time.localtime(row["at"]))
    body = ((f"Mr. Wayne thanks you for your kind invitation and accepts with pleasure — {when_}" +
             (f", {row['where']}" if row["where"] else "") + ".") if accept else
            "Mr. Wayne thanks you for your kind invitation, and regrets that a prior engagement prevents him from "
            "attending.") + "\n\nWith his compliments,\n\nA. Pennyworth\nfor Bruce Wayne"
    thread_id = message.get("thread") or message["id"]
    note = {"id": uuid.uuid4().hex[:12], "at": now, "sender": "me", "from": "Alfred Pennyworth, for Bruce Wayne",
            "address": "household@waynemanor.com", "to": message["from"], "to_address": message.get("address", ""),
            "subject": ("Re: " + message["subject"])[:160] if not message["subject"].lower().startswith("re:")
            else message["subject"], "body": body, "folder": "sent", "read": True, "kind": "sent",
            "thread": thread_id, "reply_to": message_id}
    with _lock:
        _mail().put(note["id"], note)
    if accept:
        lo, hi = _ANSWERS_DEFAULT
        queue({"kind": "reply", "id": note["id"], "thread": thread_id, "due": now + random.uniform(lo, hi) * 60})
    return row, note


def delete(message_id):
    with _lock:
        _mail().drop(message_id)


def thread(thread_id):
    """A conversation, oldest first: what they wrote, and what he wrote back."""
    return sorted((m for m in _mail().list() if (m.get("thread") or m["id"]) == thread_id), key=lambda m: m["at"])


def answerable(message):
    """Whether a letter's one he can answer — not the machine's report, the papers, the markets or the junk."""
    return bool(message) and message.get("kind") not in _UNANSWERED and message.get("folder") not in ("spam", "sent")


def reply(message_id, body, now=None):
    """
    He answers a letter. His reply goes in Sent, in its thread — from Bruce Wayne, or from "B" on the secure
    line — and whoever wrote writes back in their own time (write_one): Oracle at once, Lucius between meetings,
    Jim after a shift. Returns his message, or None if it isn't one to answer.
    """
    now = now or time.time()
    original = _mail().get(message_id)
    body = (body or "").strip()
    if original is None or not body or not answerable(original):
        return None
    thread_id = original.get("thread") or original["id"]
    secure = original.get("folder") == "secure"
    subject = original["subject"] if original["subject"].lower().startswith("re:") else "Re: " + original["subject"]
    mine = {"id": uuid.uuid4().hex[:12], "at": now, "sender": "me",
            "from": "B" if secure else "Bruce Wayne",
            "address": "b@cave.local" if secure else "bruce.wayne@wayne-enterprises.com",
            "to": original["from"], "to_address": original.get("address", ""), "subject": subject[:160],
            "body": body[:6000], "folder": "sent", "read": True, "kind": "sent", "thread": thread_id,
            "reply_to": message_id, "secure": secure}
    with _lock:
        _mail().put(mine["id"], mine)
    lo, hi = _ANSWERS_IN.get(original.get("sender"), _ANSWERS_DEFAULT)
    queue({"kind": "reply", "id": mine["id"], "thread": thread_id, "due": now + random.uniform(lo, hi) * 60})
    return mine


# How much news waits to be written about, at most — the oldest goes first when there's more.
QUEUE = 41


def queue(event):
    """
    Something happened that'll bring mail: a case ended, someone broke out. Written when the model's free. Only so
    much waits: the oldest news goes first — never a reply he's owed, which a night of busy cases used to push out.
    """
    with _lock:
        waiting = store.space("mail_events")
        waiting.put(uuid.uuid4().hex[:12], {**event, "at": time.time()})
        items = waiting.items()
        over = len(items) - QUEUE
        if over > 0:
            spare = [key for key, e in items if e.get("kind") != "reply"]
            waiting.drop(*spare[:over])


def suggest(message_id, model, options, now=None):
    """
    A few ways he might answer a letter — each a kind of answer and a draft of it, in his voice (or, on the
    secure line, Batman's): [{"kind", "body"}]. Drafts to pick from and change, never sent for him.
    """
    now = now or time.time()
    original = _mail().get(message_id)
    if original is None or not answerable(original):
        return []
    convo = thread(original.get("thread") or original["id"])[-4:]
    batman = original.get("sender") == "gordon"
    him = "Batman (signing 'B')" if batman else "Bruce Wayne — charming in public, sharp underneath"
    lines = "\n\n".join(f"— {m['from']}:\n{m['body'][:1200]}" for m in convo)
    found = _ask(model, options, _context(now, secure=original.get("folder") == "secure")
                 + f"Here is an email conversation:\n\n{lines}\n\nSuggest three different ways {him} might answer the "
                 "last letter — really different in what they do (say yes, say no, put it off, flirt, deflect, ask "
                 "something back, get serious), each short and in his voice, as he'd actually write it. JSON only: "
                 '{"options": [{"kind": "two or three words for what it does", "body": "the email"}]}',
                 "reply suggestions")
    picks = (found or {}).get("options") if isinstance((found or {}).get("options"), list) else []
    return [{"kind": str(p.get("kind") or "")[:40], "body": str(p.get("body") or "").strip()[:2000]}
            for p in picks[:3] if isinstance(p, dict) and str(p.get("body") or "").strip()]


def call_off(row, now=None):
    """
    He's called off an evening or a meeting from his diary: a note in its thread — Alfred's regrets for Bruce
    Wayne; for Batman, "B" — and an answer from them in their own time. Returns the note, or None.
    """
    now = now or time.time()
    convo = thread(row.get("thread", ""))
    theirs = next((m for m in reversed(convo) if m.get("folder") != "sent"), None)
    if theirs is None:
        return None
    batman = row.get("as") == "batman"
    body = ("Can't make it. Another time.\n\nB" if batman else
            f"Mr. Wayne asks me to send his sincere apologies: something has come up, and he will not be able to "
            f"make {row['what']}" + (f" at {row['where']}" if row.get("where") else "") + ". He hopes you'll forgive "
            "him.\n\nA. Pennyworth\nfor Bruce Wayne")
    note = {"id": uuid.uuid4().hex[:12], "at": now, "sender": "me",
            "from": "B" if batman else "Alfred Pennyworth, for Bruce Wayne",
            "address": "b@cave.local" if batman else "household@waynemanor.com", "to": theirs["from"],
            "to_address": theirs.get("address", ""), "subject": theirs["subject"] if theirs["subject"].lower()
            .startswith("re:") else "Re: " + theirs["subject"], "body": body, "folder": "sent", "read": True,
            "kind": "sent", "thread": row["thread"], "reply_to": theirs["id"]}
    with _lock:
        _mail().put(note["id"], note)
    lo, hi = _ANSWERS_IN.get(theirs.get("sender"), _ANSWERS_DEFAULT)
    queue({"kind": "reply", "id": note["id"], "thread": row["thread"], "due": now + random.uniform(lo, hi) * 60})
    return note


def _take_event(now=None):
    """
    The oldest event waiting that's due — looked at, not taken: it's only gone once its mail is (see
    _done_event). A reply he's owed isn't due till they'd have written it.
    """
    now = now or time.time()
    with _lock:
        events = [e for e in store.space("mail_events").list() if e.get("due", 0) <= now]
        return dict(events[0]) if events else None


def _done_event(event, tried=False):
    """
    An event dealt with: its mail written, or tried twice and given up on. Taken off the queue before the
    model ran, a failed write lost the case's mail for good.
    """
    with _lock:
        waiting = store.space("mail_events")
        for key, e in waiting.items():
            if e.get("id") == event.get("id") and e.get("kind") == event.get("kind"):
                if tried and not e.get("tried"):
                    waiting.put(key, {**e, "tried": True})        # one more go, later
                else:
                    waiting.drop(key)
                break


# --- the night report: assembled, not written --------------------------------------

def night_report(day=None, now=None):
    """
    The Batcomputer's report on last night — six in the evening to six this
    morning — from the record itself: cases, the scanner, rogues, the hurt, the
    patrols. (subject, body).
    """
    from ..contacts import directory
    from . import arcs, cases, codex, incidents, patrols
    now = now or time.time()
    day = day or datetime.date.fromtimestamp(now)
    end = time.mktime((day.year, day.month, day.day, 6, 0, 0, 0, 0, -1))
    start = end - 12 * 3600
    book = directory()
    names = {c.id: c.name for c in book} | {"bruce": "you"}
    # What he'd know of: Jason working something on his own is Jason's business, here as on the board.
    worked = [c for c in cases.everything() if start <= c.get("opened_at", 0) < end and cases.known(c)]
    lines = ["# Night report", f"**{datetime.date.fromtimestamp(start).strftime('%A %-d %B')} into "
             f"{day.strftime('%A %-d %B %Y')}**, 18:00–06:00", ""]
    lines.append(f"## Cases ({len(worked)})")
    if not worked:
        lines.append("None worked.")
    for c in sorted(worked, key=lambda c: c.get("opened_at", 0)):
        who = ", ".join(names.get(m, m) for m in cases.team(c)) or "—"
        res = c.get("result") or {}
        how = (f"{res.get('grade', '')} — {res.get('how', '')}".strip(" —") if res
               else "still open" if c["status"] != "closed" else "closed")
        when = time.strftime("%H:%M", time.localtime(c.get("opened_at", start)))
        lines.append(f"- **{when} · {c['kind']}** — {c['place']} ({c['area']})  ")
        lines.append(f"  Team: {who}. Outcome: **{how}**." + (f" Behind it: {c['suspect']}." if c.get("suspect") else "")
                     + (f"  \n  > {c['outcome']}" if c.get("outcome") else ""))
    hurt = [(m, i) for c in worked for m, i in ((c.get("result") or {}).get("hurt") or {}).items()]
    lines += ["", "## Injuries"] + ([f"- {names.get(m, m).capitalize()}: {i}." for m, i in hurt] or ["None reported."])
    seen = {}
    for hours in range(0, 12):
        for r in incidents.at(start + hours * 3600 + 1800):
            seen[r["id"]] = r
    worst = sorted(seen.values(), key=lambda r: (-r["severity"], -(r.get("toll") or {}).get("dead", 0)))[:6]
    lines += ["", "## The scanner", f"{len(seen)} calls; the worst:"]
    for r in worst:
        toll = incidents.toll_text(r.get("toll") or {})
        lines.append(f"- {time.strftime('%H:%M', time.localtime(r['at']))} · **{r['kind']}** — {r['place']} ({r['area']})"
                     + (f", {toll}" if toll else "") + (f" — {r['suspect']}" if r.get("suspect") else ""))
    rogue_lines = []
    for rogue in incidents.rogues():
        where = codex.where(rogue, end)
        if where.get("how") and start <= (where.get("since") or 0) < end:
            rogue_lines.append(f"- **{rogue['name']}**: {where['how']}.")
    rogue_lines += [f"- {line}." for line in arcs.lines(end)]
    lines += ["", "## Rogues"] + (rogue_lines or ["No change."])
    covered = {}
    for watch_t in range(int(start + 3 * 3600), int(end), int(patrols.WATCH)):
        for cid, on in patrols.roster(watch_t).items():
            covered.setdefault(names.get(cid, cid), []).append(on["sector"].split(",")[0])
    lines += ["", "## Patrols"] + ([f"- **{who}**: {', '.join(dict.fromkeys(areas))}." for who, areas in covered.items()]
                                   or ["Nobody out."])
    good = sum(1 for c in worked if (c.get("result") or {}).get("grade") == "success")
    partial = sum(1 for c in worked if (c.get("result") or {}).get("grade") == "partial")
    subject = (f"Night report — {len(worked)} case{'s' if len(worked) != 1 else ''}, {good} closed well"
               + (f", {partial} partly" if partial else "")
               + (f", {len(hurt)} hurt" if hurt else ""))
    return subject, "\n".join(lines)


def write_night_report(now=None):
    """Six in the morning: last night's report, once."""
    now = now or time.time()
    day = datetime.date.fromtimestamp(now)
    if time.localtime(now).tm_hour < 6:
        return None
    key = f"night:{day.isoformat()}"
    if _delivered(key):
        return None
    subject, body = night_report(day, now)
    at = time.mktime((day.year, day.month, day.day, 6, 0, 0, 0, 0, -1))
    return deliver("batcomputer", subject, body, at=at, kind="report", key=key)


def write_briefing(now=None):
    """The morning papers, as a briefing — once the day's are out."""
    from . import gazette
    now = now or time.time()
    day = datetime.date.fromtimestamp(now)
    if time.localtime(now).tm_hour < 7 or not gazette.written(day):
        return None
    items = gazette.today(day)
    if not items:
        return None
    body = "\n\n".join(f"### {i['headline']}\n*{i['outlet']}{' — ' + i['by'] if i['by'] and i['by'] != i['outlet'] else ''}*"
                       f"\n\n{i['dek']}" for i in items)
    # It came at seven, whenever he's reading it.
    seven = time.mktime((day.year, day.month, day.day, 7, 0, 0, 0, 0, -1))
    return deliver("briefing", f"Your morning briefing — {day.strftime('%A %-d %B')}",
                   "Good morning. Gotham today, as the papers and the channels have it.\n\n---\n\n" + body,
                   kind="briefing", key=f"briefing:{day.isoformat()}", at=min(now, seven) if now >= seven else now)


def write_market_close(now=None):
    """The market's close, from Investor Relations, on a trading day after half past four."""
    from . import markets
    now = now or time.time()
    day = datetime.date.fromtimestamp(now)
    local = time.localtime(now)
    if not markets.trading(day) or (local.tm_hour, local.tm_min) < (16, 35):
        return None
    key = f"market:{day.isoformat()}"
    if _delivered(key):
        return None
    subject, body = markets.report(day)
    at = time.mktime((day.year, day.month, day.day, 16, 35, 0, 0, 0, -1))
    return deliver("Wayne Enterprises Investor Relations", subject, body, at=at, kind="markets", key=key,
                   name="WE Investor Relations", address="ir@wayne-enterprises.com")


# --- written by the model ------------------------------------------------------------

def _draw(*parts):
    return int(hashlib.sha1(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _ask(model, options, instruction, purpose):
    from . import model as llm
    try:
        reply = llm.ask(model, [{"role": "user", "content": instruction}],
                        {**options, "temperature": 0.85, "num_predict": 700}, fmt="json", purpose=purpose)
        found = json.loads(reply)
    except Exception:
        return None
    return found if isinstance(found, dict) else None


_MAIL_SHAPE = ('Return JSON only: {"from_name": "...", "from_address": "...", "subject": "...", '
               '"body": "the email itself, paragraphs separated by blank lines, signed off as they would — light '
               'markdown where a real email would have it: **bold** for a date or a figure, a short - list, a > quote; '
               'headings only in a newsletter or a formal notice"}')


# The landmarks an evening is held at, besides the restaurants and hotels on the map.
_HALLS = ("Gotham Opera House", "Gotham Museum", "The Gotham Plaza", "Knightsdome", "Wayne Tower", "City Hall",
          "Gotham Public Library")


def _real_places(rng=random, most=9):
    """A few real places on the map to name — so an evening, a dinner, a meeting has somewhere he can actually go."""
    from . import places
    venues = [v for v in places.venues() if v.get("kind") in ("restaurant", "hotel", "bar", "club", "cafe")]
    picks = rng.sample(venues, min(most, len(venues))) if venues else []
    return ("If it names a place, use one of these real ones, by its exact name: "
            + "; ".join([f"{v['name']} ({v['area']})" for v in picks] + list(_HALLS)) + ". ")


def _context(now, secure=False):
    """
    The day as the mail knows it. On the secure line — Jim writing to Batman — nothing of Bruce Wayne: Jim
    doesn't know who he's writing to.
    """
    from . import gazette
    day = datetime.date.fromtimestamp(now)
    papers = "; ".join(f"“{i['headline']}”" for i in (gazette.today(day) or [])[:4])
    who = ("" if secure else "Bruce Wayne is CEO-in-name of Wayne Enterprises and its public face (the city thinks "
           "him a charming, unserious billionaire). ")
    from . import engagements
    lately = "" if secure else engagements.lately(now)
    if not secure:
        word, said = engagements.repute()
        if said:
            lately += f"Society's view of Bruce Wayne lately: {word} ({said}). "
    return (f"It's {day.strftime('%A %-d %B %Y')}, {time.strftime('%H:%M', time.localtime(now))}, in Gotham City. "
            + who + lately + (f"This morning's papers: {papers}. " if papers else ""))


def write_one(model, options, now=None):
    """
    One piece of mail, if any's due: an event's first (a capture, a bad night, a
    breakout, a chase's damage, a rescue), then the company's day, an
    invitation, the spam. Blocking; run while the model's idle.
    """
    now = now or time.time()
    event = _take_event(now)
    if event:
        made = _from_event(model, options, event, now)
        _done_event(event, tried=not made)
        return made
    hour = time.localtime(now).tm_hour
    day = datetime.date.fromtimestamp(now).isoformat()
    sent = [m for m in mail() if datetime.date.fromtimestamp(m["at"]).isoformat() == day]
    company = [m for m in sent if m.get("kind") == "company"]
    weekend = datetime.date.fromtimestamp(now).weekday() >= 5
    if 8 <= hour < 19 and len(company) < (1 if weekend else 3) and _draw("company", day, len(company)) < (0.15 if weekend else 0.5):
        return _company(model, options, now)
    letters = [m for m in sent if m.get("kind") == "letter" and m.get("sender") in CORRESPONDENTS]
    if 8 <= hour < 22 and len(letters) < (1 if weekend else 2) and _draw("letter", day, len(letters), hour) < 0.35:
        made = _correspondent(model, options, now)
        if made:
            return made
    invites = [m for m in mail() if m.get("kind") == "invitation" and now - m["at"] < 2 * 86400]
    if 9 <= hour < 21 and not invites and _draw("invite", day) < 0.6:
        return _invitation(model, options, now)
    spam = [m for m in sent if m.get("kind") == "spam"]
    if len(spam) < 4 and _draw("spam", day, len(spam), hour) < 0.35:
        return _spam(model, options, now)
    return None


def _company(model, options, now):
    which = random.choice(["lucius", "lucius", "board", "pr", "legal", "foundation"])
    name, address, folder = SENDERS[which]
    who = {"lucius": "Lucius Fox, Wayne Enterprises' president and CEO — dry, exact, quietly amused by Bruce, and "
                     "the one who keeps the company running (R&D, Applied Sciences, acquisitions, the books)",
           "board": "the board office's secretary — formal, calendar-driven: meetings, votes, minutes, a director's grumbling",
           "pr": "WE Communications — the PR team managing Bruce Wayne's image, the press, interview requests (Vicki Vale), "
                 "the Globe's latest about him",
           "legal": "WE Legal — careful, clause-heavy: contracts, a lawsuit, a settlement, signatures needed",
           "foundation": "the Wayne Foundation's director — the charity: the Narrows children's centre, grants, the "
                         "Thomas Wayne Memorial Clinic, a gala to plan"}[which]
    found = _ask(model, options, _context(now) + f"Write one email to Bruce Wayne from {who}, about something real in "
                 "the company's or the city's week — specific names, numbers, a decision he's needed for — not about "
                 "the vigilantes. It reads like real corporate mail." + _FACTS_ONLY + " " + _MAIL_SHAPE, "a company email")
    if not found or not found.get("subject"):
        return None
    return deliver(which, found["subject"], found.get("body", ""), folder=folder, kind="company",
                   name=name, address=address)


def _invitation(model, options, now):
    soon = datetime.date.fromtimestamp(now)
    found = _ask(model, options, _context(now) + "Write one invitation to Bruce Wayne — a gala, a charity auction, a "
                 "box at the Knightsdome, the mayor's fundraiser, a gallery opening on Museum Mile, a dinner Vicki Vale "
                 "insists on, an old Gotham family's anniversary. From whoever would send it, with the date (within the "
                 f"next two weeks, after {soon.isoformat()}), the time, the place, the dress, an RSVP. " + _real_places()
                 + _who() + _FACTS_ONLY + " " + _MAIL_SHAPE[:-1]
                 + ', "event": {"what": "the occasion, in a few words", "where": "the place", "date": "YYYY-MM-DD", '
                 '"time": "HH:MM, 24-hour", "dress": "black tie, cocktail…", "host": "who\'s hosting"}}', "an invitation")
    if not found or not found.get("subject"):
        return None
    from . import engagements
    event = found.get("event") if isinstance(found.get("event"), dict) else None
    extra = {"engagement": {k: str(event.get(k) or "")[:120] for k in ("what", "where", "date", "time", "dress", "host")}} \
        if event and engagements.when_from(event, now) else None
    return deliver(found.get("from_name") or "Invitation", found["subject"], found.get("body", ""), folder="inbox",
                   kind="invitation", name=found.get("from_name"), address=found.get("from_address"), extra=extra)


def _engagement_letter(model, options, event, now):
    """
    The evening's over: the host's note after it — glad he came, or the empty chair noticed — and, now and then
    when he didn't come, Vicki Vale wondering where Gotham's most eligible man was this time.
    """
    if event.get("kind") != "engagement":
        return None
    from . import engagements
    went = event.get("status") in ("attended", "left")
    early = event.get("status") == "left"
    convo = thread(event.get("thread", ""))
    theirs = next((m for m in convo if m.get("folder") != "sent"), None)
    host = event.get("host") or (theirs or {}).get("from") or "the host"
    if event.get("as") == "batman":
        # Batman's meeting, on the secure line: Jim, after it — or after waiting on a roof for nobody.
        found = _ask(model, options, _context(now, secure=True) + f"Write one short email from {host} to Batman on their "
                     f"secure line, after {event.get('what')}" + (f" at {event['where']}" if event.get("where") else "")
                     + ": " + ("Batman came; one line on what they settled, what Jim will do now." if went else
                               "Batman never showed; Jim waited. He writes as he would — tired, angry, or worried.")
                     + _FACTS_ONLY + " " + _MAIL_SHAPE, "Jim, after")
        if found and found.get("subject"):
            return deliver(theirs.get("sender") if theirs else "gordon", found["subject"], found.get("body", ""),
                           folder="secure", kind="gordon", name=host, address=(theirs or {}).get("address"),
                           thread=event.get("thread"), key=f"after:{event.get('id')}")
        return None
    story = ""
    if went:
        # The evening itself, once: who he met, what was said — something that leads somewhere.
        evening = _ask(model, options, _context(now) + f"Bruce Wayne went to {event.get('what')}"
                       + (f" at {event['where']}" if event.get("where") else "") + f", hosted by {host}. In a line or two: "
                       "what happened there that would lead somewhere — who cornered him, what was asked of him, a "
                       "rumour, a favour, a rival's move, a spark with someone." + _who() + " JSON only: "
                       '{"story": "..."}', "the evening")
        story = str((evening or {}).get("story") or "").strip()
        if story:
            engagements.remember(event.get("id"), story)
    if went:
        how = ("he came — a thank-you, warm or formal as they are"
               + (f", touching on what happened: {story}" if story else ", with one specific thing about the evening")
               + (" — though he slipped out early, and they noticed; they say so as they would." if early else "."))
    else:
        how = ("he accepted and never came — his chair empty all evening; they write as they would: gracious, "
               "wounded, pointed or amused.")
    found = _ask(model, options, _context(now) + f"Write one short email to Bruce Wayne from {host}, the morning after "
                 f"{event.get('what')}" + (f" at {event['where']}" if event.get("where") else "") + ": " + how
                 + _FACTS_ONLY + " " + _MAIL_SHAPE, "an after-note")
    made = None
    if found and found.get("subject"):
        made = deliver(host, found["subject"], found.get("body", ""), folder="inbox", kind="letter", name=host,
                       address=(theirs or {}).get("address"), thread=event.get("thread"), key=f"after:{event.get('id')}")
    if (not went or early) and "vicki" not in host.lower() and _draw("vicki", event.get("id")) < 0.4:
        nosy = _ask(model, options, _context(now) + "Write one short, teasing email to Bruce Wayne from Vicki Vale, the "
                    "Gotham Gazette's investigative reporter (they dated once; she still suspects him of something): "
                    + (f"he slipped out of {event.get('what')} early last night" if early else
                       f"he stood up {event.get('what')} last night")
                    + " and she wants to know where he went — flirtatious, sharp, fishing." + _FACTS_ONLY + " "
                    + _MAIL_SHAPE, "Vicki's note")
        if nosy and nosy.get("subject"):
            made = deliver("Vicki Vale", nosy["subject"], nosy.get("body", ""), folder="inbox", kind="letter",
                           name="Vicki Vale", address="vvale@gothamgazette.com", key=f"vicki-after:{event.get('id')}") or made
    return made


def _spam(model, options, now):
    found = _ask(model, options, _context(now) + "Write one piece of spam that lands in Bruce Wayne's junk folder — "
                 "Gotham-flavoured: a crypto scheme, a 'Wayne heir' scam, cheap Iceberg Lounge VIP passes, a "
                 "too-good yacht charter, an Arkham-surplus auction, a phishing 'WE IT security notice'. Badly "
                 "written in the way spam is. " + _MAIL_SHAPE, "spam")
    if not found or not found.get("subject"):
        return None
    return deliver(found.get("from_name") or "Unknown", found["subject"], found.get("body", ""), folder="spam",
                   kind="spam", name=found.get("from_name"), address=found.get("from_address"))


def _rogue_facts(name):
    """Who a rogue really is and where they'd be held, so a letter about them gets it right."""
    if not name:
        return ""
    from . import codex, incidents
    entry = next((g for g in incidents.rogues() if g["name"] == name), None)
    if entry is None:
        return f" ({name})"
    asylum = (codex._data()["rogues"].get(name) or {}).get("asylum")
    where = codex.where(entry)
    held = where.get("held") or ""
    return (f" ({name}" + (f", real name {entry['real']}" if entry.get("real") else "")
            + (f"; held now at {held}" if held and where.get("status") in ("in custody", "locked up") else "")
            + f"; goes to {'Arkham Asylum' if asylum else 'Blackgate Penitentiary'} once GCPD transfer them)")


# Who they are when they write back — the company's people as _company has them, Jim and Barbara on the secure
# line; anyone else, as they wrote before.
_VOICES = {
    "lucius": "Lucius Fox, Wayne Enterprises' president and CEO — dry, exact, quietly amused by Bruce",
    "board": "the board office's secretary — formal, calendar-driven",
    "pr": "WE Communications, the PR team that manages Bruce Wayne's image — brisk, a little exasperated",
    "legal": "WE Legal — careful, clause-heavy, never committal",
    "foundation": "the Wayne Foundation's director — warm, practical, always a cause in hand",
    "gordon": "Commissioner James Gordon, on the secure line to Batman — weary, decent, few words, signed 'Jim' or "
              "'G'; he doesn't know who Batman is",
    "oracle": "Oracle — Barbara Gordon at the Clocktower: fast, exact, dry, never his assistant",
}


# The people who write to him first, not only back: who they are to him and what they'd write about — and how
# often, roughly, against one another. Names and roles from the Codex (codex.json, "people").
CORRESPONDENTS = {
    "vicki": ("Vicki Vale", "vvale@gothamgazette.com", 3,
              "the Gotham Gazette's investigative reporter — they dated once; flirtatious, sharp, always fishing: a "
              "story she's chasing, a dinner, a question about where he really goes at night"),
    "evelyn": ("Evelyn Adams", "eadams@gothamgazette.com", 2,
               "the Gazette's crime reporter — terse, relentless, owes nobody: she wants Wayne Enterprises' comment on "
               "something on her desk (a crime touching WE property, the Narrows rebuilding, a security contract)"),
    "leslie": ("Dr. Leslie Thompkins", "leslie@parkrowclinic.org", 2,
               "who held him the night his parents died and runs the free clinic on Park Row — warm, blunt, "
               "motherly: the clinic needs something, a patient's story, or she's worried about him and says so"),
    "clark": ("Clark Kent", "ckent@dailyplanet.com", 2,
              "his closest friend outside Gotham, a reporter at the Daily Planet in Metropolis — easy, warm, wry: "
              "checking in, a story he's on, a visit, a dig at Bruce's public life"),
    "nakano": ("Office of Mayor Christopher Nakano", "mayor@gotham.gov", 1,
               "the mayor's office — law-and-order, up for re-election in November: courting Wayne Enterprises' "
               "support and money, pushing the drone programme, wanting Bruce at an event"),
    "grange": ("Marion Grange", "marion@grangeformayor.com", 1,
               "the councilwoman running for mayor against Nakano — housing, police reform, the Narrows rebuilding "
               "money: she wants his backing, or a straight answer"),
    "vandorn": ("Janet Van Dorn", "jvandorn@gothamda.gov", 1,
                "the District Attorney — formal and ambitious: a case that touches Wayne Enterprises, a witness, a "
                "charity board seat, a favour framed as civic duty"),
    "kate": ("Kate Kane", "kate.kane@kanefamilytrust.org", 1,
             "his cousin on his mother's side — dry, guarded, family business at arm's length: the Kane trust, a "
             "memorial, an aunt's birthday nobody wants to organise"),
    "knox": ("Alexander Knox", "aknox@gothamgazette.com", 1,
             "the Gazette's money-follower — on the Narrows audit and Daggett's contracts this month: he wants Wayne "
             "Enterprises' side before he prints"),
    "saint": ("Simon Saint", "office@saintindustries.com", 1,
              "CEO of Saint Industries — smooth and predatory: a partnership pitch, the drone contracts, the mayor's "
              "ear, a veiled reminder of who's winning"),
    "daggett": ("Roland Daggett", "rdaggett@daggettindustries.com", 1,
                "Daggett Industries' chairman — glad-handing: the Narrows rebuild, a joint venture, a "
                "'misunderstanding' he'd like smoothed over"),
    "tanya": ("Tanya Fox", "tanya@foxfamily.net", 1,
              "Lucius's wife — fond, teasing, formidable: dinner at theirs, a word about working Lucius too hard"),
}
_VOICES.update({cid: f"{entry[0]}, {entry[3]}" for cid, entry in CORRESPONDENTS.items()})


def _press():
    """What's really in the news this week — the tech, business and science Lucius follows, the policy Barbara does:
    searched, never made up (engine.culture) — for letters that would touch it. '' when nothing's been read."""
    from ..contacts import directory
    from . import culture
    book = directory()
    items = []
    for cid in ("lucius", "batgirl"):
        contact = book.get(cid)
        if contact is not None:
            items += [i for i in culture.seen(contact) if not i.startswith(("[film]", "[show]", "[game]", "[music]"))][:3]
    return ("In the real news this week (only if it fits what they'd write; never invent beyond it): "
            + "; ".join(items[:5]) + ". ") if items else ""


def _correspondent(model, options, now, rng=random):
    """
    Someone who'd write to Bruce Wayne writes, first: about their own life and Gotham's week — in their own
    voice, picking up where they last left off with him, if they have.
    """
    recent = {m.get("sender") for m in mail() if now - m["at"] < 2 * 86400}
    pool = [(cid, entry) for cid, entry in CORRESPONDENTS.items() if cid not in recent]
    if not pool:
        return None
    cid, (name, address, _, who) = rng.choices(pool, weights=[e[2] for _, e in pool])[0]
    before = [m for m in mail() if m.get("sender") == cid or (m.get("folder") == "sent" and m.get("to") == name)]
    before = sorted(before, key=lambda m: m["at"])[-3:]
    history = ("The last time you wrote to each other:\n" + "\n".join(
        f"— {m['from']}: {m['subject']} — {m['body'][:240]}" for m in before) + "\n\n") if before else ""
    found = _ask(model, options, _context(now) + _press() + history
                 + f"Write one email to Bruce Wayne from {name}, {who}. About something specific and real in their "
                 "life or Gotham's week that they'd actually write to him about — never the vigilantes as if he were "
                 "one of them. " + _real_places() + _who() + _FACTS_ONLY + " " + _MAIL_SHAPE, f"a letter from {name}")
    if not found or not found.get("subject"):
        return None
    return deliver(cid, found["subject"], found.get("body", ""), folder="inbox", kind="letter", name=name,
                   address=address)


def _reply_letter(model, options, event, now):
    """
    They write back to what he wrote — in the thread, as themselves, answering what he actually said. Nothing's
    owed if they've written again since.
    """
    if event.get("kind") != "reply":
        return None
    convo = thread(event.get("thread", ""))
    theirs = next((m for m in reversed(convo) if m.get("folder") != "sent"), None)
    if not convo or theirs is None or convo[-1].get("folder") != "sent":
        return None
    secure = theirs.get("folder") == "secure"
    him = "Batman" if theirs.get("sender") == "gordon" else "Bruce Wayne"
    voice = _VOICES.get(theirs.get("sender")) or f"{theirs['from']}, writing as they did before"
    lines = "\n\n".join(f"— {m['from']} ({time.strftime('%a %-d %b, %H:%M', time.localtime(m['at']))}):\n"
                         f"{m['body'][:1200]}" for m in convo[-6:])
    found = _ask(model, options, _context(now, secure=secure)
                 + f"Here is an email conversation between {him} and {theirs['from']}:\n\n{lines}\n\n"
                 f"Write {theirs['from']}'s reply to {him}'s last email, as {voice}. Answer what he actually wrote, "
                 "keep to everything said above, and never put words in his mouth. If it needs no more than a line, "
                 "a line is the reply. " + ("" if secure else _real_places()) + _FACTS_ONLY + " " + _MAIL_SHAPE,
                 f"a reply from {theirs['from']}")
    if not found or not str(found.get("body") or "").strip():
        return None
    subject = theirs["subject"] if theirs["subject"].lower().startswith("re:") else "Re: " + theirs["subject"]
    made = deliver(theirs.get("sender") or theirs["from"], subject, found["body"],
                   folder=theirs["folder"] if theirs["folder"] in ("inbox", "secure") else "inbox",
                   kind=theirs.get("kind") or "letter", name=theirs["from"], address=theirs.get("address"),
                   thread=event["thread"], reply_to=convo[-1]["id"], key=f"reply:{event.get('id')}")
    if made:
        _agreed(model, options, made, him, secure, now)
    return made


def _agreed(model, options, latest, him, secure, now):
    """
    Whether the thread has just settled a meeting — a place and a day and time, both of them saying yes — and if so,
    it's in the diary (engine.engagements): Bruce Wayne's dinner, or Batman's roof. Marked on their letter, so the
    letter says so.
    """
    from . import engagements
    convo = thread(latest.get("thread") or latest["id"])[-4:]
    lines = "\n\n".join(f"— {m['from']}:\n{m['body'][:1000]}" for m in convo)
    found = _ask(model, options, _context(now, secure=secure)
                 + f"Here is the end of an email conversation between {him} and {latest['from']}:\n\n{lines}\n\n"
                 f"Have they just agreed to meet — a particular place and a day and time, {him} and {latest['from']} both "
                 "saying yes (or one proposing and the other accepting)? A maybe, a 'some time', or no time is no. JSON "
                 'only: {"meeting": {"what": "what it is, in a few words — dinner with Vicki Vale", "where": "the place", '
                 '"date": "YYYY-MM-DD", "time": "HH:MM, 24-hour"} or null}', "a meeting agreed")
    meeting = (found or {}).get("meeting") if isinstance((found or {}).get("meeting"), dict) else None
    at = engagements.when_from(meeting, now) if meeting else None
    if at is None or at < now:
        return None
    row = engagements.agree(meeting.get("what") or f"meeting {latest['from']}", meeting.get("where") or "", at,
                            latest["from"], latest.get("thread") or latest["id"],
                            as_="batman" if latest.get("sender") == "gordon" else "bruce", message=latest["id"], now=now)
    with _lock:
        m = _mail().get(latest["id"])
        if m is not None:
            day = datetime.date.fromtimestamp(at)
            m["engagement"] = {"id": row["id"], "what": row["what"], "where": row["where"], "date": day.isoformat(),
                               "time": time.strftime("%H:%M", time.localtime(at)), "dress": "", "host": latest["from"],
                               "as": row["as"]}
            m["rsvp"] = "accepted"
            _mail().put(m["id"], m)
    return row


def _from_event(model, options, event, now):
    """What a night leaves in his mail — every letter it would bring, not just the first — or a reply he's owed."""
    if event.get("kind") == "reply":
        return _reply_letter(model, options, event, now)
    if event.get("kind") == "engagement":
        return _engagement_letter(model, options, event, now)
    made = None
    for writer in (_gordon_letter, _thanks_letter, _claim_letter, _arkham_letter):
        got = writer(model, options, event, now)
        made = got or made
    return made


_FACTS_ONLY = (" Use only the names given here for anyone real; never invent a name for a rogue, a victim or an "
               "officer. No 'URGENT' in the subject unless it truly is.")


def _who():
    """Who's who in Gotham, for anything that names its people (engine.codex.who_is_who) — '' if it can't be read."""
    from . import codex
    try:
        return " " + codex.who_is_who()
    except Exception:
        return ""


def _standing_with_gcpd():
    """How the department, and Jim, feel about the masks right now — for his letters to carry (see engine.bonds)."""
    from . import bonds
    dept = bonds.standing(bonds.GCPD, bonds.ME)[0]
    jim, sore, _ = bonds.standing("gordon", bonds.ME)
    lately = "; ".join(e["text"] for e in bonds.memories(bonds.GCPD, bonds.ME, most=2))
    line = (f" How GCPD sees the masks right now: {dept}" + (f" (lately: {lately})" if lately else "")
            + f"; how Jim feels toward Batman: {jim}" + (f", and he's {sore}" if sore else "") + ". Let it show the way "
            "Jim would: ")
    if dept in ("hostile", "estranged"):
        return line + ("the Mayor's task force, orders to bring the masks in on sight, his people out looking — and "
                       "whether he'd warn Batman at all, given how things are.")
    if dept in ("strained", "distant", "cool"):
        return line + "the pressure he's under for working with Batman, and what it's costing him."
    return line + "quiet thanks, if it's earned — he doesn't gush."


def _gordon_letter(model, options, event, now):
    if event.get("kind") != "case":
        return None
    how, rogue = event.get("how", ""), event.get("suspect", "")
    place, what = event.get("place", ""), event.get("what", "")
    if not (event.get("caught") or how in ("lost", "worse", "killed", "costly") or event.get("severity", 0) >= 4):
        return None
    found = _ask(model, options, _context(now, secure=True) + "Write one email from Commissioner James Gordon to Batman, on their "
                 "secure line — weary, decent, few words, signed 'Jim' or 'G'. About last night: "
                 f"the {what} at {place}" + (f", {rogue} behind it{_rogue_facts(rogue)}" if rogue else "")
                 + f" — it ended: {how}" + (f"; {event['caught']} is in GCPD custody" if event.get("caught") else "")
                 + ". What's next for GCPD, what he needs, what he can't say in public." + _standing_with_gcpd()
                 + _FACTS_ONLY + " " + _MAIL_SHAPE, "Gordon's email")
    if found and found.get("subject"):
        return deliver("gordon", found["subject"], found.get("body", ""), kind="gordon", key=f"gordon:{event.get('id')}")
    return None


def _thanks_letter(model, options, event, now):
    if event.get("kind") != "case":
        return None
    how, place, what = event.get("how", ""), event.get("place", ""), event.get("what", "")
    if not (how in ("saved", "caught", "contained") and event.get("severity", 0) >= 3 and _draw("thanks", event.get("id")) < 0.4):
        return None
    found = _ask(model, options, _context(now) + "Write one email to the Wayne Foundation (forwarded to Bruce) from "
                 f"someone who was there at the {what} at {place} last night — saved by masked people they can't name "
                 "and don't know how to thank; they're writing to the biggest charity in the city because they don't "
                 "know who else to tell. Human, unpolished, signed with a first name." + _FACTS_ONLY + " " + _MAIL_SHAPE,
                 "a thank-you")
    if found and found.get("subject"):
        return deliver("foundation", "Fwd: " + found["subject"], found.get("body", ""), kind="thanks",
                       key=f"thanks:{event.get('id')}")
    return None


def _claim_letter(model, options, event, now):
    if event.get("kind") != "case" or not event.get("chase") or _draw("claim", event.get("id")) >= 0.5:
        return None
    place = event.get("place", "")
    found = _ask(model, options, _context(now) + "Write one email from Wayne Enterprises' insurers to Bruce Wayne about "
                 f"a claim: property WE owns or insures near {place} was damaged last night in a police chase (a car "
                 "through a storefront, a security barrier, a fleet van). Dry, formal, a claim number." + _FACTS_ONLY
                 + " " + _MAIL_SHAPE, "an insurance email")
    if found and found.get("subject"):
        return deliver("Gotham Mutual Insurance", found["subject"], found.get("body", ""), kind="insurance",
                       name=found.get("from_name") or "Gotham Mutual Insurance",
                       address=found.get("from_address") or "claims@gothammutual.com", key=f"claim:{event.get('id')}")
    return None


def _arkham_letter(model, options, event, now):
    if event.get("kind") != "breakout":
        return None
    rogue = event.get("rogue", "")
    found = _ask(model, options, _context(now) + "Write one email from Arkham Asylum's administration to its "
                 f"benefactors (the Wayne Foundation funds a wing) about an escape: {rogue}{_rogue_facts(rogue)} "
                 f"{event.get('how', 'is out')}. Careful, legalistic, reassuring, 'the matter is in hand'."
                 + _FACTS_ONLY + " " + _MAIL_SHAPE, "an Arkham notice")
    if found and found.get("subject"):
        return deliver("Arkham Asylum Administration", found["subject"], found.get("body", ""), kind="arkham",
                       name=found.get("from_name") or "Arkham Asylum Administration",
                       address=found.get("from_address") or "administration@arkham-asylum.org",
                       key=f"arkham:{rogue}:{int(event.get('at', 0) // 86400)}")
    return None
