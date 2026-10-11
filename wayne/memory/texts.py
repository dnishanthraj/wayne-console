"""
The text thread with one contact, kept for the long run.

Texts are also written into the contact's conversation history, so they know
what was texted when you next call — but that history is short by design and
calls push old turns out of it. The thread is the thing you scroll back
through, so it keeps its own record: every message, when it was sent, and when
yours was read. Kept in the database a row a message (see wayne.store.threads),
sealed like the rest of a contact's memory.
"""
import re
import threading
import time
import uuid

from ..store.threads import Thread

MAX_MESSAGES = 2000
_lock = threading.Lock()


def quote_of(found):
    """A message as an answer to it keeps it: who, and the start of what they said. None for none, or a line that isn't a message."""
    if not found or found.get("kind"):
        return None
    text = found["text"]
    return {"id": found["id"], "from": found["from"], "text": text if len(text) <= 120 else text[:117] + "…"}


def quote(messages, message_id):
    """The message an answer is to, out of `messages`, quoted (see quote_of)."""
    if not message_id:
        return None
    return quote_of(next((m for m in messages if m.get("id") == message_id and not m.get("kind")), None))


def quoted_by(messages, words, fallback=False):
    """
    Which of `messages` a reply means by a few of its words ("the car thing")
    — the best fit, the latest of equals; with no fit, the latest if
    `fallback`, else None.
    """
    if not messages:
        return None
    wanted = set(re.findall(r"\w+", (words or "").lower()))

    def fit(m):
        return len(wanted & set(re.findall(r"\w+", m["text"].lower()))) / len(wanted) if wanted else 0

    best = max(reversed(messages), key=fit)
    if fit(best) >= 0.5:
        return best
    return messages[-1] if fallback else None


class TextLog:
    """One contact's thread, a row a message (see wayne.store.threads)."""

    def __init__(self, contact_id):
        self.contact_id = contact_id
        self.thread = Thread(f"dm:{contact_id}")

    def add(self, sender, text, at=None, kind=None, origin=None, reply_to=None, card=None):
        """
        Append a message ('me' or 'them'). Returns it. `kind` marks something
        that isn't a message — "missed_call", "declined_call" — shown in the
        thread as a line of its own. `reply_to` is the id of the message this
        one answers: it's kept with a short quote of it, as a phone shows.
        """
        message = {"id": uuid.uuid4().hex[:12], "from": sender, "text": text,
                   "at": at or time.time()}
        if kind:
            message["kind"] = kind
        if origin:
            message["origin"] = origin      # sent unprompted: "chase", "callback", ...
        if card:
            message["card"] = card          # something they showed him (see engine.show): a place, a file, a day
        with _lock:
            quoted = quote_of(self.thread.get(reply_to)) if reply_to else None
            if quoted:
                message["reply_to"] = quoted
            self.thread.add(message, cap=MAX_MESSAGES)
        return message

    def mark_seen(self, at=None):
        """He's opened the thread: their messages are read — and they can tell."""
        return self.thread.mark("seen_at", at or time.time(),
                                "sender = 'them' AND seen_at IS NULL AND kind IS NULL")

    def mark_read(self, ids, at=None):
        at = at or time.time()
        ids = list(ids)
        if ids:
            self.thread.mark("read_at", at, f"id IN ({','.join('?' * len(ids))}) AND read_at IS NULL", ids)
        return at

    def page(self, before=None, limit=40):
        """The newest `limit` messages older than `before`, oldest first."""
        return self.thread.tail(limit, before)

    def unread(self):
        """His messages they haven't read yet — waiting, across a restart."""
        return self.thread.where("sender = 'me' AND read_at IS NULL AND kind IS NULL AND ignored = 0")

    def ignore(self, ids):
        """Never opened, on purpose: left at Delivered for good."""
        ids = list(ids)
        if ids:
            self.thread.mark("ignored", 1, f"id IN ({','.join('?' * len(ids))})", ids)

    def react(self, message_id, who, emoji):
        """A tapback on a message ('me' or 'them'), or with no emoji, taken back. Returns it."""
        def tap(m):
            reactions = m.setdefault("reactions", {})
            if emoji:
                reactions[who] = emoji
            else:
                reactions.pop(who, None)
        return self.thread.change(message_id, tap)

    def edit(self, message_id, text, who):
        """
        A message rewritten by whoever sent it ('me' or 'them'): the words now, when, and what it said first
        (`was`, kept the first time). Returns it — or None if it isn't theirs to change, or isn't a message.
        """
        text = (text or "").strip()
        found = self.thread.get(message_id)
        if not text or not found or found.get("kind") or found.get("from") != who:
            return None
        if found["text"] == text:
            return found

        def rewrite(m):
            m.setdefault("was", m["text"])
            m["text"] = text
            m["edited_at"] = time.time()
        return self.thread.change(message_id, rewrite)

    def last(self):
        """The last real message in the thread, or None."""
        return self.thread.last(real=True)

    def clear(self):
        self.thread.clear()
