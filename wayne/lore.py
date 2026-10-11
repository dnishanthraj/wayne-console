"""
Your own Gotham, kept on this machine: a diner named for a friend, a reporter, a footballer in the papers, a thing
one of them knows — added to the city without touching the repository, which is public. Nobody's private in-jokes
belong on GitHub; they can belong on the map.

DATA_DIR/lore.json (or a path in WAYNE_LORE), every part optional:

  {"places": [{"name": "Some Diner", "kind": "diner", "area": "Burnside", "x": 108.6, "y": 68.3,
               "bio": "what it is", "note": "what he thinks of it"}],
   "people": [{"id": "someone", "name": "Some One", "group": "Gotham", "occupation": "…", "age": 26,
               "pronoun": "she", "home": "…", "bio": "…",
               "public": "who they are, in a line, as anyone who knows of them would put it",
               "unknown_to": ["lucius"]}],
   "facts": [{"text": "something they know", "known_by": ["nightwing", "robin"]}],
   "correspondents": {"someone": ["Some One", "some@gothamgazette.com", 2, "who they are, what they write about"]}}

Places go on the map beside its own diners and cafés (a kind the map draws: club, bar, diner, cafe, restaurant,
shop, gym, cinema, hotel, church, school), into the Codex and into where anyone can say they're going; people go into
the Codex — and into what everyone knows, as their `public` line, but for whoever `unknown_to` names; facts into the
briefing of whoever `known_by` names (contact ids, or how much they know — see wayne.operator); correspondents into
the mail. Missing or unreadable, there's no local lore and nothing changes.
"""
import json
import logging
import threading
from pathlib import Path

from . import config, paths

VENUE_KINDS = ("club", "bar", "diner", "cafe", "restaurant", "shop", "gym", "cinema", "hotel", "church", "school")

_cache = {"path": None, "mtime": None, "data": {}}
_lock = threading.Lock()


def path():
    """Where it's kept: WAYNE_LORE, or lore.json in the data directory (never committed)."""
    custom = (config.setting("LORE", "") or "").strip()
    return Path(custom).expanduser() if custom else paths.DATA_DIR / "lore.json"


def load():
    """The whole of it, read again only when the file changes; {} when there's none."""
    where = path()
    try:
        mtime = where.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _cache["path"] == where and _cache["mtime"] == mtime:
            return _cache["data"]
        try:
            data = json.loads(where.read_text())
        except (OSError, ValueError) as exc:
            logging.getLogger("wayne").warning("local lore %s unreadable (%s); ignored", where, exc)
            data = {}
        _cache.update(path=where, mtime=mtime, data=data if isinstance(data, dict) else {})
        return _cache["data"]


def venues():
    """Its places, as the map's own venues are: {name, kind, area, x, y, bio, note}."""
    out = []
    for p in load().get("places") or []:
        if not isinstance(p, dict) or not p.get("name") or p.get("kind") not in VENUE_KINDS:
            continue
        try:
            x, y = float(p["x"]), float(p["y"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"name": str(p["name"]), "kind": p["kind"], "area": str(p.get("area") or ""), "x": x, "y": y,
                    "bio": str(p.get("bio") or ""), "note": str(p.get("note") or ""), "lore": True})
    return out


def people():
    """Its people, as the Codex's own are (an id and a name at least)."""
    return [dict(p, lore=True) for p in load().get("people") or []
            if isinstance(p, dict) and p.get("id") and p.get("name")]


def known_to(contact_id):
    """
    The people of its Gotham this contact would know of — most know most of them; a person's `unknown_to` says who
    doesn't (Lucius has never been in a Burnside diner) — each as their `public` line, or their name and what they do.
    """
    out = []
    for person in people():
        if contact_id in (person.get("unknown_to") or ()):
            continue
        out.append(person.get("public") or f"{person['name']} — {person.get('occupation', '')}".rstrip(" —"))
    return out


def facts():
    """Its facts: [{text, known_by}] — known_by as the operator profile's world has it."""
    return [f for f in load().get("facts") or [] if isinstance(f, dict) and f.get("text")]


def correspondents():
    """Its letter-writers: {id: (name, address, how often, who they are)}."""
    out = {}
    for cid, entry in (load().get("correspondents") or {}).items():
        if isinstance(entry, (list, tuple)) and len(entry) == 4 and all(entry[i] for i in (0, 1, 3)):
            try:
                out[str(cid)] = (str(entry[0]), str(entry[1]), int(entry[2]), str(entry[3]))
            except (TypeError, ValueError):
                continue
    return out
