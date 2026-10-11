"""
The sound of where someone is, under their voice on a call — and what they hear of it.

A phone call carries the room: the traffic and the chatter of a pavement, the
espresso machine in a café, the bag thudding in a boxing gym, wind on a
rooftop, the drip of the cave. The catalogue of it is soundscape.json: a
hundred kinds of place, each a seamless loop (a bed), some laid over others (a
crowd under a busy street, rain over anywhere outdoors, rain on the windows of
anywhere with them), and a hundred and fifty sounds that happen in them — a
horn, a siren, a bus pulling up, a punch landing — each at the distances it
can happen at: right beside them, close by, far off, or going past.

Every loop and every sound is made once with ElevenLabs' sound effects
(eleven_text_to_sound_v2) the first time it's wanted, and kept on disk ever
after — within a monthly allowance of credits (WAYNE_SFX_CREDITS), past which a
place that hasn't been made yet is simply quieter.

What happens when is decided here, not in the page: `plan` lays out the next
couple of minutes for whoever is on the call (which loops, how loud, which
sounds at what moment, from how far, panned where), the page plays it, and
`heard` tells the person what's just been audible around them — so a horn
going off behind Alfred is a horn he heard, and "washing your hands?" over the
hose in the garden gets an answer. Everyone else on a group call hears it too,
down Dick's phone, and may remark on it, or not.

Which place is worked out from what they're doing and where (see `scene_for`)
— the same truth the map and their words come from.
"""
import json
import logging
import random
import re
import threading
import time
from pathlib import Path

import requests

from .. import config, paths

log = logging.getLogger("wayne")

LOOP_SECONDS = 20
# Every loop is told the same things: seamless, no music, nobody speaking words that could be mistaken for the call.
_TAIL = " Seamless loop. No music. Any voices distant and indistinct."
_TAIL_MUSIC = " Seamless loop. Any voices distant and indistinct."
# What a second of sound effects costs at most (ElevenLabs bills 11–40 credits a second when the length is
# set): the allowance is counted at the dear end.
CREDITS_PER_SECOND = 40

_CATALOGUE = json.loads((Path(__file__).with_name("soundscape.json")).read_text())
CATALOGUE_SCENES = _CATALOGUE["scenes"]
CATALOGUE_SOUNDS = _CATALOGUE["sounds"]

# The names the rest of the console has always used: every loop's prompt, and every sound's first prompt.
SCENES = {name: spec["prompt"] for name, spec in CATALOGUE_SCENES.items()}
ONE_SHOTS = {name: next(iter(spec["variants"].values())) for name, spec in CATALOGUE_SOUNDS.items()}
SCENE_SHOTS = {name: [(row[0], row[1]) for row in spec.get("sounds", [])] for name, spec in CATALOGUE_SCENES.items()}

# How a sound is heard from each distance, beyond how it was made: louder or quieter, panned, a far one dulled,
# one going past swept across. Ranges, so no two horns are quite the same.
_DISTANCE = {
    "near": {"gain": (1.0, 1.45), "pan": 0.35, "lp": None},
    "mid": {"gain": (0.55, 0.85), "pan": 0.6, "lp": (5000, 8000)},
    "far": {"gain": (0.22, 0.42), "pan": 0.85, "lp": (1800, 3200)},
    "pass": {"gain": (0.9, 1.3), "pan": 0.9, "lp": None},
}
_SAID_FROM = {"near": "right by you", "mid": "close by", "far": "in the distance", "pass": "going past"}
MIN_GAP = 2.5            # seconds between two sounds in one place: never a pile-up
HORIZON = 150            # how far ahead a plan is laid out; the page asks again every minute and a half

# Out in the weather: what's laid over a place, and what it sounds like from inside one with windows.
_WEATHER_LAYER = {"rain": ("rain_layer", 0.85), "storm": ("storm", 0.9), "snow": ("snow", 0.6),
                  "wind": ("wind_layer", 0.6)}
