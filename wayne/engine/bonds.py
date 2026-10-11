"""
How they feel about him — and about each other — and how that moves.

Every pair has a bond, one way round (Tim can be sore at Bruce while Bruce
thinks nothing's wrong): `warmth` (fondness, goodwill) and `trust` (that he'll
be there, and straight with them), both slow, from -100 to 100; `tension`, a
fresh hurt from 0 to 10 that fades over days; and, between those it applies to
(Bruce and Selina, Dick and Barbara), `romance`.

Where each of them starts, how fast they warm and how fast they cool, how long
a grudge lasts and how a hurt comes out is theirs, in their profile (`bond`):
Alfred's floor is high and his grudges are an afternoon long; Jason and Randy
cool fast, warm slowly and ghost; Barbara argues; Dick calls it out and makes
up; Cass goes quiet and forgives without saying so.

What moves a bond is what happened: the pass after a conversation (see
initiative.afterthought — most change nothing), and things the console sees
for itself — waking someone at six three mornings running, a case that went
bad with nobody sent, coming himself when it did, an evening spent together.
Each shift worth remembering is kept as an event in their name (kind "bond"),
in their words — the memory they bring up, never one they invent — and recall
finds it like anything else.

What a bond changes: a line in every turn on where they stand with him and
what's on their mind about him; how likely they are to answer, read, reply,
ghost or text first; whether they share their location; the feeling in their
voice. They're still who they are — it colours, it doesn't rewrite.

Kept in the store: "bonds" (each pair's state as of its last change, aged as
it's read) and the events.
"""
import time

from .. import store

ME = "me"
GCPD = "gcpd"            # the department's standing with the masks, as an institution

# Where someone starts with anyone, unless their profile says otherwise.
_DEFAULT = {"warmth": 50.0, "trust": 50.0}
_DRIFT_DAYS = 45.0        # how long warmth and trust take to drift halfway back to where they started
_DAY = 86400.0
# Most in a day a bond's warmth or trust can move: no spiral off one bad afternoon.
DAILY_CAP = 14.0

_LEVELS = [(85, "devoted"), (70, "close"), (55, "warm"), (40, "easy"), (25, "cordial"), (10, "cool"),
           (-10, "distant"), (-35, "strained"), (-60, "hostile"), (-1000, "estranged")]
_SORE = [(6, "furious with him"), (3.5, "sore at him"), (1.5, "a bit put out"), (0.4, "")]
_ROMANCE = [(85, "in love, and together"), (70, "together"), (55, "seeing each other, on and off"),
            (40, "something's there, and you both know it"), (25, "flirting"), (-1000, "")]


def _space():
    return store.space("bonds")


def _key(a, b):
    return f"{a}>{b}"


def _contact(cid):
    from ..contacts import directory
    return directory().get(cid)


def profile(cid):
    """Their bond settings: where they start, how they move, how a hurt comes out."""
    contact = _contact(cid)
    return dict(getattr(contact, "bond", None) or {}) if contact else {}


def baseline(a, b):
    """Where a starts with b: their profile's word for him, or for that person, else ordinary goodwill."""
    mine = profile(a)
    found = (mine.get("toward") or {}).get(b)
    if found is None and b == ME:
        found = mine.get("toward_him")
    if found is None and a == GCPD and b == ME:
        found = {"warmth": 10.0, "trust": 20.0}
    out = {**_DEFAULT, **(found or {})}
    out.setdefault("romance", None)
    return out


def _floor(a, b):
    mine = profile(a)
    return (mine.get("floor") or {}) if b == ME else {}


def state(a, b, t=None):
    """
    Where a stands with b at t: {"warmth", "trust", "tension", "romance"} — the
    last change, aged: the hurt faded by their grudge, warmth and trust drifted
    a little back toward where they started.
    """
    t = t or time.time()
    base = baseline(a, b)
    kept = _space().get(_key(a, b))
    if not kept:
        return {"warmth": base["warmth"], "trust": base["trust"], "tension": 0.0, "romance": base.get("romance")}
    days = max(0.0, (t - kept.get("at", t)) / _DAY)
    grudge = max(0.25, float(profile(a).get("grudge_days", 3)))
    drift = 1 - 0.5 ** (days / _DRIFT_DAYS)
    floor = _floor(a, b)
    out = {}
    for dim in ("warmth", "trust"):
        value = kept.get(dim, base[dim])
        value += (base[dim] - value) * drift
        out[dim] = max(floor.get(dim, -100.0), min(100.0, value))
    out["tension"] = kept.get("tension", 0.0) * 0.5 ** (days / grudge)
    romance = kept.get("romance", base.get("romance"))
    out["romance"] = None if romance is None else max(-100.0, min(100.0, romance))
    return out


