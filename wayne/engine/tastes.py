"""
What they love, what they can't stand, what they'll argue about — and the slang they don't know.

Each profile's `interests` carries researched, real things (`likes`, `dislikes`:
titles that exist, as of when they were written, each with why it matters to
them) and the debates they'd have (`debates`: a topic, their opinion, how hot,
and who'd take the other side). Their texting carries what they say and what
they'd never say (`texting_style.uses`, `.never`), the slang they wouldn't
understand (`.unfamiliar`), and when an emoji actually happens for them
(`.emoji_when`).

None of it goes into every turn: the system prompt carries one short, stable
line (it's read again at every change of voice on a call, and length there is
waiting). The rest comes in when a conversation touches it — a title named, a
film or a game asked about, a debate's topic raised — so they have their view
ready, and when someone uses slang they don't know, they're told they don't,
and may ask ("what does fml mean?") the way anyone would.
"""
import datetime
import random
import re

_KINDS = {
    "film": r"\b(film|films|movie|movies|cinema|watching tonight|box office)\b",
    "tv": r"\b(show|shows|series|season|episode|tv|telly|binge|streaming)\b",
    "game": r"\b(game|games|gaming|playing|switch|xbox|playstation|ps5|steam|console)\b",
    "book": r"\b(book|books|novel|reading|read anything|author)\b",
    "music": r"\b(song|songs|album|music|listening|band|artist|playlist|concert|gig)\b",
    "podcast": r"\b(podcast|podcasts)\b",
    "sport": r"\b(game last night|match|knights|baseball|football|golf|the score|season opener)\b",
}
_STOP = {"the", "and", "of", "a", "an", "in", "on", "to", "for", "with", "part", "season", "vol", "volume"}


def _interests(contact):
    return getattr(contact, "interests", None) or {}


def _words(title):
    """The words of a title worth matching on: 'Mario Kart World' → mario, kart, world (not 'the')."""
    return [w for w in re.findall(r"[a-z0-9']+", (title or "").lower()) if w not in _STOP and len(w) > 2]


def _named(item, text):
    """Whether the text names this title: the whole of it, or most of its words."""
    title = (item.get("title") or "").lower()
    if not title:
        return False
    low = text.lower()
    if title in low:
        return True
    words = _words(title)
    if len(words) < 2:
        return False
    hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", low))
    return hits >= max(2, round(len(words) * 0.66))


def note(contact, text):
    """
    Their view, ready, when the talk touches what they love or loathe or would argue about — '' otherwise.
    Only things in their profile, which were real when written: nothing to invent.
    """
    text = text or ""
    if len(text) < 3:
        return ""
    found = _interests(contact)
    likes, dislikes = current(contact)
    named = [("love", i) for i in likes if _named(i, text)] + [("hate", i) for i in dislikes if _named(i, text)]
    lines = []
    for how, item in named[:3]:
        view = "you love it" if how == "love" else "you can't stand it"
        lines.append(f"{item['title']} ({item.get('detail', '')}): {view} — {item.get('why', '')}".strip())
    if not named:
        low = text.lower()
        kinds = [k for k, pattern in _KINDS.items() if re.search(pattern, low)]
        if kinds:
            fits = [i for i in likes if i.get("kind") in kinds][:3]
            gripes = [i for i in dislikes if i.get("kind") in kinds][:1]
            if fits:
                lines.append("If it comes up, what you're into there: " + "; ".join(
                    f"{i['title']} — {i.get('why', '')}" for i in fits))
            if gripes:
                lines.append(f"And what you can't stand: {gripes[0]['title']} — {gripes[0].get('why', '')}")
    low = text.lower()
    for debate in found.get("debates") or []:
        topic = debate.get("topic", "")
        if topic and (_named({"title": topic}, text) or any(re.search(rf"\b{re.escape(w)}\b", low)
                                                            for w in _words(topic) if len(w) > 5)):
            heat = "and you'd fight for it" if debate.get("heat") == "strong" else "if anyone asks"
            lines.append(f"Your view on {topic}: {debate.get('opinion', '')} ({heat})")
            break
    if not lines:
        return ""
    return ("What you think, if it fits — yours, said your way, never a list: " + " | ".join(lines) + ".")


def unfamiliar(contact, text):
    """
    Slang they wouldn't know, in what they've just read — said so, so they can ask (or get it wrong) the way
    people do: Alfred and "fml". '' when there's none.
    """
    terms = ((getattr(contact, "texting_style", None) or {}).get("unfamiliar") or [])
    low = (text or "").lower()
    seen = []
    for term in terms:
        word = re.split(r"\s+[—(-]", str(term))[0].strip().lower()
        if word and re.search(rf"(?<![\w']){re.escape(word)}(?![\w'])", low):
            seen.append(word)
    if not seen:
        return ""
    quoted = ", ".join(f"“{w}”" for w in seen[:2])
    return (f"You don't know what {quoted} means. Ask, the way you would — or guess, and maybe get it wrong — "
            "or let it pass if you'd rather not admit it.")


def lately(contact, day=None):
    """One of the things they're into right now, for a status or a text out of the blue: a different one each day."""
    likes = [i for i in current(contact)[0] if i.get("title")]
    if not likes:
        return None
    day = day or datetime.date.today()
    return likes[(day.toordinal() + sum(map(ord, getattr(contact, "id", "")))) % len(likes)]


def summary(interests):
    """The one short line the system prompt carries — stable, so the cached prompt stays cached."""
    likes = [i["title"] for i in (interests or {}).get("likes") or [] if i.get("title")][:4]
    dislikes = [i["title"] for i in (interests or {}).get("dislikes") or [] if i.get("title")][:2]
    if not likes and not dislikes:
        return ""
    return ("Lately you're into " + ", ".join(likes) + "." if likes else "") + \
        (" You can't stand " + " or ".join(dislikes) + "." if dislikes else "")