_HIGH_UP = {"rooftop", "rooftop_day", "stakeout", "gliding"}
_IN_A_VEHICLE = {"car", "taxi"}

# What they're doing, said any of the ways it gets said, to a kind of place. Most particular first.
_DOING = [
    (r"shower|bath\b|bathing", "shower"),
    (r"cook\w*|making (dinner|lunch|breakfast|tea)|kitchen|baking|chopping|dinner on|washing up", "kitchen"),
    (r"trapeze|acrobat\w*|tumbling|\bflips?\b|aerial\w*|gymnastics|circus", "acrobatics"),
    (r"heavy bag|boxing|sparring|speed bag|punching bag|wildcat", "boxing"),
    (r"dojo|martial|\bkata\b|staff work|bo staff|escrima|katana", "dojo"),
    (r"lifting|weights|deadlift|bench press|squats?\b", "gym"),
    (r"\b(swim\w*|laps)\b|\bpool\b", "pool"),
    (r"grappl\w*|glid\w*|swinging|on the rooftops|rooftop run|parkour|free ?run\w*", "gliding"),
    (r"flying patrol|in the suit|armou?r", "flying"),
    (r"stake ?out|staking (it )?out|surveil\w*|casing|watching (the|a) (building|warehouse|place|door|street|docks|house|lot|club)",
     "stakeout"),
    (r"on scene|crime scene|scene of|behind the tape|cordon", "crime_scene"),
    (r"precinct|gcpd|station house|bullpen|commissioner'?s office", "police"),
    (r"\b(run|running|jog\w*|sprint\w*)\b", "running"),
    (r"walk\w* (haley|the dog|\w+'s dog)|dog walk", "dog_walk"),
    (r"\b(walk\w*|stroll\w*|on foot|heading home)\b", "walking"),
    (r"ballet|dance class|barre|danc\w*", "studio"),
    (r"grocer|supermarket|groceries", "grocery"),
    (r"\bmall\b|shopping", "mall"),
    (r"\bmarket\b", "market"),
    (r"diner|pancakes|waffles?", "diner"),
    (r"coffee|caf[eé]|espresso|brunch", "cafe"),
    (r"restaurant|dinner (at|with|out)|lunch (at|with)|dining|bistro|steakhouse", "restaurant"),
    (r"gala|fundraiser|benefit|black[- ]tie|charity|reception\b", "gala"),
    (r"nightclub|\bclub\b|rave", "club"),
    (r"\b(bar|drinks?|pub|party|hi-hat|jazz)\b", "bar"),
    (r"gaming|video ?games?|game night|playing (mario|smash|halo|fifa|madden|call of duty|street fighter|the switch|"
     r"the xbox|the playstation)|on the (xbox|playstation|switch)", "game_night"),
    (r"movie night|film night|watching (a |the )?(film|movie)s? (together|with)", "movie_night"),
    (r"(watch\w*) (a |the )?(film|movie|tv|show|series|news|telly)|netflix|\btv\b", "home_tv"),
    (r"cinema|the movies|\bfilm\b at", "cinema"),
    (r"ballgame|baseball|knights game|stadium|the game at", "stadium"),
    (r"\b(gym|training|workout|treadmill|cardio)\b", "gym"),
    (r"patrol|on the beat|rooftop", "rooftop"),
    (r"\bcave\b", "cave"),
    (r"hangar|flight checks|engine work", "hangar"),
    (r"\bflight\b|plane|flying to|jet to", "jet_cabin"),
    (r"airport", "airport"),
    (r"weld\w*|tinker\w*|workshop|fixing (the|a) (bike|car)|garage", "workshop"),
    (r"\blab\b|r&d|experiment", "lab"),
    (r"server|cod(e|ing)|hack\w*|clock ?tower|oracle|monitors?|feeds", "lab"),
    (r"surgery|operating", "hospital"),
    (r"\b(er|emergency room)\b|a&e", "er"),
    (r"hospital|clinic|ward\b|shift at|leslie", "hospital"),
    (r"lecture|seminar|\bclass\b", "lecture"),
    (r"library|studying|reading room|archives?", "library"),
    (r"campus|university|college", "campus"),
    (r"board meeting|board ?room|meeting|briefing|conference call", "boardroom"),
    (r"\b(office|paperwork|emails?|desk)\b", "office"),
    (r"interrogat\w*|holding cell|booking", "police"),
    (r"arkham", "asylum"),
    (r"blackgate|prison|visiting (hours|room)", "prison"),
    (r"court(house|room)?\b|hearing|testif\w*", "court"),
    (r"church|\bmass\b|chapel|confession", "church"),
    (r"cemetery|grave\w*", "cemetery"),
    (r"greenhouse|orchids", "greenhouse"),
    (r"garden\w*|roses|pruning|weeding|lawn", "garden"),
    (r"\bpark\b", "park"),
    (r"pier|boardwalk|amusement", "pier"),
    (r"marina|harbou?r|docks?\b|waterfront|river|skat\w*", "waterfront"),
    (r"bridge", "bridge"),
    (r"subway|metro|the train\b", "subway"),
    (r"\bbus\b", "bus_ride"),
    (r"\bcab\b|taxi", "taxi"),
    (r"driv\w*|in the car|behind the wheel", "car"),
    (r"asleep|sleep\w*|in bed|napping", "bedroom"),
]
# The map's icon for where they are, to a kind of place.
_ICON = {"manor": "manor", "police": "police", "hospital": "hospital", "lab": "lab", "clock": "clocktower",
         "university": "campus", "school": "campus", "diner": "diner", "bar": "bar", "club": "club",
         "nightlife": "bar", "dock": "docks", "marina": "waterfront", "park": "park", "garden": "garden",
         "gym": "gym", "tower": "exec_office", "home": "home", "market": "market", "station": "platform",
         "cemetery": "cemetery", "church": "church", "stadium": "stadium", "circus": "acrobatics",
         "museum": "library", "prison": "prison", "asylum": "asylum", "hotel": "hotel_lobby", "theatre": "cinema",
         "airport": "airport", "heliport": "rooftop_day", "fire": "police", "library": "library",
         "industry": "warehouse", "water": "waterfront", "beach": "waterfront", "lighthouse": "docks",
         "naval": "docks", "news": "office", "civic": "court", "zoo": "park", "observatory": "rooftop"}
