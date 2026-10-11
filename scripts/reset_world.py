"""
Start Gotham over — the conversations, the world, or both — with a backup first.

    venv/bin/python scripts/reset_world.py --all          # everything they remember, and the night
    venv/bin/python scripts/reset_world.py --chats        # calls, texts, group chats, memories, hearsay
    venv/bin/python scripts/reset_world.py --world        # where everyone is, cases, mail, the papers, rogues
    venv/bin/python scripts/reset_world.py --all --dry-run

What's kept either way: who they are (their bios), your pins, photo framing,
the Codex you've written, mutes, what each of them follows, and the audio
caches. The data directory — the database by SQLite's own backup — is copied to
data/_backup-<when>/ before anything goes. Stop the console first: a running
server would write its state straight back.
"""
import argparse
import shutil
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wayne import paths, store  # noqa: E402

# Spaces (see wayne.store.kv) — "<name>/" means every contact's; "doc:<name>" one document.
CHATS_SPACES = ("window", "groups", "vault/", "story/", "plans", "doc:initiative")
WORLD_SPACES = ("presence/", "diary_last", "cases", "dispatch", "reportlog", "mail", "mail_keys", "mail_events",
                "gazette", "rogue_status", "encountered", "doc:arcs", "doc:jet", "doc:bruce")
# Events (see wayne.store.events), by kind prefix.
CHATS_EVENTS = ("hearsay", "plan.")
WORLD_EVENTS = ("diary.", "case.", "rogue.")


def _running(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except Exception:
        return False


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
    for prefix in (CHATS_EVENTS if chats else ()) + (WORLD_EVENTS if world else ()):
        where = ("kind >= ? AND kind < ?", (prefix, _after(prefix)))
        n = db.row(f"SELECT COUNT(*) FROM events WHERE {where[0]}", where[1])[0]
        out.append((f"events {prefix}…", n, (f"DELETE FROM events WHERE {where[0]}", where[1])))
    return [t for t in out if t[1]]


def backup(root, db):
    """The data directory copied — every file but the backups and the audio — and the database, whole."""
    target = root / time.strftime("_backup-%Y%m%d-%H%M%S")
    shutil.copytree(root, target, ignore=shutil.ignore_patterns("_backup-*", "_imported-*", "ambience", "wayne.db",
                                                               "wayne.db-*", "wayne.failed-*"))
    db.backup(target / "wayne.db")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="the conversations and the world")
    parser.add_argument("--chats", action="store_true", help="calls, texts, group chats, memories, hearsay")
    parser.add_argument("--world", action="store_true", help="whereabouts, diaries, cases, mail, papers, rogues")
    parser.add_argument("--dry-run", action="store_true", help="say what would go, and stop")
    parser.add_argument("--port", type=int, default=8420, help="the console's port, to check it's stopped")
    args = parser.parse_args()
    chats, world = args.all or args.chats, args.all or args.world
    if not (chats or world):
        parser.error("say what to reset: --all, --chats or --world")
    root = paths.DATA_DIR
    if not root.exists():
        print(f"Nothing to reset: {root} doesn't exist.")
        return 0
    if not args.dry_run and _running(args.port):
        print(f"The console is running on port {args.port}: stop it first, or it'll write everything back.")
        return 1
    db = store.get()            # an older console's files are brought in first (see wayne.store.legacy)
    going = targets(db, chats, world)
    for what, n, _ in going:
        print(f"{'would remove' if args.dry_run else 'removing'} {what}: {n}")
    if args.dry_run or not going:
        print("Nothing to do." if not going else f"{len(going)} item(s).")
        return 0
    print(f"Backed up to {backup(root, db)}")
    with db.tx() as c:
        for _, _, (sql, params) in going:
            c.execute(sql, params)
        c.execute("DELETE FROM event_audience WHERE event_id NOT IN (SELECT id FROM events)")
    try:
        with db.lock:
            db.conn.execute("VACUUM")
    except sqlite3.Error:
        pass
    store.close_all()
    print(f"Done: {len(going)} item(s) removed. Start the console and Gotham begins again from now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