def texting_extras(style):
    """What they say and never say, and when an emoji happens — added to how they text, once, at load."""
    style = style or {}
    parts = []
    if style.get("uses"):
        parts.append("you use " + ", ".join(style["uses"][:8]))
    if style.get("never"):
        parts.append("never " + ", ".join(style["never"][:6]))
    if style.get("emoji_when"):
        parts.append("emoji: " + style["emoji_when"])
    return ("; ".join(parts)) if parts else ""


def texting_habits(style, rng=random):
    """
    How they send them — when it's one composed message and when a burst — and a few of their habits, a different
    few each time (a habit shown every turn becomes a tic): for the turn, never the cached prompt. '' if none.
    """
    style = style or {}
    parts = []
    if style.get("bursts"):
        parts.append("How you send them: " + str(style["bursts"]).strip())
    quirks = [str(q) for q in style.get("quirks") or []]
    if quirks:
        parts.append("Habits of yours, when they fit: " + "; ".join(rng.sample(quirks, min(3, len(quirks)))) + ".")
    return " ".join(parts)


# --- tastes that move with the world --------------------------------------------------------------
# Who they are doesn't change; what they're into does. Once a week or so, from what's actually new in the things
# they follow (engine.culture — searched, never made up), they may get into something and go off something else.
# Kept in the store ("tastes"); the profile's list is where they started.

EVOLVE_DAYS = 6
_PAST_KEEP = 12


def _kept(cid):
    from .. import store
    return store.space("tastes").get(cid) or {}


def current(contact):
    """(likes, dislikes) as they are now: what they've got into lately first, minus what they've gone off."""
    found = _interests(contact)
    kept = _kept(contact.id)
    over = {p.get("title", "").lower() for p in kept.get("past") or []}
    likes = list(kept.get("likes") or []) + [i for i in found.get("likes") or [] if i.get("title", "").lower() not in over]
    return likes, list(found.get("dislikes") or []) + list(kept.get("dislikes") or [])


def due(contact, now=None):
    import time as _time
    now = now or _time.time()
    return bool(_interests(contact).get("likes")) and now - _kept(contact.id).get("at", 0) > EVOLVE_DAYS * 86400


def evolve(contact, items, now=None):
    """
    A week of what's new in what they follow, read by them: anything they've got into, anything they've gone off
    — or nothing, which is most weeks. Only from the items given: a title not in them is never taken up.
    Background (engine.model): it waits for any call to end.
    """
    import json
    import time as _time

    from .. import store
    from . import model
    now = now or _time.time()
    likes, dislikes = current(contact)
    kept = _kept(contact.id)
    if not items:
        store.space("tastes").put(contact.id, {**kept, "at": now})
        return {}
    instruction = (
        f"You are {contact.full_name}. What you're into right now: "
        + "; ".join(f"{i['title']} ({i.get('kind', '')})" for i in likes[:10])
        + ". What you can't stand: " + "; ".join(i["title"] for i in dislikes[:6])
        + ".\n\nWhat's new this week in the things you follow (real, from the news):\n"
        + "\n".join(f"- {i}" for i in items[:12])
        + "\n\nPeople's tastes shift a little, slowly, and stay theirs. From ONLY the new items above: is there one "
        "(or two at most) you'd genuinely get into, given who you are? And has one of the things you're into "
        "worn thin, or been overtaken? Most weeks the honest answer is nothing — return empty lists. Never take up "
        "something that isn't in the list above. JSON only: "
        '{"into": [{"line": "the item exactly as listed", "title": "its name", "kind": "film|tv|game|book|music|'
        'podcast|sport|other", "why": "why, in your voice, one line"}], "over": [{"title": "one of yours", '
        '"why": "a few words"}], "cant_stand": [{"line": "the item exactly as listed", "title": "its name", '
        '"why": "in your voice"}]}')
    try:
        reply = model.ask(contact.model, [{"role": "user", "content": instruction}],
                          {**contact.options, "temperature": 0.4, "num_predict": 260}, fmt="json",
                          purpose=f"{contact.id}'s tastes")
        found = json.loads(reply)
    except Exception:
        return {}
    listed = {i.lower() for i in items}

    def real(entry):
        line = str(entry.get("line") or "").lower()
        return bool(line) and any(line in i or i in line for i in listed) and str(entry.get("title") or "").strip()
    took = [{"kind": str(e.get("kind") or "other"), "title": str(e["title"]).strip()[:80],
             "detail": f"new — {_time.strftime('%B %Y', _time.localtime(now))}", "why": str(e.get("why") or "")[:160],
             "source": "their news feed", "since": now}
            for e in (found.get("into") or [])[:2] if isinstance(e, dict) and real(e)]
    hated = [{"kind": "other", "title": str(e["title"]).strip()[:80], "detail": "new", "why": str(e.get("why") or "")[:160],
              "source": "their news feed", "since": now}
             for e in (found.get("cant_stand") or [])[:1] if isinstance(e, dict) and real(e)]
    mine = {i["title"].lower() for i in likes}
    gone = [{"title": str(e["title"]).strip()[:80], "why": str(e.get("why") or "")[:120], "since": now}
            for e in (found.get("over") or [])[:1] if isinstance(e, dict) and str(e.get("title") or "").lower() in mine]
    entry = {"at": now,
             "likes": (took + list(kept.get("likes") or []))[:8],
             "dislikes": (hated + list(kept.get("dislikes") or []))[:6],
             "past": (gone + list(kept.get("past") or []))[:_PAST_KEEP]}
    store.space("tastes").put(contact.id, entry)
    return {"into": took, "over": gone, "cant_stand": hated}
