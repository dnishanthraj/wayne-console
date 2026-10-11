"""
What they'd remember that bears on what was just said — found in the record,
handed over in a few lines, and only what they'd actually know.

Their model reads a short window of the conversation and the last few hours of
their own day; anything older was gone. Asked "what did I tell you about the
Kord deal?" or "what were you doing last night?", they made it up. Now the
record is searched — what was said with them (the transcript), what happened
to them and around them (events: their diary, news that reached them, cases
they worked, what the city knows), and the group chats they're in, as far as
they've read — and the best few go in, as background, theirs to use or not.

Asked only when the words call for it — a memory cue ("remember", "last
night", "did you…", "what happened"), a time ("on Tuesday", "the other day"),
or someone or something named that the record has more on — because every word
added to a prompt is read again before they say anything. Nothing goes in that
the conversation already holds (the window, the last few hours of their day,
the news they've just heard, the group chat they just read), and nothing about
the masks reaches anyone outside the secret, whatever the record says.
"""
import datetime
import math
import re
import time

from ..config import setting
from . import db as database
from . import events, index, transcript
from .threads import Thread

WORDS = int(setting("RECALL_WORDS", "90") or 90)
MOST = 5
# A match this far below the best one is a word in common, not the same thing.
_WEAKEST = 0.4

_CUE = re.compile(
    r"\b(remember\w*|recall\w*|forg[eo]t\w*|reminds? me|last (?:time|night|week|month|weekend|year|\w+day)|"
    r"the other (?:day|night|week)|yesterday|earlier|before|ago|back when|that time|this morning|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|did (?:you|we|i|he|she|they)|have you|had you|were you|was i|"
    r"what happened|how did|how was|how'?d|told (?:you|me|him|her)|you said|i said|he said|she said|"
    r"(?:we|you) talked|mentioned|again|still|what was (?:it|that)|"
    r"(?:did|have|had) you hear|you hear (?:anything|about|from|what)|heard (?:anything|about|from|that|what)|"
    r"any (?:word|news) (?:on|about|from)|what'?s the word on)\b", re.I)


def wanted(prompt, names=()):
    """
    Whether to look at all: a memory cue or a time — or one of the rogues named,
    whose record (the cases, the breakouts) is worth having to hand. Someone in
    the family named isn't enough on its own: "tell Dick to come home" asks for
    nothing from the past.
    """
    if not prompt or len(prompt) < 8:
        return False
    return bool(_CUE.search(prompt)) or any(kind == "rogue" for kind, _ in names)


# --- when ------------------------------------------------------------------------------

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def span(prompt, now=None):
    """
    The stretch of time the words point at — (since, until) — or None.
    "last night" is six till six; "Tuesday" is the last Tuesday before today;
    "the other day" is the last week, bar today.
    """
    now = now or time.time()
    text = (prompt or "").lower()
    today = datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)

    def at(day, hour):
        return (day + datetime.timedelta(hours=hour)).timestamp()

    yesterday = today - datetime.timedelta(days=1)
    if re.search(r"\blast night\b", text):
        return at(yesterday, 18), at(today, 6)
    if re.search(r"\byesterday morning\b", text):
        return at(yesterday, 5), at(yesterday, 12)
    if re.search(r"\byesterday (?:afternoon)\b", text):
        return at(yesterday, 12), at(yesterday, 18)
    if re.search(r"\byesterday (?:evening)\b", text):
        return at(yesterday, 17), at(today, 0)
    if re.search(r"\byesterday\b", text):
        return at(yesterday, 0), at(today, 0)
    if re.search(r"\bthis morning\b", text):
        return at(today, 5), min(now, at(today, 12))
    if re.search(r"\bthis afternoon\b", text):
        return at(today, 12), min(now, at(today, 18))
    if re.search(r"\b(?:earlier today|today)\b", text):
        return at(today, 0), now
    if re.search(r"\b(?:earlier tonight|tonight|this evening)\b", text):
        return at(today, 17), now
    m = re.search(r"\b(?:on |last )?(" + "|".join(_DAYS) + r")( night)?\b", text)
    if m:
        back = (today.weekday() - _DAYS.index(m.group(1))) % 7 or 7
        day = today - datetime.timedelta(days=back)
        return (at(day, 18), at(day, 30)) if m.group(2) else (at(day, 0), at(day, 24))
    m = re.search(r"\b(\d+|a couple of|a few|two|three|four|five) days ago\b", text)
    if m:
        n = {"a couple of": 2, "a few": 3, "two": 2, "three": 3, "four": 4, "five": 5}.get(m.group(1)) or int(m.group(1))
        day = today - datetime.timedelta(days=n)
        return at(day, 0), at(day, 24)
    if re.search(r"\blast weekend\b", text):
        saturday = today - datetime.timedelta(days=(today.weekday() - 5) % 7 or 7)
        return at(saturday, 0), at(saturday, 48)
    if re.search(r"\blast week\b", text):
        return now - 8 * 86400, at(today, 0)
    if re.search(r"\bthe other (?:day|night)\b", text):
        return now - 7 * 86400, at(today, 0)
    return None