_OFFICE_OF = {"police": "police_office", "hospital": "hospital", "lab": "lab", "news": "office"}
_BY = {"driving": "car", "on the subway": "subway", "on the train": "subway", "on the monorail": "subway",
       "over the rooftops": "gliding", "on the bike": "bike",
       "on foot": "walking", "on the Batwing": "jet_cabin", "on the Batwing, with him": "jet_cabin"}
_CASE_KIND = [(r"shoot|gunfight|gunman|gunmen|firefight|sniper|shots fired", "gunfight"),
              (r"\bfire\b|arson|blaze", "fire_scene")]

_locks, _failed = {}, {}
_guard = threading.Lock()
_plans = {}
_plans_lock = threading.Lock()


def available():
    return bool(config.AMBIENCE and config.ELEVENLABS_API_KEY)


# --- where they are ---------------------------------------------------------------------------

def scene_for(contact, t=None):
    """The kind of place they're in right now, from their day — or "" for nothing worth hearing."""
    from ..engine import cases, places, presence
    t = t or time.time()
    whereabouts = presence.of(contact)
    case = cases.active(contact.id) if contact.id in cases.FIELD else None
    if case:
        stage = cases.phase(case, t)[0]
        if stage in ("in it", "gone wrong"):
            kind = (case.get("kind") or "").lower()
            return next((scene for pattern, scene in _CASE_KIND if re.search(pattern, kind)), "scuffle")
        if stage == "wrapping up":
            return "crime_scene"
        if stage in ("arriving", "en route"):
            return "rooftop" if getattr(contact, "beat", None) else "street_night"
    trip = whereabouts.get("trip") or {}
    if trip.get("end", 0) > t and len(trip.get("pts") or []) > 1:
        by = trip.get("by") or ""
        if by in _BY:
            return _BY[by]
        return _street(t)
    doing = (whereabouts.now(t).get("doing") or "").lower()
    where, _ = whereabouts.whereabouts(t)
    home = getattr(contact, "home", "") or ""
    spot = places.resolve(where)
    data = places.gazetteer()
    icon = next((p.get("icon") for p in data["places"] if spot and p["name"] == spot["name"]), "") or ""
    for pattern, scene in _DOING:
        if re.search(pattern, doing):
            # An office is the building's kind of office: Jim's at GCPD Central has the bullpen through the door.
            if scene in ("office", "boardroom", "exec_office") and icon in _OFFICE_OF:
                return _OFFICE_OF[icon]
            return _by_hour(scene, t)
    if where and (where == home or "home" in where.lower()):
        return getattr(contact, "home_sound", "") or ("manor" if "manor" in where.lower() else "home")
    if icon in _ICON:
        return _by_hour(_ICON[icon], t)
    return _street(t) if where else ""