def nudge(a, b, warmth=0.0, trust=0.0, tension=0.0, romance=0.0, why="", weight=1, t=None, audience=(),
          links=(), extra=None):
    """
    Something happened between them: move a's bond with b — warming at their
    pace, cooling at theirs, never past their floor or more than a day can bear
    — and, if it's worth remembering (`why`), keep it, in their words.
    Returns the new state.
    """
    t = t or time.time()
    mine = profile(a)
    now = state(a, b, t)
    kept = _space().get(_key(a, b)) or {}
    day = time.strftime("%Y-%m-%d", time.localtime(t))
    moved = kept.get("moved", {}) if kept.get("day") == day else {}
    floor = _floor(a, b)
    for dim, delta in (("warmth", warmth), ("trust", trust)):
        if not delta:
            continue
        rate = mine.get("warms", 1.0) if delta > 0 else mine.get("cools", 1.0)
        change = delta * rate
        used = moved.get(dim, 0.0)
        change = max(-DAILY_CAP - used, min(DAILY_CAP - used, change))
        moved[dim] = used + change
        now[dim] = max(floor.get(dim, -100.0), min(100.0, now[dim] + change))
    if tension:
        sore = mine.get("cools", 1.0) if tension > 0 else mine.get("warms", 1.0)
        now["tension"] = max(0.0, min(10.0, now["tension"] + tension * sore))
    was = None if now.get("romance") is None else now["romance"]
    if romance and now.get("romance") is not None:
        now["romance"] = max(-100.0, min(100.0, now["romance"] + romance))
    # How far it's ever got — for knowing a fall from it for what it is (see _milestone).
    peak = max(kept.get("peak", was if was is not None else -100.0), now.get("romance") or -100.0)
    if was is not None and now.get("romance") is not None:
        peak = _milestone(a, b, was, now["romance"], now["tension"], t, peak)
    _space().put(_key(a, b), {**now, "at": t, "day": day, "moved": moved, "peak": peak})
    if why:
        from ..store import events
        events.add("bond", why, at=t, actor=a,
                   data={"toward": b, "warmth": warmth, "trust": trust, "tension": tension, "romance": romance,
                         "weight": weight, **(extra or {})},
                   links=tuple(links) + ((("contact", b),) if b not in (ME, GCPD) else ()),
                   audience=tuple(audience) or (a,))
    return now


def _milestone(a, b, before, after, hurt, t, peak):
    """
    A romance crossing a line — together, or not any more — is something they'd remember, in their words: kept as
    its own event (weight 3), so it's on their mind for weeks and colours everything. Falling under 40 after it's
    been 55 or more is the end of it — a break-up with a fresh hurt, drifting apart without one — and then the
    high-water mark starts again from there. Returns that mark.
    """
    from ..store import events
    other = _contact(b)
    them = "him" if b == ME else (other.name if other else b)
    you_and = f"you and {'Bruce' if b == ME else them}"
    line = None
    if before < 70 <= after:
        line, flag = f"{you_and} are together now", "together"
    elif before < 55 <= after:
        line, flag = f"{you_and} started seeing each other", "seeing"
    elif before >= 40 > after and peak >= 55:
        line, flag = (f"{you_and} broke up", "breakup") if hurt >= 4 else (f"{you_and} drifted apart", "apart")
        peak = after
    elif before >= 55 > after:
        line, flag = f"things cooled between {you_and}", "cooled"
    if line:
        events.add("bond", line, at=t, actor=a,
                   data={"toward": b, "romance": after - before, "weight": 3, "milestone": flag},
                   links=((("contact", b),) if b not in (ME, GCPD) else ()), audience=(a,))
    return peak


