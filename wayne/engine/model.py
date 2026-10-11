"""
The model, asked something outside a conversation turn: the background passes
— afterthoughts, day plans, status lines, dispatches, what they follow, what
travels on the grapevine.

One way of asking, so they all keep the model loaded as long as a turn would
(`MODEL_KEEP_ALIVE`) — each used to reset it to Ollama's few-minute default,
and the next call paid to load fifteen gigabytes again — and a failure is
logged rather than passing for a contact who chose to say nothing.

And one way of giving way. The model answers one request at a time and keeps
one conversation's place in memory: a day plan written while he's on a call
made the call's next turn wait for it, then read its whole prompt again —
first words after five, eight, seventeen seconds instead of one. So while a
call is on (`on_air`), a background pass waits for it to end, and one already
writing when the call starts stops and starts again after. Passes he asked for
himself (`background=False`: a plan read from what he just said, an incident
log he opened) never wait.
"""
import logging
import threading
import time

import ollama

from .. import config

log = logging.getLogger("wayne")

_on_air = threading.Event()
# However long a call runs, a pass that's waited this long goes ahead.
MOST_WAIT = 45 * 60


def on_air(live):
    """A call is on (or isn't): the model is the call's until it ends."""
    if live:
        _on_air.set()
    else:
        _on_air.clear()


def is_on_air():
    return _on_air.is_set()


def _wait_off_air(purpose):
    if not _on_air.is_set():
        return
    log.info("%s waits for the call to end", purpose)
    began = time.monotonic()
    while _on_air.is_set() and time.monotonic() - began < MOST_WAIT:
        time.sleep(1.0)


def ask(model, messages, options=None, think=False, fmt=None, purpose="a background pass", background=True):
    """
    The reply's text. Raises on failure (callers fall back as they always
    have), having logged it. `think` follows the profile when it declares one;
    the background passes otherwise ask without it, as they always did. A
    background pass gives way to a call (see the top of this module).
    """
    extra = {"keep_alive": config.MODEL_KEEP_ALIVE}
    if think is not None:
        extra["think"] = think
    if fmt:
        extra["format"] = fmt
    try:
        if not background:
            return ollama.chat(model=model, messages=messages, options=options or {}, **extra)["message"]["content"]
        while True:
            _wait_off_air(purpose)
            reply = _written_unless_called(model, messages, options or {}, extra)
            if reply is not None:
                return reply
            log.info("%s gave way to a call", purpose)
    except Exception as exc:
        log.warning("%s failed (%s): %s", purpose, model, str(exc)[:160])
        raise


def _written_unless_called(model, messages, options, extra):
    """The whole reply — or None if a call started while it was being written (the stream is dropped, and the
    model with it, so the call doesn't wait behind the rest)."""
    response = ollama.chat(model=model, messages=messages, options=options, stream=True, **extra)
    if isinstance(response, dict):          # a fake that answers whole
        return response["message"]["content"]
    parts = []
    try:
        for part in response:
            if _on_air.is_set():
                return None
            parts.append(part.get("message", {}).get("content", "") or "")
    finally:
        close = getattr(response, "close", None)
        if close:
            close()
    return "".join(parts)


def thinking(contact):
    """What to send as `think` for this contact's background passes."""
    return contact.think if getattr(contact, "think", None) is not None else False
