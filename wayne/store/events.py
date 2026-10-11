"""
What happened, as the console saw it: an event a line, with what it's about
and who knows it.

  kind      diary.<what> (a line of someone's day — see wayne.engine.diary),
            hearsay (news that travelled — wayne.engine.grapevine),
            case.closed, plan.made, rogue.captured, ...
  actor     whose it is: the contact whose diary it is, who passed it on, the
            rogue caught; '' for the city's.
  links     what it's about — ("contact", "robin"), ("case", id),
            ("place", "Ralli's"), ("rogue", "The Joker") — so "everything
            about the Joker" or "what Dick and Tim did together" is a join.
  audience  who knows it, and from when: hearsay reaches Barbara a day after
            Dick heard it, and not a minute before.
  scope     beyond the audience: 'watchers' — anyone who watches the screens
            (Alfred, Barbara) — or 'public', for what the whole city knows.
  secret    about the masks: never shown to anyone outside the secret, whatever
            the scope says.

Written where it happens; read back by the diary, the grapevine and recall.
"""
import json
import time

from . import db as database
from . import seal as sealing

PRIVATE, WATCHERS, PUBLIC = "private", "watchers", "public"

_hooks = []              # called with each event added — the search index keeps up this way
_drop_hooks = []         # called with the ids of events deleted, so it forgets them too


def on_add(hook):
    if hook not in _hooks:
        _hooks.append(hook)


def on_drop(hook):
    """`hook(db, ids)` after events are deleted."""
    if hook not in _drop_hooks:
        _drop_hooks.append(hook)


def _dropped(store, ids):
    for hook in list(_drop_hooks) if ids else ():
        try:
            hook(store, ids)
        except Exception:
            pass


def add(kind, text, at=None, actor="", data=None, links=(), audience=(), scope=PRIVATE, secret=False):
    """
    Keep an event; returns its id. `audience` is contact ids (who knew it as it
    happened) or (contact id, known from, how) for those who learn it later.
    """
    text = " ".join((text or "").split())
    if not text:
        return None
    at = time.time() if at is None else at
    store = database.get()
    with store.tx() as c:
        cur = c.execute("INSERT INTO events(at, kind, actor, text, data, scope, secret) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (at, kind, actor or "", sealing.seal(text), sealing.seal(sealing.dumps(data)) if data else None,
                         scope, 1 if secret else 0))
        event_id = cur.lastrowid
        if links:
            c.executemany("INSERT OR IGNORE INTO event_links(event_id, type, ref) VALUES (?, ?, ?)",
                          [(event_id, kind_, str(ref)) for kind_, ref in links if ref])
        rows = []
        for who in audience:
            if isinstance(who, (tuple, list)):
                cid, known, how = (list(who) + [None, ""])[:3]
                rows.append((cid, event_id, at if known is None else known, how or ""))
            elif who:
                rows.append((who, event_id, at, ""))
        if rows:
            c.executemany("INSERT OR REPLACE INTO event_audience(contact_id, event_id, known_at, how) VALUES (?, ?, ?, ?)",
                          rows)
    event = {"id": event_id, "at": at, "kind": kind, "actor": actor or "", "text": text, "scope": scope,
             "secret": bool(secret), "data": data or {}, "links": [tuple(link) for link in links if link[1]]}
    for hook in list(_hooks):
        try:
            hook(store, event)
        except Exception:
            pass
    return event_id


def _event(row, conn):
    event_id, at, kind, actor, text, data, scope, secret = row[:8]
    words = sealing.unseal(text, ("events", event_id), conn)
    if words is None:
        return None
    extra = sealing.unseal(data, ("events", f"{event_id}/data"), conn) if data is not None else None
    out = {"id": event_id, "at": at, "kind": kind, "actor": actor, "text": words, "scope": scope,
           "secret": bool(secret), "data": json.loads(extra) if extra else {}}
    if len(row) > 8:
        out["known_at"], out["how"] = row[8], row[9]
    return out


