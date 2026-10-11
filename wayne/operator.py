"""
Who the user is.

The console is a roleplay of Gotham, and by default the person on the console
is Bruce Wayne. That lives in a profile — wayne/operators/bruce.json — rather
than in any character, so the cast stays the cast and the user can be someone
else: copy the file, rewrite it about yourself, and point WAYNE_OPERATOR at it
(an id under wayne/operators/, or a path). Every contact follows, because what
they call you is part of your profile, not theirs.

Facts about the world carry who knows them. A secret identity reaches only the
characters who would know it, so nobody can let slip what they never knew.
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from . import config
from .paths import ROOT_DIR

OPERATORS_DIR = Path(__file__).parent / "operators"


@lru_cache(maxsize=1)
def profile():
    choice = (config.setting("OPERATOR", "bruce") or "bruce").strip()
    path = Path(choice).expanduser()
    if not path.suffix:
        path = OPERATORS_DIR / f"{choice}.json"
    elif not path.is_absolute():
        path = ROOT_DIR / path
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        # Said, not swallowed: a typo here silently made everyone Bruce.
        logging.getLogger("wayne").warning("operator profile %s unreadable (%s); using Bruce",
                                           path, exc)
        return json.loads((OPERATORS_DIR / "bruce.json").read_text())


def name(contact_id=None):
    """His name — as this contact knows him, if one's given: Batman to someone who knows only the mask."""
    if contact_id:
        from . import identity
        return identity.name_for(contact_id)
    return profile().get("name") or config.USER_NAME


def full_name(contact_id=None):
    if contact_id:
        from . import identity
        return identity.full_name_for(contact_id)
    return profile().get("full_name") or name()


def mask_name():
    """Who he is in the dark — Batman — to the ones who know him only that way."""
    return profile().get("mask") or name()


def _knows(fact, contact_id, level):
    """
    Whether a fact reaches this contact: '*' for everyone, or a list (or one) of
    contact ids and knowledge levels — 'all', 'his', 'masks', 'none' (see wayne.identity).
    """
    known = fact.get("known_by", "*")
    if known == "*":
        return True
    known = [known] if isinstance(known, str) else known
    return contact_id in known or level in known


def roleplay():
    """
    Whether who he plays is a character (Bruce) rather than himself: then the
    world's past is theirs to share, and nothing is "real life" to protect.
    """
    return bool(profile().get("roleplay"))


def briefing(contact_id):
    """
    Who he is, what this contact calls him, and what this contact knows — by
    how they know him (wayne.identity): to Jim, Batman and nothing more.
    """
    from . import identity
    p = profile()
    level = identity.level(contact_id)
    intro = (p.get("intros") or {}).get(level) or p.get("intro", "")
    lines = [f"=== WHO HE IS ===\n{intro}".strip()]
    address = p.get("address", {}).get(contact_id) or (p.get("default_addresses") or {}).get(level) \
        or p.get("default_address")
    if address:
        lines.append(f"What you call him: {address}")
    from . import lore
    facts = [f["text"] for f in p.get("world", []) + lore.facts() if _knows(f, contact_id, level)]
    if facts:
        lines.append("What you know — background, not material: don't recite it, raise it "
                     "only when it matters, and never claim more than this:\n"
                     + "\n".join(f"- {fact}" for fact in facts))
    others = identity.who_doesnt_know(contact_id)
    if others:
        lines.append(others)
    return "\n\n".join(lines)


def whisper_hints():
    return [name(), full_name()] + list(profile().get("whisper_hints", []))
