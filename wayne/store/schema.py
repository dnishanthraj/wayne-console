"""
The database's tables, and how an older file is brought up to date.

Four kinds of thing are kept, each the way it's asked for:

  kv          Small documents in named spaces, one row per entry: the case
              board (a row per case), the plans, Bruce's mail, each contact's
              presence (a row per key: their trips apart from their last word),
              the rogues' statuses, the Codex notes. Read whole and kept in
              memory; written a row at a time — a case advancing no longer
              rewrites the board, Tim's last word no longer rewrites his trips.
  messages    Every text and group-chat message, a row each, with what's asked
              of them in columns of their own — whose, when, read or not — so a
              page of the thread or what he hasn't read is one indexed query.
  transcript  Everything said with each contact, past the short window their
              model reads, for remembering what was said last week.
  events      What happened, a line each — a diary entry, news that travelled,
              a case closed, a rogue caught — with what it's about (event_links:
              people, places, cases, rogues) and who knows it, from when
              (event_audience). What lets "remember the Joker job?" be answered
              from the record, and only by people who'd know.

Anything personal is sealed (see wayne.store.seal): encrypted with the memory
key when one's set. The columns kept plain are only what's needed to find rows:
ids, times, kinds and read marks — about what a directory listing showed of the
JSON files before.
"""

VERSION = 1

V1 = """
CREATE TABLE meta(
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE kv(
    space TEXT NOT NULL,
    key   TEXT NOT NULL,
    seq   INTEGER NOT NULL,          -- insertion order within the space
    value BLOB NOT NULL,             -- sealed JSON
    at    REAL NOT NULL,             -- last written
    UNIQUE(space, key)
);
CREATE INDEX kv_order ON kv(space, seq);

CREATE TABLE messages(
    rid     INTEGER PRIMARY KEY,     -- order in the thread
    thread  TEXT NOT NULL,           -- 'dm:<contact>' or 'g:<group>'
    id      TEXT NOT NULL,
    sender  TEXT NOT NULL,           -- 'me', 'them', a contact id, 'system'
    at      REAL NOT NULL,
    kind    TEXT,                    -- missed_call, system, ... — or NULL for a message
    seen_at REAL,
    read_at REAL,
    ignored INTEGER NOT NULL DEFAULT 0,
    body    BLOB NOT NULL,           -- sealed JSON: the rest of the message
    UNIQUE(thread, id)
);
CREATE INDEX messages_thread ON messages(thread, rid);
CREATE INDEX messages_unread ON messages(thread)
    WHERE sender = 'me' AND read_at IS NULL AND kind IS NULL AND ignored = 0;

CREATE TABLE transcript(
    id         INTEGER PRIMARY KEY,
    contact_id TEXT NOT NULL,
    role       TEXT NOT NULL,        -- 'user' (Bruce, or everyone on a group call) or 'assistant'
    at         REAL NOT NULL,
    via        TEXT,                 -- 'text', or NULL for a call
    body       BLOB NOT NULL         -- sealed text
);
CREATE INDEX transcript_contact ON transcript(contact_id, at);

CREATE TABLE events(
    id     INTEGER PRIMARY KEY,
    at     REAL NOT NULL,
    kind   TEXT NOT NULL,            -- diary.arrived, hearsay, case.closed, plan.made, rogue.captured, ...
    actor  TEXT NOT NULL DEFAULT '', -- whose it is: a contact id, a rogue's name, or ''
    text   BLOB NOT NULL,            -- sealed: what happened, in a line
    data   BLOB,                     -- sealed JSON: the rest
    scope  TEXT NOT NULL DEFAULT 'private',   -- private | watchers | public
    secret INTEGER NOT NULL DEFAULT 0          -- the masks' business: never to anyone outside it
);
CREATE INDEX events_actor ON events(actor, kind, at);
CREATE INDEX events_kind ON events(kind, at);

CREATE TABLE event_links(
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    type     TEXT NOT NULL,          -- contact | case | place | rogue | plan | group
    ref      TEXT NOT NULL,
    PRIMARY KEY(event_id, type, ref)
) WITHOUT ROWID;
CREATE INDEX event_links_ref ON event_links(type, ref, event_id);

CREATE TABLE event_audience(
    contact_id TEXT NOT NULL,
    event_id   INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    known_at   REAL NOT NULL,        -- from when they know it: hearsay arrives later
    how        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(contact_id, event_id)
) WITHOUT ROWID;
CREATE INDEX event_audience_event ON event_audience(event_id);

CREATE TABLE quarantine(
    id    INTEGER PRIMARY KEY,
    tbl   TEXT NOT NULL,
    pk    TEXT NOT NULL,
    sha   TEXT NOT NULL,
    blob  BLOB NOT NULL,
    at    REAL NOT NULL,
    UNIQUE(tbl, pk, sha)
);
"""

# Forward only: MIGRATIONS[n] takes a file at version n to n + 1.
MIGRATIONS = {}


def apply(conn):
    """Create the tables on a new file, or bring an older one up to VERSION. True if the file was new."""
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > VERSION:
        raise RuntimeError(f"{conn} is at schema version {have}, newer than this code knows ({VERSION}) — "
                           "a newer console wrote it; update, or restore a backup")
    if have == VERSION:
        return False
    conn.execute("BEGIN IMMEDIATE")
    try:
        if have == 0:
            for statement in _statements(V1):
                conn.execute(statement)
        else:
            for n in range(have, VERSION):
                for statement in _statements(MIGRATIONS[n]):
                    conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return have == 0


def _statements(script):
    """A script split into statements — executescript() would commit halfway through our transaction."""
    import sqlite3
    out, buf = [], ""
    for line in script.splitlines():
        if line.strip().startswith("--") and not buf:
            continue
        buf += line + "\n"
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out
