"""
Who knows what about the masks — and so what each of them is told, what they
may say, and who they hear when someone speaks.

Each contact knows him one of four ways (the operator profile's `identity`,
changeable at any time from the Codex):

  all     everything: he's Batman, and who's under every other mask
          (the family, Alfred, Lucius)
  his     he's Batman — nobody else's face (Selina)
  masks   the masks, never the faces: Batman is Batman, Batgirl is Batgirl,
          and nobody's name is known (Jim Gordon)
  none    nothing: he's Bruce Wayne, and Batman is someone in the papers

Everything that keeps the secret follows from it, rather than from rules
written about anyone in particular:

  * the briefing each of them gets — who he is (Batman to Jim, never Bruce),
    and only the facts their level holds (see operator.briefing);
  * his name in their prompts, and how they hear everyone else on a call or in
    a chat: to Jim, Barbara on the line is Batgirl, Dick is Nightwing, Alfred
    is a voice on Batman's end — never a name;
  * what the ones who know more are told when someone who knows less is
    listening — call him Batman with Jim there, real names only with Selina —
    and the address guard that catches the slip anyway;
  * what news reaches whom (grapevine) and what's remembered for whom (recall).

So when the story changes — Jim learns the truth, or Selina finds out who
Nightwing is — it's one setting, and every conversation follows.
"""
from . import operator

LEVELS = ("all", "his", "masks", "none")
LABELS = {"all": "Everything — who he is, and who's under every mask",
          "his": "Who he is — nobody else's mask",
          "masks": "Only the masks — no faces, his included",
          "none": "Nothing — he's Bruce Wayne"}
# The operator himself, in group threads and on calls.
ME = ("me", "bruce")

# What would give him away, said to his face in front of someone who doesn't know: his names, and theirs for him.
_HIS_NAMES = ("Bruce", "Master Bruce", "Master Wayne", "Mr. Wayne", "Mister Wayne", "Wayne", "B", "Brucie", "Dad",
              "old man")


def _store_space():
    from . import store
    return store.space("identity")


def level(contact_id):
    """How this contact knows him: a setting made since (from the Codex), else the operator profile's."""
    if not contact_id or contact_id in ME:
        return "all"
    try:
        override = _store_space().get(contact_id)
    except Exception:
        override = None
    if override in LEVELS:
        return override
    profile = operator.profile()
    found = (profile.get("identity") or {}).get(contact_id)
    if found in LEVELS:
        return found
    # Profiles from before levels: an insiders list, and everyone else outside it.
    known = (profile.get("secrets") or {}).get("known_by")
    if known:
        return "all" if contact_id in known else "his"
    return (profile.get("identity") or {}).get("default", "all")


def set_level(contact_id, value):
    """Change what someone knows — the story moved on. `None` puts back what the profile says."""
    if value is None:
        _store_space().drop(contact_id)
    elif value in LEVELS:
        _store_space().put(contact_id, value)
    else:
        raise ValueError(f"no such level: {value}")


def knows_him(contact_id):
    """Whether they know he's Batman."""
    return level(contact_id) in ("all", "his")


def knows_faces(contact_id):
    """Whether they know who's under the other masks."""
    return level(contact_id) == "all"


def outside(contact_id):
    """Anyone who doesn't know everything — the secret's still partly kept from them."""
    return level(contact_id) != "all"


def insiders(contacts):
    """The ids, of these contacts, who know everything."""
    return {c.id for c in contacts if level(c.id) == "all"}


# --- names ---------------------------------------------------------------------------

def name_for(viewer_id):
    """His name as they know him: Batman to someone who knows only the masks, Bruce to everyone else."""
    if viewer_id and level(viewer_id) == "masks":
        return operator.mask_name()
    return operator.name()


def full_name_for(viewer_id):
    if viewer_id and level(viewer_id) == "masks":
        return operator.mask_name()
    return operator.full_name()


def _contact(contact_id):
    from .contacts import directory
    return directory().get(contact_id)


def mask_of(contact):
    """Who someone is to a person who knows only the masks: their mask, else a cover, else someone on his line."""
    if contact is None:
        return "someone on the line"
    return (getattr(contact, "mask", "") or getattr(contact, "cover", "")
            or f"someone on {operator.mask_name()}'s line")


def label(member_id, viewer_id, full=False):
    """
    How the viewer knows someone on a call or in a chat. Jim hears Barbara as
    Batgirl and Alfred as a voice on Batman's end; Selina hears Dick as Dick.
    """
    if member_id in ME:
        return full_name_for(viewer_id) if full else name_for(viewer_id)
    if member_id == viewer_id:
        return "you"
    contact = _contact(member_id)
    if contact is None:
        return member_id
    if viewer_id and level(viewer_id) == "masks" and level(member_id) != "masks":
        return mask_of(contact)
    return contact.full_name if full else contact.name


def names_of(contact):
    """Every name someone might be called by on a call: first, full, nicknames, mask, cover."""
    return {n for n in (contact.name, contact.full_name, getattr(contact, "mask", ""), getattr(contact, "cover", ""),
                        *getattr(contact, "nicknames", ())) if n}


