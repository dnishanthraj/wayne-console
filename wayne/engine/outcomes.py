"""
How a case ends — caught or gone, the hostages safe or not, solved or cold —
decided the way Gotham would decide it: by who went, what they're good at, what
it was, and who was behind it.

Everyone who works a scene has their strengths: Cass is the best fighter alive
and no detective; Tim is the reverse; Barbara is the network; Dick is the one
you want in a hostage room; Jason gets results and bodies; Randy has the sky;
Batman is the best at all of it. A situation asks for its own mix — a hostage
room wants calm and stealth, a riddle wants a detective, Croc wants someone
who can take a hit — and the city's monsters are hard to catch in their own
right: the Joker nearly never, Calendar Man most nights. The more of the
family on it, the better the odds, with less for each one added.

The numbers stay here. What comes out is an ending: who was caught, who got
away, who got hurt — and the one on the case writes it in their own words.
Seeded by the case, so it's the same ending however often it's asked.
"""
import hashlib

SKILLS = ("fight", "stealth", "detect", "tech", "pursuit", "hostage", "brutes", "toxins", "crowd", "rescue")

# How good each of them is at what a scene asks — never shown, only felt.
RATINGS = {
    "bruce":     {"fight": 0.98, "stealth": 0.97, "detect": 0.99, "tech": 0.92, "pursuit": 0.93, "hostage": 0.95,
                  "brutes": 0.92, "toxins": 0.95, "crowd": 0.9, "rescue": 0.92},
    "nightwing": {"fight": 0.88, "stealth": 0.8, "detect": 0.72, "tech": 0.6, "pursuit": 0.95, "hostage": 0.86,
                  "brutes": 0.62, "toxins": 0.6, "crowd": 0.88, "rescue": 0.86},
    "robin":     {"fight": 0.68, "stealth": 0.84, "detect": 0.95, "tech": 0.9, "pursuit": 0.72, "hostage": 0.72,
                  "brutes": 0.4, "toxins": 0.76, "crowd": 0.55, "rescue": 0.7},
    "batgirl":   {"fight": 0.78, "stealth": 0.78, "detect": 0.87, "tech": 0.98, "pursuit": 0.8, "hostage": 0.78,
                  "brutes": 0.5, "toxins": 0.7, "crowd": 0.65, "rescue": 0.76},
    "orphan":    {"fight": 0.97, "stealth": 0.96, "detect": 0.45, "tech": 0.3, "pursuit": 0.9, "hostage": 0.88,
                  "brutes": 0.78, "toxins": 0.55, "crowd": 0.8, "rescue": 0.7},
    "redhood":   {"fight": 0.9, "stealth": 0.62, "detect": 0.62, "tech": 0.55, "pursuit": 0.76, "hostage": 0.55,
                  "brutes": 0.74, "toxins": 0.55, "crowd": 0.86, "rescue": 0.5},
    "batwing":   {"fight": 0.72, "stealth": 0.55, "detect": 0.52, "tech": 0.83, "pursuit": 0.96, "hostage": 0.6,
                  "brutes": 0.68, "toxins": 0.72, "crowd": 0.76, "rescue": 0.86},
}