def _night(t):
    hour = time.localtime(t).tm_hour
    return hour >= 20 or hour < 6


def _rush(t):
    local = time.localtime(t)
    minutes = local.tm_hour * 60 + local.tm_min
    return local.tm_wday < 5 and (7 * 60 + 30 <= minutes <= 9 * 60 + 30 or 16 * 60 + 30 <= minutes <= 19 * 60)


def _street(t):
    return "street_night" if _night(t) else "street_rush" if _rush(t) else "street_day"


def _by_hour(scene, t):
    """The same place by night: a park at night, a rooftop by day, the pier when it's shut."""
    night = _night(t)
    return {"park": "park_night" if night else "park", "rooftop": "rooftop" if night else "rooftop_day",
            "pier": "pier_night" if night else "pier"}.get(scene, scene)


def _sky():
    try:
        from ..engine import world
        return world.sky()
    except Exception:
        return ""


def beds_for(scene, sky=None):
    """The loops laid down for a place, as [(name, gain)]: its own, what's under it, and the weather over it."""
    spec = CATALOGUE_SCENES.get(scene)
    if not spec:
        return []
    sky = _sky() if sky is None else sky
    beds = [(scene, 1.0)] + [(name, gain) for name, gain in spec.get("layers", [])]
    if sky in _WEATHER_LAYER:
        layer, gain = _WEATHER_LAYER[sky]
        if scene in _IN_A_VEHICLE and sky in ("rain", "storm"):
            beds.append(("car_rain", 0.8))
        elif spec.get("outdoor"):
            beds.append(("rain_roof", gain) if scene in _HIGH_UP and sky in ("rain", "storm") else (layer, gain))
        elif spec.get("windows") and sky in ("rain", "storm"):
            beds.append(("window_rain", 0.45))
    return beds


def shots_for(scene):
    """The one-off sounds over a scene: [{"name", "every"}] — seconds between, on average."""
    return [{"name": name, "every": every} for name, every in SCENE_SHOTS.get(scene, [])]


# --- what happens there, and when -------------------------------------------------------------

def _variant_file(sound, distance):
    """The clip's name for a sound from a distance: its first-made as it always was, the rest by distance."""
    variants = list(CATALOGUE_SOUNDS[sound]["variants"])
    return sound if distance == variants[0] else f"{sound}-{distance}"


def _cue(sound, at, distance, rng):
    shape = _DISTANCE[distance]
    pan = round(rng.uniform(-shape["pan"], shape["pan"]), 2)
    cue = {"id": f"{sound}-{int(at * 1000)}", "at": round(at, 2), "sound": sound, "distance": distance,
           "file": _variant_file(sound, distance), "gain": round(rng.uniform(*shape["gain"]), 2), "pan": pan}
    if shape["lp"]:
        cue["lp"] = int(rng.uniform(*shape["lp"]))
    if distance == "pass":
        side = rng.choice((-1, 1))
        cue["sweep"] = [-side * shape["pan"], side * shape["pan"]]
    return cue