# --- who and what it names ---------------------------------------------------------------

def _named(prompt, viewer):
    """[(type, ref)] for the people and rogues the words name — the ones the record links things to."""
    from ..contacts import directory
    from ..engine import incidents
    text = prompt or ""
    out = []
    for c in directory():
        if c.id != viewer and re.search(rf"\b({re.escape(c.name)}|{re.escape(c.full_name)})\b", text, re.I):
            out.append(("contact", c.id))
    for rogue in incidents.rogues():
        short = re.sub(r"^The ", "", rogue["name"])
        if re.search(rf"\b{re.escape(short)}\b", text, re.I):
            out.append(("rogue", rogue["name"]))
    return out


# --- what they can know -----------------------------------------------------------------

def _visible_event(event, viewer, known_at, watcher, outsider, now):
    if known_at is not None:
        return known_at <= now            # they were there, or it reached them — from then on
    if event["secret"] and outsider:
        return False
    return event["scope"] == events.PUBLIC or (event["scope"] == events.WATCHERS and watcher)


def _group_view(viewer):
    """{thread: (since, upto)} — how much of each group they're in they've been let see and have read."""
    from ..memory import groups as group_store
    out = {}
    for group in group_store.groups_with(viewer):
        meta = group.meta()
        out[f"g:{group.id}"] = ((meta.get("joined") or {}).get(viewer, 0), (meta.get("reads") or {}).get(viewer, 0),
                                meta.get("name", "the group"))
    return out


# --- saying when -------------------------------------------------------------------------

def _part(hour):
    return ("night" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 22
            else "night")


def when(at, now=None):
    """'this morning', 'last night', 'yesterday afternoon', 'on Tuesday', '3 weeks ago' — as people say it."""
    now = now or time.time()
    then, current = datetime.datetime.fromtimestamp(at), datetime.datetime.fromtimestamp(now)
    # The small hours belong to the night before: 1am, asked about at noon, was last night.
    night_of = (then - datetime.timedelta(hours=5)).date() if then.hour < 5 else then.date()
    days = (current.date() - night_of).days
    part = _part(then.hour)
    if days <= 0:
        return "tonight" if part == "night" or (part == "evening" and current.hour >= 17) else f"this {part}"
    if days == 1:
        if part in ("night", "evening") or then.date() == current.date():
            return "last night" if current.hour >= 5 or then.date() != current.date() else "tonight"
        return f"yesterday {part}"
    if days < 7:
        day = datetime.datetime.combine(night_of, datetime.time()).strftime("%A")
        return f"on {day} night" if part in ("night", "evening") else f"on {day}"
    if days < 14:
        return "last week"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


# --- the block -----------------------------------------------------------------------------

def block(contact, prompt, now=None, said_since=None, outsider=None, budget=WORDS):
    """
    The few things they'd remember that bear on `prompt`, as background for
    their prompt — or ''. `said_since` is where the conversation in front of
    them begins (nothing from then on is repeated); `outsider` forces the
    secret filter (someone outside it is on the line).
    """
    now = now or time.time()
    viewer = contact.id
    named = _named(prompt, viewer)
    if not wanted(prompt, named):
        return ""
    from ..engine import groupchat
    # Outside the secret themselves, or on the line with someone who is: either way, nothing of the masks.
    guarded = bool(outsider) and not groupchat.outside(viewer)
    outsider = groupchat.outside(viewer) or bool(outsider)
    watcher = bool(getattr(contact, "sees_whereabouts", False))
    window = span(prompt, now)
    words = index.terms(prompt)
    found = index.of().search(words) if words else []

    # Every candidate: (score parts, kind, ref) — the index's, the ones in the time asked about, the ones
    # linked to whoever was named.
    lexical = {}
    for kind, ref, fit in found:
        if fit >= _WEAKEST:
            lexical[(kind, ref)] = fit
    store = database.get()
    if window:
        for (event_id,) in store.rows(
                "SELECT e.id FROM events e LEFT JOIN event_audience a ON a.event_id = e.id AND a.contact_id = ? "
                "WHERE e.at >= ? AND e.at < ? AND (a.contact_id IS NOT NULL OR e.scope != 'private') "
                "ORDER BY e.at LIMIT 60", (viewer, window[0], window[1])):
            lexical.setdefault((index.EVENT, event_id), 0.0)
        for line in transcript.lines(viewer, window[0], window[1], limit=40):
            lexical.setdefault((index.SAID, line["id"]), 0.0)
    linked = set()
    for kind, ref in named:
        for (event_id,) in store.rows("SELECT event_id FROM event_links WHERE type = ? AND ref = ? "
                                      "ORDER BY event_id DESC LIMIT 30", (kind, ref)):
            lexical.setdefault((index.EVENT, event_id), 0.0)
            linked.add(event_id)
    if not lexical:
        return ""

    picked = _pick(lexical, linked, viewer, watcher, outsider, guarded, window, now, said_since)
    return _render(picked, viewer, now, budget)


