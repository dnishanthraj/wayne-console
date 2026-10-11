"""
The database written back out as the JSON files a console from before it reads.

    venv/bin/python scripts/export_db_to_json.py                 # into data/_exported-<when>/
    venv/bin/python scripts/export_db_to_json.py --to some/dir
    venv/bin/python scripts/export_db_to_json.py --in-place      # into the data directory itself

--in-place is for going back to an older console on the same data: the files
land where it looks for them (sealed with the memory key, as it wrote them).
Stop the console first. What only the database keeps — the transcript past the
window, diary lines older than a day and a half, the events — has no place in
the old files and isn't written; the database itself is left as it is.
"""
import argparse
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wayne import paths, store  # noqa: E402
from wayne.store import legacy  # noqa: E402


def _running(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--to", type=Path, help="the directory to write into")
    where.add_argument("--in-place", action="store_true", help="write into the data directory itself")
    parser.add_argument("--port", type=int, default=8420, help="the console's port, to check it's stopped")
    args = parser.parse_args()
    if not paths.db_file().exists():
        print(f"No database at {paths.db_file()}: nothing to export.")
        return 1
    if args.in_place and _running(args.port):
        print(f"The console is running on port {args.port}: stop it first.")
        return 1
    target = paths.DATA_DIR if args.in_place else (args.to or paths.DATA_DIR / time.strftime("_exported-%Y%m%d-%H%M%S"))
    try:
        written = legacy.export(target)
    finally:
        store.close_all()
    print(f"Wrote {len(written)} file(s) to {target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
