"""
Message threads: a contact's texts ('dm:<id>') and each group chat ('g:<id>'),
a row per message.

What's asked of a message most — whose it is, when, and whether it's been
seen, read or left — lives in columns of its own; the rest (the words, a quote
of what it answers, tapbacks) is sealed. So the last message, a page of the
thread, or what he hasn't read is an indexed query, and marking a thread seen
is one UPDATE, where before each meant reading, decrypting and rewriting every
message the thread had ever kept.

Messages come back as the same dicts they always were: the read marks are put
back in when set, and nothing else is added.
"""
import json

from . import db as database
from . import seal as sealing

_MARKS = ("seen_at", "read_at", "ignored")
_hooks = []              # told of every message added: the search index keeps up this way


def on_add(hook):
    """`hook(db, rid, thread, message)` after each message is kept."""
    if hook not in _hooks:
        _hooks.append(hook)
_COLUMNS = "rid, id, sender, at, kind, seen_at, read_at, ignored, body"


def _message(row, conn):
    rid, mid, _sender, _at, _kind, seen_at, read_at, ignored, body = row
    text = sealing.unseal(body, ("messages", rid), conn)
    if text is None:
        return None
    message = json.loads(text)
    if seen_at:
        message["seen_at"] = seen_at
    if read_at:
        message["read_at"] = read_at
    if ignored:
        message["ignored"] = True
    return message


class Thread:
    def __init__(self, thread):
        self.thread = thread

    @property
    def db(self):
        return database.get()

    def _fetch(self, where="", params=(), order="rid", limit=None, newest=False):
        sql = f"SELECT {_COLUMNS} FROM messages WHERE thread = ?" + (f" AND ({where})" if where else "")
        sql += f" ORDER BY {order} {'DESC' if newest else 'ASC'}" + (f" LIMIT {int(limit)}" if limit else "")
        store = self.db
        with store.lock:
            rows = store.conn.execute(sql, (self.thread, *params)).fetchall()
            found = [m for m in (_message(r, store.conn) for r in rows) if m is not None]
        return found[::-1] if newest else found

    # --- reading -----------------------------------------------------------------

    def all(self):
        return self._fetch()

    def tail(self, limit, before=None):
        """The newest `limit` messages (older than `before`, if given), oldest first."""
        return self._fetch("at < ?" if before else "", (before,) if before else (), limit=limit, newest=True)

    def get(self, message_id):
        found = self._fetch("id = ?", (message_id,), limit=1)
        return found[0] if found else None

    def last(self, real=False):
        """The newest message — or, `real`, the newest that's a message and not a missed call or a system line."""
        found = self._fetch("kind IS NULL" if real else "", limit=1, newest=True)
        return found[0] if found else None

    def where(self, clause, params=(), limit=None, newest=False):
        """Messages matching a condition on the plain columns (sender, at, kind, seen_at, read_at, ignored)."""
        return self._fetch(clause, params, limit=limit, newest=newest)

    def count(self):
        return self.db.row("SELECT COUNT(*) FROM messages WHERE thread = ?", (self.thread,))[0]

    # --- writing -----------------------------------------------------------------

    def add(self, message, cap=None):
        """Append a message dict (it has an id, from, text and at). Only the newest `cap` are kept."""
        body = {k: v for k, v in message.items() if k not in _MARKS}
        store = self.db
        with store.tx() as conn:
            cur = conn.execute(
                "INSERT INTO messages(thread, id, sender, at, kind, seen_at, read_at, ignored, body) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.thread, message["id"], message.get("from", ""), message["at"], message.get("kind") or None,
                 message.get("seen_at") or None, message.get("read_at") or None, 1 if message.get("ignored") else 0,
                 sealing.seal(sealing.dumps(body))))
            if cap:
                conn.execute("DELETE FROM messages WHERE thread = ? AND rid <= (SELECT rid FROM messages WHERE thread = ? "
                             "ORDER BY rid DESC LIMIT 1 OFFSET ?)", (self.thread, self.thread, cap))
            rid = cur.lastrowid
        for hook in list(_hooks):
            try:
                hook(store, rid, self.thread, message)
            except Exception:
                pass
        return rid

    def mark(self, column, value, clause, params=()):
        """Set a read mark (seen_at, read_at or ignored) on the messages matching `clause`. Returns how many."""
        if column not in _MARKS:
            raise ValueError(column)
        with self.db.tx() as conn:
            return conn.execute(f"UPDATE messages SET {column} = ? WHERE thread = ? AND ({clause})",
                                (value, self.thread, *params)).rowcount

    def change(self, message_id, change):
        """Change one message's words or tapbacks: `change(message)` edits it in place. Returns it, or None."""
        store = self.db
        with store.tx() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM messages WHERE thread = ? AND id = ?",
                               (self.thread, message_id)).fetchone()
            message = _message(row, conn) if row else None
            if message is None:
                return None
            change(message)
            body = {k: v for k, v in message.items() if k not in _MARKS}
            conn.execute("UPDATE messages SET body = ? WHERE rid = ?", (sealing.seal(sealing.dumps(body)), row[0]))
            return message

    def clear(self):
        with self.db.tx() as conn:
            conn.execute("DELETE FROM messages WHERE thread = ?", (self.thread,))


def _range(prefix):
    """Threads starting with `prefix`, as a range the index can use (LIKE is case-blind, and can't)."""
    return prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)


def ids(prefix):
    """The threads there are whose names start with `prefix` ('g:' for every group's)."""
    return [r[0] for r in database.get().rows("SELECT DISTINCT thread FROM messages WHERE thread >= ? AND thread < ?",
                                              _range(prefix))]


def lasts(prefix):
    """{thread: its newest message} for every thread starting with `prefix`, in one query."""
    store = database.get()
    with store.lock:
        rows = store.conn.execute(
            f"SELECT m.thread, {', '.join('m.' + c for c in _COLUMNS.split(', '))} FROM messages m "
            "JOIN (SELECT thread, MAX(rid) AS rid FROM messages WHERE thread >= ? AND thread < ? GROUP BY thread) last "
            "ON last.rid = m.rid", _range(prefix)).fetchall()
        return {r[0]: _message(r[1:], store.conn) for r in rows}
