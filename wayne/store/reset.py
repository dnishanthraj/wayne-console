"""
Starting Gotham over — the conversations, the world, or both — with a backup first.

From the console's Settings: the reset is asked for (`request`), the console restarts itself, and the reset is
done as it comes back up (`pending`, called before anything else opens the database) — so nothing still
running can write the old world straight back. From the terminal, scripts/reset_world.py does the same with
the console stopped.

What goes, for the conversations: calls, texts, group chats, memories, hearsay and word passed on, plans, where
they stand with him and each other, and what each has come to like. For the world: where everyone is, their
diaries, cases, mail and the diary of engagements, the papers, the rogues. What's kept either way: who they are
(their bios), your pins, photo framing, the Codex you've written, who knows what, mutes, what each of them
follows, and the audio caches. The data directory — the database by SQLite's own backup — is copied to
data/_backup-<when>/ before anything goes.
"""
import json
import shutil
import sqlite3
import time

from .. import paths

# Spaces (see wayne.store.kv) — "<name>/" means every contact's; "doc:<name>" one document.
CHATS_SPACES = ("window", "groups", "vault/", "story/", "plans", "doc:initiative", "bonds", "tastes")
WORLD_SPACES = ("presence/", "diary_last", "cases", "dispatch", "reportlog", "mail", "mail_keys", "mail_events",
                "engagements", "gazette", "rogue_status", "encountered", "doc:arcs", "doc:jet", "doc:bruce")
# Events (see wayne.store.events), by kind prefix.
CHATS_EVENTS = ("hearsay", "relayed", "plan.", "bond")
WORLD_EVENTS = ("diary.", "case.", "rogue.")
SCOPES = {"all": (True, True), "chats": (True, False), "world": (False, True)}

_PENDING = "_reset-pending.json"
_DONE = "_reset-done.json"


def _after(prefix):
    """The first string past every one starting with `prefix` — for a range the index can use."""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def targets(db, chats, world):
    """What would go: [(what, how many, (sql, params) that removes it)], only what's there."""
    out = []
    for name in (CHATS_SPACES if chats else ()) + (WORLD_SPACES if world else ()):
        if name.startswith("doc:"):
            where = ("space = 'doc' AND key = ?", (name[4:],))
            what = f"document {name[4:]}"
        elif name.endswith("/"):
            where = ("space >= ? AND space < ?", (name, _after(name)))
            what = f"{name}… (every contact's)"
        else:
            where = ("space = ?", (name,))
            what = name
        n = db.row(f"SELECT COUNT(*) FROM kv WHERE {where[0]}", where[1])[0]
        out.append((what, n, (f"DELETE FROM kv WHERE {where[0]}", where[1])))
    if chats:
        out.append(("texts and group messages", db.row("SELECT COUNT(*) FROM messages")[0], ("DELETE FROM messages", ())))
        out.append(("transcript", db.row("SELECT COUNT(*) FROM transcript")[0], ("DELETE FROM transcript", ())))
    if chats and world:
        out.append(("quarantined rows", db.row("SELECT COUNT(*) FROM quarantine")[0], ("DELETE FROM quarantine", ())))
    for prefix in (CHATS_EVENTS if chats else ()) + (WORLD_EVENTS if world else ()):
        where = ("kind >= ? AND kind < ?", (prefix, _after(prefix)))
        n = db.row(f"SELECT COUNT(*) FROM events WHERE {where[0]}", where[1])[0]
        out.append((f"events {prefix}…", n, (f"DELETE FROM events WHERE {where[0]}", where[1])))
    return [t for t in out if t[1]]


def backup(root, db):
    """The data directory copied — every file but the backups and the audio — and the database, whole."""
    target = root / time.strftime("_backup-%Y%m%d-%H%M%S")
    shutil.copytree(root, target, ignore=shutil.ignore_patterns("_backup-*", "_imported-*", "ambience", "_system_voice",
                                                               "wayne.db", "wayne.db-*", "wayne.failed-*", _PENDING,
                                                               _DONE))
    db.backup(target / "wayne.db")
    return target


def run(chats=True, world=True):
    """Back up, then remove what goes. {"backup": its folder name, "removed": [(what, how many)]}."""
    from .. import store
    root = paths.DATA_DIR
    db = store.get()
    going = targets(db, chats, world)
    kept = backup(root, db)
    with db.tx() as c:
        for _, _, (sql, params) in going:
            c.execute(sql, params)
        c.execute("DELETE FROM event_audience WHERE event_id NOT IN (SELECT id FROM events)")
        c.execute("DELETE FROM event_links WHERE event_id NOT IN (SELECT id FROM events)")
    try:
        with db.lock:
            db.conn.execute("VACUUM")
    except sqlite3.Error:
        pass
    store.close_all()
    return {"backup": kept.name, "removed": [(what, n) for what, n, _ in going]}


def request(scope):
    """Settings asked for a reset: written down, to be done as the console comes back up (see pending)."""
    if scope not in SCOPES:
        raise ValueError(f"no such reset: {scope}")
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (paths.DATA_DIR / _PENDING).write_text(json.dumps({"scope": scope, "at": time.time()}))


def pending():
    """
    A reset Settings asked for, done now — at start-up, before anything else has the database open — and what it
    did kept for the page to say once (see done). None if there wasn't one.
    """
    marker = paths.DATA_DIR / _PENDING
    if not marker.exists():
        return None
    try:
        scope = json.loads(marker.read_text()).get("scope", "all")
    except (OSError, ValueError):
        scope = "all"
    marker.unlink(missing_ok=True)
    chats, world = SCOPES.get(scope, SCOPES["all"])
    result = {**run(chats, world), "scope": scope, "at": time.time()}
    (paths.DATA_DIR / _DONE).write_text(json.dumps(result))
    return result


def done():
    """What the last reset from Settings did — said once, then forgotten. None if there's nothing to say."""
    marker = paths.DATA_DIR / _DONE
    if not marker.exists():
        return None
    try:
        found = json.loads(marker.read_text())
    except (OSError, ValueError):
        found = None
    marker.unlink(missing_ok=True)
    return found