def _windows(rhythm, start, end, rng, state):
    """For a place with bursts and rests (a training hall): the stretches where something's happening."""
    on_lo, on_hi, off_lo, off_hi = rhythm
    windows, t, on = [], state.get("t", start), state.get("on", True)
    while t < end:
        length = rng.uniform(on_lo, on_hi) if on else rng.uniform(off_lo, off_hi)
        if on:
            windows.append((t, t + length))
        t, on = t + length, not on
    state.update(t=t, on=on)
    return windows


def _lay_out(scene, start, end, rng, state, sky=None):
    """The sounds of a place between two moments: each at its own rate, from its own distances, spaced apart."""
    spec = CATALOGUE_SCENES.get(scene) or {}
    rows = [list(r) for r in spec.get("sounds", [])]
    sky = _sky() if sky is None else sky
    if sky == "storm" and (spec.get("outdoor") or spec.get("windows")):
        rows.append(["thunder", 45, "near,far"])
    windows = _windows(spec["rhythm"], start, end, rng, state) if spec.get("rhythm") else None
    found = []
    for sound, every, distances in rows:
        if sound not in CATALOGUE_SOUNDS:
            continue
        t = start + rng.expovariate(1.0 / every)
        while t < end:
            if windows is None or any(a <= t < b for a, b in windows):
                found.append(_cue(sound, t, rng.choice(distances.split(",")), rng))
            t += rng.expovariate(1.0 / every)
    found.sort(key=lambda c: c["at"])
    spaced, last = [], state.get("last", start - MIN_GAP)
    for cue in found:
        if cue["at"] - last >= MIN_GAP:
            spaced.append(cue)
            last = cue["at"]
    state["last"] = last
    return spaced


def plan(contact, t=None, rng=None):
    """
    The next couple of minutes of where they are, for the page to play: the
    loops and how loud, and each sound with its moment, distance, pan and tone.
    Asked again while it runs, it carries on — the sounds already promised
    stay where they were — unless they've moved, when it starts afresh.
    """
    t = t or time.time()
    rng = rng or random.Random()
    scene = scene_for(contact, t)
    if not scene or scene not in CATALOGUE_SCENES:
        with _plans_lock:
            _plans.pop(contact.id, None)
        return {"scene": "", "beds": [], "cues": [], "cuts": [], "level": 1.0, "now": t}
    sky = _sky()
    with _plans_lock:
        kept = _plans.get(contact.id)
        if not kept or kept["scene"] != scene or kept.get("sky") != sky:
            kept = {"scene": scene, "sky": sky, "cues": [], "until": t, "state": {}}
        if kept["until"] < t + HORIZON:
            kept["cues"] += _lay_out(scene, max(kept["until"], t), t + HORIZON, rng, kept["state"], sky)
            kept["until"] = t + HORIZON
        kept["cues"] = [c for c in kept["cues"] if c["at"] > t - 120]
        kept["fetched"] = t
        _plans[contact.id] = kept
        cues = [c for c in kept["cues"] if c["at"] >= t]
    spec = CATALOGUE_SCENES[scene]
    cuts = [{"file": _variant_file(name, next(iter(CATALOGUE_SOUNDS[name]["variants"]))), "p": p}
            for name, p in spec.get("cuts", []) if name in CATALOGUE_SOUNDS]
    return {"scene": scene, "beds": [{"name": n, "gain": g} for n, g in beds_for(scene, sky)],
            "cues": cues, "cuts": cuts, "level": spec.get("level", 1.0), "now": t}


def forget(contact_id):
    """Off the call: nothing's playing behind them any more."""
    with _plans_lock:
        _plans.pop(contact_id, None)


# --- what they hear of it ---------------------------------------------------------------------

