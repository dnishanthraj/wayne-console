"""
Keeping the database in shape: what's past keeping goes, once a day.

  transcript  past TRANSCRIPT_DAYS (WAYNE_TRANSCRIPT_DAYS, 90)
  events      past EVENT_DAYS (WAYNE_EVENT_DAYS, 365) — a year of their days,
              the cases, the city's news
  quarantine  never: a value that wouldn't open is someone's, until the right
              key reads it (scripts/migrate_to_db.py --check lists them)

Then SQLite's own tidy (PRAGMA optimize), a checkpoint so the write-ahead log
doesn't grow without end — and a copy of the whole database, taken whole while
it's in use, into data/_backup-daily/ (the last BACKUPS days of them). One file
holds everything now: one bad day shouldn't cost more than a day.
"""
import logging
import time

from ..config import setting
from . import db as database
from . import transcript

EVENT_DAYS = float(setting("EVENT_DAYS", "365") or 0)
DIARY_DAYS = 30
BACKUPS = int(setting("BACKUPS", "7") or 0)
_log = logging.getLogger("wayne")


def run(now=None):
    """Clear out what's past keeping. Returns {what: how many went}."""
    now = now or time.time()
    store = database.get()
    gone = {}
    with store.tx() as c:
        if transcript.DAYS > 0:
            gone["transcript"] = c.execute("DELETE FROM transcript WHERE at < ?", (now - transcript.DAYS * 86400,)).rowcount
        if EVENT_DAYS > 0:
            gone["events"] = c.execute("DELETE FROM events WHERE at < ?", (now - EVENT_DAYS * 86400,)).rowcount
        # Their days — 'diary.' lines, hundreds a day — matter for a month, not a year: the search index loads them all.
        gone["diary lines"] = c.execute("DELETE FROM events WHERE kind >= 'diary.' AND kind < 'diary/' AND at < ?",
                                        (now - DIARY_DAYS * 86400,)).rowcount
        c.execute("DELETE FROM event_audience WHERE event_id NOT IN (SELECT id FROM events)")
        c.execute("DELETE FROM event_links WHERE event_id NOT IN (SELECT id FROM events)")
    with store.lock:
        store.conn.execute("PRAGMA optimize")
        store.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    if BACKUPS > 0:
        backup(store, now)
    store.set_meta("upkeep_at", now)
    if any(gone.values()):
        from . import index
        index.stale(store)              # what went is gone from what's found, too: built afresh when next asked
        _log.info("upkeep: %s", ", ".join(f"{n} {what}" for what, n in gone.items() if n))
    return gone


def backup(store, now=None):
    """Today's copy of the database in _backup-daily/, and only the last BACKUPS of them kept."""
    folder = store.path.parent / "_backup-daily"
    folder.mkdir(exist_ok=True)
    target = folder / time.strftime("wayne-%Y-%m-%d.db", time.localtime(now or time.time()))
    store.backup(target)
    for old in sorted(folder.glob("wayne-*.db"))[:-BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def due(now=None):
    """Whether a day has passed since the last upkeep."""
    now = now or time.time()
    try:
        last = float(database.get().meta("upkeep_at", 0) or 0)
    except ValueError:
        last = 0.0
    return now - last > 86400


def daily():
    """Run it if it's due — at start, and from the pulse."""
    if due():
        run()