def memories(a, b, t=None, most=3):
    """What's on a's mind about b, in their words: the weightier and the more recent first; small things fade."""
    from ..store import events
    t = t or time.time()
    found = [e for e in events.of(a, "bond", since=t - 120 * _DAY) if (e.get("data") or {}).get("toward") == b]

    def salience(e):
        weight = abs((e.get("data") or {}).get("weight", 1)) or 1
        age = max(0.0, (t - e["at"]) / _DAY)
        return weight * 0.5 ** (age / (3.0 * weight))     # a big thing lasts; a small one is gone in days
    found = [e for e in found if salience(e) > 0.15]
    found.sort(key=salience, reverse=True)
    return [e for e in found[:most]]


def level(value):
    for at, word in _LEVELS:
        if value >= at:
            return word
    return "estranged"


def standing(a, b, t=None):
    """(how things are between them in a word, how sore, the romance in words) — '' where there's nothing."""
    s = state(a, b, t)
    overall = level(s["warmth"] * 0.6 + s["trust"] * 0.4)
    sore = next((word for at, word in _SORE if s["tension"] >= at), "")
    romance = "" if s.get("romance") is None else next((w for at, w in _ROMANCE if s["romance"] >= at), "")
    return overall, sore, romance


def _ago(t, then):
    days = (t - then) / _DAY
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 7:
        return time.strftime("on %A", time.localtime(then))
    return f"{int(days // 7)} week{'s' if days >= 14 else ''} ago"


def note(contact, t=None):
    """
    Where they stand with him, for a turn: how things are, how sore, what's on
    their mind about him and how a hurt comes out with them — and for Jim, how
    his department sees the masks — '' when it's simply how it always is.
    """
    t = t or time.time()
    cid = contact.id
    base = baseline(cid, ME)
    usual = level(base["warmth"] * 0.6 + base["trust"] * 0.4)
    overall, sore, romance = standing(cid, ME, t)
    recent = memories(cid, ME, t)
    parts = []
    if overall != usual or sore or recent or romance:
        parts.append(f"Between you and him right now: {overall}" + (f" — and you're {sore}" if sore else "") + ".")
        if romance:
            parts.append(f"Romantically: {romance}.")
        if recent:
            parts.append("On your mind about him: " + "; ".join(f"{e['text'].rstrip('.')} ({_ago(t, e['at'])})"
                                                             for e in recent) + ".")
        mine = profile(cid)
        if sore and mine.get("when_hurt"):
            parts.append(f"When you're hurt, you {mine['when_hurt']}.")
    if getattr(contact, "sees_work", "") == "gcpd":
        dept = standing(GCPD, ME, t)[0]
        gbase = baseline(GCPD, ME)
        lately = memories(GCPD, ME, t, most=2)
        if dept != level(gbase["warmth"] * 0.6 + gbase["trust"] * 0.4) or lately:
            parts.append(f"Your department on the masks right now: {dept}"
                         + (" — " + "; ".join(e["text"] for e in lately) if lately else "") + ".")
    if not parts:
        return ""
    parts.append("It colours how you are with him — warmer or cooler, quicker or slower to help — never who you are. "
                 "Only what's above happened; bring up nothing else.")
    return " ".join(parts)


def between(contact, others, t=None):
    """For a call or a chat with others in it: how they are with the ones it's notable with — '' if nobody."""
    t = t or time.time()
    lines = []
    for other in others:
        if other.id == contact.id:
            continue
        overall, sore, romance = standing(contact.id, other.id, t)
        base = baseline(contact.id, other.id)
        usual = level(base["warmth"] * 0.6 + base["trust"] * 0.4)
        if overall != usual or sore or (romance and romance != ""):
            bits = [overall] + ([sore.replace("him", other.name)] if sore else []) + ([romance] if romance else [])
            lines.append(f"{other.name}: {', '.join(bits)}")
    return ("With the others here: " + "; ".join(lines) + ".") if lines else ""