def _room(scene, sky):
    spec = CATALOGUE_SCENES.get(scene) or {}
    parts = [spec.get("say", "")]
    parts += [CATALOGUE_SCENES[name]["say"] for name, _ in spec.get("layers", []) if name in CATALOGUE_SCENES]
    for name, _ in beds_for(scene, sky)[1 + len(spec.get("layers", [])):]:
        parts.append(CATALOGUE_SCENES.get(name, {}).get("say", ""))
    return ", ".join(p for p in parts if p)


def _ago(seconds):
    return "just now" if seconds < 8 else "a few seconds ago" if seconds < 20 else "a little while ago"


def _just_heard(kept, t, loud_only=False):
    recent = [c for c in kept["cues"] if t - 45 <= c["at"] <= t
              and (not loud_only or c["distance"] in ("near", "pass") or c["sound"] in ("gunshots", "siren", "thunder"))]
    recent.sort(key=lambda c: -c["at"])
    return [f"{CATALOGUE_SOUNDS[c['sound']]['say']}, {_SAID_FROM[c['distance']]}, {_ago(t - c['at'])}"
            for c in recent[:3]]


def heard(contact_id, t=None, others=()):
    """
    What's audible on the line right now, for whoever's speaking: the room
    around them and what's just happened in it — and on a group call, what's
    come down the others' phones (`others`: [(id, name as they know them)]).
    "" when nothing's playing (no call, no sound, the page not asking).
    """
    t = t or time.time()
    with _plans_lock:
        mine = _plans.get(contact_id)
        theirs = [(name, _plans.get(cid)) for cid, name in others]
    lines = []
    if mine and t - mine.get("fetched", 0) < 200:
        lines.append(f"Around you on the line, and down it to him: {_room(mine['scene'], mine.get('sky'))}.")
        recent = _just_heard(mine, t)
        if recent:
            lines.append("Just now: " + "; ".join(recent) + ".")
    for name, kept in theirs:
        if kept and t - kept.get("fetched", 0) < 200:
            recent = _just_heard(kept, t, loud_only=True)
            if recent:
                lines.append(f"Down {name}'s phone: " + "; ".join(recent) + ".")
    if not lines:
        return ""
    return (" ".join(lines) + " That's your world, heard as it happens: if he asks about a sound you know what "
            "it was, and something loud or close might get a word out of you, as it would anyone — or nothing. "
            "Don't narrate it.")


# --- the clips --------------------------------------------------------------------------------

def path(name):
    return paths.DATA_DIR / "ambience" / f"{name}.mp3"


def _what(name):
    """(prompt, seconds, looped, music) for a clip name: a loop, a sound's first-made, or a sound from a distance."""
    if name in CATALOGUE_SCENES:
        spec = CATALOGUE_SCENES[name]
        return spec["prompt"], LOOP_SECONDS, True, bool(spec.get("music"))
    sound, _, distance = name.partition("-")
    spec = CATALOGUE_SOUNDS.get(sound)
    if not spec:
        return None
    variants = spec["variants"]
    distance = distance or next(iter(variants))
    if distance not in variants:
        return None
    return variants[distance], spec.get("seconds", 4), False, False


def _spent_key(t=None):
    return "spent:" + time.strftime("%Y-%m", time.localtime(t or time.time()))


def _allowance(seconds):
    """Whether making this much more fits this month's allowance — and, if so, counted."""
    from .. import store
    cost = int(seconds * CREDITS_PER_SECOND)
    cap = config.SFX_CREDITS
    if cap <= 0:
        return False
    space = store.space("ambience")
    spent = int(space.get(_spent_key()) or 0)
    if spent + cost > cap:
        return False
    space.put(_spent_key(), spent + cost)
    return True


def spent(t=None):
    from .. import store
    return int(store.space("ambience").get(_spent_key(t)) or 0)


