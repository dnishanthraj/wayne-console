"""
Group chats: one log per group, the single record of what was said in it.

Nobody's own memory holds a copy. What a contact knows of a group is what they
have read of it — a read pointer per member — so Dick on a call can tell Bruce
what Barbara and Tim have been on about, but only as far as he's actually got
through the thread, and two contacts can never remember the same chat
differently. The settings are an entry in the database's "groups" space and the
messages a thread of their own (see wayne.store), sealed like the rest of memory.
"""
import threading
import time
import uuid

from .. import store
from ..store import threads
from .texts import quote_of

MAX_MESSAGES = 3000
_lock = threading.RLock()


def _space():
    return store.space("groups")


class Group:
    """One group chat: who's in it, what was said, and how far each member has read."""

    def __init__(self, group_id):
        self.id = group_id
        self.thread = threads.Thread(f"g:{group_id}")

    # --- storage -------------------------------------------------------------

    def exists(self):
        return self.id in _space()

    def meta(self):
        return _space().get(self.id, {})

    def _save_meta(self, meta):
        _space().put(self.id, meta)

    def update_meta(self, change):
        """
        Read, change and write the group's settings as one step, under the
        store's lock — and not at all once the group's gone. Done piecemeal
        outside the lock, a member's read pointer written meanwhile was lost.
        """
        with _lock:
            if not self.exists():
                return None
            meta = self.meta()
            change(meta)
            self._save_meta(meta)
            return meta

    def messages(self):
        """The whole thread, oldest first. Most callers want only the end of it: see recent()."""
        return self.thread.all()

    def recent(self, limit):
        """The last `limit` messages, oldest first."""
        return self.thread.tail(limit)

    # --- reading and writing ------------------------------------------------------

    @property
    def name(self):
        return self.meta().get("name", "Group")

    @property
    def members(self):
        """Contact ids in the group — Bruce is always in it, and isn't listed."""
        return list(self.meta().get("members", []))

    def add(self, sender, text, at=None, origin=None, reply_to=None):
        """
        Post a message from 'me' (Bruce) or a contact id. Returns it — or None if
        the group's gone (deleted while they were typing: nothing is written,
        or a nameless "Group" rose from the log).
        """
        message = {"id": uuid.uuid4().hex[:12], "from": sender, "text": text, "at": at or time.time()}
        if origin:
            message["origin"] = origin
        with _lock:
            if not self.exists():
                return None
            quoted = quote_of(self.thread.get(reply_to)) if reply_to else None
            if quoted:
                message["reply_to"] = quoted
            # You've read what you wrote — and what came before it, if you'd read
            # that: a question that landed while you typed is still unread.
            caught_up = sender != "me" and not self.thread.where("at > ? AND sender != ?",
                                                                 (self.read_upto(sender), sender), limit=1)
            self.thread.add(message, cap=MAX_MESSAGES)
            if caught_up:
                self._mark(sender, message["at"])
        return message

    def _mark(self, member, at):
        if not self.exists():
            return
        meta = self.meta()
        reads = meta.setdefault("reads", {})
        if at > reads.get(member, 0):
            reads[member] = at
            self._save_meta(meta)

    def mark_read(self, member, at=None):
        """They've read up to now (or `at`). Returns the time."""
        at = at or time.time()
        with _lock:
            self._mark(member, at)
        return at

    def read_upto(self, member):
        return self.meta().get("reads", {}).get(member, 0)

    def unread_for(self, member):
        """Messages they haven't read, not counting their own."""
        upto = self.read_upto(member)
        # System lines count — "Bruce removed Tim" is something to react to.
        return self.thread.where("at > ? AND sender != ?", (upto, member))

    def seen_by(self, member, limit=12):
        """
        The tail of the thread as far as they've read it — what they can know —
        and everything they wrote themselves, read or not: Dick posted with
        others' messages still unread, and then couldn't recall his own text.
        """
        upto = self.read_upto(member)
        # Nothing from before they were let in: Selina added with no backlog still "knew" everything the
        # family had said about patrol, because reading up to the moment she joined covered all of it.
        since = (self.meta().get("joined") or {}).get(member, 0)
        return self.thread.where("(at >= ? AND at <= ?) OR sender = ?", (since, upto, member), limit=limit, newest=True)

    def page(self, before=None, limit=40):
        return self.thread.tail(limit, before)

    def system(self, text, at=None, said=None):
        """
        A line about the group, not in it: 'Randy left', 'Dick added Barbara'.
        `said` is how the characters read it when it differs from what Bruce
        sees — "You added Barbara" to him is "Bruce added Barbara" to them.
        """
        message = {"id": uuid.uuid4().hex[:12], "from": "system", "kind": "system", "text": text,
                   "at": at or time.time()}
        if said:
            message["said"] = said
        with _lock:
            if self.exists():
                self.thread.add(message, cap=MAX_MESSAGES)
        return message

    def react(self, message_id, member, emoji):
        """A tapback on a message — or, with no emoji, taking one back. Returns the message."""
        def tap(message):
            reactions = message.setdefault("reactions", {})
            if emoji:
                reactions[member] = emoji
            else:
                reactions.pop(member, None)
        with _lock:
            return self.thread.change(message_id, tap)

    def add_member(self, contact_id, at=None, backlog=6):
        """
        In they come, with the last few messages to catch up on (`backlog`) —
        not the whole history, which went to the model whole, and told someone
        outside the secret everything the family had said before they arrived.
        """
        with _lock:
            meta = self.meta()
            if contact_id in meta.get("members", []):
                return False
            meta.setdefault("members", []).append(contact_id)
            said = self.thread.where("kind IS NOT 'system'", limit=backlog + 1, newest=True)
            seen_to = said[-backlog - 1]["at"] if backlog and len(said) > backlog else (0 if backlog else time.time())
            meta.setdefault("reads", {})[contact_id] = seen_to
            # What they're let see of the past: the backlog they're given, or nothing before now.
            meta.setdefault("joined", {})[contact_id] = (said[-backlog]["at"] if backlog and len(said) >= backlog
                                                         else 0 if backlog else time.time())
            self._save_meta(meta)
        return True

    def remove_member(self, contact_id, by_bruce=False):
        with _lock:
            meta = self.meta()
            if contact_id not in meta.get("members", []):
                return False
            meta["members"].remove(contact_id)
            if by_bruce:
                # Kept out: nobody else gets to add them straight back.
                meta.setdefault("removed", {})[contact_id] = time.time()
            self._save_meta(meta)
        return True

    def rename(self, name):
        with _lock:
            meta = self.meta()
            meta["name"] = (name or meta.get("name", "Group")).strip()[:60]
            self._save_meta(meta)

    def readers_of(self, message):
        """Which members have read a given message."""
        reads = self.meta().get("reads", {})
        return [m for m in self.members if reads.get(m, 0) >= message["at"] and m != message["from"]]

    def summary(self, meta=None, last=False):
        meta = self.meta() if meta is None else meta
        last = self.thread.last() if last is False else last
        return {"id": self.id, "name": meta.get("name", "Group"), "members": meta.get("members", []),
                "created_at": meta.get("created_at"), "reads": meta.get("reads", {}),
                "last": last,
                "comms": bool(meta.get("comms")), "archived": bool(meta.get("archived")), "case": meta.get("case", "")}

    def delete(self):
        with _lock:
            self.thread.clear()
            _space().drop(self.id)


def create(name, members):
    """A new group of Bruce and these contacts. Returns it."""
    group = Group(uuid.uuid4().hex[:10])
    with _lock:
        group._save_meta({"name": (name or "Group").strip()[:60], "members": list(dict.fromkeys(members)),
                          "created_at": time.time(), "reads": {}})
    return group


def _ordered(metas):
    """Groups, the latest active first — by their last message, or when they were made — in one query."""
    lasts = threads.lasts("g:")
    when = {gid: ((lasts.get(f"g:{gid}") or {}).get("at") or meta.get("created_at", 0)) for gid, meta in metas.items()}
    return [Group(gid) for gid in sorted(metas, key=lambda gid: when[gid], reverse=True)]


def all_groups():
    return _ordered(_space().dict())


def summaries():
    """Every group's summary, latest active first, without asking each one again."""
    metas, lasts = _space().dict(), threads.lasts("g:")
    return [g.summary(metas[g.id], lasts.get(f"g:{g.id}")) for g in _ordered(metas)]


def of(group_id):
    group = Group(group_id)
    return group if group.exists() else None


def groups_with(contact_id):
    return _ordered({gid: meta for gid, meta in _space().dict().items() if contact_id in meta.get("members", [])})
