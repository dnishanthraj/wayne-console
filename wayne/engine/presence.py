"""
What a contact is doing right now, and what they mean to do next.

Everything about how a contact behaves on their phone comes from this one
state, rather than from a rule per behaviour:

  * the dot beside their name — online, idle, busy, offline;
  * how soon they read a text, and whether they answer it properly or in a
    line from the middle of something;
  * when they text first, or ring — because they said they would, because
    the errand he sent them on is done, or because something came to mind;
  * what they're in the middle of when he calls.

Three things set it, in order of precedence:

  1. **What the conversation established.** He sends Dick to the docks; Lucius
     says he's going into a board meeting; Selina says she's going to bed.
     After each exchange a short model pass reads what was said and records
     what they're now doing and for how long (see `initiative.afterthought`).
     Nobody should be online at the docks.
  2. **Being in a conversation.** Someone who has just texted back is holding
     their phone, and stays online for a few minutes — so a back-and-forth
     runs at the speed of a back-and-forth, not one reply an hour.
  3. **Their routine.** Asleep at night, at work by day, on patrol on some
     nights but not others — declared in the profile, and decided per day
     with a seeded draw so it never flickers between refreshes. Outside the
     routine, free time drifts between online and idle in twenty-minute
     spells.

And intentions — "I'll call you when I'm out", "text me when it's done",
"report back" — are kept here with the time they fall due. The console's
pulse carries them out (see `Console.pulse`).
"""
import datetime
import hashlib
import math
import random
import re
import threading
import time
import uuid

from .. import store
from . import patrols, places, travel, week
from . import plans as plans_made

ONLINE, IDLE, BUSY, OFFLINE = "online", "idle", "busy", "offline"
# What the afterthought writes when it doesn't know — never a thing anyone is doing.
NOTHING = {"null", "none", "not specified", "unspecified", "unknown", "n/a", "na", "nothing", "not mentioned",
           "not stated", "no", "false", "unclear"}
STATUSES = (ONLINE, IDLE, BUSY, OFFLINE)

# How long someone stays online after they last sent or read something of his.
ENGAGED_FOR = 300
# Free time comes in spells this long: phone in hand, or put down.
_SPELL = 1200
_LEADS = {}         # (plan, contact) -> minutes to set off before it

_lock = threading.Lock()
_registry = {}


def of(contact):
    """The one Presence for a contact, shared by the console and their session."""
    with _lock:
        found = _registry.get(contact.id)
        if found is None or found.contact is not contact:
            found = _registry[contact.id] = Presence(contact)
        return found


def _draw(*parts):
    """A random number fixed by its inputs: the same day gives the same answer."""
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


def _drift(seed, t, knot):
    """
    Noise that wanders rather than jumps: a value in [0, 1] at time t, eased
    between random values `knot` seconds apart — the same at the same moment,
    for everyone who asks.
    """
    k = t / knot
    i = math.floor(k)
    f = k - i
    f = f * f * (3 - 2 * f)
    a, b = _draw(seed, i), _draw(seed, i + 1)
    return a + (b - a) * f


def tide(t=None):
    """
    How much the whole city is on its phones right now, about 0.4–1.9: the
    day's own shape — the commute, lunch, the evening scroll, the dead small
    hours — on waves nobody could name, so now and then everyone happens to be
    online at once, and now and then nobody is.
    """
    t = t or time.time()
    local = time.localtime(t)
    hour = local.tm_hour + local.tm_min / 60
    shape = (0.5 if hour < 6 else 1.05 if hour < 9.5 else 0.85 if hour < 12 else 1.15 if hour < 13.75
             else 0.85 if hour < 17 else 1.0 if hour < 20 else 1.3 if hour < 23.5 else 0.95)
    wave = 0.7 + 0.6 * _drift("tide", t, 1500)
    surge = 1.0 + 0.45 * max(0.0, _drift("surge", t, 700) - 0.82) / 0.18     # the odd everyone-at-once
    return shape * wave * surge


def mood(contact, t=None):
    """
    How glued to their phone they are right now, about 0.3–2: their habit —
    Dick lives on his, Randy barely touches his — carried on slow waves of
    their own: a flowing hour, then an afternoon of one-word answers.
    """
    t = t or time.time()
    habit = float((contact.texting_pace or {}).get("phone", 0.3))
    knot = 1200 + 1800 * _draw(contact.id, "knot")          # how long their spells run: 20–50 minutes
    return (0.55 + habit * 1.6) * (0.4 + 1.2 * _drift(f"mood:{contact.id}", t, knot))


def engagement(contact, t=None):
    """The two together — the city's pull and their own — kept to something a person could be."""
    return max(0.3, min(2.2, tide(t) * mood(contact, t)))


# Two plans a short walk apart put people in the same place (in map units).
TOGETHER_WITHIN = 6.0


def together(presence, t=None):
    """
    (leader, group) — who this person is actually with at t, as Presences,
    and whose place the group is in.

    A plan or a conversation can say "with Cass"; it only holds if it holds
    for Cass too: she said the same, or she's free then, or her own plan
    already has her there. What she said about where she is outranks anyone's
    plan; someone asleep or out of reach is in nobody's plans but their own;
    and Jason and Randy, who keep their lives to themselves, are with someone
    only when they say so too. Everyone in a group is in one place — so the
    map, the card under their name and what each of them is told all agree.
    """
    from ..contacts import directory
    t = t or time.time()
    book = directory()
    everyone = {presence.contact.id: presence}
    # Who can be found together is the family and their circle on the app — not Jim, who keeps his own
    # company and isn't on it (whoever's asked about him gets nobody).
    members = [c for c in book if getattr(c, "family_app", True)]
    if not getattr(presence.contact, "family_app", True):
        members = []
    for contact in members:
        if contact.id not in everyone:
            everyone[contact.id] = _registry.get(contact.id) or of(contact)
    # One order for everyone, whoever's asking: put first in their own list, Dick
    # and Tim each led the pair — Dick at home, Tim "in Blüdhaven", together.
    ids = [c.id for c in members]
    order = {cid: i for i, cid in enumerate(ids + ([presence.contact.id] if presence.contact.id not in ids else []))}
    seen, raw = {}, {}

    def own(cid):
        if cid not in raw:
            raw[cid] = everyone[cid]._situation(t)
        return raw[cid]

    def situation(cid):
        if cid not in seen:
            block, company, firm = own(cid)
            # Out as a pair — whether the roster says so or one of them just said "heading out on patrol"
            # (which used to split the pair while their dots walked the roofs side by side).
            if places.is_patrol(block.get("doing")) and not block.get("follow"):
                # Out as a pair tonight: Dick and Tim on the same roofs — while both of them are out.
                mates = [m for m in patrols.partner(cid, t) if m in everyone and m not in company
                         and places.is_patrol(own(m)[0].get("doing"))]
                company = list(company) + mates
            seen[cid] = (block, company, firm)
        return seen[cid]

    def near(a, b):
        here = places.resolve(everyone[a]._home_is_home(situation(a)[0].get("where") or ""))
        there = places.resolve(everyone[b]._home_is_home(situation(b)[0].get("where") or ""))
        return bool(here and there) and math.dist((here["x"], here["y"]), (there["x"], there["y"])) <= TOGETHER_WITHIN

    def holds(a, b):
        """a says they're with b: is b with a?"""
        if b not in everyone or b == a:
            return False
        for follower, followed in ((a, b), (b, a)):
            if situation(follower)[0].get("follow") == followed:
                # One went to be with the other: wherever that is, they're there together — unless the
                # other's asleep, and the one who came is just at the door.
                return situation(followed)[0].get("status") != OFFLINE
        block, company, firm = situation(b)
        if a in company:
            return True             # they both say so
        if firm or block.get("status") == OFFLINE:
            return False            # b said where they are, or is asleep or out of reach
        if not getattr(everyone[b].contact, "shares_status", True):
            return False            # in nobody's plans but their own, unless they say so too
        return not block or near(a, b)      # free then, or already there by their own plan

    links = {cid: set() for cid in everyone}
    for a in everyone:
        for b in situation(a)[1]:
            if holds(a, b):
                links[a].add(b)
                links[b].add(a)
    group, todo = {presence.contact.id}, [presence.contact.id]
    while todo:
        for other in links[todo.pop()] - group:
            group.add(other)
            todo.append(other)
    # The place is the one whoever arranged it had in mind: what someone said
    # before any plan, someone who named the others before someone named.
    leader = min(group, key=lambda cid: (not situation(cid)[2],
                                         not (set(situation(cid)[1]) & group), order[cid]))
    return everyone[leader], [everyone[cid] for cid in sorted(group, key=order.get)]


def sharing(company):
    """Those of them who let him see where they are: "with Jason" would give Jason away."""
    from ..contacts import directory
    book = directory()
    return [c for c in company if book.get(c) is None or getattr(book.get(c), "shares_location", True)]