def clip(name):
    """
    The clip for a loop or a sound — made the first time it's wanted and kept:
    a path, or None — no key, switched off, an unknown name, the month's
    allowance used up, or ElevenLabs saying no (asked again only after a while).
    Blocking: run off the event loop.
    """
    what = _what(name)
    if what is None or not available():
        return None
    target = path(name)
    if target.exists() and target.stat().st_size > 1000:
        return target
    prompt, seconds, looped, music = what
    with _guard:
        lock = _locks.setdefault(name, threading.Lock())
    with lock:
        if target.exists() and target.stat().st_size > 1000:
            return target
        if time.time() - _failed.get(name, 0) < 600:
            return None
        if not _allowance(seconds):
            _failed[name] = time.time()
            log.info("ambience %s not made: this month's sound allowance (WAYNE_SFX_CREDITS) is spent", name)
            return None
        try:
            response = requests.post(
                "https://api.elevenlabs.io/v1/sound-generation",
                params={"output_format": "mp3_44100_128"},
                json=({"text": prompt + (_TAIL_MUSIC if music else _TAIL), "duration_seconds": seconds,
                       "loop": True, "prompt_influence": 0.45, "model_id": "eleven_text_to_sound_v2"} if looped else
                      {"text": prompt + " No music.", "duration_seconds": seconds, "loop": False,
                       "prompt_influence": 0.55, "model_id": "eleven_text_to_sound_v2"}),
                headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"},
                timeout=60)
            if response.status_code != 200 or len(response.content) < 1000:
                raise RuntimeError(f"{response.status_code}: {response.text[:160]}")
        except Exception as exc:
            _failed[name] = time.time()
            log.warning("ambience %s couldn't be made: %s", name, str(exc)[:200])
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        log.info("ambience %s made (%d KB)", name, len(response.content) // 1024)
        return target


def _wanted(scene):
    """Every clip a place needs, its loop first: the layers under it, then its sounds from each distance."""
    spec = CATALOGUE_SCENES.get(scene) or {}
    names = [scene] + [name for name, _ in spec.get("layers", [])]
    for sound, _, distances in spec.get("sounds", []):
        names += [_variant_file(sound, d) for d in str(distances).split(",")]
    return [n for n in dict.fromkeys(names) if _what(n)]


def upcoming(directory, t=None, hours=2):
    """The places the family will be in over the next couple of hours, the most of them first."""
    import collections
    t = t or time.time()
    counts = collections.Counter()
    for contact in directory:
        for step in range(0, hours * 2 + 1):
            try:
                counts[scene_for(contact, t + step * 1800)] += 1
            except Exception:
                continue
    return [scene for scene, _ in counts.most_common() if scene in CATALOGUE_SCENES]


def _warm_key(t=None):
    return "warm:" + time.strftime("%Y-%m-%d", time.localtime(t or time.time()))


def warm(directory, t=None, most=2):
    """
    Makes up to `most` of the clips the coming couple of hours will want and don't have yet — the places most of
    them will be in first, each place's loop before its sounds — within the day's warm-up allowance
    (WAYNE_SFX_WARM_PER_DAY) and the month's. Blocking: run off the event loop, never on a call. Returns what was
    made.
    """
    from .. import store
    if not available() or config.SFX_WARM_PER_DAY <= 0:
        return []
    space = store.space("ambience")
    made = []
    for scene in upcoming(directory, t):
        for name in _wanted(scene):
            if len(made) >= most:
                return made
            if path(name).exists() or time.time() - _failed.get(name, 0) < 600:
                continue
            cost = int(_what(name)[1] * CREDITS_PER_SECOND)
            today = int(space.get(_warm_key(t)) or 0)
            if today + cost > config.SFX_WARM_PER_DAY:
                return made
            if clip(name) is not None:
                space.put(_warm_key(t), today + cost)
                made.append(name)
    return made


def every_clip():
    """[(name, seconds)] for everything the catalogue could ever play — what a full render would make."""
    out = [(name, LOOP_SECONDS) for name in CATALOGUE_SCENES]
    for sound, spec in CATALOGUE_SOUNDS.items():
        for distance in spec["variants"]:
            out.append((_variant_file(sound, distance), spec.get("seconds", 4)))
    return out