# What each sort of call asks for.
_HOSTAGE = {"hostage": 0.45, "stealth": 0.3, "fight": 0.25}
_STREET = {"fight": 0.45, "pursuit": 0.35, "crowd": 0.2}
_HUNT = {"detect": 0.6, "tech": 0.25, "stealth": 0.15}
_DISASTER = {"rescue": 0.45, "crowd": 0.3, "toxins": 0.25}
NEEDS = {
    "Hostage situation": _HOSTAGE, "Kidnapping": {"detect": 0.4, "pursuit": 0.3, "hostage": 0.3},
    "Child abduction": {"detect": 0.45, "pursuit": 0.35, "hostage": 0.2},
    "Surgical abduction": {"detect": 0.4, "stealth": 0.3, "fight": 0.3},
    "Body found": _HUNT, "Homicide": _HUNT, "Serial killing": {"detect": 0.7, "stealth": 0.3},
    "Riddle left at a crime scene": {"detect": 0.75, "tech": 0.25},
    "Missing person": {"detect": 0.55, "tech": 0.3, "pursuit": 0.15},
    "Torture victim found": {"detect": 0.55, "fight": 0.25, "stealth": 0.2},
    "Cult gathering": {"stealth": 0.4, "fight": 0.35, "crowd": 0.25},
    "Explosion reported": _DISASTER, "Arson": _DISASTER, "Chemical spill": {"rescue": 0.5, "toxins": 0.5},
    "Toxin exposure": {"toxins": 0.55, "rescue": 0.25, "fight": 0.2},
    "Laughing-gas attack": {"toxins": 0.5, "crowd": 0.25, "fight": 0.25},
    "Mass overdose": {"rescue": 0.6, "toxins": 0.25, "detect": 0.15},
    "Freezing incident": {"tech": 0.4, "brutes": 0.3, "fight": 0.3},
    "Plant overgrowth attack": {"toxins": 0.4, "stealth": 0.3, "fight": 0.3},
    "Riot": {"crowd": 0.6, "fight": 0.4}, "Turf war": {"fight": 0.45, "crowd": 0.35, "pursuit": 0.2},
    "Mauling": {"brutes": 0.6, "fight": 0.4}, "Assassination": {"pursuit": 0.4, "fight": 0.35, "detect": 0.25},
    "Mob hit": {"detect": 0.45, "pursuit": 0.3, "fight": 0.25}, "Officer down": {"fight": 0.45, "rescue": 0.35, "pursuit": 0.2},
    "Mass shooting": {"fight": 0.4, "rescue": 0.35, "crowd": 0.25}, "Vehicle pursuit": {"pursuit": 0.8, "tech": 0.2},
    "Foot chase": {"pursuit": 0.7, "fight": 0.3}, "Hit-and-run": {"pursuit": 0.6, "tech": 0.25, "detect": 0.15},
    "Cat burglary": {"stealth": 0.35, "pursuit": 0.4, "detect": 0.25},
    "Carjacking": {"pursuit": 0.6, "fight": 0.4},
    "Breakout": {"pursuit": 0.4, "fight": 0.35, "detect": 0.25},
}

# How hard each of them is to bring in, on their own night (0–1).
DIFFICULTY = {
    "Ra's al Ghul": 0.96, "The Court of Owls": 0.94, "The Joker": 0.9, "Deathstroke": 0.9, "Catwoman": 0.88, "Bane": 0.86,
    "Hush": 0.8, "Carmine Falcone": 0.8, "Clayface": 0.76, "Scarecrow": 0.72, "Poison Ivy": 0.72,
    "Mr. Freeze": 0.72, "Killer Croc": 0.7, "Hugo Strange": 0.7, "Black Mask": 0.66, "The Riddler": 0.66,
    "Rupert Thorne": 0.64, "The Penguin": 0.62, "Two-Face": 0.62, "Man-Bat": 0.6, "Professor Pyg": 0.6,
    "Sal Maroni": 0.58, "Harley Quinn": 0.56, "Victor Zsasz": 0.55, "Firefly": 0.55, "Mad Hatter": 0.5,
    "The Ventriloquist": 0.45, "Calendar Man": 0.4, "Killer Moth": 0.3,
}

# What kind of ending a call has.
_FAMILY = {k: "hostage" for k in ("Hostage situation", "Kidnapping", "Child abduction", "Surgical abduction")}
_FAMILY.update({k: "hunt" for k in ("Body found", "Homicide", "Serial killing", "Riddle left at a crime scene",
                                     "Missing person", "Torture victim found", "Mob hit", "Assassination")})
_FAMILY.update({k: "disaster" for k in ("Explosion reported", "Arson", "Chemical spill", "Toxin exposure",
                                         "Laughing-gas attack", "Mass overdose", "Freezing incident",
                                         "Plant overgrowth attack", "Riot")})