_COLUMNS = "e.id, e.at, e.kind, e.actor, e.text, e.data, e.scope, e.secret"


def query(where="1", params=(), order="e.at, e.id", limit=None, join=""):
    """Events matching a condition (columns of `e`, and of whatever `join` brings in), decoded."""
    store = database.get()
    sql = f"SELECT {_COLUMNS} FROM events e {join} WHERE {where} ORDER BY {order}" + (f" LIMIT {int(limit)}" if limit else "")
    with store.lock:
        rows = store.conn.execute(sql, params).fetchall()
        return [e for e in (_event(r, store.conn) for r in rows) if e is not None]


def by_ids(ids):
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    found = query(f"e.id IN ({','.join('?' * len(ids))})", ids)
    return {e["id"]: e for e in found}


def of(actor, prefix, since=0.0, until=None, limit=None):
    """An actor's events of kinds starting with `prefix` ('diary.'), oldest first — the newest `limit`, if given."""
    where = "e.actor = ? AND e.kind >= ? AND e.kind < ? AND e.at >= ?" + (" AND e.at <= ?" if until is not None else "")
    params = (actor, prefix, prefix + "￿", since) + ((until,) if until is not None else ())
    if limit:
        return query(where, params, order="e.at DESC, e.id DESC", limit=limit)[::-1]
    return query(where, params)


def known_to(contact_id, kind, limit=None):
    """Events of `kind` with this contact in their audience, newest added first, with when and how they knew."""
    store = database.get()
    sql = (f"SELECT {_COLUMNS}, a.known_at, a.how FROM event_audience a JOIN events e ON e.id = a.event_id "
           "WHERE a.contact_id = ? AND e.kind = ? ORDER BY e.id DESC" + (f" LIMIT {int(limit)}" if limit else ""))
    with store.lock:
        rows = store.conn.execute(sql, (contact_id, kind)).fetchall()
        return [e for e in (_event(r, store.conn) for r in rows) if e is not None]


def links(event_ids):
    """{event id: [(type, ref)]} for these events."""
    ids = [int(i) for i in event_ids]
    if not ids:
        return {}
    out = {i: [] for i in ids}
    for event_id, kind, ref in database.get().rows(
            f"SELECT event_id, type, ref FROM event_links WHERE event_id IN ({','.join('?' * len(ids))})", ids):
        out[event_id].append((kind, ref))
    return out


def audience(event_ids):
    """{event id: {contact id: known_at}}."""
    ids = [int(i) for i in event_ids]
    if not ids:
        return {}
    out = {i: {} for i in ids}
    for event_id, cid, known in database.get().rows(
            f"SELECT event_id, contact_id, known_at FROM event_audience WHERE event_id IN ({','.join('?' * len(ids))})",
            ids):
        out[event_id][cid] = known
    return out


def forget_audience(contact_id, kind):
    """They no longer know any of these (a reset of what they've heard); events nobody knows any more go too."""
    store = database.get()
    with store.tx() as c:
        c.execute("DELETE FROM event_audience WHERE contact_id = ? AND event_id IN (SELECT id FROM events WHERE kind = ?)",
                  (contact_id, kind))
        unknown = "kind = ? AND scope = ? AND id NOT IN (SELECT event_id FROM event_audience)"
        gone = [r[0] for r in c.execute(f"SELECT id FROM events WHERE {unknown}", (kind, PRIVATE))]
        c.execute(f"DELETE FROM events WHERE {unknown}", (kind, PRIVATE))
    _dropped(store, gone)


def drop(where, params=()):
    """Delete events matching a condition on `events` (links and audience go with them). Returns how many."""
    store = database.get()
    with store.tx() as c:
        gone = [r[0] for r in c.execute(f"SELECT id FROM events WHERE {where}", params)]
        c.execute(f"DELETE FROM events WHERE {where}", params)
    _dropped(store, gone)
    return len(gone)