def describe_until(until, now=None):
    """'for about an hour', 'until about 7' — how long, said the way a person would."""
    if not until:
        return ""
    left = until - (now or time.time())
    if left <= 0:
        return ""
    if left < 50 * 60:
        return f"for another {max(5, int(round(left / 300.0)) * 5)} minutes or so"
    if left < 100 * 60:
        return "for about another hour"
    return f"until about {time.strftime('%-I%p', time.localtime(until)).lower()}"


# Words a plan's "doing" can start with that read straight after "you're": "on patrol",
# "at the clock tower", "asleep". A noun ("dinner", "late night snacks") doesn't.
_STATE_WORDS = {"on", "in", "at", "out", "off", "asleep", "awake", "home", "with", "up", "back", "away", "still",
                "just", "about", "busy", "free", "between", "around", "over", "inside", "outside", "stuck",
                "halfway", "deep", "done", "tied", "hiding", "laid", "holed"}
# -ing words that aren't doing anything: "Nightwing patrol", "morning run".
_NOT_DOING = {"nightwing", "batwing", "morning", "evening", "something", "nothing", "anything", "everything", "king",
              "thing", "wing", "ring", "spring", "string", "building", "wedding", "pudding", "ceiling", "lightning"}


_NAMES = {"nightwing", "robin", "batgirl", "batwing", "batman", "bruce", "gotham", "blüdhaven", "wayne", "red", "i"}


def _lowered(d):
    """The front of a phrase lower-cased for the middle of a sentence — not a name's: 'Nightwing patrol' stays."""
    first = d.split()[0]
    if first.lower() in _NAMES or any(ch.isupper() for ch in first[1:]):
        return d
    return d[0].lower() + d[1:]


def _doing_phrase(doing):
    """What they're doing, as it goes after "you're" or "they're" — 'patrol' is 'on patrol' — or '' if it can't."""
    d = (doing or "").strip().rstrip(".")
    if not d:
        return ""
    first = d.split()[0].lower()
    if first in ("patrol", "patrolling") and len(d.split()) == 1:
        return "on patrol"
    if (first.endswith("ing") and first not in _NOT_DOING) or first in _STATE_WORDS:
        return _lowered(d)
    return ""


def _you_are(doing):
    """'you're watching the docks', 'you're on patrol' — or, for a plan's noun, 'you've got late night snacks …'."""
    phrase = _doing_phrase(doing)
    if phrase:
        return f"you're {phrase}"
    d = (doing or "").strip().rstrip(".")
    return f"you've got {_lowered(d)}" if d else "you're free"


def how_far(d):
    """A distance on the map (a unit's about 150 m), as someone would put it to whoever's at the other end."""
    metres = d * 150
    if metres < 150:
        return "right by you"
    if metres < 500:
        return "a couple of streets from you"
    if metres < 1000:
        return f"about {int(round(metres, -2))} m from you"
    km = metres / 1000
    return f"about {km:.0f} km from you" if km >= 10 else f"about {km:.1f} km from you"


def _masked(contact_id, t):
    """Whether what they're up to right now is the work — a case, a patrol — which outsiders never hear of."""
    from ..contacts import directory
    from . import cases
    if cases.active(contact_id):
        return True
    found = directory().get(contact_id)
    them = (_registry.get(contact_id) or of(found)) if found else None
    return bool(them) and places.is_patrol(them._situation(t)[0].get("doing"))


def glance(contact, t=None, here=None, viewer=None):
    """
    Where one of them is and what they're doing, as the family's app shows it to
    someone standing at `here` (x, y): "on the way to the shots fired at Central
    Business District, about a minute out — right by you". The case they're on,
    if they're on one — the board shows that, even for the ones who keep their dot
    to themselves. '' if there's nothing to see.
    """
    from ..contacts import directory
    from . import cases, groupchat
    t = t or time.time()
    if viewer is not None and groupchat.outside(viewer) and _masked(contact.id, t):
        return ""           # Selina knows Dick left; never that he's on a case, or on the roofs
    book = directory()
    them = _registry.get(contact.id) or of(contact)
    shows_dot = getattr(contact, "shares_location", True)
    case = cases.active(contact.id)
    bits = []
    if case and case.get("status") != "closed":
        mine = (case.get("members") or {}).get(contact.id) or {}
        what = f"the {case['kind'].lower()} at {case['place']}"
        if mine.get("status") == "on scene":
            bits.append(f"on scene at {what}, working it")
        else:
            due = mine.get("joined", case.get("opened_at", t)) + mine.get("travel", case.get("travel", 5)) * 60
            left = max(1, round((due - t) / 60))
            bits.append(f"on the way to {what}, about {left} min out")
        mates = [book.get(c).name if book.get(c) else "Bruce" for c in cases.team(case) if c != contact.id]
        if mates:
            bits.append("with " + " and ".join(mates) + " on it")
    elif not shows_dot:
        return ""
    else:
        where, company = them.whereabouts(t)
        block, _company, _firm = them._situation(t)
        doing = (block.get("doing") or "").strip()
        trip = them.trip(where, them.spot(where, t), t) if where else None
        label = them.label(where)
        if trip:
            left = max(1, round((trip["end"] - t) / 60))
            bits.append(f"on the way to {label}" + (f", {trip['by']}" if trip.get("by") else "")
                        + f", about {left} min out" + (f" (for: {doing})" if doing else ""))
        elif doing:
            phrase = _doing_phrase(doing) or _lowered(doing)
            bits.append(phrase + (f" — {label}" if label and label.split(",")[0].lower() not in doing.lower() else ""))
        elif label:
            bits.append(f"at {label}")
        mates = [book.get(c).name for c in sharing(company) if book.get(c)]
        if mates:
            bits.append("with " + " and ".join(mates))
        if them.now(t)["status"] == OFFLINE:
            bits.append("phone off")
    spot = them.position(t) if shows_dot else ((case["x"], case["y"]) if case else None)
    if here and spot:
        bits[-1] += f" — {how_far(math.dist(here, spot))}"
    return ", ".join(bits)