_PETTY = ("Mugging", "Break-in", "Vandalism", "Drug deal", "Phone snatch", "Car break-in", "Shoplifting", "Bar fight",
          "Smash-and-grab", "Suspicious package")
_VIOLENT_WORDS = ("stab", "machete", "shoot", "drive-by", "turf", "gang", "assault", "attack", "robbery", "carjack",
                  "brawl", "mob hit", "mass shooting", "officer down", "domestic")
# How long there is to stop it, in minutes from the first call: a phone snatch is over
# before anyone arrives; a standoff can go on for hours; a body waits for whoever comes.
WINDOW = {
    "Phone snatch": 3, "Mugging": 6, "Knifepoint robbery": 6, "Carjacking": 6, "Hit-and-run": 5, "Drive-by shooting": 6,
    "Smash-and-grab": 8, "Car break-in": 8, "Cat burglary": 12, "Unprovoked attack": 8, "Assault": 10, "Mob hit": 10, "Officer down": 10,
    "Assassination": 10, "Shoplifting": 10, "Stabbing": 12, "Machete attack": 12, "Vandalism": 12, "Break-in": 15,
    "Drug deal": 15, "Bar fight": 15, "Brawl": 15, "Armed robbery": 15, "Shots fired": 15, "Sexual assault": 15,
    "Vehicle pursuit": 15, "Mass shooting": 15, "Domestic violence": 20, "Overdose": 20, "Mauling": 20,
    "Gang activity": 30, "Protection racket": 30, "Arson": 30, "Explosion reported": 30, "Turf war": 40,
    "Laughing-gas attack": 40, "Mass overdose": 45, "Freezing incident": 45, "Toxin exposure": 45, "Suspicious package": 60,
    "Chemical spill": 60, "Cult gathering": 60, "Plant overgrowth attack": 60, "Kidnapping": 90, "Riot": 90,
    "Child abduction": 120, "Surgical abduction": 120, "Breakout": 120, "Hostage situation": 180,
    "Riddle left at a crime scene": 180, "Missing person": 240,
}


# How long it takes once they're there, in minutes: a mugging is a chase round the block
# and a word with GCPD; a standoff, a hunt or a fire is a long night.
WORK = {"petty": (8, 16), "street": (12, 26), "hunt": (25, 50), "hostage": (30, 70), "disaster": (25, 55)}


def family(kind):
    """What sort of call it is: hostage, hunt, disaster, petty or street."""
    return _FAMILY.get(kind, "petty" if kind in _PETTY else "street")


def work(case, team=None):
    """
    Minutes on scene before it's over, one way or the other: how long this kind of
    call takes, and how good whoever's on it is at it — Batman on a mugging is a
    minute's work; Tim alone against a crew is a long night. Fixed for the same
    case and team.
    """
    from . import cases
    lo, hi = WORK[family(case["kind"])]
    base = lo + (hi - lo) * _roll(case, "work") + 4 * max(0, case.get("severity", 2) - 2)
    crew = team if team is not None else cases.team(case)
    pace = 1.35 - 0.7 * team_fit(crew, case["kind"]) if crew else 1.0
    return base * max(0.5, min(1.35, pace)) * (1 + 0.4 * outnumbered({**case, "team": crew}))


def arrival(case):
    """When the first of them got there (or will)."""
    members = case.get("members") or {}
    if members:
        return min(m.get("joined", case.get("opened_at", 0)) + m.get("travel", 9) * 60 for m in members.values())
    return case.get("opened_at", 0) + case.get("travel", 9) * 60


def too_late(case):
    """Whether it was over before anyone got there — the robber long gone, the snatch done."""
    window = WINDOW.get(case["kind"])
    return window is not None and arrival(case) - case.get("began", case.get("opened_at", 0)) > window * 60


