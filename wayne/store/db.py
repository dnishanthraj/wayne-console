"""
The connection to the console's database, DATA_DIR/wayne.db.

One connection per database file, shared by every thread under one re-entrant
lock: the console's loop, the worker threads it hands model calls to, and the
grapevine's daemon. SQLite here is built serialised, and everything done inside
the lock takes microseconds — nothing slow (no model, no network) ever runs in a
transaction — so one connection is simpler than a pool and never sees "database
is locked" from itself. Other processes (a script, a second console) are kept
in step by WAL and a busy timeout, and their writes are noticed: what's cached
in memory is dropped when the file's data_version moves.

The file is opened on first use, never at import, and found again from DATA_DIR
on every call, so a test or the evaluator that points DATA_DIR elsewhere gets a
database of its own. Opening a new one where the JSON files of an older console
are found imports them first (see wayne.store.legacy).
"""
import contextlib
import logging
import os
import sqlite3
import threading
import time

from .. import paths
from . import schema

_log = logging.getLogger("wayne")
_open = {}                          # DATA_DIR -> Database
_open_lock = threading.RLock()

# How often to ask whether another process has written, at most (seconds).
_LOOK_EVERY = 0.5


class Database:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=5.0)
        for pragma in ("journal_mode = WAL", "synchronous = NORMAL", "foreign_keys = ON", "busy_timeout = 5000",
                       "temp_store = MEMORY"):
            self.conn.execute(f"PRAGMA {pragma}")
        self.created = schema.apply(self.conn)
        self._depth = 0
        self._undo = []             # caches to drop if the transaction rolls back
        self._spaces = {}
        self.epoch = 0              # bumped when another process is seen to have written
        self._version = self.conn.execute("PRAGMA data_version").fetchone()[0]
        self._looked = time.monotonic()
        self.extras = {}            # per-database singletons other modules keep here (the search index)

    # --- transactions ------------------------------------------------------------

    @contextlib.contextmanager
    def tx(self):
        """
        One transaction (BEGIN IMMEDIATE: it takes the write lock up front, so it
        can't fail halfway on a lock another process holds). Nested, it joins the
        outer one. Caches changed inside it are dropped if it rolls back.
        """
        with self.lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
                if outer:
                    self.conn.execute("COMMIT")
            except BaseException:
                self._depth -= 1
                if outer:
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    undo, self._undo = self._undo, []
                    for drop in undo:
                        drop()
                raise
            self._depth -= 1
            if outer:
                self._undo = []

    def on_rollback(self, drop):
        """Have `drop()` run if the transaction under way rolls back (a cache it touched is wrong then)."""
        if self._depth and drop not in self._undo:
            self._undo.append(drop)

    def rows(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def row(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def look(self, force=False):
        """
        Whether another process has written since we last looked — at most every
        half second, unless forced. Bumps `epoch` when it has, for caches to see.
        """
        now = time.monotonic()
        if not force and now - self._looked < _LOOK_EVERY:
            return
        with self.lock:
            self._looked = now
            version = self.conn.execute("PRAGMA data_version").fetchone()[0]
            if version != self._version:
                self._version = version
                self.epoch += 1

    # --- spaces ------------------------------------------------------------------

    def space(self, name):
        """The named key-value space (see wayne.store.kv), one object per database so its cache is shared."""
        found = self._spaces.get(name)
        if found is None:
            from .kv import Space
            with self.lock:
                found = self._spaces.setdefault(name, Space(self, name))
        return found

    # --- meta --------------------------------------------------------------------

    def meta(self, key, default=None):
        got = self.row("SELECT value FROM meta WHERE key = ?", (key,))
        return got[0] if got else default

    def set_meta(self, key, value):
        with self.tx() as c:
            c.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, str(value)))

    # --- the file ----------------------------------------------------------------

    def backup(self, target):
        """A consistent copy of the whole database at `target`, taken while it's in use."""
        target = str(target)
        with self.lock:
            out = sqlite3.connect(target)
            try:
                self.conn.backup(out)
            finally:
                out.close()

    def close(self):
        with self.lock:
            for space in self._spaces.values():
                space.forget()
            for extra in self.extras.values():
                close = getattr(extra, "close", None)
                if close:
                    close()
            self.extras.clear()
            try:
                self.conn.close()
            except sqlite3.Error:
                pass


def _guard(path):
    """Tests never open the real database: a forgotten fixture would have written into someone's memories."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        real = (paths.ROOT_DIR / "data").resolve()
        try:
            path.resolve().relative_to(real)
        except ValueError:
            return
        raise RuntimeError(f"a test tried to open the real database ({path}); give it a data directory of its own")


def get():
    """The database for the current DATA_DIR, opened (and created, and imported into) on first use."""
    key = paths.DATA_DIR
    found = _open.get(key)
    if found is not None:
        return found
    with _open_lock:
        found = _open.get(key)
        if found is not None:
            return found
        path = paths.db_file()
        _guard(path)
        found = Database(path)
        _open[key] = found
        if found.created:
            from . import legacy
            try:
                legacy.adopt(found)
            except Exception:
                # Never half a world: the import is one transaction and has rolled back. The JSON files are where
                # they were; the database is set aside so the next start tries again.
                found.close()
                _open.pop(key, None)
                aside = path.with_name(f"wayne.failed-{int(time.time())}.db")
                for suffix in ("", "-wal", "-shm"):
                    try:
                        os.replace(f"{path}{suffix}", f"{aside}{suffix}")
                    except OSError:
                        pass
                _log.exception("importing the JSON files into %s failed; they're untouched, and the empty "
                               "database is kept as %s", path, aside)
                raise
        return found


def close_all():
    """Close every database this process has open — a test's teardown, or the console shutting down."""
    with _open_lock:
        for found in _open.values():
            found.close()
        _open.clear()