_HALF_LIFE = {"diary": 2 * 86400, "said": 7 * 86400, "message": 5 * 86400, "case": 10 * 86400,
              "hearsay": 10 * 86400, "rogue": 7 * 86400, "plan": 7 * 86400}


def _pick(candidates, linked, viewer, watcher, outsider, guarded, window, now, said_since):
    ids = {kind: [ref for k, ref in candidates if k == kind] for kind in (index.EVENT, index.SAID, index.MESSAGE)}
    happened = events.by_ids(ids[index.EVENT])
    heard = events.audience(list(happened))
    lines = transcript.by_ids(ids[index.SAID])
    groups = _group_view(viewer) if ids[index.MESSAGE] else {}
    chats = _messages(ids[index.MESSAGE], groups, viewer)
    out, paired = [], set()
    for (kind, ref), lex in candidates.items():
        if kind == index.EVENT:
            event = happened.get(ref)
            if event is None:
                continue
            known = (heard.get(ref) or {}).get(viewer)
            if not _visible_event(event, viewer, known, watcher, outsider, now):
                continue
            family = event["kind"].split(".")[0]
            at = known if known is not None else event["at"]
            # Already in front of them: the last few hours of their own day, the news they've just heard.
            if family == "diary" and event["actor"] == viewer and now - event["at"] < 3 * 3600:
                continue
            if family == "hearsay" and now - at < 10 * 86400:
                continue
            item = {"kind": family, "at": at, "text": event["text"], "event": event}
        elif kind == index.SAID:
            line = lines.get(ref)
            if line is None or line["contact_id"] != viewer:
                continue
            # The exchange it was part of — a question found by its words comes with the answer, and an answer
            # with what it answered — once, however many of its lines were found.
            ask, answer = transcript.exchange(line)
            first = ask or answer
            if first["id"] in paired or (said_since is not None and first["at"] >= said_since):
                continue
            paired.add(first["id"])
            item = {"kind": "said", "at": first["at"], "ask": ask, "answer": answer,
                    "text": " ".join(x["text"] for x in (ask, answer) if x)}
        else:
            message = chats.get(ref)
            if message is None or now - message["at"] < 3 * 3600:
                continue
            item = {"kind": "message", "at": message["at"], "text": message["text"], "message": message}
        # Their own conversation they were part of, and what the whole city knows, they know; anything else — or
        # anything at all with an outsider listening — is checked against the secret.
        public = kind == index.EVENT and item["event"]["scope"] == events.PUBLIC and not item["event"]["secret"]
        if outsider and not public and (item["kind"] != "said" or guarded) and _secret(item["text"], viewer, guarded):
            continue
        recency = math.exp(-max(0.0, now - item["at"]) / _HALF_LIFE.get(item["kind"], 5 * 86400))
        timely = 1.0 if window and window[0] <= item["at"] < window[1] else 0.0
        about = 1.0 if kind == index.EVENT and ref in linked else 0.0
        weight = 0.15 if item["kind"] == "case" else 0.0
        item["score"] = 0.5 * lex + 0.2 * recency + 0.35 * timely + 0.25 * about + weight
        if lex == 0.0 and not timely and not about:
            continue
        item["timely_only"] = lex == 0.0 and not about
        out.append(item)
    # Asked about a stretch of time and nothing more ("what did you do last night?"): the night, start to end,
    # rather than whichever five lines came first.
    plain = [i for i in out if i.get("timely_only")]
    if len(plain) > MOST:
        plain.sort(key=lambda i: i["at"])
        keep = {id(plain[round(k * (len(plain) - 1) / (MOST - 1))]) for k in range(MOST)}
        out = [i for i in out if not i.get("timely_only") or id(i) in keep]
    out.sort(key=lambda i: -i["score"])
    chosen = []
    for item in out:
        mine = set(index.terms(item["text"]))
        if len(mine) >= 2 and any(len(mine & theirs) >= 0.6 * len(mine | theirs)
                                  for theirs in (set(index.terms(c["text"])) for c in chosen)):
            continue              # the same thing twice — a diary line and the case it was about
        chosen.append(item)
        if len(chosen) >= MOST * 2:
            break
    return chosen