def effects(contact, t=None):
    """
    What where they stand does to how they act toward him: answering, reading,
    replying, texting first, sharing their location, the feeling in their voice.
    """
    t = t or time.time()
    s = state(contact.id, ME, t)
    mine = profile(contact.id)
    base = baseline(contact.id, ME)
    cooler = max(0.0, (base["warmth"] - s["warmth"]) / 100.0)          # how far below their usual
    hurt = s["tension"]
    ghosts = bool(mine.get("ghosts"))
    answer = 1.0 - min(0.7, cooler * 0.8 + hurt * (0.09 if ghosts else 0.04))
    if mine.get("loyal"):
        answer = max(0.85, answer)
    quiet = 1.0 + hurt * (0.45 if ghosts else 0.2) + cooler * (2.0 if ghosts else 0.8)
    reach = max(0.15, min(1.4, 1.0 + (s["warmth"] - base["warmth"]) / 120.0 - hurt * 0.12))
    hides = bool(mine.get("hides_when_hurt")) and hurt >= 3.5
    shares = bool(mine.get("guarded")) and s["trust"] >= mine.get("shares_at", 65) and hurt < 1.5
    cues = list(mine.get("cues_when_hurt", ())) if hurt >= 2.5 else []
    if s.get("romance") is not None and s["romance"] >= 55 and hurt < 1:
        cues += ["softly", "playful"]
    return {"answer": max(0.15, min(1.05, answer)), "quiet": quiet, "reach": reach, "hides": hides,
            "shares": shares, "cues": cues, "hurt": hurt}


def turned_away(contact, how, times, t=None):
    """
    He didn't take their call, or left their texts unanswered: once is nothing much — a little put out, unremembered;
    again, the same day, it stings, and it's remembered. How it lands is theirs (their profile's pace).
    """
    t = t or time.time()
    what = {"declined": "he declined your call", "missed": "he didn't pick up when you rang",
            "on_read": "he read your text and never answered"}.get(how, "he didn't answer")
    if times <= 1:
        return nudge(contact.id, ME, warmth=-0.4, tension=0.3 if how != "missed" else 0.15, t=t)
    return nudge(contact.id, ME, warmth=-1.0 * min(times, 4), tension=0.6 * min(times, 4), t=t, weight=min(2, times - 1),
                 why=f"{what} — {times} times today" if how != "on_read" else f"{what}, again")


# Letting them down — a plan called off, an evening he never showed for, an invitation left unanswered — is a slow
# burn: once is life, the second time in a month is noticed, and a habit of it is what hurts. Knowing why (a case,
# to someone who knows who he is) takes most of the sting out; how they take it is still theirs (their pace).
_LETDOWN_SCALE = (0.5, 1.0, 1.6, 2.2)
LETDOWN_DAYS = 30


def letdowns(a, b=ME, t=None, days=LETDOWN_DAYS):
    """How many times lately b has let a down (see let_down)."""
    from ..store import events
    t = t or time.time()
    return sum(1 for e in events.of(a, "bond", since=t - days * _DAY)
               if (e.get("data") or {}).get("toward") == b and (e.get("data") or {}).get("letdown"))


def let_down(a, why, warmth=0.0, trust=0.0, tension=0.0, weight=1, understood=False, t=None):
    """
    He's let a down: moved by how often he has lately, and by less if they understand why. Returns how many times
    that is now, this one included.
    """
    times = letdowns(a, ME, t) + 1
    scale = _LETDOWN_SCALE[min(times, len(_LETDOWN_SCALE)) - 1] * (0.35 if understood else 1.0)
    if times == 2:
        why += " — the second time lately"
    elif times > 2:
        why += f" — again; {times} times this month"
    nudge(a, ME, warmth=warmth * scale, trust=trust * scale, tension=tension * scale, why=why,
          weight=weight + (1 if times > 2 else 0), t=t, extra={"letdown": True})
    return times


def woken(contact, t=None):
    """He woke them: once is nothing; again this week starts to grate — and is remembered."""
    from ..store import events
    t = t or time.time()
    week = [e for e in events.of(contact.id, "bond", since=t - 7 * _DAY)
            if (e.get("data") or {}).get("woken")]
    times = len(week) + 1
    when = time.strftime("%-I:%M %p", time.localtime(t)).lower()
    why = (f"he woke you at {when}" if times == 1 else
           f"he woke you again at {when} — {times} times this week")
    return nudge(contact.id, ME, warmth=-0.5 * max(0, times - 1), tension=0.6 * times, weight=min(3, times), t=t,
                 why=why, extra={"woken": True}), times