def strict(listener_ids):
    """Whether someone listening may hear no face at all — Jim, or anyone who doesn't know."""
    return any(level(lid) in ("masks", "none") for lid in listener_ids)


def handle(member_id, listener_ids):
    """
    What someone is called where all of these can hear — a tag in a group chat:
    '@Batgirl' with Jim in it, '@Barbara' without; him as 'Batman' to Jim.
    """
    if member_id in ME:
        return operator.mask_name() if any(level(lid) == "masks" for lid in listener_ids) else operator.name()
    contact = _contact(member_id)
    if contact is None:
        return member_id
    if getattr(contact, "mask", "") and level(member_id) == "all" and strict(listener_ids):
        return contact.mask
    return contact.name


# --- with someone listening who knows less ---------------------------------------------------

def guards(listener_ids):
    """
    What has to be kept with these people listening: 'him' (his name and face), 'faces' (who's under
    the other masks), 'work' (patrols, cases, the cave — anything of the life at all).
    """
    needs = set()
    for lid in listener_ids:
        lv = level(lid)
        if lv == "his":
            needs |= {"faces", "work"}      # Selina knows the faces: any of the work would put a mask on one
        elif lv == "masks":
            needs |= {"him", "faces"}       # Jim knows the work — by the masks, and only by them
        elif lv == "none":
            needs |= {"him", "faces", "work"}
    return needs


def guard_note(speaker_id, listener_ids, where="call"):
    """
    For someone who knows more than whoever else is listening: who that is,
    what they don't know, and how to talk with them there. '' when nobody needs
    guarding from — or the speaker knows no more than they do.
    """
    mine = level(speaker_id)
    if mine in ("masks", "none"):
        return ""
    speaker = _contact(speaker_id)
    on = "on this call" if where == "call" else "in this chat"
    notes = []
    for lid in listener_ids:
        if lid == speaker_id or lid in ME:
            continue
        lv, other = level(lid), _contact(lid)
        if other is None:
            continue
        who = other.full_name
        if lv == "masks":
            you = mask_of(speaker) if mine == "all" else ""
            notes.append(
                f"{who} is {on}. {other.pronoun.capitalize() if other.pronoun != 'they' else 'They'} "
                f"know{'s' if other.pronoun != 'they' else ''} {operator.mask_name()} and the masks — never who anyone "
                f"is under them, and not who he is. While {other.name} is there: he's {operator.mask_name()}, never "
                "his name; masks only, never anyone's real name"
                + (f" — to {other.name} you're {you}" if you else "")
                + "; nothing about the cave, the Manor, the family, anyone's day life, or anything that would put "
                "a face to a mask. Keep to the business at hand.")
        elif lv == "his" and mine == "all":
            notes.append(
                f"{who} is {on}. {other.name} knows he's {operator.mask_name()} — not who's under any of the other "
                f"masks. In front "
                f"of {other.name}: no patrols, cases, villains, suits, gear, the cave, or who's under any other "
                "mask — real names only, nothing a family and its friends wouldn't say. If someone slips, cover "
                "it lightly, as you would.")
        elif lv == "none":
            notes.append(
                f"{who} is {on} and doesn't know he's {operator.mask_name()}, or anything about it: nothing of the "
                "work at all — he's Bruce Wayne, and the rest of you are who you are by day.")
        around = (getattr(speaker, "around", None) or {}).get(lid) if speaker else None
        if around and lv != "all":
            notes.append(around)
    return " ".join(notes)


def guard_vocatives(speaker_id, listener_ids):
    """
    Names the speaker must not call anyone by, with these people listening —
    caught by the address guard if the model says them anyway: 'Bruce' with
    Jim there, 'Tim' with Jim there (he's Robin), 'Nightwing' with Selina there.
    """
    mine = level(speaker_id)
    needs = guards([lid for lid in listener_ids if lid != speaker_id])
    if not needs or mine in ("masks", "none"):
        return ()
    from .contacts import directory
    present = [c for c in (directory().get(lid) for lid in listener_ids) if c is not None]
    out = set()
    if "him" in needs and knows_him(speaker_id):
        out |= set(_HIS_NAMES) | {operator.name()}
    if "faces" in needs and knows_faces(speaker_id):
        strict = any(level(lid) in ("masks", "none") for lid in listener_ids)
        for c in present:
            if getattr(c, "mask", ""):
                if strict:
                    out |= {c.name, c.full_name}          # to Jim, Dick is Nightwing: never 'Dick'
                if any(level(lid) == "his" for lid in listener_ids):
                    out.add(c.mask)                       # to Selina, Dick is Dick: never 'Nightwing'
                    out |= {n for n in getattr(c, "nicknames", ()) if n.lower() in _MASK_NICKNAMES}
            elif getattr(c, "cover", "") and strict and level(c.id) == "all":
                out |= {c.name, c.full_name}              # Alfred on Batman's line is a voice, never 'Alfred'
    return tuple(sorted(out))