INJURIES = ("a cracked rib", "a knife cut along the forearm", "a concussion", "a dislocated shoulder",
            "a burn across the back of the hand", "a sprained ankle", "a split lip and two black eyes",
            "a graze from a bullet", "bruised ribs that'll show for a week")


# The ratings, in words — what they'd do on a scene, never a number.
_SKILL_WORDS = {"fight": "fighting", "stealth": "getting in unseen", "detect": "detective work",
                "tech": "tech and the cameras", "pursuit": "the chase", "hostage": "talking people down, keeping hostages safe",
                "brutes": "taking on the big ones", "toxins": "toxins and antidotes", "crowd": "crowds and holding a line",
                "rescue": "getting people out"}


def strengths(member, kind=None, top=2):
    """What they bring — to this kind of call, if one's given — as words: ['detective work', 'tech and the cameras']."""
    ratings = RATINGS.get(member) or {}
    want = needs(kind) if kind else {}
    ranked = sorted(ratings, key=lambda k: -(ratings[k] * (1 + 2 * want.get(k, 0))))
    return [_SKILL_WORDS[k] for k in ranked[:top]]


def weakest(member, kind=None):
    """Where they're least at home on this call: 'detective work' for Cass, 'taking on the big ones' for Tim."""
    ratings = RATINGS.get(member) or {}
    pool = {k: v for k, v in ratings.items() if not kind or k in needs(kind)} or ratings
    return _SKILL_WORDS[min(pool, key=pool.get)] if pool else ""


def roles(team, kind, names):
    """
    Who does what on this call: what it asks for most goes to whoever's best at
    it, the next to the best of the rest — 'Dick on talking people down, Cass on
    getting in unseen, Tim on the cameras' — and anyone over is on what they're best at.
    """
    crew = [m for m in team if m in RATINGS]
    asked = sorted(needs(kind), key=lambda k: -needs(kind)[k])
    extra = [k for k in SKILLS if k not in asked]
    jobs = {}
    for skill in asked + extra:
        free = [m for m in crew if m not in jobs]
        if not free:
            break
        best = max(free, key=lambda m: RATINGS[m].get(skill, 0))
        if skill in asked or RATINGS[best].get(skill, 0) >= 0.85:
            jobs[best] = skill
    for m in crew:
        jobs.setdefault(m, max(RATINGS[m], key=RATINGS[m].get))
    return "; ".join(f"{names.get(m, m)} on {_SKILL_WORDS[jobs[m]]}" for m in crew)


