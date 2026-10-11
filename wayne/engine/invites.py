"""
The family asking him to things — the other way round from a plan he makes.

Dick's movie night, Tim's games night, a Sunday at the Manor are theirs, on their weeks (engine.week); now and then
one of them asks him along. Or it's something of their own, once: Cass's recital, Barbara's chess club final, a jazz
night Lucius has two tickets for — written by the model from who they are, somewhere on the map. Either way it's a
text from them (session.reach_out, "invite") and a line in his diary, invited, to say yes or no to — from the
diary, or just by answering them. And afterwards, it counts with them: he came, or he said he would and didn't
(engine.engagements).
"""
import datetime
import json
import random
import time

from . import engagements, week

# Who'd ask him to something, and how often against one another.
ASKERS = {"nightwing": 3, "robin": 2, "batgirl": 2, "orphan": 2, "lucius": 1, "catwoman": 1, "alfred": 1, "redhood": 0.4,
          "batwing": 0.4}
GAP = 18 * 3600            # at most one invitation in this long, across all of them


def _pending(cid):
    return [e for e in engagements.all_engagements() if e.get("inviter") == cid and e["status"] == "invited"]


def _theirs(contact, now):
    """One of their own regular things in the coming week that he's not part of — (habit, at, until) or None."""
    for days in range(1, 7):
        day = datetime.date.fromtimestamp(now) + datetime.timedelta(days=days)
        noon = time.mktime((day.year, day.month, day.day, 12, 0, 0, 0, 0, -1))
        for habit in week.on(contact, noon):
            if not habit.get("together") or not habit.get("where"):
                continue
            start, end = week._hours(habit.get("from", "0")), week._hours(habit.get("to", "0"))
            at = noon - 12 * 3600 + start * 3600
            until = noon - 12 * 3600 + (end if end > start else end + 24) * 3600
            if not any(e.get("inviter") and abs(e["at"] - at) < 3600 for e in engagements.all_engagements()):
                return habit, at, until
    return None


def _one_off(contact, model_name, options, now):
    """Something of their own, once, they'd ask him to — the model's, from who they are, somewhere real."""
    from . import inbox
    from . import model as llm
    soon = datetime.date.fromtimestamp(now)
    likes = "; ".join(i.get("title", "") for i in ((getattr(contact, "interests", None) or {}).get("likes") or [])[:5])
    instruction = (
        f"You are {contact.full_name}. Think of one thing of your own in the next ten days (after {soon.isoformat()}) "
        f"you'd ask Bruce along to — a show, a match, a recital, a gallery, a dinner, a night out — true to who you are "
        "in the comics, specific and a little surprising (the thing only you would ask him to)"
        + (f" (you're into: {likes})" if likes else "") + ". " + inbox._real_places() + inbox._who()
        + ' JSON only: {"what": "what it is, in a few words", "where": "a place from the list, by its exact name", '
          '"date": "YYYY-MM-DD", "time": "HH:MM", "hours": 2, "why": "why you want him there, in a line"}')
    try:
        reply = llm.ask(model_name, [{"role": "user", "content": instruction}],
                        {**options, "temperature": 0.8, "num_predict": 200}, fmt="json", purpose=f"{contact.id}'s invitation")
        found = json.loads(reply)
    except Exception:
        return None
    at = engagements.when_from(found, now)
    if at is None or at < now + 3600 or not str(found.get("what") or "").strip():
        return None
    try:
        hours = max(1.0, min(5.0, float(found.get("hours") or 2)))
    except (TypeError, ValueError):
        hours = 2.0
    return {"what": str(found["what"]).strip()[:100], "where": str(found.get("where") or "")[:80], "at": at,
            "until": at + hours * 3600, "why": str(found.get("why") or "")[:160]}


def make(directory, model_name, options, now=None, rng=random):
    """
    Maybe one of them asks him to something now: (contact, diary row, what to say) — or None. At most one in GAP,
    one waiting answer each.
    """
    now = now or time.time()
    last = max((e.get("answered_at", 0) for e in engagements.all_engagements() if e.get("source") == "invite"), default=0)
    if now - last < GAP:
        return None
    pool = [(directory.get(cid), w) for cid, w in ASKERS.items()
            if directory.get(cid) is not None and not _pending(cid)]
    if not pool:
        return None
    contact = rng.choices([c for c, _ in pool], weights=[w for _, w in pool])[0]
    regular = _theirs(contact, now)
    if regular and rng.random() < 0.6:
        habit, at, until = regular
        event = {"what": habit.get("doing", "")[:100], "where": habit.get("where", ""), "at": at, "until": until,
                 "why": ""}
    else:
        event = _one_off(contact, model_name, options, now)
        if event:
            event["one_off"] = True
    if not event:
        return None
    row = engagements.invited(event["what"], event["where"], event["at"], event["until"], contact, now=now,
                              one_off=bool(event.get("one_off")))
    from . import plans
    about = (f"{event['what']}" + (f" at {event['where']}" if event["where"] else "") + f", {plans.when(event['at'])}"
             + (f" — {event['why']}" if event["why"] else ""))
    return contact, row, about
