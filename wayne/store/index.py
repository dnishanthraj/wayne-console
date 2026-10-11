"""
A full-text index over what's been said and what's happened, kept in memory.

Three kinds of thing are found by their words: lines of the transcript (what
was said with each contact), events (diary lines, hearsay, cases closed, rogues
caught) and group-chat messages. Texts are in the transcript already: they go
into each contact's conversation as they're answered.

In memory on purpose. A full-text index stores the words it indexes in the
clear, and everything else in the database is sealed when there's a key — so
the index never touches the disk. It's built from the database the first time
it's asked (or at start, in the background: see warm), a few thousand rows in
milliseconds, and kept up to date as things are written.
"""
import json
import re
import sqlite3
import threading

from . import db as database
from . import events, threads, transcript
from . import seal as sealing

# Row ids in the index: the source's own id, with which source in the low bits.
EVENT, SAID, MESSAGE = 0, 1, 2
_KINDS = 4


class Index:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE VIRTUAL TABLE docs USING fts5(text, owner UNINDEXED, at UNINDEXED, "
                          "tokenize = 'porter unicode61 remove_diacritics 2')")
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.epoch = None            # the database's, as of the build: another process writing moves it on
        self._building = False
        self._pending = []           # written while the build read the database: put after it

    @property
    def built(self):
        return self.ready.is_set()

    def close(self):
        with self.lock:
            self.conn.close()

    def _put(self, kind, ref, text, owner, at):
        rowid = int(ref) * _KINDS + kind
        self.conn.execute("DELETE FROM docs WHERE rowid = ?", (rowid,))
        if text:
            self.conn.execute("INSERT INTO docs(rowid, text, owner, at) VALUES (?, ?, ?, ?)", (rowid, text, owner, at))

    def put(self, kind, ref, text, owner="", at=0.0):
        with self.lock:
            if self.ready.is_set():
                self._put(kind, ref, text, owner, at)
            elif self._building:
                self._pending.append((kind, ref, text, owner, at))

    def forget(self, kind, refs):
        """Rows deleted at their source: gone from what's found, too — not left to crowd out the ones still there."""
        for ref in refs:
            self.put(kind, ref, "")

    def build(self, store):
        """
        Everything there is, read once — sealed rows opened here, never written
        back out. The database is read without holding the index (a writer
        holding the database waits on the index to tell it something new), and
        whatever's written meanwhile is put in after.
        """
        with self.lock:
            if self.ready.is_set():
                return
            if self._building:
                waiting = True
            else:
                self._building, waiting = True, False
        if waiting:
            self.ready.wait(10)
            return
        with store.lock:
            self.epoch = store.epoch
            said = store.conn.execute("SELECT id, contact_id, at, body FROM transcript").fetchall()
            happened = store.conn.execute("SELECT id, actor, kind, at, text FROM events").fetchall()
            chats = store.conn.execute("SELECT rid, thread, sender, at, body FROM messages "
                                       "WHERE thread >= 'g:' AND thread < 'g;'").fetchall()
        rows = [(SAID, row_id, sealing.unseal(body), cid, at) for row_id, cid, at, body in said]
        rows += [(EVENT, event_id, _event_words(kind, actor, sealing.unseal(text)), actor, at)
                 for event_id, actor, kind, at, text in happened]
        rows += [(MESSAGE, rid, _message_words(sender, sealing.unseal(body)), thread, at)
                 for rid, thread, sender, at, body in chats]
        with self.lock:
            for row in rows + self._pending:
                self._put(*row)
            self._pending, self._building = [], False
            self.ready.set()

    def search(self, terms, limit=120):
        """
        [(kind, ref, fit)] for rows matching any of `terms`, best first — `fit`
        how well, against the best match (1.0); a row matching only a word or two
        of a long question comes well below one that matches its names.
        """
        if not terms:
            return []
        query = " OR ".join(f'"{t}"*' for t in terms)
        with self.lock:
            try:
                rows = self.conn.execute("SELECT rowid, bm25(docs) FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) "
                                         "LIMIT ?", (query, limit)).fetchall()
            except sqlite3.OperationalError:
                return []
        if not rows:
            return []
        best = rows[0][1] or -1.0          # bm25 is negative: the lower, the better
        return [(rowid % _KINDS, rowid // _KINDS, (score / best) if best else 0.0) for rowid, score in rows]


def _event_words(kind, actor, text):
    """What an event is found by: its line — and who passed it on, for hearsay ("what did Dick tell you?")."""
    if not text:
        return ""
    return f"{text} ({actor})" if kind in ("hearsay",) or kind.startswith("rogue.") else text


def _message_words(sender, body):
    """A group message as it's found: who wrote it, and what (as the characters read it)."""
    if not body:
        return ""
    if isinstance(body, dict):
        message = body
    else:
        try:
            message = json.loads(body)
        except ValueError:
            return ""
    words = message.get("said") or message.get("text") or ""
    from ..contacts import directory
    who = directory().get(sender)
    return f"{who.name if who else sender}: {words}" if words else ""


def of(store=None):
    """The index for this database, built on first use."""
    store = store or database.get()
    found = store.extras.get("index")
    store.look()
    if found is not None and found.built and found.epoch != store.epoch:
        # Another process wrote (a script, a second console): what it wrote never came through the hooks.
        found.close()
        found = None
        store.extras.pop("index", None)
    if found is None:
        found = store.extras.setdefault("index", Index())
    if not found.built:
        found.build(store)
    return found


def warm():
    """Build the index now — at start, off the loop — so the first question that needs it doesn't wait."""
    of()


def stale(store=None):
    """
    After a sweep that deleted more than it's worth naming row by row (the daily upkeep): built again from what's
    left, the next time it's asked.
    """
    store = store or database.get()
    found = store.extras.pop("index", None)
    if found is not None:
        found.close()


# --- keeping up ------------------------------------------------------------------------

def _on_event(store, event):
    found = store.extras.get("index")
    if found is not None:
        found.put(EVENT, event["id"], _event_words(event["kind"], event["actor"], event["text"]), event["actor"],
                  event["at"])


def _on_said(store, row_id, contact_id, role, at, via, text):
    found = store.extras.get("index")
    if found is not None:
        found.put(SAID, row_id, text, contact_id, at or 0.0)


def _on_message(store, rid, thread, message):
    found = store.extras.get("index")
    if found is not None and thread.startswith("g:"):
        found.put(MESSAGE, rid, _message_words(message.get("from", ""), message), thread, message.get("at", 0.0))


def _on_events_dropped(store, ids):
    found = store.extras.get("index")
    if found is not None:
        found.forget(EVENT, ids)


def _on_messages_dropped(store, rids, thread):
    found = store.extras.get("index")
    if found is not None and thread.startswith("g:"):
        found.forget(MESSAGE, rids)


events.on_add(_on_event)
events.on_drop(_on_events_dropped)
transcript.on_change(_on_said)
threads.on_add(_on_message)
threads.on_drop(_on_messages_dropped)


# --- what a question is looking for -----------------------------------------------------

_STOP = {"the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been", "i", "im", "i'm", "my", "me",
         "you", "your", "he", "she", "it", "we", "they", "to", "of", "in", "on", "at", "for", "with", "that", "this",
         "have", "has", "do", "does", "did", "what", "who", "when", "where", "how", "why", "about", "remember",
         "recall", "know", "tell", "told", "said", "say", "last", "night", "yesterday", "week", "day", "ago",
         "time", "just", "still", "again", "earlier", "today", "tonight", "morning", "afternoon", "evening",
         "think", "thing", "things", "something", "anything", "really", "would", "could", "should", "there",
         "then", "them", "their", "his", "her", "him", "our", "us", "can", "will", "get", "got", "like", "yeah",
         "okay", "ok", "well", "any", "all", "out", "up", "not", "no", "yes", "so", "if", "from", "into", "over",
         "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "other", "few", "couple",
         "hey", "while", "back", "way", "how'd", "what's", "that's", "it's", "you're", "i've", "we're", "don't",
         "didn't", "end", "ended", "go", "went", "going", "come", "came", "mean", "talk", "talked", "mentioned",
         "happen", "happened", "ever", "never", "since", "before", "after", "long", "much", "many", "lot",
         "good", "bad", "right", "sure", "maybe", "guess", "kind", "sort", "bit", "little", "one", "two"}


def terms(text):
    """The words worth looking for — the names and the nouns, not the asking."""
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", (text or "").lower())
    return list(dict.fromkeys(w.strip("'-") for w in words if len(w) > 2 and w not in _STOP))[:12]
