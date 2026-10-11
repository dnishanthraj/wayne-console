"""
Everything said with each contact, kept past the window their model reads.

The window (wayne.memory.history) is short on purpose — every word of it is
read again on every turn — so what was said last week fell out of it and was
gone. Here it stays, a row a line, for recall (see wayne.store.recall) to find
when it comes up: "what did I tell you about the Kord deal?".

Kept for TRANSCRIPT_DAYS (WAYNE_TRANSCRIPT_DAYS, 90 by default; 0 keeps none),
sealed like the rest, and wiped with the contact's memory.
"""
from ..config import setting
from . import db as database
from . import seal as sealing

DAYS = float(setting("TRANSCRIPT_DAYS", "90") or 0)

_hooks = []


def on_change(hook):
    """`hook(db, row_id, contact_id, role, at, via, text)` on every line kept or changed; text None when removed."""
    if hook not in _hooks:
        _hooks.append(hook)


def _tell(store, *args):
    for hook in list(_hooks):
        try:
            hook(store, *args)
        except Exception:
            pass


def keep(contact_id, role, at, text, via=None):
    """A new line; returns its id (None when nothing's kept)."""
    if DAYS <= 0 or not (text or "").strip():
        return None
    store = database.get()
    with store.tx() as c:
        row_id = c.execute("INSERT INTO transcript(contact_id, role, at, via, body) VALUES (?, ?, ?, ?, ?)",
                           (contact_id, role, at, via, sealing.seal(text))).lastrowid
    _tell(store, row_id, contact_id, role, at, via, text)
    return row_id


def change(row_id, contact_id, role, at, text, via=None):
    """A line said differently after all (an aside added, a reply amended)."""
    store = database.get()
    with store.tx() as c:
        c.execute("UPDATE transcript SET at = ?, via = ?, body = ? WHERE id = ?", (at, via, sealing.seal(text), row_id))
    _tell(store, row_id, contact_id, role, at, via, text)


def ids_since(contact_id, since):
    """{(role, at): id} for their lines from `since` on — how a window read back finds its lines here."""
    return {(role, at): row_id for row_id, role, at in database.get().rows(
        "SELECT id, role, at FROM transcript WHERE contact_id = ? AND at >= ?", (contact_id, since))}


def lines(contact_id, since=0.0, until=None, limit=None):
    """Their lines, oldest first: [{id, role, at, via, text}]."""
    store = database.get()
    sql = "SELECT id, role, at, via, body FROM transcript WHERE contact_id = ? AND at >= ?"
    params = [contact_id, since]
    if until is not None:
        sql += " AND at <= ?"
        params.append(until)
    sql += " ORDER BY at DESC, id DESC" + (f" LIMIT {int(limit)}" if limit else "")
    with store.lock:
        rows = store.conn.execute(sql, params).fetchall()
        out = []
        for row_id, role, at, via, body in rows:
            text = sealing.unseal(body, ("transcript", row_id), store.conn)
            if text is not None:
                out.append({"id": row_id, "role": role, "at": at, "via": via, "text": text})
    return out[::-1]


def exchange(line):
    """
    The exchange a line was part of: (what Bruce said, what they said back) —
    either None when there wasn't one (a line they let pass, a text they sent first).
    """
    store = database.get()
    cid = line.get("contact_id")
    if line["role"] == "user":
        nxt = store.row("SELECT id FROM transcript WHERE contact_id = ? AND id > ? ORDER BY id LIMIT 1", (cid, line["id"]))
        other = by_ids([nxt[0]]).get(nxt[0]) if nxt else None
        return line, other if other and other["role"] == "assistant" and other["at"] - line["at"] < 900 else None
    prev = store.row("SELECT id FROM transcript WHERE contact_id = ? AND id < ? ORDER BY id DESC LIMIT 1", (cid, line["id"]))
    other = by_ids([prev[0]]).get(prev[0]) if prev else None
    return (other if other and other["role"] == "user" and line["at"] - other["at"] < 900 else None), line


def by_ids(ids):
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    store = database.get()
    with store.lock:
        rows = store.conn.execute(
            f"SELECT id, contact_id, role, at, via, body FROM transcript WHERE id IN ({','.join('?' * len(ids))})",
            ids).fetchall()
        out = {}
        for row_id, cid, role, at, via, body in rows:
            text = sealing.unseal(body, ("transcript", row_id), store.conn)
            if text is not None:
                out[row_id] = {"id": row_id, "contact_id": cid, "role": role, "at": at, "via": via, "text": text}
    return out


def forget(contact_id):
    store = database.get()
    with store.tx() as c:
        gone = [r[0] for r in c.execute("SELECT id FROM transcript WHERE contact_id = ?", (contact_id,))]
        c.execute("DELETE FROM transcript WHERE contact_id = ?", (contact_id,))
    for row_id in gone:
        _tell(store, row_id, contact_id, None, None, None, None)
