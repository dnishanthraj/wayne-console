"""
The database (DATA_DIR/wayne.db): bring an older console's JSON files in, and look it over.

    venv/bin/python scripts/migrate_to_db.py              # what's there: the files to bring in, or the database
    venv/bin/python scripts/migrate_to_db.py --now        # bring them in (the console does this itself on start)
    venv/bin/python scripts/migrate_to_db.py --check      # integrity, what's in it, anything quarantined
    venv/bin/python scripts/migrate_to_db.py --reseal     # every sealed value written again with today's key

Bringing the files in is one transaction, read back and compared before it's
kept; the files are then moved, untouched, to data/_imported-<when>/. To go back
to a console from before the database: scripts/export_db_to_json.py.

--reseal is for after WAYNE_MEMORY_KEY is first set or changed: whatever was
written plain (or with the old key, if it's still set as the key that opens it)
is sealed again with the current one. Values that won't open are left alone —
they're in the quarantine table, waiting for the key that opens them.
"""
import argparse
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wayne import paths, store  # noqa: E402
from wayne.store import legacy  # noqa: E402
from wayne.store import seal as sealing  # noqa: E402

# Every sealed column: (table, key column, value column).
SEALED = (("kv", "rowid", "value"), ("messages", "rid", "body"), ("transcript", "id", "body"),
          ("events", "id", "text"), ("events", "id", "data"))


def _running(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except Exception:
        return False


def status():
    db_file = paths.db_file()
    waiting = legacy.found(paths.DATA_DIR)
    if not db_file.exists():
        print(f"No database yet at {db_file}.")
        print(f"{len(waiting)} file(s) would be brought in:" if waiting else "Nothing to bring in: it starts empty.")
        for p in waiting:
            print("  " + str(p.relative_to(paths.DATA_DIR)))
        return 0
    db = store.get()
    print(f"Database: {db_file} ({db_file.stat().st_size // 1024} KB)")
    print(f"  imported: {db.meta('imported_at') and 'yes, into ' + str(db.meta('imported_to') or '?') or 'nothing (started empty)'}")
    if waiting:
        print(f"  {len(waiting)} JSON file(s) are still in the data directory; the database doesn't read them:")
        for p in waiting:
            print("    " + str(p.relative_to(paths.DATA_DIR)))
    return 0


def check():
    db = store.get()
    ok = db.row("PRAGMA integrity_check")[0]
    keys = db.rows("PRAGMA foreign_key_check")
    print(f"integrity: {ok}; foreign keys: {'ok' if not keys else f'{len(keys)} broken'}")
    print("what's in it:")
    for space, n in db.rows("SELECT CASE WHEN instr(space, '/') THEN substr(space, 1, instr(space, '/')) || '…' "
                            "ELSE space END AS s, COUNT(*) FROM kv GROUP BY s ORDER BY s"):
        print(f"  {space:16} {n}")
    for table in ("messages", "transcript", "events", "event_links", "event_audience"):
        print(f"  {table:16} {db.row(f'SELECT COUNT(*) FROM {table}')[0]}")
    for kind, n in db.rows("SELECT CASE WHEN instr(kind, '.') THEN substr(kind, 1, instr(kind, '.')) || '…' "
                           "ELSE kind END AS k, COUNT(*) FROM events GROUP BY k ORDER BY k"):
        print(f"    {kind:14} {n}")
    held = db.rows("SELECT tbl, pk FROM quarantine ORDER BY at")
    print(f"quarantined (wouldn't open with this key): {len(held)}")
    for tbl, pk in held[:20]:
        print(f"  {tbl} {pk}")
    return 0 if ok == "ok" and not keys else 1


def reseal():
    db = store.get()
    count = 0
    for table, key, column in SEALED:
        with db.tx() as c:
            rows = c.execute(f"SELECT {key}, {column} FROM {table} WHERE {column} IS NOT NULL").fetchall()
            for pk, blob in rows:
                text = sealing.unseal(blob, (table, pk), c)
                if text is None:
                    continue
                c.execute(f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (sealing.seal(text), pk))
                count += 1
    db.conn.execute("VACUUM")          # the old pages, with whatever they held, gone from the file
    print(f"Sealed {count} value(s) again with the current key.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--now", action="store_true", help="bring the JSON files in now")
    parser.add_argument("--check", action="store_true", help="integrity, contents, quarantine")
    parser.add_argument("--reseal", action="store_true", help="seal every value again with the current key")
    parser.add_argument("--port", type=int, default=8420, help="the console's port, to check it's stopped")
    args = parser.parse_args()
    if (args.now or args.reseal) and _running(args.port):
        print(f"The console is running on port {args.port}: stop it first.")
        return 1
    try:
        if args.now:
            existed = paths.db_file().exists()
            db = store.get()
            if existed:
                print("The database was already there; nothing brought in.")
            else:
                print(f"Brought in; the originals are in {db.meta('imported_to') or '(there were none)'}.")
                skipped = db.meta("import_skipped")
                if skipped:
                    print(f"Left where they were (couldn't be read): {skipped}")
            return 0
        if args.check:
            return check()
        if args.reseal:
            return reseal()
        return status()
    finally:
        store.close_all()


if __name__ == "__main__":
    sys.exit(main())