# Nicknames that are also ordinary words: only a capital makes them a name ("Ran", not "ran").
_PLAIN_WORDS = {"dick", "barb", "ran", "jay", "replacement", "drake", "todd"}
# Nicknames that are a mask in all but name: to someone who knows no masks, "Oracle" gives Barbara away.
_MASK_NICKNAMES = {"oracle", "boy wonder"}
# How the family say someone with respect — swept up with the name, or "Master Dick" became "Master Nightwing".
_HONORIFIC = r"(?:(?:Master|Miss|Mister|Mr\.?|Ms\.?|Mrs\.?)\s+)?"
# A surname that's a company or a place, not a person: "Drake Industries", "Wayne Enterprises", "the Fox house".
_NOT_A_PERSON = (r"(?!\s+(?:Industries|Enterprises|Tower|Manor|Foundation|Corp\w*|Inc\b|Group|Estate|Center|Centre|"
                 r"Building|house|House|Memorial))")


def _spoken_cover(contact):
    """A cover said out loud: "a British voice on Batman's line" → "the British voice on Batman's line"."""
    import re
    return re.sub(r"^(?:a|an)\s+", "the ", contact.cover)


def scrub(text, speaker_id, listener_ids):
    """
    The slip the address guard can't catch, mended: his name in the middle of
    a sentence with someone listening who knows him only as Batman — and, with
    Jim there, anyone's name or nickname under a mask ('thanks babs' → 'thanks
    batgirl', '@Tim' → '@Robin'). 'Bruce Wayne', the man in the papers, is left alone.
    """
    if not text:
        return text
    others = [lid for lid in listener_ids if lid != speaker_id]
    needs = guards(others)
    import re
    if knows_him(speaker_id) and "him" in needs:
        first = re.escape(operator.name())
        surname = re.escape(operator.full_name().split()[-1]) if " " in operator.full_name() else "Wayne"
        text = re.sub(rf"\b{_HONORIFIC}{first}\b(?!\s+{surname})", operator.mask_name(), text)
    if knows_faces(speaker_id) and "faces" in needs and strict(others):
        from .contacts import directory
        for c in directory():
            mask = getattr(c, "mask", "") or (_spoken_cover(c) if getattr(c, "cover", "") else "")
            if not mask or level(c.id) != "all":
                continue
            # Longest first, so 'Barbara Gordon' goes before 'Barbara', and 'Dickiebird' before 'Dickie'.
            for name in sorted({c.full_name, c.name, *getattr(c, "nicknames", ())}, key=len, reverse=True):
                plain = name.lower() in _PLAIN_WORDS

                def masked(m, mask=mask):
                    if mask[:1].isupper():                # a mask is a name: Nightwing, said as he'd write it
                        return mask if m.group(0)[:1].isupper() else mask.lower()
                    opening = not m.string[:m.start()].strip() or m.string[:m.start()].rstrip()[-1:] in ".!?"
                    return mask[:1].upper() + mask[1:] if opening else mask   # a cover: "The British voice…"
                text = re.sub(rf"(?<![\w-]){_HONORIFIC}{re.escape(name)}(?![\w-]){_NOT_A_PERSON}", masked, text,
                              flags=0 if plain else re.IGNORECASE)
            # "@babs Barbara" becomes the mask twice over: said once.
            text = re.sub(rf"(@?{re.escape(mask)})(?:\s+@?{re.escape(mask)}\b)+", r"\1", text, flags=re.IGNORECASE)
    return text


def who_doesnt_know(viewer_id):
    """For someone who knows everything: who doesn't, and what — '' if everyone does."""
    if level(viewer_id) != "all":
        return ""
    from .contacts import directory
    lines = []
    for c in directory():
        if c.id == viewer_id:
            continue
        lv = level(c.id)
        if lv == "his":
            lines.append(f"{c.full_name} knows he's {operator.mask_name()}, and nobody else's mask")
        elif lv == "masks":
            lines.append(f"{c.full_name} knows {operator.mask_name()} and the masks — not one face, his included")
        elif lv == "none":
            lines.append(f"{c.full_name} doesn't know he's {operator.mask_name()} at all")
    if not lines:
        return ""
    return ("Who doesn't know — on a call or in a chat they're in, nobody gives anyone away: " + "; ".join(lines)
            + ".")


# --- someone mentioned, in words everyone hears ------------------------------------------------

_MENTION = "⁣"          # invisible separator: never typed, never spoken


def mention(contact_id):
    """Someone named in a line the console writes for a call ('<them> is on the call now'), said per listener."""
    return f"{_MENTION}{contact_id}{_MENTION}"


def render(text, viewer_id=None, full=False):
    """A line with its mentions said as this listener knows each person: Dick, to Tim; Nightwing, to Jim."""
    if not text or _MENTION not in text:
        return text
    import re
    if viewer_id is None:
        from .contacts import directory
        book = directory()

        def plain(match):
            found = book.get(match.group(1))
            return (found.full_name if full else found.name) if found else (
                operator.name() if match.group(1) in ME else match.group(1))
        return re.sub(f"{_MENTION}([^{_MENTION}]+){_MENTION}", plain, text)
    return re.sub(f"{_MENTION}([^{_MENTION}]+){_MENTION}",
                  lambda m: label(m.group(1), viewer_id, full=full), text)
