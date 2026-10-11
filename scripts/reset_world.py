"""
Start Gotham over — the conversations, the world, or both — with a backup first.

    venv/bin/python scripts/reset_world.py --all          # everything they remember, and the night
    venv/bin/python scripts/reset_world.py --chats        # calls, texts, group chats, memories, hearsay
    venv/bin/python scripts/reset_world.py --world        # where everyone is, cases, mail, the papers, rogues
    venv/bin/python scripts/reset_world.py --all --dry-run

What's kept either way: who they are (bio.txt), your pins, photo framing, the
Codex you've written, mutes, what each of them follows, and the audio caches.
The whole data directory is copied to data/_backup-<when>/ before anything goes.
Stop the console first — a running server would write its state straight back.
"""
import argparse
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wayne import paths  # noqa: E402

# Per contact, under data/<id>/.
CHATS_EACH = ("history.json", "texts.json", "hearsay.json", "vault.txt", "story.txt")
WORLD_EACH = ("presence.json", "diary.json")
# Under data/ itself.
CHATS = ("_groups", "_initiative.json", "_plans.json")
WORLD = ("_cases.json", "_dispatch.json", "_reportlog.json", "_inbox.json", "_arcs.json", "_jet.json",
         "_bruce.json", "_gazette.json", "_rogue_status.json", "_encountered.json")


def _running(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except Exception:
        return False


def targets(root, chats, world):
    """Everything that would go, as paths that exist."""
    out = []
    names = (CHATS if chats else ()) + (WORLD if world else ())
    each = (CHATS_EACH if chats else ()) + (WORLD_EACH if world else ())
    out += [root / n for n in names if (root / n).exists()]
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
                         and p.name != "ambience"):
        out += [folder / n for n in each if (folder / n).exists()]
    return out


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
    going = targets(root, chats, world)
    for p in going:
        print(("would remove " if args.dry_run else "removing ") + str(p.relative_to(root)))
    if args.dry_run or not going:
        print("Nothing to do." if not going else f"{len(going)} item(s).")
        return 0
    if _running(args.port):
        print(f"The console is running on port {args.port}: stop it first, or it'll write everything back.")
        return 1
    backup = root / time.strftime("_backup-%Y%m%d-%H%M%S")
    shutil.copytree(root, backup, ignore=shutil.ignore_patterns("_backup-*", "ambience"))
    print(f"Backed up to {backup}")
    for p in going:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    print(f"Done: {len(going)} item(s) removed. Start the console and Gotham begins again from now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