def _roll(case, what):
    return int(hashlib.sha1(f"{case['id']}:{what}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def needs(kind):
    """What a call of this kind asks for, as weights over the skills."""
    if kind in NEEDS:
        return NEEDS[kind]
    lowered = kind.lower()
    if any(w in lowered for w in _VIOLENT_WORDS):
        return _STREET
    if kind in _PETTY:
        return {"pursuit": 0.55, "fight": 0.45}
    return {"fight": 0.4, "pursuit": 0.3, "detect": 0.3}


def fit(member, kind):
    """How well one of them suits this call, 0–1 — less, carrying an injury from the last one."""
    ratings = RATINGS.get(member)
    if not ratings:
        return 0.4
    return sum(w * ratings.get(skill, 0.5) for skill, w in needs(kind).items()) * (0.7 if _hurt(member) else 1.0)


def _hurt(member):
    """Whether they're still carrying an injury (a cracked rib heals in a day and a half, not three hours)."""
    if member == "bruce":
        return False
    import time as _time

    from ..contacts import directory
    from . import presence
    found = directory().get(member)
    if found is None:
        return False
    hurt = presence.of(found).get("hurt") or {}
    return hurt.get("until", 0) > _time.time()


def team_fit(members, kind):
    """The whole team's strength on this call: each one helps, the second less than the first."""
    left = 1.0
    for m in members:
        left *= 1 - fit(m, kind) * 0.75
    return 1 - left


def difficulty(case):
    """How hard this one is to close: whoever's behind it, or how bad the crime is."""
    suspect = case.get("suspect") or ""
    if suspect in DIFFICULTY:
        return DIFFICULTY[suspect]
    return 0.15 + 0.1 * case.get("severity", 2)


def outnumbered(case):
    """How badly they're outnumbered: 0 when it's even, toward 1 against Bane and the thirty men he brought."""
    from . import cases
    crew = case.get("crew") or 1
    team = max(1, len(cases.team(case)))
    return max(0.0, min(1.0, (crew / (team * 4.0) - 1) / 4))        # each of them is good for about four


def _holds_a_line(members):
    """How well a team stands up to numbers: the fighters and the ones who can take a hit."""
    rated = [RATINGS[m] for m in members if m in RATINGS]
    if not rated:
        return 0.5
    return max((r["crowd"] + r["brutes"] + r["fight"]) / 3 for r in rated)


def chance(case, gone_wrong=False, backup=False, late=False):
    """The odds of a good ending, 0.05–0.95."""
    from . import cases
    members = cases.team(case)
    p = 0.5 + (team_fit(members, case["kind"]) - difficulty(case)) * 1.3
    # Thirty of Bane's men are thirty men: Tim alone goes under, Cass and Dick hold the door.
    p -= 0.45 * outnumbered(case) * (1.15 - _holds_a_line(members))
    # GCPD are there too: what they'd manage alone is a floor under the family's odds, not nothing —
    # however it goes for the family, it never goes worse than if nobody had come.
    floor = baseline(case)
    p = max(p, floor) + (1 - max(p, floor)) * floor * 0.25
    if gone_wrong:
        p -= 0.05 if backup else 0.18          # it went bad; help coming made the difference
    if late:
        p -= 0.1                               # by the time they got there it was half over
    if "bruce" in members and hunted():
        p -= 0.08                              # the task force is out for Batman too: one eye on the roofs behind him
    return max(floor, 0.05, min(0.95, p))


def hunted(t=None):
    """Whether GCPD are after the masks — the department hostile or worse: the Mayor's task force out (engine.bonds)."""
    from . import bonds
    return bonds.standing(bonds.GCPD, bonds.ME, t)[0] in ("hostile", "estranged")


GRADES = ("success", "partial", "failure")


def grade(lives, culprit, sort):
    """
    How it's scored, lives first — Batman's way: everyone safe and the one
    behind it caught is a success; people saved but them gone, or them caught
    with people hurt, is partial; anyone they were there to save dying is a
    failure, whoever's in a cell for it. A hunt has nobody left to save: it's
    the culprit or nothing.
    """
    if lives == "lost":
        return "failure"
    if sort == "hunt":
        return "success" if culprit in ("caught", "dead") else "failure"
    if lives == "safe" and culprit in ("caught", "dead", "none"):
        return "success"
    if lives == "safe" or culprit in ("caught", "dead"):
        return "partial"
    return "failure"


def _ending(how, lives, culprit, sort, line, p, caught="", hurt=None):
    g = grade(lives, culprit, sort)
    return {"ok": g == "success", "grade": g, "lives": lives, "culprit": culprit, "how": how, "caught": caught,
            "hurt": hurt or {}, "line": line, "chance": round(p, 2)}


def decide(case, now=None):
    """
    The ending, on two counts — the people (safe, hurt, lost) and whoever was
    behind it (caught, escaped, dead) — and graded from them (see grade):
    {"ok": a success, "grade", "lives", "culprit", "how": the word for it
    (caught / saved / solved / contained / recovered / got away / too late /
    cold / lost / worse / killed / costly), "caught": the rogue brought in or "",
    "hurt": {member: injury}, "line": what happened, for whoever writes it up}.
    Seeded by the case: the same ending however often it's asked.
    """
    from . import cases, incidents
    members = present(case)
    case = {**case, "team": members}
    sort = family(case["kind"])
    if case["kind"] in incidents.MOVING:
        return _chase_ending(case, members)
    gone_wrong = cases.goes_wrong(case)
    late = too_late(case)
    suspect = case.get("suspect") or ""
    who = suspect or "whoever did it"
    if late and sort in ("street", "petty"):
        # Over before anyone got there: it went however it went for GCPD, who were there —
        # turning up late changes nothing, for better or worse.
        theirs = gcpd(case)
        took = theirs["culprit"] == "caught"
        return _ending("too late", theirs["lives"], theirs["culprit"], sort,
                       f"by the time they got there it was over — GCPD had {suspect or 'them'} in cuffs" if took
                       else f"by the time they got there it was over — {who} long gone", baseline(case),
                       caught=suspect if took else "")
    p = chance(case, gone_wrong, backup=len(members) > 1, late=late)
    if late:
        p = max(baseline(case), p - 0.2)       # the standoff, the fire, the abduction: hours lost
    r = _roll(case, "end")
    good = r < p
    middling = not good and r < p + (1 - p) * 0.55
    hurt = {}
    if not good and sort in ("street", "hostage", "disaster") and _roll(case, "hurt") < 0.4:
        crew = [m for m in members if m != "bruce"] or members
        if crew:
            hurt[crew[int(_roll(case, "who") * len(crew))]] = INJURIES[int(_roll(case, "injury") * len(INJURIES))]
    if suspect == "Catwoman":
        # Selina: the diamonds back in their case by morning, and her nowhere — even "caught", she isn't, for long.
        if good:
            held = _roll(case, "cat") < 0.12
            return _ending("caught" if held else "recovered", "safe", "escaped", sort,
                           "they had her cuffed on the roof — and she was out of them before the squad car came" if held
                           else "what she took was back where it belonged by morning; she was long gone", p)
        return _ending("got away", "safe", "escaped", sort, "she was gone over the roofs with it", p, hurt=hurt)
    if good:
        how = {"hostage": "saved", "hunt": "solved", "disaster": "contained"}.get(sort, "caught")
        took = bool(suspect) and (sort not in ("hostage", "disaster") or _roll(case, "caught") < 0.75)
        if sort in ("street", "petty", "hunt", "hostage") and not suspect:
            # Nobody named: the ones who did it, arrested. A hunt with no rogue behind it always failed
            # ("identified, still out there"), a hostage-taking could only ever be partial — both worse
            # than GCPD alone, which broke the floor.
            took = True
        culprit = "caught" if took else ("none" if sort == "disaster" and not suspect else "escaped")
        lives = "safe"
        if sort in ("hostage", "street") and case.get("severity", 2) >= 3 and _roll(case, "costly") < (1 - p) * 0.35:
            # Got them — and not everyone came out of it.
            lives = "lost"
            how = "costly"
        line = {"saved": "everyone got out alive" + (f", and {who} was taken down" if took else f" — but {who} got away"),
                "solved": f"it was worked out — {who} identified" + (" and brought in" if took else ", still out there"),
                "contained": "it was contained before it got worse" + (f"; {who} was brought in" if took else ""),
                "caught": f"{who} was caught and handed to GCPD",
                "costly": f"they got {who} — but someone they were there for didn't make it"}[how]
        if "redhood" in members and sort == "street" and not suspect and _roll(case, "jason") < 0.3:
            how, culprit, line = "killed", "dead", "the man responsible didn't survive Jason — GCPD found what was left"
        return _ending(how, lives, culprit, sort, line, p, caught=suspect if (took and suspect) else "", hurt=hurt)
    if sort == "disaster" and not suspect:
        # A fire, a spill, a collapse with nobody behind it: there's no "whoever did it" to get away.
        if middling:
            return _ending("contained", "hurt", "none", sort, "it was contained, but not before people were hurt", p,
                           hurt=hurt)
        lost = _roll(case, "dead") < 0.35
        return _ending("worse", "lost" if lost else "hurt", "none", sort,
                       f"it got worse before it was over — people {'died' if lost else 'were hurt'}", p, hurt=hurt)
    if middling:
        if sort == "hunt":
            return _ending("cold", "safe", "escaped", sort, f"it went nowhere — no one for it yet, {who} still out there",
                           p, hurt=hurt)
        return _ending("got away", "safe", "escaped", sort, f"nobody else was hurt, but {who} got away", p, hurt=hurt)
    how = {"hostage": "lost", "hunt": "cold", "disaster": "worse"}.get(sort, "worse" if sort == "street" else "got away")
    lives = {"lost": "lost", "worse": "lost" if _roll(case, "dead") < 0.35 else "hurt", "cold": "safe",
             "got away": "safe"}[how]
    line = {"lost": f"it went wrong — someone they were there to save didn't make it, and {who} got away",
            "cold": f"it went cold — no one for it, {who} gone",
            "worse": f"it went bad — more people {'died' if lives == 'lost' else 'hurt'} before it was over, and {who} got away",
            "got away": f"{who} got away"}[how]
    return _ending(how, lives, "escaped", sort, line, p, hurt=hurt)


def present(case):
    """
    Whoever was actually there before it ended — the ones it's graded on, and the only ones who can come
    away hurt. Tim, still driving when it closed, came out of a car crash he was never at.
    """
    from . import cases
    team = cases.team(case)
    members = case.get("members") or {}
    due = case.get("due") or 0
    there = [m for m in team if (members.get(m) or {}).get("status") == "on scene"
             or not due or (members.get(m) or {}).get("joined", 0) + (members.get(m) or {}).get("travel", 9) * 60 <= due + 60]
    return there or team


def baseline(case):
    """
    The odds with nobody from the family on it — GCPD on their own: a mugging's
    often cleared, a hostage-taking rarely ends well, the Joker never. 0.03–0.8.
    """
    sort = family(case["kind"])
    sev = case.get("severity", 2)
    p = {"petty": 0.62, "street": 0.5 - 0.08 * max(0, sev - 2), "hunt": 0.35, "hostage": 0.28, "disaster": 0.45}[sort]
    d = DIFFICULTY.get(case.get("suspect") or "")
    if d is not None:
        p = min(p, max(0.03, 0.42 - 0.45 * d))
    crew = case.get("crew") or 1
    p -= 0.04 * max(0, crew - 3) ** 0.7               # two officers, and a crowd
    return round(max(0.03, min(0.8, p)), 3)


def gcpd(report):
    """
    How a call ended on GCPD's own — nobody from the family on it, or nobody there in
    time to change it: their arrest, or not, and the people. Seeded by the report.
    """
    p = baseline(report)
    sort = family(report["kind"])
    took = _roll(report, "gcpd") < p
    lives = "safe" if took or sort in ("petty", "hunt") else ("lost" if _roll(report, "gcpd-dead") < 0.25 else "hurt")
    culprit = "caught" if took else "escaped"
    g = grade(lives, culprit, sort)
    return {"ok": g == "success", "grade": g, "how": "arrest" if took else "no arrest", "lives": lives, "culprit": culprit}


def catches(case, who=None):
    """Whether the one cutting off a getaway — or chasing it — stops it: decided once, when they get there."""
    from . import cases
    team = cases.team(case) or ([who] if who else [])
    p = 0.5 + (team_fit(team, case["kind"]) - difficulty(case)) * 1.3
    # GCPD's cars are in the chase too: never worse than they'd do alone.
    return _roll(case, f"catch:{','.join(sorted(team))}") < max(baseline(case), 0.08, min(0.92, p))


def _chase_ending(case, members):
    """A getaway ends where it was stopped, or it doesn't: nobody cut it off, and it's gone."""
    who = case.get("suspect") or "whoever it was"
    caught = (case.get("chase") or {}).get("caught")
    if caught and case.get("suspect") == "Catwoman":
        return _ending("recovered", "safe", "escaped", "street",
                       "they ran her down across the roofs and got the bag back — she went off the edge laughing", 1.0)
    if caught:
        hurt = {}
        if case["kind"] != "Foot chase" and _roll(case, "crash") < 0.25:
            crew = [m for m in members if m != "bruce"]
            if crew:
                hurt[crew[int(_roll(case, "who") * len(crew))]] = INJURIES[int(_roll(case, "injury") * len(INJURIES))]
        return _ending("caught", "safe", "caught", "street",
                       f"they ran {who} down on foot" if case["kind"] == "Foot chase"
                       else f"they cut the car off and {who} was pulled out of it and handed to GCPD", 1.0,
                       caught=case.get("suspect") or "", hurt=hurt)
    if not case.get("chase"):
        # Nobody got ahead of it: it was GCPD's cars or nothing, as if nobody had gone.
        theirs = gcpd(case)
        took = theirs["culprit"] == "caught"
        return _ending("too late", "safe", theirs["culprit"], "hunt",
                       f"nobody got near it — GCPD's cars boxed {who} in" if took
                       else f"by the time they were anywhere near it {who} was long gone", baseline(case),
                       caught=(case.get("suspect") or "") if took else "")
    # They were on it: a getaway's whole point is the catching — gone is a failure, however quiet.
    return _ending("got away", "safe", "escaped", "hunt", f"they were on it, and {who} still got away", 0.0)


def estimate(case, now=None, projected=False):
    """
    The odds of a good ending as the Batcomputer would put them right now —
    never the roll that decides it. As it stands (`projected` False): only
    whoever's actually there counts, and once they're working it the figure
    moves with how it's going, the way a scene does — closer to the truth the
    longer they're in it. None when nobody's there yet. Projected: the whole
    team as if they were all there, as the odds on paper. 0–1.
    """
    import time as _time

    from . import cases, incidents
    now = now or _time.time()
    sort = family(case["kind"])
    members = case.get("members") or {}
    if projected:
        team = cases.team(case)
    else:
        team = [m for m in cases.team(case)
                if (members.get(m) or {}).get("status") == "on scene"
                or (members.get(m) or {}).get("joined", now) + (members.get(m) or {}).get("travel", 9) * 60 <= now]
    if not team:
        return None
    trial = {**case, "team": team}
    if case["kind"] in incidents.MOVING:
        if not case.get("chase"):
            return baseline(case)                # nobody's got ahead of it: GCPD's cars, as if nobody went
        p = max(baseline(case), 0.08, min(0.92, 0.5 + (team_fit(team, case["kind"]) - difficulty(trial)) * 1.3))
    else:
        late = too_late(case)
        if late and sort in ("street", "petty"):
            return baseline(case)                # over before anyone's there: GCPD's, as if nobody went
        # Once it's gone wrong it stays gone wrong — the ending keeps the penalty, so the odds do too
        # (they dipped during "gone wrong" and bounced back while it wrapped up).
        shown_wrong = (case.get("status") == "on scene" and cases.goes_wrong(case)
                       and cases.phase(case, now)[0] in ("gone wrong", "wrapping up"))
        p = chance(trial, gone_wrong=shown_wrong, backup=len(team) > 1, late=late)
        if late:
            p = max(baseline(case), p - 0.2)
    if not projected:
        # Worked a while, it shows which way it's going: drawn toward how it'll actually end.
        landed = min((members[m].get("joined", now) + members[m].get("travel", 9) * 60) for m in team if m in members) \
            if any(m in members for m in team) else now
        span = max(60.0, case.get("due", now) - landed)
        f = max(0.0, min(1.0, (now - landed) / span))
        if f > 0:
            truth = 1.0 if decide(case).get("ok") else 0.0
            w = 0.55 * f * f
            p = p * (1 - w) + truth * w
    return round(max(0.0, min(1.0, p)), 3)


def threat(case):
    """Who's behind it, as the board would grade them: '' for nobody in particular."""
    d = DIFFICULTY.get(case.get("suspect") or "")
    if d is None:
        return ""
    return "lethal" if d >= 0.85 else "dangerous" if d >= 0.68 else "tricky" if d >= 0.5 else "manageable"