def _secret(text, viewer, guarded):
    """Whether a line touches the masks for this listener (an insider, guarded, is checked as an outsider would be)."""
    from ..engine import grapevine
    if guarded:
        return bool(grapevine._THE_WORK.search(text)) or grapevine._crosses_secret(text, "__outside__")
    return grapevine._crosses_secret(text, viewer)


def _messages(rids, groups, viewer):
    """Group messages by rid, only those they can know: their own, and the rest as far as they've read."""
    if not rids:
        return {}
    store = database.get()
    out = {}
    for rid, thread in store.rows(f"SELECT rid, thread FROM messages WHERE rid IN ({','.join('?' * len(rids))})",
                                  [int(r) for r in rids]):
        if thread not in groups:
            continue
        since, upto, name = groups[thread]
        for message in Thread(thread).where("rid = ?", (rid,)):
            if since <= message["at"] <= upto or message.get("from") == viewer:
                out[rid] = {**message, "text": message.get("said") or message.get("text") or "", "group": name}
    return out


def _render(chosen, viewer, now, budget):
    from ..contacts import directory
    from ..operator import name as operator_name
    book = directory()
    lines, used = [], 0
    for item in chosen:
        text = " ".join(item["text"].split())
        if item["kind"] == "said":
            ask, answer = item["ask"], item["answer"]
            by = " (by text)" if (ask or answer).get("via") == "text" else ""
            parts = []
            if ask:
                words = " ".join(ask["text"].split())
                parts.append(f"on a call: \"{_clip(words, 22)}\"" if _LABELLED.match(words)       # everyone's lines
                             else f"{operator_name()} said{by}: \"{_clip(words, 22)}\"")
            if answer:
                parts.append(f"you said{by if not ask else ''}: \"{_clip(' '.join(answer['text'].split()), 22)}\"")
            text = " — ".join(parts)
        elif item["kind"] == "message":
            sender = item["message"].get("from")
            who = "you" if sender == viewer else operator_name() if sender == "me" else \
                (book.get(sender).name if book.get(sender) else "someone")
            text = f"in {item['message']['group']}, {who} wrote: \"{_clip(text, 30)}\""
        elif item["kind"] == "hearsay":
            text = f"{item['event']['actor']} told you: {_clip(text, 30)}"
        elif item["kind"] == "diary":
            actor = item["event"]["actor"]
            text = (f"you: {_clip(text, 24)}" if actor == viewer else
                    f"{book.get(actor).name if book.get(actor) else actor}: {_clip(text, 24)}")
        elif item["kind"] == "case":
            data = item["event"]["data"]
            others = [book.get(c).name if book.get(c) else "Bruce" if c == "bruce" else c
                      for c in data.get("team", []) if c != viewer]
            mine = viewer in data.get("team", [])
            text = (("you worked " if mine else "the board had ") + _clip(_without_team(text), 34)
                    + (f" (with {' and '.join(others)})" if mine and others else ""))
        else:
            text = _clip(text, 30)
        cost = len(text.split()) + 3
        if lines and used + cost > budget:
            break
        lines.append((item["at"], f"- {when(item['at'], now)}: {text}"))
        used += cost
        if len(lines) >= MOST:
            break
    if not lines:
        return ""
    lines.sort()
    # Named as "your memory" in so many words: the standing rule is to deny any conversation that isn't in the
    # transcript or their memory, and a block that didn't say it was theirs was denied ("You didn't, sir").
    return ("Your memory — what you remember from before this conversation. It's real: if he asks about any of it, "
            "you remember it. Bring it up your own way, never recite it:\n" + "\n".join(line for _, line in lines))


_LABELLED = re.compile(r"^\(?[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,2}\)?:\s")


def _without_team(text):
    """A case's line without the 'Dick and Tim on it' part — said from the viewer's side instead."""
    return re.sub(r", [^,:—]+ on it(?= — |:)", "", text)


def _clip(text, words):
    parts = text.split()
    return text if len(parts) <= words else " ".join(parts[:words]) + "…"