class Presence:
    def __init__(self, contact):
        self.contact = contact
        # The afterthought writes from a worker thread while the console's
        # loop reads and updates: one lock round every change and save, or a
        # kept promise came back from a stale copy and was kept twice.
        self._lock = threading.RLock()
        self._state = self._load()
        # When the page last looked: a move it saw happen is travelled; one that
        # happened while nobody watched (overnight, the console off) just is.
        self._looked = None

    # --- storage -----------------------------------------------------------

    # Their state is the space presence/<id>, a row per key: a word to him rewrites last_active, not the
    # week of trips beside it — saving writes only the keys that changed.

    @property
    def _space(self):
        return store.space(f"presence/{self.contact.id}")

    def _load(self):
        state = self._space.dict()
        state.setdefault("activity", None)
        state.setdefault("last_active", 0)
        state.setdefault("intents", [])
        return state

    def save(self):
        with self._lock:
            self._space.replace(self._state)

    # --- the routine -------------------------------------------------------

    def _routine(self, t):
        """
        The routine block covering time t, if there is one today. A loose
        schedule, not a timetable: each block's edges drift by up to an hour or
        so, differently every day, and some days it doesn't happen at all.
        """
        # A plan made with him comes first: they set off in time, and they're there.
        kept = plans_made.block(self.contact, t, self._lead)
        if kept:
            return kept
        # Something of their own they asked him to, once — Cass's recital, Lucius's jazz night: they go, him or not.
        from . import engagements
        theirs = engagements.their_block(self.contact, t, self._lead)
        if theirs:
            return theirs
        # Then what they keep with each other — Sunday lunch at Jim's, movie night at Dick's: on for all of them.
        joint = week.together_block(self.contact, t)
        if joint:
            return joint
        local = time.localtime(t)
        hour = local.tm_hour + local.tm_min / 60
        made = self._state.get("planned_at") or {}
        # A day sketched at midnight doesn't reach back over the evening already had:
        # before the plan was made, what happened was what the routine had them doing.
        plans = {key: blocks for key, blocks in (self._state.get("plans") or {}).items() if t >= made.get(key, 0)}
        # Today, and yesterday for a night that began then — by the calendar, not "24 hours ago", which on the
        # night the clocks change landed on the day before yesterday and lost the night's patrol.
        today = datetime.date.fromtimestamp(t)
        days = ((today, 0), (today - datetime.timedelta(days=1), 24))
        for day, carry in days:
            key = day.isoformat()
            for block in plans.get(key) or []:
                start, end = block["from"], block["to"]
                if end <= start:
                    end += 24
                at = hour + carry
                if start <= at < end:
                    return block
        # A day with no plan of its own runs on their week: the gym on a Monday,
        # golf on a Saturday morning, dinner at the Manor every other Sunday.
        if not plans.get(today.isoformat()):
            habit = week.block(self.contact, t)
            if habit:
                return habit
        for index, block in enumerate(getattr(self.contact, "routine", ()) or ()):
            for day, carry in days:             # a night may have begun yesterday
                key = day.isoformat()
                if plans.get(key):
                    continue    # they planned that day themselves; the routine is only a fallback
                if "days" in block and day.weekday() not in block["days"]:
                    continue
                if _draw(self.contact.id, index, key) >= block.get("chance", 1.0):
                    continue     # not that day
                drift = block.get("drift", 1.25)
                start = block.get("from", 0) + (_draw(self.contact.id, index, key, "start") - 0.5) * 2 * drift
                end = block.get("to", 0) + (_draw(self.contact.id, index, key, "end") - 0.5) * 2 * drift
                if end <= start:
                    end += 24                   # runs past midnight
                at = hour + carry
                if start <= at < end:
                    return block
        return None

    def _lead(self, plan):
        """How long before a plan they set off: the drive from home to there, and five minutes' slack."""
        key = (plan["id"], self.contact.id)
        if key not in _LEADS:
            if len(_LEADS) > 500:
                _LEADS.clear()           # plans come and go; their lead times needn't stay forever
            home = places.resolve(getattr(self.contact, "home", "") or "")
            there = places.resolve(plan.get("where") or "")
            minutes = 30
            if home and there:
                try:
                    _pts, minutes = travel.route((home["x"], home["y"]), (there["x"], there["y"]), home["name"],
                                                 there["name"], mode=getattr(self.contact, "gets_about", "drive") or "drive")
                except Exception:
                    minutes = 30
            _LEADS[key] = max(10, min(90, round(minutes) + 5))
        return _LEADS[key]

    def has_plan(self, t=None):
        key = time.strftime("%Y-%m-%d", time.localtime(t or time.time()))
        return bool((self._state.get("plans") or {}).get(key))

    def plan_today(self, t=None):
        key = time.strftime("%Y-%m-%d", time.localtime(t or time.time()))
        return list((self._state.get("plans") or {}).get(key) or [])

    def set_plan(self, blocks, t=None):
        """Their own plan for the day — kept for today and yesterday's late night."""
        key = time.strftime("%Y-%m-%d", time.localtime(t or time.time()))
        with self._lock:
            plans = dict(self._state.get("plans") or {})
            plans[key] = blocks
            self._state["plans"] = dict(sorted(plans.items())[-2:])
            # When it was made: it holds from then on, never over what had already happened.
            made = {**(self._state.get("planned_at") or {}), key: t or time.time()}
            self._state["planned_at"] = {k: v for k, v in made.items() if k in self._state["plans"]}
            self.save()

    def _whim(self, t):
        """
        Something unplanned, now and then, in free time: walking the dog, out
        with a friend, at the bookshop. Decided per two-hour spell, so it holds
        while it lasts and doesn't flicker.
        """
        whims = self.contact.texting_pace.get("whims") or getattr(self.contact, "whims", ())
        if not whims or self.has_plan(t):
            return None     # a plan of their own already has the day's spontaneity in it
        spell = int(t // 7200)
        if _draw(self.contact.id, "whim", spell) >= self.contact.texting_pace.get("whim_rate", 0.18):
            return None
        whim = whims[int(_draw(self.contact.id, "which", spell) * len(whims))]
        start, end = whim.get("hours", (8, 22))
        hour = time.localtime(t).tm_hour
        if not (start <= hour < end if start < end else hour >= start or hour < end):
            return None     # nobody's on the golf course at three in the morning
        lasts = whim.get("minutes", 60) * 60
        begins = spell * 7200 + _draw(self.contact.id, "when", spell) * max(0, 7200 - lasts)
        return whim if begins <= t < begins + lasts else None

    def _free_time(self, t):
        """
        Phone in hand or put down, in spells — the same spell reads the same.
        Gotham's people live at night: in the evening and small hours their
        phones are out far more than in the day (`phone_night`).
        """
        pace = self.contact.texting_pace
        hour = time.localtime(t).tm_hour
        night = hour >= 20 or hour < 4
        share = pace.get("phone_night", pace.get("phone", 0.3)) if night else pace.get("phone", 0.3)
        # On the city's waves and their own: some evenings everyone's on, some
        # afternoons nobody is.
        share = min(0.95, share * engagement(self.contact, t))
        return ONLINE if _draw(self.contact.id, int(t // _SPELL)) < share else IDLE

    # --- now ---------------------------------------------------------------

    def now(self, t=None):
        """
        {"status", "doing", "until", "source", "last_active"} for time t.
        `doing` is a few words ("on patrol", "in a board meeting") or "".
        """
        t = t or time.time()
        last = self._state["last_active"]
        activity = self._activity_at(t)
        if activity:
            return {"status": activity["status"], "doing": activity["doing"],
                    "until": activity["until"], "source": "conversation", "last_active": last,
                    "terminal": activity.get("terminal", False)}
        # Just been on their phone with him: still in the thread, for as long as
        # they'd linger there — Tim for ages, Jason not long (`lingers`, seconds).
        linger = (self.contact.texting_pace or {}).get("lingers", ENGAGED_FOR)
        if 0 <= t - last < linger:
            return {"status": ONLINE, "doing": "", "until": last + linger,
                    "source": "engaged", "last_active": last}
        block = self._routine(t) or self._whim(t)
        if block:
            return {"status": block.get("status", BUSY), "doing": block.get("doing", ""),
                    "until": 0, "source": "routine", "last_active": last,
                    "terminal": block.get("terminal", False)}
        return {"status": self._free_time(t), "doing": "", "until": 0,
                "source": "free", "last_active": last}

    def line_key(self, t=None):
        """
        What their status line is about: the thing they're doing, or for
        free time — and for anyone who doesn't share — the day. A line is
        written once per key, so it changes when their situation does.
        """
        state = self.now(t)
        t = t or time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(t))
        if not self.contact.shares_status:
            return f"day:{day}"             # nothing to give away; a new mood a day
        if not state["doing"]:
            # Free time: a fresh line every few hours — the spell length drawn
            # per contact and day, so they don't all change on the hour together.
            span = 2 + int(_draw(self.contact.id, "span", day) * 4)     # 2–5 hours
            return f"free:{day}:{time.localtime(t).tm_hour // span}"
        return f"{state['source']}:{state['doing']}"

    def line(self, t=None):
        """
        The status line they wrote for now — or, until they've written one,
        the last they set. A status stays up until someone changes it.
        """
        lines = self._state.get("lines") or {}
        return lines.get(self.line_key(t)) or (list(lines.values())[-1] if lines else "")

    def has_current_line(self, t=None):
        return self.line_key(t) in (self._state.get("lines") or {})

    def set_line(self, key, text):
        with self._lock:
            lines = dict(self._state.get("lines") or {})
            lines[key] = text.strip()[:80]
            # Only the recent ones: yesterday's lines are no use to anyone.
            self._state["lines"] = dict(list(lines.items())[-12:])
            self.save()

    def whereabouts(self, t=None):
        """
        (place, [contact ids with them]) for right now — always from what
        they're actually doing: somewhere a conversation sent them, the place in
        their plan, a spot on their beat while they patrol (moving every
        quarter hour or so), or home when nothing puts them elsewhere. Anyone
        they're really with (see `together`) is in the same place, and has
        them in their company too.
        """
        t = t or time.time()
        leader, group = together(self, t)
        place = (self if leader is self else leader)._own_place(t)
        # Together once they're both there: whoever's still on the way to the
        # place isn't with anyone yet. Dick said Tim was over for Mario Kart
        # while Tim had twenty minutes of driving left.
        if self._travelling(place, t):
            return place, []
        company = [p.contact.id for p in group if p is not self and not p._travelling(place, t)]
        return place, company + [cid for cid in self._kept_together(place, t) if cid not in company]

    def _kept_together(self, place, t):
        """
        Those keeping a shared evening with them right here — Sunday lunch at Jim's, movie night at Dick's —
        even someone not on the family's app: Jim's with Barbara at his place because they both are.
        """
        joint = week.together_block(self.contact, t)
        if not joint:
            return []
        from ..contacts import directory
        book, out = directory(), []
        for cid in joint["with"]:
            other = book.get(cid)
            if other is None:
                continue
            theirs = of(other)
            mine = week.together_block(other, t)
            if mine and mine["together"] == joint["together"] and theirs._own_place(t) == place \
                    and not theirs._travelling(place, t):
                out.append(cid)
        return out

    def _travelling(self, place, t):
        """On their way to `place` at t, as the journey was last worked out."""
        trip = self._state.get("trip")
        return bool(trip and trip.get("to") == place and len(trip.get("pts") or []) > 1
                    and trip.get("left", trip.get("start", 0)) <= t < trip.get("end", 0))

    def coming(self, t=None):
        """Those on their way to join them, with the minutes they've got to go: [(name, minutes)]."""
        from ..contacts import directory
        t = t or time.time()
        leader, group = together(self, t)
        place = (self if leader is self else leader)._own_place(t)
        out = []
        for p in group:
            if p is self:
                continue
            p.trip(place, p.spot(place, t), t)                 # their journey, worked out as of now
            if p._travelling(place, t):
                found = directory().get(p.contact.id)
                out.append((found.name if found else p.contact.id, max(1, round((p._state["trip"]["end"] - t) / 60))))
        return out

    def _situation(self, t):
        """
        What decides where they are at t, on their own: (block, company, firm).
        Firm is what a conversation established — they said where they are —
        and it outranks any plan, theirs or anyone else's.
        """
        activity = self._activity_at(t)
        if activity:
            return activity, list(activity.get("with") or []), True
        block = self._routine(t) or self._whim(t) or {}
        return block, list(block.get("with") or []), False

    def _activity_at(self, t):
        """
        What a conversation (or a case, a lift, a follow) had them doing at t —
        the current activity, or one it replaced: the past stays as it was, so
        a journey out of it starts where they actually were.
        """
        for activity in [self._state.get("activity")] + list(reversed(self._state.get("earlier") or [])):
            if (activity and activity.get("since", 0) <= t < activity.get("until", 0)
                    and (activity.get("doing") or "").strip().lower().strip(" .") not in NOTHING):
                return activity
        return None

    def _follow_target(self, activity, t, seen=None):
        """
        Whoever they've gone to be with, at the end of any chain of following —
        Cass with Tim, who's with Dick: Dick — or None: nobody, a loop (each
        stays put), or someone asleep, who isn't going anywhere with anyone.
        """
        from ..contacts import directory
        seen = (seen or set()) | {self.contact.id}
        cid = (activity or {}).get("follow")
        other = directory().get(cid) if cid else None
        if other is None or cid in seen:
            return None
        them = _registry.get(other.id) or of(other)
        theirs = them._activity_at(t)
        if theirs and theirs.get("follow"):
            return them._follow_target(theirs, t, seen)
        block, _company, _firm = them._situation(t)
        if block.get("status") == OFFLINE:
            return None
        return them

    def _own_place(self, t):
        """Where they'd be at t by themselves, before anyone they're with comes into it."""
        block, _, firm = self._situation(t)
        if firm:
            return self._activity_place(block, t)
        return self._planned_place(block, t)

    def _activity_place(self, activity, t):
        """
        Where what they said they're doing puts them: the patrol they named,
        walked where they said; the place they named; a place in what they're
        doing ("a board meeting at Wayne Tower"); home, gone to sleep; and
        otherwise wherever they were when they said it.
        """
        doing, where = activity.get("doing") or "", activity.get("where") or ""
        if activity.get("follow"):
            target = self._follow_target(activity, t)
            if target is not None:
                return target._own_place(t) or where
        if places.is_patrol(doing):
            covering = patrols.sector(self.contact, where or doing, t)
            if covering:
                return covering
        if where:
            return self._home_is_home(where)
        named = places.resolve(doing)
        if named:
            return named["name"]
        if activity.get("status") == OFFLINE:
            return getattr(self.contact, "home", "") or ""
        since = activity.get("since", t)
        block = self._routine(since) or self._whim(since) or {}
        if places.is_patrol(block.get("doing")):
            # Playing Mario Kart while the routine had him out on patrol: nobody games on
            # a rooftop at the docks. Whatever they said they're doing instead, they
            # stayed in to do it.
            return getattr(self.contact, "home", "") or ""
        return self._planned_place(block, since)

    def _planned_place(self, block, t):
        """Where their plan or routine has them at t."""
        if places.is_patrol(block.get("doing")):
            # A patrol covers a sector — tonight's roster gives each of them one,
            # the worst of the city first, a new one each watch — inside wherever
            # the plan put them: Dick's Blüdhaven, Jason's Crime Alley.
            covering = patrols.sector(self.contact, block.get("where") or block.get("doing") or "", t)
            if covering:
                return covering
        if block.get("where"):
            return self._home_is_home(block["where"])
        return getattr(self.contact, "home", "") or ""

    def _home_is_home(self, where):
        """
        'Home, Bristol' in Alfred's plan is the Manor: home is where they live.
        But 'Home, Blüdhaven' in Tim's is someone else's place over there — Dick's
        — so it stays where it says.
        """
        home = getattr(self.contact, "home", "") or ""
        found = re.match(r"(?i)\s*(?:at\s+)?home\b[\s,:-]*(.*)", where or "")
        if not home or not found:
            return where
        there, mine = places.resolve(found.group(1)) if found.group(1) else None, places.resolve(home)
        if there and mine and there["area"] != mine["area"]:
            # Someone else's home over there: theirs, by name — Dick's apartment, not the middle of Blüdhaven.
            from ..contacts import directory
            theirs = next((c.home for c in directory() if c.id != self.contact.id and getattr(c, "home", "")
                           and (places.resolve(c.home) or {}).get("area") == there["area"]), None)
            return theirs or found.group(1)
        return home

    def trail(self, hours=2.0, step=300, t=None):
        """
        Where they've been over the last few hours, oldest first: one point per
        place, with when they got there — the path the map draws behind them.
        """
        t = t or time.time()
        points = []
        for at in range(int(t - hours * 3600), int(t) + 1, step):
            where, _ = self.whereabouts(at)
            spot = places.resolve(where)
            if spot and (not points or points[-1]["where"] != where):
                points.append({"at": at, "where": where, **spot})
        return points

    def public(self, t=None):
        """
        What the page is told. Someone who doesn't share their status — Jason,
        Selina — shows as unknown: Bruce wouldn't know, so the console doesn't
        either. Their status line is still theirs to set.
        """
        state = self.now(t)
        # As things stand with him: hurt, Tim goes dark; trusting him at last, Jason turns his location on.
        from . import bonds
        feel = bonds.effects(self.contact, t)
        open_ = (self.contact.shares_status or feel["shares"]) and not feel["hides"]
        if not open_:
            shown = getattr(self.contact, "hidden_as", "unknown") or "unknown"
            out = {"status": shown, "doing": "", "last_active": None, "line": self.line(t)}
            # On scene at a case, they're seen — witnesses, GCPD, the family on it —
            # and tracked while they're there; gone from it, they're a last-seen again.
            now = t or time.time()
            live, how = self._seen_working(now), "seen working a case"
            if not live:
                live, how = self._seen_on_a_job(now)
            if not live:
                live, how = self._known_with(now)
            if not live:
                live, how = self._glimpsed(now)
            if live:
                out.update(spot=live, where=live["name"], seen_live=True, doing="working a case" if how.startswith("seen working") else "")
                with self._lock:        # read on threads now: never a change mid-save
                    was = self._state.get("seen") or {}
                    self._state["seen"] = {"where": live["name"], "x": live["x"], "y": live["y"], "at": now, "how": how}
                    if was.get("where") != live["name"] or now - was.get("at", 0) > 300:
                        self.save()     # the last he saw of them outlasts a restart — written when it moves on
                return out
            out["last_seen"] = self._last_seen(t)   # the last he knows of where they were, and how — never nowhere
            return out
        shown = {"status": state["status"], "doing": state["doing"],
                 "last_active": state["last_active"] or None, "line": self.line(t)}
        if getattr(self.contact, "shares_location", True) or feel["shares"]:
            where, company = self.whereabouts(t)
            shown["with"] = sharing(company)
            shown["spot"] = self.spot(where, t)
            shown["where"] = self.label(where)
            trip = self.trip(where, shown["spot"], t)
            beside = None if trip else self._beside(t or time.time())
            if trip:
                here = travel.position(trip, t or time.time())
                shown["route"] = {"pts": trip["pts"], "start": trip["start"], "end": trip["end"], "from": trip.get("from", ""),
                                  "by": travel.crossing(*here) or trip.get("by", ""),
                                  **({"times": trip["times"]} if trip.get("times") else {})}
            elif beside:
                shown["spot"] = {**(shown["spot"] or {}), "x": beside[0], "y": beside[1]}
            else:
                strolling = self.strolling(where, t)
                if strolling:
                    shown["spot"] = {**(shown["spot"] or {}), "x": strolling["x"], "y": strolling["y"]}
                    if strolling["near"]:
                        shown["where"] = f"{strolling['near']}, {where}" if strolling["near"] not in where else where
                walking = None if strolling else self.patrolling(where, t)
                if walking:
                    # Covering a sector: a run over the roofs to somewhere to watch from, a few
                    # minutes there, on to the next — never a dot parked on the district's name.
                    shown["spot"] = {**(shown["spot"] or {}), "x": walking["x"], "y": walking["y"]}
                    shown["where"] = (f"{walking['near']}, {where}" if walking["near"] else where)
                    if walking["leg"]:
                        shown["route"] = {**walking["leg"], "from": "", "by": "over the rooftops", "patrol": True}
        elif getattr(self.contact, "sightings", None):
            # Not on the app, but seen in public — Jim's car on the scanner: the last he knows, never live.
            shown["last_seen"] = self._last_seen(t)
        return shown

    def _last_seen(self, t=None):
        """
        The last he knows of where they are — the latest sighting, however old: someone off the app stays on the map
        where they were last seen, never gone from it. Before any sighting, where he'd last have known them to be:
        their own place.
        """
        seen = self._state.get("seen")
        if seen:
            return seen
        home = places.resolve(getattr(self.contact, "home", "") or "")
        if not home:
            return None
        now = t or time.time()
        with self._lock:
            self._state["seen"] = {"where": home["name"], "x": home["x"], "y": home["y"], "at": now - 6 * 3600,
                                   "how": "the last you knew"}
            self.save()
        return self._state["seen"]

    def patrolling(self, where, t=None):
        """
        Where on their patrol they are, if they're covering a sector right now:
        {"x", "y", "near", "leg"} — see engine.patrols — or None.
        """
        t = t or time.time()
        if not where or not patrols.is_sector(where):
            return None
        block, _company, _firm = self._situation(t)
        if not places.is_patrol(block.get("doing")):
            return None
        trip = self._state.get("trip") or {}
        arrived = trip.get("end") if trip.get("to") == where else None
        mates = patrols.partner(self.contact.id, t)
        # A pair walk one walk, a step apart: whoever's first on tonight's roster leads it.
        leader = min([self.contact.id] + mates, key=lambda cid: patrols.FIELD.index(cid) if cid in patrols.FIELD else 99)
        offset = (0.0, 0.0) if leader == self.contact.id else (0.28, 0.18)
        return patrols.where(self.contact.id, where, t, arrived=arrived, seed_id=leader, offset=offset)

    _STROLL = re.compile(r"(?i)\b(walk(?:ing)?\b(?! to\b| back\b| home\b)|stroll\w*|jog\w*|running\b|a run\b|dog walk\w*|"
                         r"walking (?:haley|the dog))")

    def strolling(self, where, t=None):
        """
        Out for a walk, a run, the dog: going round, not parked — along the edge of a park if there's one near,
        or a loop of the streets round where they are. {"x", "y", "near"} or None. The same for the same moment.
        """
        t = t or time.time()
        if not where:
            return None
        block, _company, _firm = self._situation(t)
        doing = block.get("doing") or ""
        if not self._STROLL.search(doing) or places.is_patrol(doing):
            return None
        spot = places.resolve(where)
        if not spot:
            return None
        here = (spot["x"], spot["y"])
        path, near = None, ""
        for park in (places.gazetteer().get("parks") or []):
            ring = park.get("coast") or []
            if len(ring) < 3:
                continue
            cx = sum(p[0] for p in ring) / len(ring)
            cy = sum(p[1] for p in ring) / len(ring)
            if math.dist(here, (cx, cy)) < 10:
                # A step inside the edge: the path round the park, not the railings.
                path = [(cx + (p[0] - cx) * 0.85, cy + (p[1] - cy) * 0.85) for p in ring]
                near = park.get("name", "")
                break
        if path is None:
            r = 1.1 if re.search(r"(?i)run|jog", doing) else 0.75
            path = [(here[0] + dx * r, here[1] + dy * r) for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        legs = [math.dist(path[i], path[(i + 1) % len(path)]) for i in range(len(path))]
        total = sum(legs) or 1.0
        lap = (22 if re.search(r"(?i)run|jog", doing) else 40) * 60          # seconds round, whatever its size
        since = float(block.get("since") or 0) or (t - t % lap)
        along = ((t - since) % lap) / lap * total
        for i, leg in enumerate(legs):
            if along <= leg or i == len(legs) - 1:
                a, b = path[i], path[(i + 1) % len(path)]
                f = 0 if not leg else min(1.0, along / leg)
                return {"x": a[0] + (b[0] - a[0]) * f, "y": a[1] + (b[1] - a[1]) * f, "near": near}
            along -= leg
        return None

    def position(self, t=None):
        """Exactly where they are at t, (x, y) — on the road, on a patrol, or where their day has them — or None."""
        t = t or time.time()
        where, _ = self.whereabouts(t)
        spot = self.spot(where, t)
        if not spot:
            return None
        trip = self.trip(where, spot, t)
        if trip:
            return travel.position(trip, t)
        beside = self._beside(t)
        if beside:
            return beside
        walking = self.patrolling(where, t) or self.strolling(where, t)
        if walking:
            return (walking["x"], walking["y"])
        return (spot["x"], spot["y"])

    def _beside(self, t):
        """
        Gone to be with one of them and got there: at their side, wherever they are along their own way —
        not parked at the middle of the district while they walk the roofs half a kilometre off.
        """
        activity = self._activity_at(t)
        if not activity or not activity.get("follow"):
            return None
        target = self._follow_target(activity, t)
        if target is None or target is self:
            return None
        there = target.position(t)
        if there is None:
            return None
        return (round(there[0] + 0.22, 2), round(there[1] + 0.14, 2))

    # Who spots them, now and then, going about a life they keep to themselves.
    _SPOTTERS = ("Barbara caught them on a traffic camera", "Dick saw them on a rooftop", "a GCPD patrol called it in",
                 "Tim's drone picked them up", "someone posted a blurry photo", "Alfred saw them on the cave's feeds",
                 "Cass saw them, and said so in one word")

    def _glimpsed(self, t):
        """
        Someone who keeps where they are to themselves is still seen now and then —
        a camera, a rooftop, a patrol — where their own day has them: a few minutes
        live, then a last-seen. Rarer by day than by night; rarest for Selina.
        """
        spell = int(t // 3600)
        hour = time.localtime(t).tm_hour
        rate = (self.contact.texting_pace or {}).get("glimpsed", 0.16) * (1.0 if hour >= 19 or hour < 5 else 0.5)
        if _draw(self.contact.id, "glimpse", spell) >= rate:
            return None, ""
        start = spell * 3600 + _draw(self.contact.id, "glimpse-at", spell) * 2700
        if not start <= t < start + 12 * 60:
            return None, ""
        where = self._own_place(t)
        spot = places.resolve(where or "")
        if not spot:
            return None, ""
        how = self._SPOTTERS[int(_draw(self.contact.id, "spotter", spell) * len(self._SPOTTERS))]
        return {"name": spot["name"], "x": spot["x"], "y": spot["y"], "area": spot.get("area", "")}, how

    def _known_with(self, t):
        """
        Where they are when it's known for sure without them sharing it: with
        someone who does (Dick's dot gives Jason away), or at a plan he made
        with them that they said yes to.
        """
        from ..contacts import directory
        book = directory()
        where, company = self.whereabouts(t)
        sharers = [book.get(c).name for c in company if book.get(c) and getattr(book.get(c), "shares_location", True)]
        kept = plans_made.block(self.contact, t, self._lead)
        if not sharers and not kept:
            return None, ""
        spot = places.resolve(where or "")
        if not spot:
            return None, ""
        how = f"with {' and '.join(sharers)}" if sharers else "at the plan they said yes to"
        return {"name": spot["name"], "x": spot["x"], "y": spot["y"], "area": spot.get("area", "")}, how

    def _seen_on_a_job(self, t):
        """
        Selina, the night she's out: a gallery's cameras, an alarm, a shape on a roof
        — seen at her own job for the first few minutes of it, then a last-seen.
        """
        if self.contact.id != "catwoman":
            return None, ""
        from . import incidents
        for r in incidents.at(t):
            if r.get("suspect") == incidents.CATWOMAN and t - r["at"] < 15 * 60:
                return ({"name": r["place"], "x": r["x"], "y": r["y"], "area": r.get("area", "")},
                        "caught on the cameras at a job of hers")
        return None, ""

    def _seen_working(self, t):
        """The scene of the case they're on, if they've got there and it's still going: they're seen."""
        from . import cases
        case = cases.active(self.contact.id)
        if not case:
            return None
        mine = (case.get("members") or {}).get(self.contact.id) or {}
        if mine.get("status") != "on scene" and case.get("status") != "on scene":
            return None
        return {"name": case["place"], "x": case["x"], "y": case["y"], "area": case.get("area", "")}

    # --- getting about -----------------------------------------------------

    def _set_off(self, where, t, horizon=150 * 60, step=150):
        """
        (when, from where) they left for `where`: the last moment before t that
        their day had them somewhere else — or (None, None) if they've been
        there all along, as far back as a journey could reach.
        """
        for back in range(step, horizon + step, step):
            before, _ = self.whereabouts(t - back)
            if before != where:
                lo, hi = t - back, t - back + step          # elsewhere at lo, there by hi
                for _ in range(6):
                    mid = (lo + hi) / 2
                    if self.whereabouts(mid)[0] == where:
                        hi = mid
                    else:
                        lo = mid
                return hi, before
        return None, None

    def trip(self, where, spot, t=None):
        """
        Their journey, while they're making one: from the moment their day took
        them somewhere new — a block of their plan beginning, something they
        said — they go there from wherever they were, mid-journey or not, along
        the roads and taking as long as the roads take (see wayne.engine.travel).
        Worked out from their day itself, not from anyone watching: called just
        after he left Burnside, Tim is on the train, not already at the library.
        None once they've arrived.
        """
        if not spot:
            return None
        t = t or time.time()
        with self._lock:
            self._looked = t
            trip = self._state.get("trip")
            here = (spot["x"], spot["y"])
            if trip and trip.get("to") == where:
                return trip if t < trip["end"] else None
            seen = trip
        left_at, before = self._set_off(where, t)
        with self._lock:
            trip = self._state.get("trip")
            if trip is not seen and trip and trip.get("to") == where:
                # Decided elsewhere while this was being worked out — a lift, a pursuit (set_trip): that's the
                # journey. Overwritten, the Batwing lift became "on the bike".
                return trip if t < trip["end"] else None
            if left_at is None:
                self._state["trip"] = {"to": where, "pts": [list(here)], "start": t, "end": t}
                self.save()
                return None
            moving = bool(trip and trip.get("pts") and len(trip["pts"]) > 1 and trip.get("start", 0) <= left_at < trip["end"])
            lead = None
            if moving:
                origin = travel.position(trip, left_at)        # turned round on the way somewhere else
                ashore = travel.landfall(trip, left_at)        # on a ferry or a bridge: across it first
                if ashore:
                    lead, origin, crossed = ashore
            else:
                came = places.resolve(before or "")
                origin = (came["x"], came["y"]) if came else None
                walked = self.patrolling(before, left_at - 1) if before and patrols.is_sector(before) else None
                if walked:
                    origin = (walked["x"], walked["y"])     # off the roof they were watching from, not the district's middle
            if origin is None or math.dist(origin, here) < 0.8:
                self._state["trip"] = {"to": where, "pts": [list(here)], "start": t, "end": t}
                self.save()
                return None
            doing = (self.now(t).get("doing") or "").lower()
            # In the suit — patrolling, or heading to a case — it's the roofs, not the roads.
            suited = places.is_patrol(doing) or ("on the way to the" in doing and bool(getattr(self.contact, "beat", None)))
            how = getattr(self.contact, "gets_about", "drive") or "drive"
            if suited:
                went = travel.fastest(self.contact.id, origin, here, spot["name"], left_at, jet=False)
                pts, minutes = went["pts"], (went["end"] - went["start"]) / 60
            else:
                # Out of the suit: the car, a walk — or the train, for those who take it (engine.transit).
                went = travel.way(origin, here, None if moving else before, spot["name"], mode=how, who=self.contact.id)
                pts, minutes = went["pts"], went["minutes"]
            times = went.get("times")
            if lead:
                # The rest of the crossing, then the new way from where it lands.
                minutes += (crossed - left_at) / 60
                pts = lead + pts[1:]
                times = None
            trip = {"to": where, "from": "" if moving else (before or ""), "pts": pts, "start": left_at,
                    "end": left_at + minutes * 60, "by": went["by"],
                    **({"times": times} if times and len(times) == len(pts) else {}),
                    **({"legs": went["legs"]} if went.get("legs") and not lead else {})}
            self._state["trip"] = trip
            self._state["trips"] = (self._state.get("trips") or [])[-7:] + [trip]
            self.save()
            return trip if t < trip["end"] else None

    def patrol_legs(self, since, until=None):
        """The runs across the roofs they made on patrol since `since`, oldest first — for the map's trail."""
        until = until or time.time()
        since = max(since, until - 6 * 3600)
        # Each run starts as a stop does: look a moment into every stop, never between them.
        t = since - (patrols._local(since) % patrols.STOP) + 1
        out = []
        while t < until:
            where, _ = self.whereabouts(t)
            walking = self.patrolling(where, t) if where else None
            if walking and walking["leg"] and (not out or out[-1]["start"] != walking["leg"]["start"]):
                out.append({**walking["leg"], "by": "over the rooftops"})
            t += patrols.STOP
        return out

    def set_trip(self, where, trip):
        """A journey decided elsewhere — the jet booked for them, a chase to cut off: the way they're going."""
        with self._lock:
            trip = {**trip, "to": where, "from": trip.get("from", "")}
            self._state["trip"] = trip
            self._state["trips"] = (self._state.get("trips") or [])[-7:] + [trip]
            self.save()
        return trip

    def spot(self, where, t=None):
        """Where `where` is on the map for them: the place — or the exact point what they're doing pinned it to."""
        found = places.resolve(where or "")
        t = t or time.time()
        activity = self._activity_at(t) or {}
        if found and activity.get("xy") and activity.get("where") == where:
            found = {**found, "x": activity["xy"][0], "y": activity["xy"][1]}
        return found

    def trips(self, since):
        """The journeys they've made since `since`, oldest first — the roads behind them on the map."""
        return [tr for tr in (self._state.get("trips") or []) if tr.get("end", 0) >= since and len(tr.get("pts") or []) > 1]

    def seen_at(self, where, how, t=None):
        """
        Someone who keeps their whereabouts to themselves was seen, or said,
        where they are: the last he knows, and how he knows it.
        """
        spot = places.resolve(where)
        if not spot:
            return
        with self._lock:
            self._state["seen"] = {"where": spot["name"], "x": spot["x"], "y": spot["y"], "at": t or time.time(),
                                   "how": how}
            self.save()

    def note(self, t=None):
        """
        One line for the model: what they're doing and where — the place the
        map shows him, so "where are you?" gets the truth, not "at home" — and
        anyone with them. A call to someone on patrol is answered from a
        rooftop, one at 4am by someone he woke. Talking to him changes their
        status, not their day: mid-call, Alfred is still in the garden.
        """
        from ..contacts import directory
        from . import diary
        t = t or time.time()
        diary.observe(self, t)                  # what's just happened goes down before they're asked about it
        state = self.now(t)
        block, _, firm = self._situation(t)
        doing = (block.get("doing") or "").strip()
        # Who's still on their way first: that works out their journeys, and who's here follows from them —
        # Tim was told "waiting on Dick, 13 minutes out" and "you're with Dick" in one breath.
        arriving = self.coming(t)
        where, company = self.whereabouts(t)
        book = directory()
        trip = self.trip(where, self.spot(where, t), t) if where else None
        if trip and not company:
            # On the way: who they're going to meet there.
            company = [p.contact.id for p in together(self, t)[1] if p is not self]
        names = [book.get(c).name for c in company if book.get(c)]
        # The line they set, which he can see — "didn't you read my status?"
        status = self.line(t)
        said = f' Your status on the family app, which he can see, says "{status}".' if status else ""
        place = self._spoken_place(where)
        on_the_way = [] if trip else arriving
        if trip:
            line = self._en_route(trip, place, doing, t)
        elif doing and on_the_way:
            # Mario Kart with Tim doesn't start until Tim's there.
            who = " and ".join(n for n, _ in on_the_way)
            soonest = min(m for _, m in on_the_way)
            line = (f"Right now you're at {place}, waiting on {who} — about {soonest} minute{'s' if soonest != 1 else ''} "
                    f"out — for: {doing}. It hasn't started; they aren't there yet.")
        elif doing:
            how_long = describe_until(block.get("until", 0), t) if firm else ""
            line = (f"Right now {_you_are(doing)}"
                    + (f", at {place}" if place and place.split(" (")[0].lower() not in doing.lower() else "")
                    + (f", {how_long}" if how_long else "") + ".")
        else:
            line = f"Right now you're at {place}." if place else ""
        if names:
            company_line = "You're meeting" if trip else "You're with"
            line += f" {company_line} {' and '.join(names)}."
        # Whoever was with them till a moment ago, and where that one's gone: Tim was on the
        # roof when Dick said he'd take the shots fired two streets over — he'd know.
        here = self.position(t)
        for cid, at in diary.parted(self.contact.id, t):
            other = book.get(cid)
            if other is None or cid in company:
                continue
            seen = glance(other, t, here, viewer=self.contact.id)
            till = time.strftime('%H:%M', time.localtime(at))
            line += (f" {other.name} was with you till {till}; now {seen}." if seen
                     else f" {other.name} was with you till {till}, and has gone since.")
        if on_the_way and not doing:
            line += " On their way to you, not there yet: " + ", ".join(
                f"{n} (about {m} minute{'s' if m != 1 else ''} out)" for n, m in on_the_way) + "."
        spot = self.spot(where, t) if where and not trip else None
        walking = self.patrolling(where, t) if spot else None
        if walking:
            # Where on the patrol, exactly — the roof, the run — so "where are you?" gets the truth.
            line += (f" Just now you're {'on the move across the roofs, heading for' if walking['leg'] else 'watching from a roof over'} "
                     f"{walking['near'] or 'the streets of ' + where}.")
            # What the ground is like — Blüdhaven's downtown is bank towers, not a dock — so what they
            # say they can see down there is what's there.
            sector = patrols.sector_named(where)
            about = (places.gazetteer().get("areas") or {}).get(sector["district"] if sector else "", "")
            if about:
                line += f" The streets you're covering: {about.split('. ')[0].rstrip('.')}."
            why = patrols.lead(self.contact.id, t)
            if why:
                line += (f" You're working this ground tonight because {why} {'is' if why[:1].isupper() else 'are'} on the "
                         "scanner here again, like the case you worked — a lead worth following.")
            spot = {**spot, "x": walking["x"], "y": walking["y"]}
        nearby = places.around(spot["x"], spot["y"], but=spot["name"]) if spot else []
        if nearby:
            line += f" Close by: {', '.join(nearby)}."
        hurt = self.get("hurt")
        if hurt and hurt.get("until", 0) > t:
            line += f" You're hurt — {hurt['how']}, from {hurt.get('on', 'a case')}; it shows when you move."
        if state["status"] == OFFLINE:
            line += " Your phone wasn't in your hand; he's reached you anyway."
        elif state["status"] == BUSY and doing and not on_the_way:
            line += " You're in the middle of it."
        # Their own last few hours, as they went — "what were you just doing?" has an answer.
        lately = diary.summary(self.contact.id, t)
        if lately:
            line += (" What you've actually done these last few hours, as it happened — that's all of it, nothing "
                     f"else happened; tell it your way, but don't add places you weren't or things you didn't do: {lately}.")
        return (line + said).strip()

    def _en_route(self, trip, place, doing, t):
        """
        Where they are on a journey, in so many words: left from, going to, how long — and where exactly, as they'd
        know it: the road they're on, what they're passing, the line and the next stop on a train; or, out between
        places, nowhere with a name.
        """
        from . import transit
        left = max(1, round((trip["end"] - t) / 60))
        gone = max(1, round((t - trip["start"]) / 60))
        by = trip.get("by") or ""
        x, y = travel.position(trip, t)
        crossing = travel.crossing(x, y)
        by = crossing or by
        passing = places.near(x, y)
        origin = self._spoken_place(trip.get("from") or "")
        share = (t - trip["start"]) / max(1.0, trip["end"] - trip["start"])
        on_train = transit.where_on(trip["legs"], share, place) if trip.get("legs") and not crossing else ""
        road = places.road_near(x, y) if by in ("driving", "on foot", "on the bike", "running") else ""
        through = {"on the subway": "under", "over the rooftops": "over the roofs", "on foot": "walking through"}.get(by, "through")
        if on_train:
            now = f"; right now you're {on_train}"
        elif road:
            now = f"; just now on {road}" + (f", {passing}" if passing else "")
        elif passing:
            now = f"; just now {through} {passing}"
        elif by in ("driving", "on foot", "on the bike", "running", "over the rooftops"):
            now = "; just now somewhere between places — nowhere with a name you'd know"
        else:
            now = ""
        line = (f"Right now you're on your way to {place}" + (f", {by}" if by else "")
                + (f" — you left {origin} {gone} minute{'s' if gone != 1 else ''} ago" if origin else "")
                + now
                + f". About {left} minute{'s' if left != 1 else ''} to go. You aren't there yet: if he asks where you "
                  "are, you're on the way — put it the way you'd actually know it, which needn't be exact.")
        if doing and doing.lower() not in ("travelling", "on the way"):
            line += f" When you get there: {doing}."
        return line

    def _whose_home(self, where):
        """The contact whose home `where` is, if it's someone else's — 'Home, Blüdhaven' in Tim's day is Dick's."""
        if not where or where == (getattr(self.contact, "home", "") or ""):
            return None
        from ..contacts import directory
        return next((c for c in directory() if c.id != self.contact.id and (getattr(c, "home", "") or "") == where), None)

    def label(self, where):
        """Where they are as the page shows it: someone else's home by whose it is — 'Dick's place, Blüdhaven'."""
        other = self._whose_home(where)
        if other is None or not re.match(r"(?i)\s*home\b", where):
            return where or ""          # a home with a name says whose it is: "Dick's apartment"
        rest = where.split(",", 1)[1].strip() if "," in where else ""
        return f"{other.name}'s place" + (f", {rest}" if rest else "")

    def _spoken_place(self, where):
        """'Home, Blüdhaven' as they'd think of it — home if it's theirs, Dick's place if it's his; anywhere else, by its name."""
        if not where:
            return ""
        other = self._whose_home(where)
        if other is not None:
            if not re.match(r"(?i)\s*home\b", where):
                return f"{other.name}'s place ({where})"
            rest = where.split(",", 1)[1].strip() if "," in where else ""
            return f"{other.name}'s place" + (f" in {rest}" if rest else "")
        home = getattr(self.contact, "home", "") or ""
        if where == home and not re.match(r"(?i)\s*home\b", where):
            area = (places.resolve(home) or {}).get("area", "")
            return f"home — {home}" + (f", {area}" if area and area not in home else "")
        if where == home or re.match(r"(?i)\s*home\b", where):
            rest = where.split(",", 1)[1].strip() if "," in where else (where if where != "Home" else "")
            return f"home ({rest})" if rest and rest.lower() != "home" else "home"
        return where

    # --- changes -----------------------------------------------------------

    def touch(self, t=None):
        """They've just been on their phone with him."""
        with self._lock:
            self._state["last_active"] = t or time.time()
            self.save()

    def set_activity(self, doing, status, minutes, t=None, where="", company=(), xy=None, follow=None, owner="",
                     terminal=False):
        """
        What they're doing now, for so long. `owner` is what set it when it isn't
        just something they said — "case", "lift", "follow" — which what they
        say in passing doesn't overwrite (see `owned`). `terminal`: at their screens.
        """
        with self._lock:
            t = t or time.time()
            status = status if status in STATUSES else BUSY
            minutes = max(5, min(int(minutes or 60), 14 * 60))
            self._retire(t)
            self._state["activity"] = {"doing": doing.strip()[:80], "status": status,
                                       "since": t, "until": t + minutes * 60,
                                       "where": (where or "").strip()[:60],
                                       "with": [c for c in company if c != self.contact.id]}
            if xy:
                # Not the place's middle but a point by it: the corner a getaway was cut off at.
                self._state["activity"]["xy"] = [round(xy[0], 2), round(xy[1], 2)]
            if follow:
                # Gone to meet one of them: wherever they are, that's where this one's headed — and then is.
                self._state["activity"]["follow"] = follow
            if owner:
                self._state["activity"]["owner"] = owner
            if terminal:
                self._state["activity"]["terminal"] = True
            self.save()
        if where and not getattr(self.contact, "shares_status", True):
            self.seen_at(where, "they told you", t)

    def _retire(self, t):
        """The activity in hand, ended at t and kept a while — what they were doing an hour ago stays true."""
        old = self._state.get("activity")
        if not old:
            return
        if old.get("until", 0) > t:
            old = {**old, "until": max(old.get("since", t), t)}
        earlier = [a for a in (self._state.get("earlier") or []) if a.get("until", 0) > t - 6 * 3600]
        if old.get("until", 0) > old.get("since", 0):
            earlier.append(old)
        self._state["earlier"] = earlier[-6:]
        self._state["activity"] = None

    def clear_activity(self, t=None):
        """Done with it: it ends now — and is still what they were doing until now."""
        with self._lock:
            if self._state.get("activity"):
                self._retire(t or time.time())
                self.save()

    def owned(self, t=None):
        """What set what they're doing, if it was a case, a lift or a follow and it still holds — else ''."""
        activity = self._activity_at(t or time.time())
        return (activity or {}).get("owner", "") if activity is self._state.get("activity") else ""

    def last_until(self, until):
        """Keep what they're doing going till at least `until` — an errand isn't over before they've got there."""
        with self._lock:
            activity = self._state.get("activity")
            if activity and activity.get("until", 0) < until:
                activity["until"] = until
                self.save()

        # --- intentions ----------------------------------------------------------

    def intend(self, action, about, due, origin="promise", **extra):
        """Something they mean to do: text him, or call him, at `due` — and anything else worth keeping with it."""
        with self._lock:
            intent = {**extra, "id": uuid.uuid4().hex[:10], "action": "call" if action == "call" else "text",
                      "about": about.strip()[:300], "due": due, "origin": origin}
            # One plan per purpose: a newer promise replaces an older one of the same kind.
            self._state["intents"] = [i for i in self._state["intents"]
                                      if i["origin"] != origin][-4:] + [intent]
            self.save()
            return intent

    def due(self, t=None):
        t = t or time.time()
        return [i for i in self._state["intents"] if i["due"] <= t]

    def forget_intents(self):
        """Every promise and errand of theirs, gone — told to forget everything, they don't carry on with them."""
        with self._lock:
            self._state["intents"] = []
            self.save()

    def postpone(self, intent_id, seconds, count=True):
        """Later. `count` is False when nothing was tried — they were asleep."""
        with self._lock:
            for intent in self._state["intents"]:
                if intent["id"] == intent_id:
                    intent["due"] = time.time() + seconds
                    if count:
                        intent["tries"] = intent.get("tries", 0) + 1
            self.save()

    def done(self, intent_id):
        with self._lock:
            self._state["intents"] = [i for i in self._state["intents"] if i["id"] != intent_id]
            self.save()

    def drop(self, origin):
        """Forget every intention of one kind — a callback, once they've talked."""
        with self._lock:
            kept = [i for i in self._state["intents"] if i["origin"] != origin]
            if len(kept) != len(self._state["intents"]):
                self._state["intents"] = kept
                self.save()

    def drop_reached(self, origin, t=None):
        """
        Forget the intentions of one kind they'd already have acted on by talking to him: word passed on to them
        (`arrives`) before they spoke — they'll have said it then. One that hasn't reached them yet stays.
        """
        t = t or time.time()
        with self._lock:
            kept = [i for i in self._state["intents"] if i["origin"] != origin or i.get("arrives", 0) > t]
            if len(kept) != len(self._state["intents"]):
                self._state["intents"] = kept
                self.save()

    def intents(self):
        return list(self._state["intents"])

    # --- bookkeeping the pulse needs ---------------------------------------

    def get(self, key, default=None):
        return self._state.get(key, default)

    def put(self, key, value):
        with self._lock:
            self._state[key] = value
            self.save()

    def clear(self):
        with self._lock:
            self._state = {"activity": None, "last_active": 0, "intents": []}
            self._space.clear()


# How likely they are to answer, by what they're doing — unless the profile
# says otherwise. Someone free nearly always picks up; someone in a meeting
# usually doesn't; someone asleep mostly sleeps through it.
_ANSWERS = {ONLINE: 0.97, IDLE: 0.9, BUSY: 0.45, OFFLINE: 0.25}


_DO_NOT_DISTURB = re.compile(r"(?i)\b(do not disturb|dnd|don'?t call|no calls|not now|leave me alone|"
                             r"asleep|sleeping|off the grid|offline)\b|💤|🔕|😴|🛌")


# What they're doing, as far as a phone goes. Easy: hands busy, head free — the roses, the stove, the drive, the
# walk, the stakeout with an earpiece in: "busy" on a status, but they'd pick up. Hard: they can't — mid-fight,
# sparring, in surgery, on a stage, at a funeral, at the podium. Social: they could step away, and might not.
_EASY = re.compile(
    r"(?i)\b(tend\w*|garden\w*|roses|flowers|prun\w*|potting|cook\w*|bak\w*|kitchen|clean\w*|polish\w*|iron\w*|"
    r"laundry|tidy\w*|dust\w*|walk\w*|stroll\w*|driv\w*|commut\w*|on the way|heading|en route|on (?:his|her|their) way|"
    r"travel\w*|waiting|queue\w*|reading|read\b|watching|tv|telly|film|movie|listening|crossword|tinker\w*|fixing|"
    r"repair\w*|errand\w*|shopping|groceries|feeding|coffee|tea\b|breakfast|snack\w*|at home|home\b|lounging|"
    r"patrol\w*|on watch|stake ?out|staking|surveillance|lookout|rooftop\w*|perched|working late|paperwork|"
    r"office|desk|bench|workshop|garage|lab\b)\b")
_HARD = re.compile(
    r"(?i)\b(in the thick|fight\w*|brawl\w*|sparr\w*|training|workout|gym|boxing|surgery|operating|in court|"
    r"testif\w*|on stage|perform\w*|press conference|press briefing|exam\b|lectur\w*|teaching|in class|funeral|"
    r"memorial|church|mass\b|interrogat\w*|shower\w*|bath\b|swimming|diving|on a flight|flying)\b")
_SOCIAL = re.compile(
    r"(?i)\b(dinner with|lunch with|brunch with|drinks with|a drink with|coffee with|date\b|party|gala|reception|"
    r"meeting|board|interview|with friends|bowling|karaoke|sunday dinner|movie night|game night)\b")


def reach(contact, state):
    """
    Whether they could answer at all right now — 0 to 1 — and which kind of not
    if they don't: ("ring" — the phone was out of reach, it rang out; "decline" —
    they saw it and couldn't step away). From what they're actually doing, the
    case they're on above all: on the way there, or wrapping up, they'll talk; in
    the thick of a fight they won't; just arrived, sizing it up, maybe.
    """
    try:
        from . import cases
        case = cases.active(contact.id)
    except Exception:
        case = None
    if case is not None:
        key, _how = cases.phase(case)
        mine = (case.get("members") or {}).get(contact.id) or {}
        if key == "en route" or mine.get("status") == "assigned":
            return 0.95, "ring"
        violent = any(w in case["kind"].lower() for w in cases._VIOLENT)
        return {"arriving": (0.55, "decline"), "in it": ((0.08, "ring") if violent else (0.6, "decline")),
                "gone wrong": (0.25, "ring"), "wrapping up": (0.9, "decline")}.get(key, (0.7, "decline"))
    doing = state.get("doing") or ""
    if _HARD.search(doing):
        return 0.15, "ring"
    if _SOCIAL.search(doing):
        return 0.6, "decline"
    return 1.0, "decline"


def answers(contact, state, again=False, rng=random):
    """
    Whether they pick up: None if they do, else "declined" (they saw it and
    rejected it) or "no_answer" (it rang out). `again` is a second call hard
    on the heels of the first, which reads as urgent — people answer those.

    Two questions, as with anyone: could they (see reach — the roses, the drive
    there, the fight they're in), and would they (their own odds by how they
    are, in their profile — Randy mostly doesn't). Busy at something that
    leaves the hands busy and the head free is free time, as far as a phone goes.
    """
    odds = {**_ANSWERS, **(contact.initiative or {}).get("answers", {})}
    status = state["status"]
    doing = state.get("doing") or ""
    if status == BUSY and _EASY.search(doing) and not _HARD.search(doing) and not _SOCIAL.search(doing):
        status = IDLE
    could, otherwise = reach(contact, state) if status != OFFLINE else (1.0, "ring")
    # Would they, as things stand with him: sore, or cooler than they were, they let it ring — by their nature.
    from . import bonds
    chance = odds.get(status, 0.9) * could * bonds.effects(contact)["answer"]
    # They said so on their status — "do not disturb", 💤 — and mean it: the
    # first call mostly goes unanswered. A second one soon after gets through,
    # as phones let repeated calls through a do-not-disturb.
    if not again and _DO_NOT_DISTURB.search(of(contact).line() or ""):
        chance *= 0.35
    # Each call hard on the heels of the last cuts the chance of ignoring it —
    # by how much is theirs (`redial`): Alfred picks up the second time almost
    # surely; Randy, who doesn't answer, mostly still doesn't. Not through a
    # fight, though: a phone ringing in a pocket mid-punch stays ringing.
    tries = int(again)
    if tries and could > 0.2:
        chance = 1 - (1 - chance) / ((contact.initiative or {}).get("redial", 3.0) ** tries)
    if rng.random() < chance:
        return None
    if state["status"] == OFFLINE or otherwise == "ring":
        return "no_answer"          # the phone was nowhere near them, or they couldn't get to it
    if state["status"] == BUSY or could < 1.0:
        return "declined" if rng.random() < 0.8 else "no_answer"
    return "declined" if rng.random() < 0.5 else "no_answer"


def read_delay(contact, state, rng=random):
    """
    Seconds before they look at a text, from what they're doing — or None
    while their phone is out of reach (they'll see it when that changes).
    """
    pace = contact.texting_pace or {}
    status = state["status"]
    if state.get("source") == "engaged":
        # Mid-conversation, phone in hand: read in seconds, every time — never slower
        # than they'd read with the phone out anyway.
        lo, hi = pace.get("online_read", [2, 9])
        return rng.uniform(*pace.get("thread_read", [min(lo, 1.0), min(hi, 5.0)]))
    # Glued to it, a text's read in seconds; in a dry spell it waits.
    pull = engagement(contact) ** 0.8
    if status == ONLINE:
        if rng.random() < 0.1:
            return rng.uniform(30, 180) / pull      # saw it, finished what they were doing first
        return rng.uniform(*pace.get("online_read", [2, 9])) / pull
    if status == IDLE:
        return rng.uniform(*pace.get("idle_read", [60, 600])) / pull
    if status == BUSY:
        if rng.random() < min(0.9, pace.get("glance", 0.3) * pull):
            return rng.uniform(20, 150)     # a glance between things
        return rng.uniform(*pace.get("busy_read", [900, 3600])) / pull
    return None
