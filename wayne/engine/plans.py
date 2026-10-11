"""
Plans he makes with them — "dinner at Ralli's, Friday at eight, everyone?" —
kept, answered, and kept to.

Whoever's asked answers their own way, from what their day holds then: Dick's
in, Jason's got a thing, Cass is a maybe. The answer rides on what they'd say
anyway, as an [rsvp: yes|no|maybe] at its end. On the day, the ones who said
yes set off in time to get there — by road, as long as the roads take — and
are there together: on the map, on each other's hover cards, and in what they
say. A maybe is a coin they toss on the day.
"""
import datetime
import hashlib
import json
import re
import threading
import time
import uuid

from .. import store
from ..store import events
from . import places

_lock = threading.Lock()

# A plan, roughly: a when and a let's. Only a gate — what the plan is, the model reads.
_WHEN = re.compile(
    r"\b(tonight|today|tomorrow|this (?:evening|afternoon|weekend|week)|next (?:week|weekend|\w+day)|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|weekend|noon|midnight|lunchtime|"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|at \d{1,2}(?::\d{2})?\b|o'?clock)\b", re.I)
_LETS = re.compile(
    r"\b(let'?s|we'?re (?:going|getting|doing|having|heading)|we are going|come (?:to|with|over|along)|"
    r"join (?:us|me)|meet (?:me|us|up)|dinner|lunch|brunch|breakfast|drinks?|coffee|movie|film|game|match|"
    r"party|bbq|barbecue|picnic|bowling|karaoke|(?:everyone|everybody|anyone)\s+(?:free|in|up for|coming|want|keen|fancy|"
    r"around|game)|who'?s in|you in|are you free|"
    r"free (?:on|for|tonight|tomorrow)|reservation|booked|table for)\b", re.I)
RSVP = re.compile(r"\[\s*rsvp\s*:\s*(yes|no|maybe)\s*\]", re.I)
ANSWERS = ("yes", "no", "maybe")


def all_plans():
    """Every plan kept: the space "plans", a row a plan (see wayne.store.kv)."""
    return store.space("plans").list()


def _save(rows):
    # The last few weeks' worth: a plan long past is only a memory (and an event: see add).
    keep = [p for p in rows if p.get("until", 0) > time.time() - 14 * 86400][-60:]
    store.space("plans").replace({p["id"]: p for p in keep})


def maybe_proposal(text):
    """Whether this could be him making a plan — a when and a let's. The model decides if it is."""
    return bool(text) and bool(_WHEN.search(text)) and bool(_LETS.search(text))


def read(text, invited, model, options, now=None):
    """
    The plan in what he said, as the model reads it — what, where, when, who —
    or None if he wasn't making one. `invited` is who could be asked: the chat's
    members, or the one he's talking to, as (id, first name).
    """
    from . import model as llm
    now = now or time.time()
    today = datetime.datetime.fromtimestamp(now)
    names = ", ".join(name for _, name in invited)
    instruction = (
        f"Today is {today.strftime('%A %-d %B %Y, %H:%M')}. Bruce wrote, to {names}:\n\"{text}\"\n\n"
        "Is he making a plan to meet up — a definite time to be somewhere together? If so, read it: what it "
        "is (a few words, as you'd say it: \"dinner at Ralli's\"), where (the place's name, or \"\" if he "
        "didn't say), the date (YYYY-MM-DD — work out \"Friday\" or \"tomorrow\" from today) and time (HH:MM, "
        "24-hour; a sensible one if he only said \"dinner\" or \"tonight\"), how many hours it'll last, and who "
        f"he's asking — [\"everyone\"], or first names from: {names} — and whether it only happens because of "
        "him (needs_him: he's called them together to talk, it's about him or his thing, or they'd have no reason "
        "to meet without him — not a family dinner, which they'd have anyway), and as which of him he'll be there "
        "(as: \"batman\" for anything in the suit — a rooftop meet, a stakeout, the job — otherwise \"bruce\"). A "
        "question about a plan already made, a joke, or a maybe-someday isn't one. Return JSON only: "
        '{"plan": true, "what": "...", "where": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "hours": 2, '
        '"who": ["everyone"], "needs_him": false, "as": "bruce"} or {"plan": false}')
    try:
        reply = llm.ask(model, [{"role": "user", "content": instruction}],
                        {**options, "temperature": 0.1, "num_predict": 140}, fmt="json", purpose="reading a plan",
                        background=False)
        got = json.loads(reply)
    except Exception:
        return None
    if not got.get("plan"):
        return None
    try:
        at = datetime.datetime.strptime(f"{got['date']} {got['time']}", "%Y-%m-%d %H:%M").timestamp()
    except (KeyError, ValueError, TypeError):
        return None
    if not now - 1800 < at < now + 21 * 86400:
        return None         # in the past, or so far off it's a someday
    hours = got.get("hours")
    hours = min(8.0, max(0.5, float(hours))) if isinstance(hours, (int, float)) else 2.0
    who = [str(w).strip().lower() for w in got.get("who") or [] if str(w).strip()]
    asked = [cid for cid, name in invited if not who or "everyone" in who or name.lower() in who]
    where = str(got.get("where") or "").strip()
    found = places.resolve(where) if where else None
    return {"what": str(got.get("what") or "a plan").strip()[:80], "where": found["name"] if found else where,
            "at": at, "until": at + hours * 3600, "who": asked or [cid for cid, _ in invited],
            **({"needs_him": True} if got.get("needs_him") is True else {}),
            **({"as": "batman"} if str(got.get("as") or "").lower() == "batman" else {})}


_read = {}


def read_once(text, invited, model, options, now=None):
    """`read`, once per thing he said: on a group call, everyone hears the same plan."""
    # Per hour as well as per words: "dinner tonight at 8, everyone?" said again on Friday is Friday's plan.
    key = (text, tuple(sorted(invited)), time.strftime("%Y-%m-%d %H", time.localtime(now or time.time())))
    if key not in _read:
        if len(_read) > 40:
            _read.clear()
        _read[key] = read(text, invited, model, options, now)
    return _read[key]


def add(plan, made_in=""):
    """Keep a plan he's made; the same plan said twice (the same time and place) is one plan."""
    with _lock:
        rows = all_plans()
        merged = None
        for p in rows:
            if abs(p["at"] - plan["at"]) < 1800 and p.get("where", "") == plan.get("where", ""):
                p["who"] = sorted(set(p["who"]) | set(plan["who"]))
                _save(rows)
                merged = p
                break
        if merged is None:
            plan = {**plan, "id": uuid.uuid4().hex[:10], "made_in": made_in, "made": time.time(), "answers": {}}
            rows.append(plan)
            _save(rows)
    if merged is not None:
        try:
            from . import engagements
            engagements.rewith(merged["id"], ", ".join(merged["who"]))     # his diary says who, now
        except Exception:
            pass
        return merged
    try:
        # His too: in the diary, where being there is what counts (engine.engagements).
        from . import engagements
        engagements.agree(plan["what"], plan.get("where", ""), plan["at"], ", ".join(plan["who"]), f"plan:{plan['id']}",
                          as_="batman" if plan.get("as") == "batman" else "bruce", source="plan", plan=plan["id"],
                          until=plan.get("until"))
    except Exception:
        pass
    events.add("plan.made", f"Bruce asked {', '.join(plan['who'])} to {plan['what']}{_place(plan)}, "
               f"{time.strftime('%A %-d %B at %H:%M', time.localtime(plan['at']))}",
               data={"plan": plan["id"], "at": plan["at"], "until": plan["until"], "who": plan["who"]},
               links=[("plan", plan["id"]), ("place", plan.get("where", ""))] + [("contact", c) for c in plan["who"]],
               audience=list(plan["who"]))
    return plan


def answer(plan_id, contact_id, said, why=""):
    """Their yes, no or maybe — the last one they gave."""
    if said not in ANSWERS:
        return
    with _lock:
        rows = all_plans()
        for p in rows:
            if p["id"] == plan_id:
                p.setdefault("answers", {})[contact_id] = {"said": said, "why": why[:160], "at": time.time()}
                _save(rows)
                return


def move(plan_id, what=None, where=None, at=None, until=None):
    """
    He's changed it in his diary: the plan moves with it — what, where, when — so the family go to the right place
    at the right time. Returns the plan, or None.
    """
    with _lock:
        rows = all_plans()
        for p in rows:
            if p["id"] == plan_id:
                changed = (at is not None and abs(p["at"] - at) > 60) or (where is not None and where != p.get("where"))
                p.update({k: v for k, v in (("what", what), ("where", where), ("at", at), ("until", until))
                          if v is not None})
                if changed:
                    p["moved_at"] = time.time()
                _save(rows)
                return p
    return None


def he_cant(plan_id, why=""):
    """
    He's called it off for himself, saying why or not: the rest can still go — unless it was his to host, or only
    ever him and one of them (see off). Returns the plan, or None.
    """
    with _lock:
        rows = all_plans()
        for p in rows:
            if p["id"] == plan_id:
                p["his"] = {"said": "no", "why": str(why or "")[:200], "at": time.time()}
                _save(rows)
                return p
    return None


def off(plan):
    """
    Whether it's off for everyone now he's out: his party; something that only ever happened because of him (he
    called them together to talk); or not enough of them left who'd go without him — one, or two who aren't close
    (Tim and Jason don't make an evening of it). A family dinner with three of them still on goes ahead.
    """
    if (plan.get("his") or {}).get("said") != "no":
        return False
    if plan.get("host") == "me" or plan.get("needs_him"):
        return True
    still = [c for c in plan.get("who") or [] if going(plan, c)]
    if len(still) < 2:
        return True
    if len(still) == 2:
        from . import bonds
        a, b = still
        return min(bonds.state(a, b)["warmth"], bonds.state(b, a)["warmth"]) < 55
    return False


def reconsidering(contact_id, now=None):
    """Plans he's dropped out of that they'd said yes to and haven't said since whether they still go, soonest first."""
    now = now or time.time()
    return [p for p in kept(contact_id, now) if (p.get("his") or {}).get("said") == "no" and not off(p)
            and p["answers"][contact_id].get("at", 0) < p["his"].get("at", 0)]


def _his_word(p, contact_id=""):
    """What he's said about coming himself, for their prompt: '' unless he's called it off."""
    his = p.get("his") or {}
    if his.get("said") != "no":
        return ""
    why = f" (he said: \u201c{his['why']}\u201d)" if his.get("why") else ""
    if off(p):
        return f" Bruce has called it off{why}, so it's off — " + (
            "it was his to host." if p.get("host") == "me" else "it only ever made sense with him there."
            if p.get("needs_him") else "there's not enough of you left to make it worth it.")
    if (p.get("answers") or {}).get(contact_id, {}).get("at", 0) < his.get("at", 0):
        return (f" Bruce has since said he can't make it{why}. Whether you still go without him is yours — some "
                "things are still worth it with the others, some only made sense with him there. If it comes up, "
                "say, and end what you write with [rsvp: yes] if you're still going or [rsvp: no] if not.")
    return f" Bruce has since said he can't make it{why} — it's going ahead without him."


def pending(contact_id, now=None):
    """Plans they've been asked to and haven't answered, soonest first."""
    now = now or time.time()
    return sorted((p for p in all_plans() if contact_id in p["who"] and contact_id not in p.get("answers", {})
                   and p["until"] > now), key=lambda p: p["at"])


def kept(contact_id, now=None, ahead=7 * 86400):
    """Plans they've said yes or maybe to, still to come (or under way), soonest first."""
    now = now or time.time()
    return sorted((p for p in all_plans() if p.get("answers", {}).get(contact_id, {}).get("said") in ("yes", "maybe")
                   and p["until"] > now and p["at"] < now + ahead), key=lambda p: p["at"])


def when(at, now=None):
    """'tonight at 8', 'tomorrow at 7:30', 'Friday at 8' — as people say it."""
    now = now or time.time()
    then, today = datetime.datetime.fromtimestamp(at), datetime.datetime.fromtimestamp(now).date()
    clock = then.strftime("%-I:%M").replace(":00", "") + ("am" if then.hour < 12 else "pm")
    gap = (then.date() - today).days
    day = ("tonight" if then.hour >= 17 else "today") if gap == 0 else "tomorrow" if gap == 1 else \
        then.strftime("%A") if gap < 7 else then.strftime("%A %-d %B")
    return f"{day} at {clock}"


def _place(plan):
    """' at Ralli's Family Restaurant', unless the plan already says where ('dinner at Ralli's')."""
    where = plan.get("where") or ""
    first = where.split()[0].lower() if where else ""
    return f" at {where}" if where and first not in plan["what"].lower() else ""


def _name(cid, directory):
    found = directory.get(cid) if directory else None
    return found.name if found else cid


def note(contact, directory=None, now=None, schedule=None):
    """
    For their prompt: plans they've been asked to and not answered — with what
    their day holds then, and how to answer — and the ones they've said they'll
    be at, with who else is and isn't coming. '' when there are none.
    """
    now = now or time.time()
    lines = []
    for p in [p for p in pending(contact.id, now) if not off(p)][:2]:
        then = schedule(p["at"]) if schedule else ""
        others = [f"{_name(c, directory)} {a['said']}" for c, a in p.get("answers", {}).items() if c != contact.id]
        lines.append(
            f"He's asked you to come to {p['what']}{_place(p)}, {when(p['at'], now)}."
            + (f" Around then you'd be {then}." if then else "")
            + (f" So far: {', '.join(others)}." if others else "") + _his_word(p, contact.id)
            + " You haven't said yet. Say whether you're in, your way — yes, no (and why, as you'd put it), "
            "or maybe — and end what you write with [rsvp: yes], [rsvp: no] or [rsvp: maybe].")
    for p in kept(contact.id, now)[:2]:
        mine = p["answers"][contact.id]["said"]
        coming = [_name(c, directory) for c, a in p.get("answers", {}).items() if a["said"] == "yes" and c != contact.id]
        out = [_name(c, directory) for c, a in p.get("answers", {}).items() if a["said"] == "no"]
        lines.append(
            f"You've said you'll {'come' if mine == 'yes' else 'maybe come'} to {p['what']}{_place(p)}, "
            f"{when(p['at'], now)}" + (f" — with {', '.join(coming)}" if coming else "")
            + (f"; {', '.join(out)} can't make it" if out else "") + "." + _his_word(p, contact.id))
    return " ".join(lines)


def _coin(plan_id, contact_id):
    return int(hashlib.sha1(f"{plan_id}:{contact_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def going(plan, contact_id):
    """Whether they're actually going: a yes is a yes; a maybe, a coin they toss."""
    said = plan.get("answers", {}).get(contact_id, {}).get("said")
    return said == "yes" or (said == "maybe" and _coin(plan["id"], contact_id) < 0.5)


def block(contact, t=None, lead_minutes=None):
    """
    The plan they're keeping at t, as a block of their day: on the way there
    first — setting off early enough to arrive on time — then there, with
    whoever else is going. None if nothing's on.
    """
    t = t or time.time()
    for p in store.space("plans").view():          # every routine, every pulse: looked at, never changed
        if contact.id not in p["who"] or not going(p, contact.id) or off(p):
            continue
        lead = (lead_minutes(p) if lead_minutes else 30) * 60
        if p["at"] - lead <= t < p["until"]:
            company = [c for c in p["who"] if c != contact.id and going(p, c)]
            start = time.localtime(p["at"] - lead)
            return {"from": start.tm_hour + start.tm_min / 60, "to": 0, "doing": p["what"],
                    "status": "idle", "where": p["where"], "with": company, "plan": p["id"]}
    return None
