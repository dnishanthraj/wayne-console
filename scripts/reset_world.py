"""
Start Gotham over — the conversations, the world, or both — with a backup first.

    venv/bin/python scripts/reset_world.py --all          # everything they remember, and the night
    venv/bin/python scripts/reset_world.py --chats        # calls, texts, group chats, memories, hearsay
    venv/bin/python scripts/reset_world.py --world        # where everyone is, cases, mail, the papers, rogues
    venv/bin/python scripts/reset_world.py --all --dry-run

What goes and what's kept is wayne.store.reset's, which the console's
Settings uses too (that one restarts the console and resets as it comes back
up). The data directory — the database by SQLite's own backup — is copied to
data/_backup-<when>/ before anything goes. Stop the console first: a running
server would write its state straight back.
"""
import argparse
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wayne import paths, store  # noqa: E402
from wayne.store.reset import backup, targets  # noqa: E402 — the one list of what goes


def _running(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except Exception:
        return False


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
