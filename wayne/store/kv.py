"""
Named spaces of small documents, a row per entry: the case board is the space
"cases" with a row per case; Bruce's state is the entry "bruce" in the space
"doc"; Tim's presence is the space "presence/robin" with a row per key.

A space is read whole — they're small — and kept in memory as the JSON text of
each entry, so reading it costs a parse and nothing else: no file, no decrypt.
Every reader gets its own copy, as it did from a file, to change as it likes.
Writes go a row at a time, and only for entries that actually changed: saving
the whole board after one case moved writes one row.

Entries keep the order they were first written in, as a dict's keys do, so a
space read as a list comes back in the order it was built.
"""
import json
import time

from . import seal as sealing


class Space:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.forget()

    def forget(self):
        """Drop what's cached; the next read loads the space again."""
        self._rows = None           # key -> JSON text, in order
        self._unreadable = set()    # keys whose value wouldn't open: never deleted by replace()
        self._epoch = None
        self._joined = None         # the whole space as one JSON array's text, built when asked for
        self._object = None         # ... and as one JSON object's text
        self._view = None           # ... and parsed once, for readers that only look (see view)
        self._view_dict = None

    # --- reading -----------------------------------------------------------------

    def _ready(self):
        """The cached rows, loaded (again) if they're not there or another process has written since."""
        self.db.look()
        if self._rows is None or self._epoch != self.db.epoch:
            rows, unreadable = {}, set()
            conn = self.db.conn
            for key, blob in conn.execute("SELECT key, value FROM kv WHERE space = ? ORDER BY seq", (self.name,)):
                text = sealing.unseal(blob, ("kv", f"{self.name}/{key}"), conn)
                if text is None:
                    unreadable.add(key)
                else:
                    rows[key] = text
            self._rows, self._unreadable = rows, unreadable
            self._epoch = self.db.epoch
            self._joined = self._object = self._view = self._view_dict = None
        return self._rows

    def get(self, key, default=None):
        with self.db.lock:
            text = self._ready().get(key)
        return json.loads(text) if text is not None else _copy(default)

    def __contains__(self, key):
        with self.db.lock:
            return key in self._ready()

    def __len__(self):
        with self.db.lock:
            return len(self._ready())

    def keys(self):
        with self.db.lock:
            return list(self._ready())

    def list(self):
        """Every entry's value, in order."""
        with self.db.lock:
            rows = self._ready()
            if self._joined is None:
                self._joined = "[" + ",".join(rows.values()) + "]"
            joined = self._joined
        return json.loads(joined)

    def dict(self):
        """The space as {key: value}, in order."""
        with self.db.lock:
            rows = self._ready()
            if self._object is None:
                self._object = "{" + ",".join(f"{sealing.dumps(k)}:{v}" for k, v in rows.items()) + "}"
            whole = self._object
        return json.loads(whole)

    def items(self):
        return list(self.dict().items())

    def view(self):
        """
        Every value, in order, parsed once per change and shared: for the hot
        paths that only look — a routine checking the plans, the scanner reading
        its dispatches, hundreds of times a pulse. Never change what it returns
        (copy what you hand on); everyone else uses list().
        """
        with self.db.lock:
            rows = self._ready()
            if self._view is None:
                self._view = json.loads("[" + ",".join(rows.values()) + "]")
            return self._view

    def view_dict(self):
        """view(), as {key: value}."""
        with self.db.lock:
            rows = self._ready()
            if self._view_dict is None:
                self._view_dict = json.loads("{" + ",".join(f"{sealing.dumps(k)}:{v}" for k, v in rows.items()) + "}")
            return self._view_dict

    # --- writing -----------------------------------------------------------------

    def put(self, key, value):
        """Keep `value` under `key` — a new key goes after the rest. Writes nothing if it's unchanged."""
        self.put_many({key: value})

    def put_many(self, entries):
        with self.db.tx() as conn:
            rows = self._ready()
            for key, value in entries.items():
                key = str(key)
                text = sealing.dumps(value)
                if rows.get(key) == text:
                    continue
                self._stage()
                if key in rows or key in self._unreadable:
                    conn.execute("UPDATE kv SET value = ?, at = ? WHERE space = ? AND key = ?",
                                 (sealing.seal(text), time.time(), self.name, key))
                    self._unreadable.discard(key)
                else:
                    # After every entry there is — counted in the database, so another process's are counted too.
                    conn.execute("INSERT INTO kv(space, key, seq, value, at) VALUES "
                                 "(?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM kv WHERE space = ?), ?, ?)",
                                 (self.name, key, self.name, sealing.seal(text), time.time()))
                rows[key] = text

    def drop(self, *keys):
        with self.db.tx() as conn:
            rows = self._ready()
            gone = [str(k) for k in keys if str(k) in rows or str(k) in self._unreadable]
            if not gone:
                return
            self._stage()
            conn.executemany("DELETE FROM kv WHERE space = ? AND key = ?", [(self.name, k) for k in gone])
            for key in gone:
                rows.pop(key, None)
                self._unreadable.discard(key)

    def clear(self):
        with self.db.tx() as conn:
            self._stage()
            conn.execute("DELETE FROM kv WHERE space = ?", (self.name,))
            self._rows, self._unreadable = {}, set()
            self._epoch = self.db.epoch
            self._joined = self._object = self._view = self._view_dict = None

    def replace(self, entries):
        """
        Make the space hold exactly `entries` ({key: value}): what changed is
        written, what's gone is deleted, the rest isn't touched. (An entry that
        couldn't be read is never deleted this way — it's still someone's.)
        """
        entries = {str(k): v for k, v in entries.items()}
        with self.db.tx():
            rows = self._ready()
            self.drop(*[k for k in rows if k not in entries])
            self.put_many(entries)

    def update(self, key, change, default=None):
        """
        Read, change and write one entry as one step: `change(value)` changes the
        value in place, or returns a new one. Returns what was kept.
        """
        with self.db.tx():
            value = self.get(key, default)
            changed = change(value)
            value = value if changed is None else changed
            self.put(key, value)
            return value

    def trim(self, keep):
        """Only the last `keep` entries, by when they were first written."""
        with self.db.tx():
            rows = self._ready()
            if len(rows) > keep:
                self.drop(*list(rows)[:len(rows) - keep])

    def _stage(self):
        """About to change the cache: the whole-space texts are stale, and all of it is if this rolls back."""
        self.db.on_rollback(self.forget)
        self._joined = self._object = self._view = self._view_dict = None


def _copy(value):
    return json.loads(json.dumps(value)) if isinstance(value, (dict, list)) else value
