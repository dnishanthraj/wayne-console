"""
Local web console.

FastAPI serves a static page and one WebSocket. The page owns presentation and
audio playback; this module owns the sessions and translates engine events onto
the socket. No build step and no node toolchain — the console is plain
HTML/CSS/JS, so the launcher only has to start a Python process.

Audio is *not* played here. The bytes go to the browser, which plays them
through Web Audio so the visualizer can read the real frequency spectrum and
the transcript can be revealed in time with the speech.

Synthesis is pipelined: each sentence is sent to ElevenLabs the moment the
model finishes writing it, several in flight at once, but the resulting clips
are released to the page strictly in order.
"""
import asyncio
import concurrent.futures
import contextlib
import datetime
import logging
import logging.handlers
import math
import random
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import config, events, paths, store
from .. import identity as wayne_identity
from .. import operator as wayne_operator
from ..audio import stt, system_voice
from ..audio.tts import get_voice_engine
from ..contacts import directory
from ..engine import (
    ContactSession,
    bonds,
    codex,
    culture,
    grapevine,
    groupchat,
    guards,
    initiative,
    places,
    presence,
    travel,
    world,
)
from ..engine import model as llm
from ..engine.party import MAX_CONTACTS, ROOM_GUEST, Call
from ..engine.session import ASKS_REACTION
from ..memory import groups as group_store
from ..memory import migrate_legacy
from ..memory.history import describe_gap
from ..memory.texts import TextLog, quoted_by
from ..paths import WEB_DIR
from .groupchats import GroupChats

# Synthesized clips waiting to be fetched. Bounded — a long session would
# otherwise hold every reply's audio in memory for the whole run.
_MAX_CACHED_CLIPS = 64

# What happened, when, and how long it took — for the times a reply goes quiet
# and there is otherwise nothing to look at. data/ is gitignored.
log = logging.getLogger("wayne")
# How often two people on a group call start into the same pause together.
TALK_OVER = 0.12
# How often someone on a call remarks on a line ringing in.
RING_REMARK = 0.25
# How much go a call has left after each line nobody prompted: his voice
# restores it, and it runs down — a few lines, a handful — never forever.
LULL_DECAY = 0.82
MOST_UNPROMPTED = 10
if not log.handlers:
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # A line per sentence adds up over months of evenings: two megabytes, then
    # it rolls over, keeping the last two.
    _handler = logging.handlers.RotatingFileHandler(paths.DATA_DIR / "console.log", maxBytes=2_000_000,
                                                    backupCount=2)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)

# How long a spoken line stays eligible to be recognised as an echo of itself.
# Long enough to cover a reply playing out plus the transcription round trip,
# short enough that legitimately repeating a phrase later still gets through.
ECHO_WINDOW_SECONDS = 25

# How long a call rings before it is answered. Varied, because a constant delay
# is only marginally less mechanical than none at all.
PICKUP_DELAY = (0.9, 3.4)
# Longer when they're in the middle of something, longer still when the phone
# was nowhere near them.
PICKUP_EXTRA = {presence.BUSY: (1.5, 5.0), presence.OFFLINE: (4.0, 9.0)}

# How long an incoming call rings before it counts as missed.
RING_FOR = 28
# A call they won't take: rejected after a ring or two, or left to ring out.
DECLINE_AFTER = (3.0, 8.0)
NO_ANSWER_AFTER = (16.0, 24.0)
# Ringing back within this long reads as urgent (see presence.answers).
RING_AGAIN = 240
# Several texts inside this window is a burst — enough to make someone look.
BURST_WINDOW = 300
# Words that make even someone ghosting him answer.
_URGENT = re.compile(r"(?i)\b(help|emergency|hurt|hospital|bleeding|urgent|now|please call|call me|"
                     r"are you (ok|okay|alive|safe)|answer)\b")

# How often the console's clock ticks: presence, promises, the odd text.
PULSE_SECONDS = 30
# On a case's comms: the quiet lines between events, at most, and how long the
# channel stays up after the sign-off before it's put away (seconds).
COMMS_LINES = 4
COMMS_LINGER = 8 * 60
COMMS_QUIET = 10 * 60          # and nobody's said anything on it for this long: put away
# Nobody texts out of the blue twice within this long, across everyone.
# The least time between unprompted texts from anyone: often enough to feel
# like a life going on around him, not so often it's a feed.
INITIATIVE_GAP = 22 * 60
# How often someone whose phone is out of reach is looked at again.
RECHECK_SECONDS = 15


class Console(GroupChats):
    """
    The console as a whole: a directory of contacts, one live session per
    contact you have spoken to, and the fan-out to connected browser tabs.
    """
    # The ring in progress (to one, or to a group), and who a group ring invites.
    _ring_token = _group_ring = _inviting = call = None

    # Every sentence sent to the page gets a key that is never reused. The
    # engine numbers sentences from 0 within each reply, and the page used
    # those numbers to remember what it had already shown — so after "One
    # moment." took slot 0, the real answer's first sentence (also 0) was
    # treated as already shown and never appeared, and a check-in after an
    # unanswered reply could vanish the same way.
    _said = 0
    PULSE_SECONDS = PULSE_SECONDS

    # Who's on the line. Setting it tells the background passes the model is the call's (see wayne.engine.model):
    # a day plan written mid-call made the call's next turn wait, then read its whole prompt again.
    @property
    def current_id(self):
        return self._current_id

    @current_id.setter
    def current_id(self, value):
        self._current_id = value
        llm.on_air(bool(value))

    def __init__(self):
        self.directory = directory()
        self.voice = get_voice_engine()
        self.clients = set()
        self.audio_clips = OrderedDict()
        self.turn_lock = asyncio.Lock()
        self.sessions = {}
        self.current_id = None
        # What the contact has said lately, for recognising its own voice
        # arriving back through the microphone.
        self.recent_speech = deque(maxlen=12)
        # Bumped whenever something supersedes the turn in flight. A turn that
        # finds its epoch stale stops forwarding events and stops synthesising,
        # which is what makes interrupting him actually interrupt him rather
        # than queue behind whatever he was already saying.
        self.turn_epoch = 0
        # True while a turn is mid-flight, so the next input can tell whether it
        # cut him off or merely followed him.
        self.voice_busy = False
        self._heard_upto = None     # the last sentence he heard begin, when he cut in (see _remember_heard)
        self._plan_backoff = {}     # contact id -> not before: a day plan that failed waits its turn again
        self._last_said = None
        self.boot_task = None
        self._release = None   # pending model release after a hang-up
        self.call = None       # the call in progress (see wayne.engine.party)
        # Texts waiting to be read, and the task answering each contact's.
        self._pending_texts = {}
        self._texters = {}
        # A contact ringing him: {"id", "about"}, and the timer that gives up.
        self._incoming = None
        self._ring_timer = None
        # The status each contact's dot last showed, to send only changes.
        self._shown_presence = {}
        # A call ringing out that won't be answered, and when each contact
        # last let him ring out — so ringing straight back reads as urgent.
        self._ringing_out = None
        self._refused_at = {}
        # When he last rang or texted each contact — for "he keeps calling".
        self._call_attempts = {}
        # How much life a group call has left for talk nobody prompted: each
        # line said into a pause uses some up, his next word restores it.
        self._lull_energy = 1.0
        self._ring_refusals = {}      # who's turned down each group ring so far
        self._lulls = 0
        self._last_lull = None
        self._text_bursts = {}
        # Set from the moment a call is placed until it's up, so nobody rings
        # him in the gap while the line is still being opened.
        self._connecting = False
        self._tasks = set()
        self._adding, self._dropped_adds = set(), set()     # rung in and still ringing; stopped
        self._closing = set()        # cases being wound up, so the tick doesn't start another
        self._writing_lines = False
        self._typing_now = set()
        self._mail_seen = set()       # breakouts already queued for mail
        self._mail_next = 0.0         # when the model may next write him something
        self._invite_next = time.time() + 15 * 60     # when one of them might next ask him along to something
        self._init_groups()
        self.migrated = migrate_legacy(config.DEFAULT_CONTACT)

    # --- contacts ---------------------------------------------------------

    @property
    def contact(self):
        return self.directory.get(self.current_id) if self.current_id else None

    def session_for(self, contact_id):
        if contact_id not in self.sessions:
            contact = self.directory.get(contact_id)
            if contact is None:
                raise KeyError(contact_id)
            self.sessions[contact_id] = ContactSession(contact)
        return self.sessions[contact_id]

    # --- fan-out ----------------------------------------------------------

    def _note_typing(self, event):
        """
        Who is actually typing right now — not who merely has a reply pending.
        A thread opened mid-wait asked "is anyone typing?" and was told yes for
        anyone waiting to read, which for Randy can be hours of dots.
        """
        kind = event.get("type")
        if kind == "text_typing":
            self._typing_now.add(("text", event["speaker"]))
        elif kind in ("text_reply", "text_idle"):
            self._typing_now.discard(("text", event["speaker"]))
        elif kind == "group_typing":
            self._typing_now.add((event["group"], event["speaker"]))
        elif kind == "group_idle":
            self._typing_now.discard((event["group"], event["speaker"]))
        elif kind == "group_message":
            self._typing_now.discard((event["group"], event["message"]["from"]))

    async def broadcast(self, event):
        self._note_typing(event)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    # --- speech pipeline --------------------------------------------------

    def _store_clip(self, audio):
        clip_id = uuid.uuid4().hex
        self.audio_clips[clip_id] = audio
        while len(self.audio_clips) > _MAX_CACHED_CLIPS:
            self.audio_clips.popitem(last=False)
        return clip_id

    def interrupt(self):
        """Supersede whatever is being generated or spoken right now — on the call, even a line written whole."""
        self.turn_epoch += 1
        for member in (self.call.members if getattr(self, "call", None) else []):
            cancel = getattr(member, "cancel", None)
            if cancel:
                cancel()
        return self.turn_epoch

    async def drive(self, generator, contact, epoch=None, release_at=None):
        """
        Consume a blocking engine generator on a worker thread, forwarding its
        events to the socket and synthesizing each sentence as it appears.

        Once the turn is superseded, nothing more is sent to the page or
        synthesised, so a contact talked over goes quiet immediately — and the
        generator is *closed*, not merely abandoned. Abandoning it left the
        worker thread reading the model to the end of a reply nobody would
        hear, and Ollama serves one request at a time, so the next turn queued
        behind every remaining token of the last. Closing it closes the HTTP
        stream, which is what makes Ollama stop.

        `release_at` holds everything back from the page until that moment on
        the event loop's clock, while generation and synthesis carry on behind
        it. That is how a call is answered: the greeting is written and voiced
        while the line rings, and he speaks the instant he picks up instead of
        picking up and then thinking about what to say.
        """
        loop = asyncio.get_running_loop()
        began, first_at = time.monotonic(), None
        voices = set()           # who spoke in this turn: theirs is the prefix worth keeping warm
        # Every event of the turn carries its number, so a page that's cut it off drops the rest of it.
        turn = epoch if epoch is not None else self.turn_epoch
        said_now, started_wall = {}, time.time()        # this turn's sentences, by key: what he heard, if cut off

        async def hold():
            if release_at is not None:
                await asyncio.sleep(max(0.0, release_at - loop.time()))
        queue = asyncio.Queue()
        done = object()

        def superseded():
            return epoch is not None and epoch != self.turn_epoch

        def pump():
            try:
                for event in generator:
                    if superseded():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:  # a worker crash must not kill the socket
                loop.call_soon_threadsafe(
                    queue.put_nowait, events.notice(f"Engine error: {exc}", "error")
                )
            finally:
                generator.close()
                loop.call_soon_threadsafe(queue.put_nowait, done)

        threading.Thread(target=pump, daemon=True).start()

        # Events go to the page through a releaser that waits for `release_at`
        # once; synthesis starts the moment each sentence exists. Holding the
        # main loop for pickup instead held synthesis too — the first event is
        # never a sentence — so every call had a voice round trip of silence
        # after "pickup", the opposite of the point.
        outbox, released, closed = asyncio.Queue(), set(), asyncio.Event()
        released_changed = asyncio.Condition()

        async def releaser():
            try:
                await hold()
                while True:
                    event = await outbox.get()
                    if event is done:
                        return
                    if superseded():
                        continue
                    await self.broadcast({**event, "epoch": turn})
                    if event.get("type") == "sentence":
                        async with released_changed:
                            released.add(event["key"])
                            released_changed.notify_all()
            finally:
                closed.set()
                async with released_changed:
                    released_changed.notify_all()

        async def shown(key):
            """Until the page has the sentence a clip belongs to (or never will)."""
            async with released_changed:
                await released_changed.wait_for(
                    lambda: key in released or closed.is_set() or superseded())
            return key in released and not superseded()

        speech = asyncio.Queue()
        sender = asyncio.create_task(releaser())
        worker = asyncio.create_task(self._speech_worker(speech, shown, superseded, turn))
        self.voice_busy = True

        try:
            while True:
                event = await queue.get()
                if event is done:
                    break
                if superseded():
                    # Wait for the worker to close the generator before letting
                    # the next turn in: it is one token away from noticing, and
                    # two turns writing history at once is worse than a beat.
                    while await queue.get() is not done:
                        pass
                    break
                if event.get("type") == "sentence":
                    self._said += 1
                    event = {**event, "key": self._said}
                    said_now[self._said] = event.get("text", "")
                if event.get("type") == "sentence":
                    voices.add(event.get("speaker") or contact.id)
                if event.get("type") == "sentence" and first_at is None:
                    first_at = time.monotonic()
                    log.info("%s first sentence after %.2fs", contact.id, first_at - began)
                if event.get("type") in ("sentence", "reply_end", "sources") and not event.get("speaker"):
                    # Whoever's line it is, said: on a group call the first to
                    # pick up greeted it unlabelled, and their seat stayed blank.
                    event = {**event, "speaker": contact.id}
                # On a call with company, each sentence in its speaker's voice.
                speaker = self.directory.get(event.get("speaker") or "") or contact
                if event.get("type") == "sentence" and self._can_speak(speaker):
                    spoken = event.get("voice") or event["text"]
                    task = asyncio.create_task(
                        asyncio.to_thread(self._timed_synthesis, spoken, speaker, superseded)
                    )
                    speech.put_nowait((event["key"], event["text"], task, speaker.id))
                if (event.get("type") == "reply_end" and not event.get("interim") and event.get("text")
                        and getattr(self.call, "comms", None)):
                    self._comms_record(speaker.id, event["text"])      # a live comms call is the channel's too
                outbox.put_nowait(event)
        finally:
            outbox.put_nowait(done)
            speech.put_nowait(None)
            await sender
            await worker
            self.voice_busy = False
            if superseded() and said_now:
                self._remember_heard(contact, said_now, started_wall)
            # Written already but still playing on his speakers: if he cuts in now, it's trimmed then.
            self._last_said = (contact, dict(said_now), started_wall) if said_now else None
            if epoch is None or epoch == self.turn_epoch:
                # Only now is the turn genuinely finished: the model has stopped
                # writing and every sentence has been synthesized and released.
                await self.broadcast(events.turn_complete())
                if voices:
                    self._spawn(self._keep_warm(voices, self.turn_epoch))

    async def _keep_warm(self, voices, epoch):
        """
        While he takes in what was said, each voice on the call has their own
        story so far read ahead, up to their last line (see
        ContactSession.keep_warm) — so whoever speaks next starts in a second
        and a half rather than seven, even with someone else in between. One at
        a time, and only while nothing else wants the model: anything he says
        meanwhile goes first.
        """
        await asyncio.sleep(0.4)
        if self.call and self.call.is_group and getattr(self.call, "last_speaker", None):
            likeliest = self.call.last_speaker.contact.id
            voices = [likeliest] if likeliest in voices else list(voices)[:1]
        for cid in list(voices):
            on_call = cid == self.current_id or cid in self._members()
            if epoch != self.turn_epoch or self.voice_busy or not on_call or cid not in self.sessions:
                return
            try:
                await asyncio.to_thread(self.sessions[cid].keep_warm)
            except Exception as exc:          # a warm-up that fails costs nothing but the warm-up
                log.debug("keeping %s warm failed: %s", cid, exc)

    def _timed_synthesis(self, text, contact, cancelled=None):
        started = time.monotonic()
        try:
            import inspect
            takes = "cancelled" in inspect.signature(self.voice.synthesize_timed).parameters
            audio, words = (self.voice.synthesize_timed(text, contact.voice_id, cancelled=cancelled) if takes
                            else self.voice.synthesize_timed(text, contact.voice_id))
        except Exception as exc:
            if type(exc).__name__ == "SynthesisCancelled":
                raise                          # not a failure: nobody wanted it any more
            log.warning("%s synthesis FAILED after %.2fs: %s", contact.id,
                        time.monotonic() - started, str(exc)[:160])
            raise
        log.info("%s synthesis %.2fs, %d chars%s", contact.id, time.monotonic() - started,
                 len(text), "" if words else " (no timings)")
        return audio, words

    def _can_speak(self, contact):
        return self.voice.available and contact.has_voice

    def _remember_heard(self, contact, said_now, started_wall):
        """
        Cut off, they're remembered as having said what he heard — up to the sentence he heard begin — and
        no further: the rest of what the model wrote was never said to him.
        """
        session = self.sessions.get(contact.id)
        upto = getattr(self, "_heard_upto", None)
        if session is None or upto is None or (self.call and self.call.is_group):
            return
        history = session.history
        last = history.messages[-1] if history.messages else None
        if not last or last["role"] != "assistant" or last.get("at", 0) < started_wall:
            return
        heard = " ".join(text for key, text in sorted(said_now.items()) if key <= upto)
        history.amend_last_reply((heard + " —").strip() if heard else "—")
        self._heard_upto = None

    async def _speech_worker(self, speech, shown, superseded, turn=None):
        """
        Release clips in submission order. Synthesis runs concurrently, but a
        later sentence finishing first must never jump the queue — the page
        plays what it is handed.

        Once the turn is superseded, what is still being synthesised is
        cancelled where it can be and discarded where it can't: a contact
        talked over must not carry on three sentences later, and requests
        nobody will hear were holding ElevenLabs slots the next reply needed —
        which is how talking over someone degraded the voice link.

        When synthesis fails — quota gone, network down, key expired — the
        voice link degrades and the contact carries on in text. The text still
        reaches the screen: the page renders any sentence that never got audio.
        """
        reported = False
        while True:
            item = await speech.get()
            if item is None:
                return
            key, text, task, speaker = item
            if superseded():
                task.cancel()
                continue
            try:
                audio, words = await task
            except asyncio.CancelledError:
                continue
            except Exception:
                if not reported and not superseded():
                    reported = True
                    await self.broadcast(events.notice(
                        "Voice link degraded. Continuing in text.", "warn"))
                continue
            if audio and await shown(key):
                self.recent_speech.append((time.monotonic(), text))
                await self.broadcast({**events.speak(self._store_clip(audio), text, key, words),
                                      "speaker": speaker, "epoch": turn})

    async def connect(self, contact_id, incoming=None, ensure=False, also_ringing=()):
        """
        Switch the console to a contact, booting them on first connection.
        `incoming` is why they rang, when it was them who called.
        """
        contact = self.directory.get(contact_id)
        if contact is None:
            return
        if not incoming and self._incoming and self._incoming["id"] == contact_id:
            return await self.answer(contact_id)     # they're ringing him: that's answering
        if self._release:
            self._release.cancel()
            self._release = None
        if self._incoming and not incoming:
            # He rang someone else while a call was coming in: that one is missed.
            # One line at a time, in either direction.
            if self._ring_timer:
                self._ring_timer.cancel()
                self._ring_timer = None
            self._spawn(self._unanswered("missed"))
        self._connecting = True
        try:
            return await self._connect(contact, contact_id, incoming, ensure, also_ringing)
        finally:
            self._connecting = False

    async def _connect(self, contact, contact_id, incoming, ensure=False, also_ringing=()):
        switching = False
        if not self._abandon_ring() and self.current_id and self.current_id != contact_id:
            switching = True
            self.interrupt()
        async with self.turn_lock:
            if switching and self.current_id:
                # Switching lines mid-call: whoever was on it notices next time.
                # Inside the lock, so the turn in flight has finished first —
                # ending the call under it pulled its `call` away mid-sentence.
                for member in (self.call.members if self.call else [self.sessions[self.current_id]]):
                    member.call_ended("switched")
                self._end_call()
            self.current_id = contact_id
            session = self.session_for(contact_id)
            self.call = Call()
            self.call.join(session)
            # Everyone rung together is invited from the start, so a quick
            # decliner can still dial back in.
            self.call.invited.update(self._inviting or ())
            self._inviting = None
            await self.broadcast(events.contact_changed(contact_id))
            await self.broadcast(events.party([contact_id]))

            if not contact.availability.is_available():
                await self.broadcast(events.notice(
                    contact.availability.away_message, "warn"))
                return

            state = presence.of(contact).now()
            whereabouts = state["status"]
            rung = 0 if incoming else self._attempt(self._call_attempts, contact_id, RING_AGAIN)
            refused = None if (incoming or ensure) else presence.answers(contact, state, again=rung)
            if refused:
                # Not picking up. The ring happens outside the lock, so texts
                # to anyone else carry on meanwhile. The ring has a token of its
                # own: a text or an add meanwhile used to bump the shared epoch,
                # the refusal was forgotten, and they "answered" after all.
                self._ringing_out = contact_id
                ring = self._ring_token = object()
        if refused:
            return await self._let_it_ring(contact, refused, state, ring)
        hour = time.localtime().tm_hour
        if (not incoming and state["status"] == presence.OFFLINE and (hour >= 23 or hour < 9)
                and re.search(r"(?i)asleep|sleep|in bed|napping", state.get("doing") or "asleep")):
            # He woke them. Once is nothing; again this week starts to grate, and they'll say so (see bonds).
            _, times = bonds.woken(contact)
            log.info("%s woken by him (%d this week)", contact.id, times)
        async with self.turn_lock:
            if self.current_id != contact_id:
                return
            session = self.session_for(contact_id)
            # Nobody answers the instant it rings, so the line rings for a
            # varying moment — but the greeting is generated and synthesised
            # during it rather than after, so the ring is the whole wait.
            ring = (random.uniform(0.2, 0.6) if incoming else
                    random.uniform(*PICKUP_DELAY) + random.uniform(*PICKUP_EXTRA.get(whereabouts, (0, 0))))
            pickup = asyncio.get_running_loop().time() + ring

            for problem in config.missing_requirements():
                await self.broadcast(events.notice(problem, "warn"))
            if self.voice.available and not contact.has_voice:
                await self.broadcast(events.notice(
                    f"No voice set for {contact.name} yet — text only.", "warn"))
            if self.migrated:
                await self.broadcast(events.notice(
                    f"Migrated existing {' and '.join(self.migrated)} into "
                    f"data/{config.DEFAULT_CONTACT}/.", "info"))
                self.migrated = []
            await self.drive(session.boot(incoming, rung, also_ringing), contact, self.interrupt(),
                             release_at=pickup)
            if self.current_id != contact_id:
                return          # he'd moved on to another line while this one booted
            await self.broadcast({"type": "picked_up", "speaker": contact_id})
            self._call_attempts.pop(contact_id, None)
            presence.of(contact).touch()
            # Talking now: any plan to get back to him about a missed call is moot — or because Dick said to.
            presence.of(contact).drop("callback")
            presence.of(contact).drop_reached("relayed")
            await self._presence_changed(contact)
            # Whoever's with them is on the line too — in the room, on their speaker.
            self._spawn(self._bring_the_room(contact))

    async def _bring_the_room(self, host, arriving=False):
        """
        He's called someone who isn't alone: whoever's actually with them —
        arrived, the same place — is on the call too, on the host's speaker. No
        ring (they're right there); a moment while the phone goes on speaker;
        their voices come through the room, quieter, and either of them answers
        when he says their name.
        """
        await asyncio.sleep(random.uniform(1.5, 3.5))
        if not self.call or host.id not in self._members():
            return
        _, company = presence.of(host).whereabouts()
        apart = self.__dict__.get("_stepped_away", {}).get(host.id, set())
        for cid in company:
            other = self.directory.get(cid)
            if (other is None or cid in self._members() or cid in apart or cid in getattr(self, "_adding", ())
                    or len(self.call.members) >= MAX_CONTACTS
                    or presence.of(other).now()["status"] == presence.OFFLINE):
                continue
            # Not speaking to him: Tim's at Dick's and doesn't want to talk. He stays out of it — Dick knows, and
            # covers for him or doesn't — or he's there, and lets it be known through the room.
            sore = bonds.effects(other)
            overall = bonds.standing(other.id, bonds.ME)[0]
            if sore["hurt"] >= 4 or overall in ("strained", "hostile", "estranged"):
                why = next((e["text"] for e in bonds.memories(other.id, bonds.ME)), "")
                self.call.avoiding[cid] = (host.id, why)
                if random.random() < 0.5:
                    log.info("%s is at %s's and won't talk to him", cid, host.id)
                    continue
            self.call.speakerphone[cid] = host.id
            await self.broadcast({"type": "room", "with": dict(self.call.speakerphone)})
            if arriving:
                # Walked in on it: they know there's a call on, and whoever they've walked in on notices them.
                note = (f"you've just got to where {host.name} is, in person — and {host.name} is on the phone to "
                        "Bruce, on speaker. You walk in on the call: you're in the room, not on a line of your own")
            else:
                note = (f"you're with {host.name} — Bruce rang {host.name}, who's put him on speaker; "
                        "you're not on a line of your own, you're in the room")
            await self.add(cid, ensure=True, willing=True, remark=False, note=note, overheard=not arriving)
            if arriving and self.call and host.id in self._members():
                await self._react(f"{other.name} has just walked in — here, where you are, in person, while you're "
                                  "on the phone to him.", who=host.id)
        if self.call and self.call.speakerphone:
            await self.broadcast({"type": "room", "with": dict(self.call.speakerphone)})

    def _abandon_ring(self):
        """
        He gave up on a call that was never going to be answered. Nothing was
        said, so nothing is remembered as a call — but they'll see they missed
        him, and may get back to him. True if there was such a ring.
        """
        contact = self.directory.get(self._ringing_out) if self._ringing_out else None
        if contact is None or self.current_id != contact.id:
            return False
        self._ringing_out = self._ring_token = None
        self._refused_at[contact.id] = time.time()    # ringing straight back is urgent
        self.current_id = None
        if self.call:
            for member in list(self.call.members):
                self.call.leave(member)
        self.call = None
        self.interrupt()
        state = presence.of(contact).now()
        # A call all the same: in the thread, as a missed call is on their phone.
        entry = TextLog(contact.id).add("me", "", kind="cancelled_call")
        self._spawn(self.broadcast({"type": "text_sent", "speaker": contact.id, "message": entry}))
        self._spawn(self._after_refusal(contact, "no_answer", state))
        return True

    async def _let_it_ring(self, contact, how, state, ring):
        """
        A call they won't take. It rings — briefly if they reject it, until it
        gives up if they don't — then the line closes, and what they do about
        it afterwards is theirs: a quick "can't talk", a call back once
        they're free, a text later with or without a reason, or nothing.
        """
        await asyncio.sleep(random.uniform(*(DECLINE_AFTER if how == "declined" else NO_ANSWER_AFTER)))
        if self._ring_token is not ring or self.current_id != contact.id:
            return          # he hung up, or rang someone else (or them again), first
        self._ringing_out = self._ring_token = None
        self._refused_at[contact.id] = time.time()
        self.current_id = None
        if self.call:
            for member in list(self.call.members):
                self.call.leave(member)
        self.call = None
        log.info("%s %s the call (%s)", contact.id, how, state["status"])
        entry = TextLog(contact.id).add("me", "", kind="refused_call" if how == "declined" else "unanswered_call")
        await self.broadcast({"type": "call_refused", "speaker": contact.id, "how": how})
        await self.broadcast({"type": "text_sent", "speaker": contact.id, "message": entry})
        self._spawn(self._after_refusal(contact, how, state))

    async def _after_refusal(self, contact, how, state):
        leaning = contact.initiative or {}
        doing = state["doing"] or ("asleep" if state["status"] == presence.OFFLINE else "")
        reason = f"you were {doing}" if doing else "you just didn't pick up"
        call = self.call
        if (how == "declined" and state["status"] == presence.BUSY
                and random.random() < leaning.get("busy_text", 0.4)):
            await asyncio.sleep(random.uniform(6, 25))
            await self._send_unprompted(contact, reason, "busy_now")
        if random.random() >= leaning.get("callback", 0.8):
            return          # some people just don't
        if call is not None and call is self.call and contact.id in call.invited:
            # A group call that's still going: they may dial back into it
            # rather than ring him about it afterwards.
            return await self._join_late(contact, call, reason)
        self._plan_callback(contact, reason)

    async def _join_late(self, contact, call, reason):
        """
        Missed the ring of a call that's still going. Free in a minute or two,
        they come on — saying where they were, or not; busy, it waits until
        they are; and if it's over by then, they get back to him as they would.
        """
        whereabouts = presence.of(contact)
        current = whereabouts.now()
        if current["status"] == presence.OFFLINE:
            return self._plan_callback(contact, reason)
        wait = random.uniform(40, 240)
        if current["source"] == "conversation" and current["until"]:
            wait = max(wait, current["until"] - time.time())
        elif current["status"] == presence.BUSY:
            wait += random.uniform(120, 600)
        await asyncio.sleep(wait)
        if self.call is not call or contact.id in self._members():
            return self._plan_callback(contact, reason)
        if len(call.members) >= MAX_CONTACTS:
            return
        log.info("%s dialling back into the call they missed", contact.id)
        await self.add(contact.id, ensure=True, willing=True,
                       note=f"you missed his call a few minutes ago ({reason}) and have just "
                            "dialled back into it")

    def _plan_callback(self, contact, reason):
        leaning = contact.initiative or {}
        whereabouts = presence.of(contact)
        now, current = time.time(), whereabouts.now()
        if current["source"] == "conversation" and current["until"]:
            due = current["until"] + random.uniform(2, 15) * 60
        elif current["status"] in (presence.BUSY, presence.OFFLINE):
            due = now + random.uniform(25, 90) * 60
        else:
            due = now + random.uniform(4, 35) * 60
        action = "call" if random.random() < leaning.get("callback_call", 0.5) else "text"
        whereabouts.intend(action, reason, due, origin="callback")

    def _is_own_echo(self, text):
        """
        Discard a transcript that is the contact's own voice fed back through
        the microphone. Only recent speech counts: repeating something Alfred
        said an hour ago is a legitimate thing for a person to do.
        """
        cutoff = time.monotonic() - ECHO_WINDOW_SECONDS
        recent = [line for stamp, line in self.recent_speech if stamp > cutoff]
        return guards.echoes(text, recent)

    async def disconnect(self):
        """
        Hang up. The session and its memory stay — this ends the call, it does
        not forget the conversation — but nothing is on the line afterwards, and
        a later call re-greets rather than resuming mid-sentence.
        """
        self.interrupt()
        self._group_ring = None
        if self._abandon_ring():
            return
        async with self.turn_lock:
            contact = self.contact
            self.current_id = None
            self._end_call()
            self.recent_speech.clear()
        if contact:
            self._release = self._spawn(self._release_after(contact))

    def _end_call(self):
        members = self.call.members if self.call else (
            [self.sessions[self.current_id]] if self.current_id in self.sessions else [])
        comms = getattr(self.call, "comms", None) if self.call else None
        if comms and group_store.of(comms["group"]) is not None:
            line = group_store.of(comms["group"]).system("You came off comms")
            self._spawn(self.broadcast({"type": "group_message", "group": comms["group"], "message": line}))
        present = {m.contact.id for m in members}
        if len(present) > 1:
            self._spawn(self._after_group_call(present))
        for member in members:
            self._wrap_up(member, present)
        if self.call:
            for member in list(self.call.members):
                self.call.leave(member)
        self.call = None

    def _wrap_up(self, member, present):
        """
        One contact off the phone: what was said may travel (see
        wayne.engine.grapevine), they were just on the phone with him, and
        whatever the call set in motion is noted. The same whether the call
        ended or they were let off it early.
        """
        grapevine.note_call(member, list(self.directory), member.call_index(), present)
        member._comms = None                       # off the live channel with the call
        presence.of(member.contact).touch()
        said = member.call_messages()
        began = getattr(member, "call_started_at", 0) or 0
        if any(m["role"] == "user" for m in said) and began:
            from ..engine import diary
            others = [self.directory.get(c).name for c in (present or ()) if c != member.contact.id and self.directory.get(c)]
            diary.write(member.contact.id, "call", f"on the phone with Bruce{', ' + ' and '.join(others) + ' on too' if others else ''}"
                        f" ({time.strftime('%H:%M', time.localtime(began))}–{time.strftime('%H:%M')})")
        if any(m["role"] == "user" for m in said):
            self._spawn(self._afterthought(member, said, "call", present))

    def _spawn(self, coroutine):
        """
        A background task that is kept and whose failure is logged — a bare
        create_task's exception vanished, and the task could be collected
        mid-flight.
        """
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)

        def finished(t):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                log.error("background task failed", exc_info=t.exception())
        task.add_done_callback(finished)
        return task

    def _members(self):
        return [m.contact.id for m in self.call.members] if self.call else []

    async def read_ahead(self):
        """
        He's started talking on a one-to-one call: the contact reads ahead the
        part of the coming turn that won't depend on his words (see
        ContactSession.prefetch_turn), so it's ready when he stops — skipped if
        the model's busy speaking, where his turn would only queue behind it.
        """
        cid = self.current_id
        if not cid or self.voice_busy or cid not in self.sessions:
            return
        if self.call and self.call.is_group:
            # With company: whoever spoke last is likeliest to answer him — read ahead theirs.
            likely = self.call.last_speaker if self.call.last_speaker in self.call.members else None
            likely = likely or next((m for m in self.call.members if m.contact.id not in self.call.muted), None)
            if likely is None or likely.contact.id in self.call.muted:
                return
            try:
                await asyncio.to_thread(likely.prefetch_call_turn)
            except Exception as exc:
                log.debug("reading ahead for %s failed: %s", likely.contact.id, exc)
            return
        try:
            await asyncio.to_thread(self.sessions[cid].prefetch_turn)
        except Exception as exc:
            log.debug("reading ahead for %s failed: %s", cid, exc)

    def set_muted(self, on):
        """
        He's muted his mic, or unmuted it: on a call the other end can see
        that, so everyone on the line knows — "you're on mute" is something
        people say.
        """
        self.muted = on
        for cid in set(self._members()) | ({self.current_id} if self.current_id else set()):
            if cid in self.sessions:
                self.sessions[cid].he_muted = on
                self.sessions[cid].muted_since = time.time() if on else None

    async def add(self, contact_id, ensure=False, note="", willing=False, by_him=False, remark=True,
                  overheard=False):
        """
        Patch another contact into the call. They ring, pick up knowing who is on the line (and, `overheard`
        — in the room from the start — the last few things said), and greet the call.
        """
        contact = self.directory.get(contact_id)
        if (contact is None or not self.call or contact_id in self._members() or self._ringing_out
                or not contact.availability.is_available()):
            return
        call = self.call
        if len(self.call.members) >= MAX_CONTACTS:
            await self.broadcast(events.notice(
                f"The line's full — {contact.name} can't be patched in until someone drops off.", "warn"))
            return
        # Already with someone on the call — Tim at Dick's — they're in the room: on Dick's speaker, no ring.
        host = next((m for m in self.call.members
                     if contact_id in presence.of(m.contact).whereabouts()[1] and contact_id not in self.call.speakerphone),
                    None)
        if host is not None:
            self.call.speakerphone[contact_id] = host.contact.id
            await self.broadcast({"type": "room", "with": dict(self.call.speakerphone)})
            ensure, willing, remark = True, True, False
            note = note or (f"you're with {host.contact.name} — Bruce is on {host.contact.name}'s phone and they've put "
                            "him on speaker; you're not on a line of your own, you're in the room")
        self.call.invited.add(contact_id)
        self._adding.add(contact_id)
        try:
            return await self._ring_in(contact, call, ensure, note, willing, by_him, remark, overheard)
        finally:
            self._adding.discard(contact_id)
            self._dropped_adds.discard(contact_id)

    async def _ring_in(self, contact, call, ensure, note, willing, by_him, remark, overheard=False):
        """The ring itself, for add(): every step checks it's still that call, and still wanted."""
        contact_id = contact.id

        def gone():
            return self.call is not call or contact_id in self._dropped_adds
        state = presence.of(contact).now()
        how = presence.answers(contact, state,
                               again=self._attempt(self._call_attempts, contact_id, RING_AGAIN))
        if how and not ensure:
            # Patched in, it rings — and they don't take it. The call goes on.
            await self.broadcast(events.party(self._members() + [contact_id], added=contact_id))
            rang = asyncio.get_running_loop().time()
            await self._while_it_rings(contact)
            wait = random.uniform(*(DECLINE_AFTER if how == "declined" else NO_ANSWER_AFTER))
            await asyncio.sleep(max(0.0, wait - (asyncio.get_running_loop().time() - rang)))
            self._refused_at[contact_id] = time.time()
            if gone():
                return          # hung up meanwhile, or he stopped ringing them
            await self.broadcast({"type": "call_refused", "speaker": contact_id, "how": how})
            await self.broadcast(events.party(self._members()))
            self._spawn(self._after_refusal(contact, how, state))
            missed = "didn't pick up" if how == "no_answer" else "declined"
            if self.call is call:
                call.event(f"{contact.name} {missed} when he tried to add them")
            # Someone on the line may remark on it — or not.
            if random.random() < 0.45:
                await self._react(f"{contact.name} {missed} when he tried to add them to the call.",
                                  absent=contact)
            return
        # He asked for it: whoever's mid-sentence stops. Rung in by someone else
        # — a chase, a call back, the rest of a group ring — it waits its turn.
        epoch = self.interrupt() if by_him else self.turn_epoch
        loop = asyncio.get_running_loop()
        await self.broadcast(events.party(self._members() + [contact_id], added=contact_id,
                                          room=contact_id in self.call.speakerphone))
        rang = loop.time()
        if remark:
            await self._while_it_rings(contact)
        async with self.turn_lock:
            newcomer = self.session_for(contact_id)
            if gone() or not self.call.join(newcomer, overheard=overheard):
                # The call's gone, he stopped the ring, or the line filled: their seat goes.
                if self.call is call:
                    await self.broadcast(events.party(self._members(), removed=contact_id))
                return
            # The greeting is made while it rings and released when they "pick up".
            pickup = max(rang + 2.6 + random.uniform(0.3, 1.6), loop.time() + 0.5)
            await self.drive(self.call.greet(newcomer, note, willing), contact,
                             self.turn_epoch if epoch != self.turn_epoch else epoch, release_at=pickup)
        if self.call is not call or newcomer not in self.call.members:
            return
        # Whatever happened to the greeting, they've picked up — and the page is
        # told who's on the line now, whatever else it was told meanwhile.
        await self.broadcast({"type": "picked_up", "speaker": contact_id})
        await self.broadcast(events.party(self._members()))
        presence.of(contact).drop_reached("relayed")      # on the line with him: whatever was passed on, said here
        if self.call is call:
            call.event(f"{contact.name} is on the call now")
        if getattr(newcomer, "_hanging_up", False):
            # Picked up only to say no, and gone: the others may say something.
            await self._hang_ups(reason="said their piece on joining and hung up")
            return
        # And someone already on the line may greet them back — unless they
        # already have, answering the greeting.
        # Rung together, they're all arriving at once: nobody needs welcoming.
        if (self.call and not self.call.aside_answered and "all at once" not in note
                and random.random() < 0.3):
            joined = (f"{contact.name} has just dialled back into the call" if "dialled back" in note else
                      f"{contact.name} has just joined the call")
            await self._react(joined + ".", but=contact_id)

    def caught(self, contact_id, lines):
        """
        Someone just patched in hears the call from the moment they picked up: whatever was still being said as
        they did — the rest of a sentence, the ones queued after it — as the page was playing it. Nothing
        before (see Call.join).
        """
        newcomer = self.sessions.get(contact_id)
        if not self.call or newcomer is None or newcomer not in self.call.members:
            return
        on = set(self._members())
        caught = []
        for line in (lines if isinstance(lines, list) else [])[:6]:
            speaker = (line or {}).get("speaker") if isinstance(line, dict) else None
            text = str((line or {}).get("text") or "").strip()[:400] if isinstance(line, dict) else ""
            if speaker not in on or speaker == contact_id or not text:
                continue
            needs = wayne_identity.guards([contact_id])
            if needs and wayne_identity.level(speaker) != wayne_identity.level(contact_id):
                # Said before they were on — unguarded: Jim catching "…Master Bruce" is Jim told. Mended where
                # it can be; where a name or the work they mustn't hear is still in it, they caught nothing.
                text = wayne_identity.scrub(text, speaker, [contact_id])
                if (any(re.search(rf"\b{re.escape(n)}\b", text, re.I)
                        for n in wayne_identity.guard_vocatives(speaker, [contact_id]))
                        or ("work" in needs and grapevine._crosses_secret(text, contact_id))):
                    continue
            caught.append(f"(as you picked up) {self.call.heard_as(speaker, text, newcomer)}")
        if caught:
            at = min(getattr(newcomer, "_picked_up_at", 0), len(newcomer.heard))
            newcomer.heard[at:at] = caught

    async def drop(self, contact_id):
        """Let one contact off the call. They say goodbye; the call goes on."""
        if self.call and contact_id in self._adding and contact_id not in self._members():
            # Still ringing: he's thought better of it.
            self._dropped_adds.add(contact_id)
            await self.broadcast(events.party(self._members(), removed=contact_id))
            return
        if not self.call or contact_id not in self._members():
            return
        if len(self.call.members) == 1:
            return await self.disconnect()
        epoch = self.interrupt()
        async with self.turn_lock:
            # Re-checked: a hang-up, a switch or a second drop may have landed
            # while this waited.
            if not self.call or contact_id not in self._members() or len(self.call.members) < 2:
                return
            member = self.sessions[contact_id]
            await self.drive(self.call.farewell(member), member.contact, epoch)
            if not self.call or member not in self.call.members:
                return
            self.call.leave(member)
            self.call.event(f"{member.contact.name} dropped off — he let them go")
            self._wrap_up(member, {contact_id} | set(self._members()))
            if self.current_id == contact_id and self.call.members:
                self.current_id = self.call.members[0].contact.id
            await self.broadcast(events.party(self._members(), removed=contact_id))
            await self._room_goes_with(contact_id)
        # They said goodbye on the line; everyone heard it. A remark now and then, no more.
        if self.call and self.call.is_group and random.random() < 0.2:
            await self._react(f"{member.contact.name} has just dropped off the call.")

    async def _while_it_rings(self, contact):
        """
        While a line rings, someone already on the call may say something about
        it — "ugh, why are you ringing him?" — their way, or nothing.
        """
        if not self.call or not self.call.members or random.random() >= RING_REMARK:
            return
        member = random.choice(self.call.members)
        epoch = self.turn_epoch
        async with self.turn_lock:
            if epoch != self.turn_epoch or not self.call or member not in self.call.members:
                return
            await self.drive(self.call.ringing(member, contact.name), member.contact, epoch)

    async def _react(self, what_happened, but=None, absent=None, who=None):
        """
        One person on the call, picked at random, reacts to it — or doesn't. If
        it's someone who didn't pick up, they may chase them ("I'll text her"),
        and the one chased may then join.
        """
        if not self.call or not self.call.members:
            return
        candidates = [m for m in self.call.members if m.contact.id != but
                      and (who is None or m.contact.id == who)
                      and (who is not None or m.contact.id not in self.call.muted)]
        if not candidates:
            return
        member = random.choice(candidates)
        # Waits for whoever's speaking — a greeting, a reply — rather than
        # cutting them off; anything he says first makes it moot.
        epoch = self.turn_epoch
        async with self.turn_lock:
            if epoch != self.turn_epoch or not self.call or member not in self.call.members:
                return
            await self.drive(self.call.react(member, what_happened, chase=absent is not None),
                             member.contact, epoch)
        if absent is not None and getattr(member, "_nudging", False):
            member._nudging = False
            self._spawn(self._chased(absent, member.contact))
        # A reaction can be "I'm out too" or "I'll get Barbara on".
        await self._hang_ups()
        await self._call_moves()
        await self._call_adds()

    async def _chased(self, contact, by):
        """
        Someone on the call chased them. A minute later they've seen it — and,
        far likelier now than when the phone just rang, they join, knowing who
        got them there.
        """
        await asyncio.sleep(random.uniform(20, 60))
        if (not self.call or contact.id in self._members()
                or len(self.call.members) >= MAX_CONTACTS):
            return
        state = presence.of(contact).now()
        if presence.answers(contact, state, again=2):
            log.info("%s didn't respond to %s chasing them", contact.id, by.id)
            if random.random() < 0.65 and by.id in self._members():
                await self._react(self._chase_came_back(contact, state, by), who=by.id)
            return
        log.info("%s joined after %s chased them", contact.id, by.id)
        await self.add(contact.id, ensure=True, willing=True,
                       note=f"{by.name} texted you to get on this call — you've only just seen it.")

    @staticmethod
    def _chase_came_back(contact, state, by=None):
        """
        What the one who chased them has heard back — from where they really
        are, since that's what their reply would say. The private say nothing,
        and nobody tells someone outside the secret that they're on patrol.
        """
        doing = state.get("doing") or ""
        if (not contact.shares_status or not doing or state["status"] == presence.OFFLINE
                or (by is not None and groupchat.outside(by.id)) or random.random() < 0.3):
            return (f"You texted {contact.name} to get on the call a minute ago; nothing back yet. "
                    "Tell the call, if you'd bother.")
        return (f"{contact.name} has just texted you back: they can't get on right now — they're "
                f"{doing}. Pass it on the way you would.")

    async def _call_adds(self):
        """Someone on the call rang someone else in, because he asked or they're needed."""
        if not self.call:
            return
        for member in list(self.call.members):
            for name in getattr(member, "_call_add", [])[:1]:
                member._call_add = []
                wanted = self.directory.find(name)
                if wanted and wanted.id not in self._members() and len(self.call.members) < MAX_CONTACTS:
                    log.info("%s is ringing %s into the call", member.contact.id, wanted.id)
                    await self.add(wanted.id)

    async def _hang_ups(self, reason="has just hung up and left the call"):
        """
        Anyone on a group call who said goodbye and [hang up] drops off; the call
        goes on without them. The last one out ends it.
        """
        if not self.call or not self.call.is_group:
            return
        for member in list(self.call.members):
            if getattr(member, "_hanging_up", False) and len(self.call.members) == 1:
                # The last of them has said goodbye: the call ends when they've finished saying it, as a
                # one-to-one does — it used to stay up, and they'd fill the next pause after their goodbye.
                member._hanging_up = False
                await self.broadcast(events.call_ending())
                return
            if getattr(member, "_hanging_up", False) and len(self.call.members) > 1:
                member._hanging_up = False
                self.call.leave(member)
                self.call.event(f"{member.contact.name} hung up")
                self._wrap_up(member, {member.contact.id} | set(self._members()))
                if self.current_id == member.contact.id and self.call.members:
                    self.current_id = self.call.members[0].contact.id
                await self.broadcast(events.party(self._members(), removed=member.contact.id))
                await self._room_goes_with(member.contact.id)
                # Whoever was in the room with them saw why they went — and may be going too.
                beside = [m for m in self.call.members if member.contact.name in self.call.in_the_room(m)]
                if beside:
                    await self._react(f"{member.contact.name}, who's right there with you, {reason}. You saw why; "
                                      "if it's yours to deal with too, you'd be going as well.", who=beside[0].contact.id)
                elif random.random() < 0.2:
                    # Their goodbye was heard by all — said it was coming, often agreed to. Seldom remarked on.
                    await self._react(f"{member.contact.name} {reason}.")

    async def _call_moves(self):
        """
        What a line just decided about the room and the mic: stepping away from
        whoever's on their speaker ("I only need you") or going back to them,
        and muting for a minute — the door, a friend arriving.
        """
        members = list(self.call.members) if self.call else (
            [self.sessions[self.current_id]] if self.current_id in self.sessions else [])
        for member in members:
            move, member._room_move = getattr(member, "_room_move", None), None
            why, member._muting = getattr(member, "_muting", None), None
            if move == "away" and self.call:
                await self._step_away(member)
            elif move == "back" and self.call:
                self.__dict__.setdefault("_stepped_away", {}).pop(member.contact.id, None)
                self._spawn(self._bring_the_room(member.contact))
            if why and member.contact.id not in self.__dict__.setdefault("_they_muted", {}):
                await self._mute_them(member, why)

    async def _step_away(self, member):
        """
        Somewhere quieter: whoever was on their speaker is off the call — they
        were only ever in the room — and stays off till they go back. A guest
        doing it leaves the two of them to it.
        """
        cid = member.contact.id
        host = cid if cid not in self.call.speakerphone else self.call.speakerphone[cid]
        guests = [g for g, h in self.call.speakerphone.items() if h == host] if host == cid else [cid]
        if not guests:
            return
        self.__dict__.setdefault("_stepped_away", {}).setdefault(host, set()).update(guests)
        for gid in guests:
            guest = next((m for m in self.call.members if m.contact.id == gid), None)
            self.call.speakerphone.pop(gid, None)
            if guest is None:
                continue
            self.call.leave(guest)
            who = self.directory.get(host)
            self.call.event(f"{who.name if who else 'they'} stepped away from {guest.contact.name}")
            self._wrap_up(guest, {gid} | set(self._members()))
            await self.broadcast(events.party(self._members(), removed=gid))
        await self.broadcast({"type": "room", "with": dict(self.call.speakerphone)})

    async def _mute_them(self, member, why):
        """Their mic off for a minute: the page shows it, their room goes quiet, and they're back when it's done."""
        cid = member.contact.id
        quick = re.search(r"(?i)\b(sec|second|moment|door|kettle|phone|text)\b", why or "")
        info = {"since": time.time(), "until": time.time() + random.uniform(*((15, 45) if quick else (35, 120))),
                "why": why, "missed": []}
        self._they_muted[cid] = info
        if self.call:
            self.call.muted[cid] = why
        log.info("%s muted themselves (%s)", cid, why)
        await self.broadcast({"type": "they_muted", "speaker": cid, "on": True})
        self._spawn(self._back_from_mute(cid))

    async def _back_from_mute(self, cid):
        """Back on the line — and something about where they went, or what he said meanwhile, or just 'back'."""
        info = self._they_muted.get(cid)
        await asyncio.sleep(max(1.0, info["until"] - time.time()))
        info = self._they_muted.pop(cid, None)
        if self.call:
            self.call.muted.pop(cid, None)
        if not info or cid not in (set(self._members()) | {self.current_id}):
            return
        await self.broadcast({"type": "they_muted", "speaker": cid, "on": False})
        away = int(time.time() - info["since"])
        what = (f"You muted yourself about {away} seconds ago — {info['why']} — and you've just unmuted. Come back on "
                "the line the way you would: that you're back, and, if it's worth a word, what that was — briefly, "
                "as people do"
                + (f". While you were off he said: “{' / '.join(info['missed'][-3:])}” — pick that up" if info["missed"]
                   else "") + ".")
        if self.call and self.call.is_group:
            return await self._react(what, who=cid)
        session = self.sessions.get(cid)
        if session is None or self.current_id != cid:
            return
        epoch = self.interrupt()
        async with self.turn_lock:
            if epoch != self.turn_epoch or self.current_id != cid:
                return
            instruction = ("[REFERENCE — context only]\n" + (presence.of(session.contact).note() or "")
                           + "\n[END REFERENCE]\n\n" + what)
            await self.drive(session.say(instruction), session.contact, epoch)

    async def _room_goes_with(self, host_id):
        """
        Someone who hung up had others on their speaker — Tim at Dick's: they were
        only ever on Dick's phone, so they go when Dick does. The call goes on for
        whoever's left on lines of their own; the last one out ends it.
        """
        if not self.call:
            return
        roomed = [cid for cid, host in list(self.call.speakerphone.items()) if host == host_id]
        for cid in roomed:
            self.call.speakerphone.pop(cid, None)
            member = next((m for m in self.call.members if m.contact.id == cid), None)
            if member is None:
                continue
            # Even the last of them: Tim was never on a line of his own — Dick gone, he's gone, and if
            # that was everyone, the call's over (below).
            self.call.leave(member)
            self.call.event(f"{member.contact.name} went with them — they were on their speaker")
            self._wrap_up(member, {cid} | set(self._members()))
            if self.current_id == cid and self.call.members:
                self.current_id = self.call.members[0].contact.id
            await self.broadcast(events.party(self._members(), removed=cid))
        if roomed:
            await self.broadcast({"type": "room", "with": dict(self.call.speakerphone)})
        if self.call and not self.call.members:
            # Spawned, not awaited: a drop calls this holding the turn lock, which hanging up takes too.
            self._spawn(self.disconnect())

    async def call_many(self, ids):
        """
        Ring several at once. Each picks up — or doesn't — in their own time;
        the first to answer opens the line, knowing who else is being rung, and
        the rest join as they pick up. Nobody answers: nobody answers.
        """
        contacts = [c for c in (self.directory.get(i) for i in dict.fromkeys(ids)) if c][:MAX_CONTACTS]
        if not contacts:
            return
        if len(contacts) == 1:
            return await self.connect(contacts[0].id)
        if self.current_id or self.call:
            # Ringing a group from a call is leaving that call: it ends here,
            # whether or not anyone picks up — not left running under the ring.
            await self.disconnect()
        # Together in one place, they're one phone: ring the first of them, and the rest are in the
        # room when it's answered (see _bring_the_room). Tim at Dick's doesn't get a call of his own.
        heads, roomed = [], set()
        for c in contacts:
            if c.id in roomed:
                continue
            heads.append(c)
            company = set(presence.of(c).whereabouts()[1])
            roomed |= {o.id for o in contacts if o is not c and o.id in company}
        if roomed:
            log.info("one phone per room: ringing %s, with %s on speaker", [c.id for c in heads], sorted(roomed))
            contacts = heads
            if len(contacts) == 1:
                return await self.connect(contacts[0].id)
        ring = self._group_ring = object()
        decisions = {c.id: presence.answers(c, presence.of(c).now()) for c in contacts}
        # Those who are together share the moment: busy for one is busy for both,
        # and one picking up brings the other on — "Tim's here, you're on speaker".
        for c in contacts:
            _, group = presence.together(presence.of(c))
            beside = [o for o in contacts if o is not c and o.id in {p.contact.id for p in group}]
            if not beside:
                continue
            if decisions[c.id] == "declined" and random.random() < 0.7:
                for o in beside:
                    decisions[o.id] = decisions[o.id] or "declined"
            elif decisions[c.id] is None:
                for o in beside:
                    if decisions[o.id] == "no_answer" and random.random() < 0.8:
                        decisions[o.id] = None
        await self.broadcast({"type": "group_ringing", "members": [c.id for c in contacts]})
        answering = [c for c in contacts if decisions[c.id] is None]
        for c in contacts:
            if decisions[c.id]:
                self._spawn(self._refuse_group_ring(c, decisions[c.id], ring))
        if not answering:
            return
        first, rest = answering[0], answering[1:]
        others = [c.name for c in contacts if c is not first]
        self._inviting = {c.id for c in contacts}
        await self.connect(first.id, ensure=True, also_ringing=others)
        for contact in rest:
            if self.call and self.current_id:
                together = [c.name for c in contacts if c is not contact]
                # Rung together, so nobody remarks on the ringing — they're all being rung.
                await self.add(contact.id, ensure=True, remark=False,
                               note=f"he rang you along with {' and '.join(together)}, all at once")

    async def _refuse_group_ring(self, contact, how, ring):
        await asyncio.sleep(random.uniform(*(DECLINE_AFTER if how == "declined" else NO_ANSWER_AFTER)))
        self._refused_at[contact.id] = time.time()
        if self._group_ring is not ring:
            return          # that ring's long over: he hung up, or rang again
        await self.broadcast({"type": "call_refused", "speaker": contact.id, "how": how, "group": True})
        self._spawn(self._after_refusal(contact, how, presence.of(contact).now()))
        missed = "didn't pick up" if how == "no_answer" else "declined"
        self._ring_refusals.setdefault(id(ring), []).append(f"{contact.name} {missed}")
        if self.call and self.call.members:
            self.call.event(f"{contact.name} {missed} — he'd rung them too")
            if len(self._ring_refusals[id(ring)]) == 1 and random.random() < 0.3:
                await self._react(f"{contact.name} {missed} — he'd rung them too.", absent=contact)

    async def text(self, contact_id, body, reply_to=None):
        """
        A text message to a contact — any contact, on a call or not.

        Texts behave like texts: delivered at once, read when the contact gets
        to it (promptly for Alfred, eventually for Jason, sometimes not for a
        while because someone is busy), answered after a typing delay that fits
        the reply — and several sent in a row are read and answered together.
        To whoever you're on a call with, a text lands mid-call and they answer
        it out loud: "sent you the address" — "got it, the docks."
        """
        contact = self.directory.get(contact_id)
        body = (body or "").strip()
        if contact is None or not body:
            return
        thread = TextLog(contact_id)
        message = thread.add("me", body, reply_to=reply_to)
        await self.broadcast({"type": "text_sent", "speaker": contact_id, "message": message})

        if (contact_id in self._members() or contact_id == self.current_id) and self._ringing_out != contact_id:
            read_at = thread.mark_read([message["id"]])
            await self.broadcast({"type": "text_read", "speaker": contact_id,
                                  "ids": [message["id"]], "at": read_at})
            return await self._text_into_call(contact_id, body)

        self._pending_texts.setdefault(contact_id, []).append(message)
        burst = self._attempt(self._text_bursts, contact_id, BURST_WINDOW) + 1
        self.session_for(contact_id).pestered = burst
        whereabouts = presence.of(contact)
        if burst >= 3 and whereabouts.now()["status"] != presence.ONLINE:
            # A phone buzzing over and over gets looked at — more likely with
            # every message past the second, even through sleep.
            if random.random() < (contact.texting_pace or {}).get("wake", 0.2) * (burst - 2):
                log.info("%s picked up their phone after %d texts", contact_id, burst)
                whereabouts.touch()
                await self._presence_changed(contact)
        self._start_answering(contact)

    @staticmethod
    def _attempt(attempts, contact_id, window):
        """Record an attempt; return how many came before it inside the window."""
        now = time.time()
        recent = [t for t in attempts.get(contact_id, []) if now - t < window]
        attempts[contact_id] = recent + [now]
        return len(recent)

    def _start_answering(self, contact):
        if contact.id not in self._texters:
            self._texters[contact.id] = self._spawn(self._answer_texts(contact))

    async def _answer_texts(self, contact):
        """
        Read when they get to it, then think, type and reply. When they get to
        it is whatever they're doing (see wayne.engine.presence): seconds if
        the phone is in their hand, minutes if it's down, an hour in a meeting,
        the morning if they're asleep — and a glance from the middle of
        something now and then, which gets a line rather than an answer.
        """
        loop = asyncio.get_running_loop()
        whereabouts = presence.of(contact)
        pace = contact.texting_pace or {}
        try:
            await self._wait_to_read(contact)
            batch = self._pending_texts.pop(contact.id, [])
            if not batch:
                return
            thread = TextLog(contact.id)
            ids = [m["id"] for m in batch]
            body = "\n".join(m["text"] for m in batch)
            # Dry spells leave more on read and more unanswered; a flowing hour, less.
            dry = max(0.35, 1.6 - 0.6 * presence.engagement(contact))
            # Sore at him, or cooler than they were: more left on read, more never answered — by their nature.
            quiet = bonds.effects(contact)["quiet"]
            ghost = min(0.95, max(pace.get("ghost", 0), 0.04 if quiet > 1.5 else 0) * dry * quiet)
            if _URGENT.search(body):
                ghost *= 0.1        # even Randy answers "are you okay"
            if random.random() < ghost:
                # Ghosted. Some open it and say nothing; some never open it at all.
                if random.random() < pace.get("ghost_unread", 0.3):
                    thread.ignore(ids)
                else:
                    read_at = thread.mark_read(ids)
                    await self.broadcast({"type": "text_read", "speaker": contact.id,
                                          "ids": ids, "at": read_at})
                log.info("%s ghosted %d text(s)", contact.id, len(batch))
                return
            read_at = thread.mark_read(ids)
            await self.broadcast({"type": "text_read", "speaker": contact.id,
                                  "ids": ids, "at": read_at})
            if contact.id in self._members():
                # He rang them before they got to it: they read it now and
                # answer out loud, as with any text that lands mid-call.
                return await self._text_into_call(contact.id, "\n".join(m["text"] for m in batch))
            glancing = whereabouts.now()["status"] == presence.BUSY
            if not glancing:
                whereabouts.touch()
            await self._presence_changed(contact)
            if not glancing and random.random() < min(0.95, pace.get("on_read", 0) * dry * quiet):
                # Left on read, for a while. Some people do — and having done it,
                # they aren't offered it again when they get round to answering.
                self.session_for(contact.id).left_on_read = True
                await asyncio.sleep(random.uniform(*pace.get("on_read_for", [180, 1200])))
            else:
                await asyncio.sleep(random.uniform(0.8, 3.0))
            # Written first, typed after: the dots only appear once there's
            # something to type. Shown while the model worked, they sat there
            # for minutes whenever it was busy with something else.
            turn = await self._write_text(contact, "\n".join(_as_read(m) for m in batch))
            reply, session = turn["text"], self.session_for(contact.id)
            if turn["choice"] == "none":
                log.info("%s chose not to answer", contact.id)
                return
            if turn["choice"] == "later" and turn["deferred"]:
                # Their choice: they'll answer, just not now. If he texts again
                # meanwhile, this is dropped and they answer everything at once.
                waiting = len(self._pending_texts.get(contact.id, []))
                await asyncio.sleep(random.uniform(*pace.get("on_read_for", [480, 2700])))
                if len(self._pending_texts.get(contact.id, [])) > waiting:
                    # He texted again meanwhile: what they held back goes in with
                    # the new ones, answered together — not dropped.
                    self._pending_texts[contact.id] = batch + self._pending_texts.get(contact.id, [])
                    return
                said, held = turn["deferred"]
                session.history.record_exchange(said, held, via="text")
                log.info("%s answered later", contact.id)
            group_task, meant = turn["group_task"], turn["meant"]
            if meant is not None and not group_task and reply:
                if groupchat.speaks_to_group(reply, meant, self.directory, contact.id):
                    # Written to the group, in the wrong thread: it goes where it
                    # was meant, and their DM with him keeps only what was his.
                    log.info("%s wrote the group's message in the DM; moved", contact.id)
                    self._spawn(self._post_as(meant, contact, given=reply))
                    session.history.amend_last_reply(f"(Posted in “{meant.name}”: {reply})")
                    reply = ""
                # Otherwise they answered him here and chose not to go to the chat — theirs to choose. Sent
                # there anyway with his words as the task, "what did Tim say in the group?" became a post in it.
            if group_task and reply.strip(" .").lower() in ("", "mm"):
                # Nothing to say to him: they've gone to say it in the group.
                session.history.amend_last_reply(f"(Went to say it in “{group_task[0]}”.)")
                reply = ""
            if turn["take"]:
                self._spawn(self._take_by_name(contact, turn["take"]))
            if turn.get("join"):
                self._spawn(self._join_by_name(contact, turn["join"]))
            asked = ASKS_REACTION.search(batch[-1]["text"] or "")
            if asked and not turn["react"]:
                # Asked to react, they sent the emoji as a text: it's the reaction he asked for.
                lone = [ln for ln in reply.splitlines() if ln.strip() in _TAPBACKS]
                if lone:
                    turn["react"] = lone[0].strip()
                    reply = "\n".join(ln for ln in reply.splitlines() if ln.strip() not in _TAPBACKS).strip()
            if turn["react"]:
                if reply.strip(" .").lower() in ("", "mm"):
                    reply = ""          # the reaction was the reply
                # "Thumbs up my message" means the one before it, if there is one.
                target = batch[-1]["id"]
                if asked:
                    earlier = [m for m in thread.page(limit=12) if m["from"] == "me" and m["id"] != batch[-1]["id"]
                               and not m.get("kind")]
                    target = earlier[-1]["id"] if earlier else target
                await self._tapback_text(contact, target, "them", turn["react"])
            if reply:
                await self._deliver(contact, reply, loop.time(), reply_to=_quoting(thread, batch, turn["quote"]))
                whereabouts.drop("callback")     # back in touch; no need to ring him back
                whereabouts.drop_reached("relayed")     # nor to text him because Dick said to: they're talking
            else:
                await self.broadcast({"type": "text_idle", "speaker": contact.id})
            await self._use_tools(session, "text")      # what they did, not just said: a card, a reminder
            if group_task:
                self._spawn(self.carry_out_group_task(contact, *group_task))
            if reply or turn["react"] or group_task:
                if not glancing:
                    whereabouts.touch()
                    if random.random() < min(0.9, (contact.texting_pace or {}).get("drift", 0)
                                             * max(0.4, 1.5 - 0.5 * presence.engagement(contact))):
                        # And then they put the phone down, mid-conversation,
                        # as people do: the next text waits.
                        whereabouts.touch(time.time() - presence.ENGAGED_FOR)
                # What the exchange set in motion — an errand, a promise, worry, a
                # case closed — read after every one, not only the group requests.
                self._spawn(self._afterthought(session, session.history.messages[-6:], "text"))
                if (reply and not glancing and random.random()
                        < (contact.texting_style or {}).get("second_thought", 0)):
                    self._spawn(self._second_thought(contact))
            self._release_later(contact)
        finally:
            self._texters.pop(contact.id, None)
            try:
                again = bool(self._pending_texts.get(contact.id)) and not asyncio.current_task().cancelling()
            except RuntimeError:
                again = False       # the loop is gone: shutting down
            if again:
                # More arrived while they were replying: another round.
                self._start_answering(contact)

    async def _second_thought(self, contact):
        """'oh and —': one more text a minute later, unless he's already replied."""
        await asyncio.sleep(random.uniform(20, 90))
        last = TextLog(contact.id).last()
        if not last or last["from"] != "them" or contact.id in self._texters:
            return
        await self._send_unprompted(contact, "", "second_thought")

    async def _wait_to_read(self, contact):
        """
        Until they'd look at their phone. Re-judged whenever what they're doing
        changes — a meeting ending, or him ringing them — so a text sent to
        someone asleep is read when they wake, not on a timer set at 3am.
        """
        whereabouts = presence.of(contact)
        deadline, last = None, None
        while True:
            state = whereabouts.now()
            if state["status"] != last:
                last = state["status"]
                delay = presence.read_delay(contact, state)
                if delay is None:
                    deadline = None      # gone to sleep before getting to it
                else:
                    candidate = time.time() + delay
                    deadline = candidate if deadline is None else min(deadline, candidate)
            if deadline is not None and time.time() >= deadline:
                return
            await asyncio.sleep(RECHECK_SECONDS if deadline is None
                                else min(RECHECK_SECONDS, max(0.05, deadline - time.time())))

    async def _deliver(self, contact, reply, started, origin=None, reply_to=None):
        """
        A reply sent as they'd send it: one composed message, or three in a
        row, each typed at their own speed (less the time spent thinking) —
        the first quoting `reply_to`, when it answers one text in particular.
        """
        loop = asyncio.get_running_loop()
        per_second = max(1.0, (contact.texting_pace or {}).get("wpm", 50) * 5 / 60)
        thread = TextLog(contact.id)
        recent = [m["text"] for m in thread.page(limit=8) if m["from"] == "them"][-3:]
        parts = initiative.bubbles(contact, initiative.untic(contact, reply, recent))
        parts = await self._mixed_up(contact, parts, session=self.session_for(contact.id))
        async with self._hands(contact.id) as waited:
            for i, part in enumerate(parts):
                await self.broadcast({"type": "text_typing", "speaker": contact.id})
                await self._type_out(contact.id, part, per_second,
                                     already=(loop.time() - started) if i == 0 and not waited else 0)
                sent = thread.add("them", part, origin=origin, reply_to=reply_to if i == 0 else None)
                await self.broadcast({"type": "text_reply", "speaker": contact.id, "message": sent})
                if getattr(part, "edit_to", None):
                    # Caught it: fixed in place a moment later, and the phone says so.
                    self._spawn(self._edit_later(contact, sent["id"], part.edit_to))
                if i < len(parts) - 1:
                    await asyncio.sleep(random.uniform(0.4, 1.4))

    async def _mixed_up(self, contact, parts, session=None):
        """
        Rarely, by their nature, one of the messages has a real mix-up in it — the wrong day, the wrong place —
        put right a moment later: edited, or a word after it (initiative.misspeak). Not on top of a typo. What
        they remember saying is what they sent, fix and all.
        """
        if not parts or any(getattr(p, "edit_to", None) for p in parts) or not (contact.texting_style or {}).get("misspeaks"):
            return parts
        i = max(range(len(parts)), key=lambda k: len(parts[k]))
        found = await asyncio.to_thread(initiative.misspeak, contact, str(parts[i]), contact.model, contact.options)
        if not found:
            return parts
        slipped, fix = found
        log.info("%s mixed something up (%s)", contact.id, "edited" if fix is None else "corrected")
        if fix and session is not None:
            said = session.history.last_assistant()
            if said:
                amended = said.replace(str(parts[i]), f"{slipped}\n{fix}", 1) if str(parts[i]) in said else \
                    f"{said}\n(Typing fast you first wrote “{slipped}”, then put it right: “{fix}”.)"
                session.history.amend_last_reply(amended)
        return parts[:i] + [slipped] + ([fix] if fix else []) + parts[i + 1:]

    async def _use_tools(self, session, channel):
        """
        What they did on the line, not just said (engine.show): the card they put in front of him — on the call's
        stage, or as a message of its own in the thread — the reminder they set, what they wrote in his diary. Each
        as it would be: in the diary for real, and a card to show it was done.
        """
        contact = getattr(session, "contact", None)
        if contact is None:
            return
        try:
            await self._carry_out_tools(session, contact, channel)
        except Exception as exc:          # what they did must never cost what they said
            log.warning("%s's tools on the %s failed: %s", contact.id, channel, str(exc)[:200])

    async def _carry_out_tools(self, session, contact, channel):
        from ..engine import engagements, show
        shows = getattr(session, "_shows", None) or []
        reminds = getattr(session, "_reminds", None) or []
        diaries = getattr(session, "_diaries", None) or []
        session._shows, session._reminds, session._diaries = [], [], []
        now = time.time()
        cards = []
        for when, what in reminds:
            at = show.when(when, now)
            if at is None or not what.strip():
                log.info("%s's reminder had no time anyone could read: %r", contact.id, when)
                continue
            row = engagements.remind(what.strip(), at, by=contact.id, now=now)
            cards.append({"kind": "reminder", "id": row["id"], "name": row["what"], "at": at,
                          "date": datetime.date.fromtimestamp(at).isoformat()})
            log.info("%s set a reminder for %s", contact.id, time.strftime("%a %H:%M", time.localtime(at)))
        for when, what, where in diaries:
            at = show.when(when, now)
            if at is None or not what.strip():
                continue
            row = engagements.add(what.strip(), where.strip(), at, now=now, source="manual", by=contact.id)
            cards.append({"kind": "entry", "id": row["id"], "name": row["what"], "where": row["where"], "at": at,
                          "date": datetime.date.fromtimestamp(at).isoformat()})
            log.info("%s put %s in the diary", contact.id, time.strftime("%a %H:%M", time.localtime(at)))
        if reminds or diaries:
            await self.broadcast({"type": "diary"})
        for thing in shows:
            found = show.card(thing, contact, now, sources=getattr(session, "_last_sources", None) or ())
            if found:
                cards.append(found)
            else:
                log.info("%s went to show %r, and there was nothing to show", contact.id, thing)
        for card in cards[:3]:
            await self._shown(contact, card, channel)

    async def _shown(self, contact, card, channel):
        """A card from them: on the stage while they're on the line, in the thread when it's a text."""
        if channel == "text":
            message = TextLog(contact.id).add("them", "", origin="card", card=card)
            await self.broadcast({"type": "text_reply", "speaker": contact.id, "message": message})
        else:
            await self.broadcast({"type": "shown", "speaker": contact.id, "card": card})

    async def _edit_later(self, contact, message_id, text, group=None):
        """They've spotted the slip: a few seconds on, the message is edited to what they meant."""
        await asyncio.sleep(random.uniform(2.5, 9.0))
        if group is not None:
            found = group_store.of(group)
            message = found.edit(message_id, contact.id, text) if found is not None else None
            if message is not None:
                await self.broadcast({"type": "group_edited", "group": group, "message": message})
            return
        message = TextLog(contact.id).edit(message_id, text, "them")
        if message is not None:
            await self.broadcast({"type": "text_edited", "speaker": contact.id, "message": message})

    @contextlib.asynccontextmanager
    async def _hands(self, contact_id):
        """
        One pair of thumbs: they type in one thread at a time — his DM or a group
        chat, never both at once. Busy in one, the other waits till they're done,
        and a moment more to switch over. Yields whether they had to wait.
        """
        locks = self.__dict__.setdefault("_thumbs", {})
        lock = locks.setdefault(contact_id, asyncio.Lock())
        waited = lock.locked()
        async with lock:
            if waited:
                await asyncio.sleep(random.uniform(1.5, 5.0))
            yield waited

    async def _type_out(self, contact_id, text, per_second, already=0, group=None, cap=20):
        """
        Typing that looks like a person: roughly as long as the message takes at
        their speed — never exactly, nobody types at a steady rate — and on a
        longer one the dots stop and start as they pause mid-thought.
        """
        speed = per_second * random.lognormvariate(0, 0.22)
        total = min(len(text) / max(0.5, speed), cap) - already
        if total <= 0:
            return
        typing = {"type": "group_typing", "group": group, "speaker": contact_id} if group else \
            {"type": "text_typing", "speaker": contact_id}
        idle = {"type": "group_idle", "group": group, "speaker": contact_id} if group else \
            {"type": "text_idle", "speaker": contact_id}
        if len(text) > 60 and total > 4 and random.random() < 0.6:
            first = total * random.uniform(0.3, 0.6)
            await asyncio.sleep(first)
            await self.broadcast(idle)
            await asyncio.sleep(random.uniform(1.2, 4.0))
            await self.broadcast(typing)
            await asyncio.sleep(total - first)
        else:
            await asyncio.sleep(total)

    async def _afterthought(self, session, exchanges, by, present=()):
        """What the exchange set in motion — see wayne.engine.initiative."""
        await self._call_first()          # someone who's left a call that goes on: read once it's over
        try:
            found = await asyncio.to_thread(initiative.afterthought, session, exchanges, by, tuple(present))
            if found:
                # What, never the words: the log sits beside encrypted memory.
                log.info("%s afterthought: doing=%s contact=%s passing on=%d", session.contact.id,
                         bool(found.get("doing")), bool(found.get("contact")),
                         len(found.get("pass_on") or []) if isinstance(found.get("pass_on"), list) else 0)
                if found.get("pickup") is True:
                    # "Stay there, I'll come and get you": he does, in the Batwing.
                    lifted = await self.give_lift(session.contact.id)
                    if lifted.get("error"):
                        log.info("%s: no lift — %s", session.contact.id, lifted["error"])
                joining = found.get("joining")
                if isinstance(joining, str) and joining.strip().lower().strip(" .") not in presence.NOTHING:
                    # "On my way to Dick", said in so many words and no marker: they still go.
                    await self._join_by_name(session.contact, joining, by="self")
            await self._presence_changed(session.contact)
        except Exception as exc:
            log.warning("%s afterthought failed: %s", session.contact.id, str(exc)[:160])

    def _release_later(self, contact):
        """Free the model a while after a text, as after a call — unless a call is up."""
        if self.current_id is None:
            if self._release:
                self._release.cancel()
            self._release = self._spawn(self._release_after(contact))

    async def _tapback_text(self, contact, message_id, who, emoji):
        """A reaction on a message in a DM — theirs on his, or his on theirs."""
        if who == "them":
            await asyncio.sleep(random.uniform(0.6, 2.5))
        message = TextLog(contact.id).react(message_id, who, (emoji or "")[:16] or None)
        if message is None:
            return
        await self.broadcast({"type": "text_reaction", "speaker": contact.id, "message": message})
        if who == "me" and emoji:
            # They'll see it next time they look at the thread.
            session = self.session_for(contact.id)
            session.tapbacks_seen = (getattr(session, "tapbacks_seen", []) +
                                     [f"he reacted {emoji} to your text “{message['text'][:60]}”"])[-3:]
            if random.random() < initiative.tapback_odds(contact, emoji):
                self._spawn(self._answer_tapback(contact, message, emoji))

    async def _answer_tapback(self, contact, message, emoji):
        """
        His reaction on their text: seen when they next look — and now and
        then it gets something back, a word or "what's the ❓ for", in their
        own way. Not if he's said something since: that's what they answer.
        """
        delay = presence.read_delay(contact, presence.of(contact).now())
        if delay is None:
            return
        await asyncio.sleep(delay + random.uniform(3, 25))
        thread = TextLog(contact.id)
        last = thread.last()
        mine = next((m for m in thread.page(limit=12) if m["id"] == message["id"]), None)
        if (mine is None or (mine.get("reactions") or {}).get("me") != emoji or last is None
                or last["id"] != message["id"] or self._pending_texts.get(contact.id)):
            return
        session = self.session_for(contact.id)
        session.tapbacks_seen = [t for t in getattr(session, "tapbacks_seen", []) if emoji not in t]
        await self._send_unprompted(contact, f"{emoji} to your text “{message['text'][:80]}”", "tapback")

    async def text_react(self, contact_id, message_id, emoji):
        contact = self.directory.get(contact_id)
        if contact:
            await self._tapback_text(contact, message_id, "me", emoji)

    async def text_edit(self, contact_id, message_id, text):
        """
        He edits a text of his. Not read yet: they read it as it is now. Read already, and it says something
        different: they see it's been edited, and may answer the change like anything else he sends. A typo
        fixed is just fixed.
        """
        if self.directory.get(contact_id) is None:
            return
        thread = TextLog(contact_id)
        before = thread.thread.get(message_id)
        message = thread.edit(message_id, text, "me")
        if message is None or before is None:
            return
        await self.broadcast({"type": "text_edited", "speaker": contact_id, "message": message})
        held = next((m for m in self._pending_texts.get(contact_id) or [] if m.get("id") == message_id), None)
        if held is not None:
            held["text"] = message["text"]          # still to be read: read as it is now
            return
        if not message.get("read_at") or _just_a_typo(before["text"], message["text"]):
            return
        self._pending_texts.setdefault(contact_id, []).append(
            {"id": message_id, "from": "me", "at": time.time(),
             "text": f"(He's edited a text he sent you — it said “{before['text'][:160]}”, now it says "
                     f"“{message['text'][:240]}”)"})
        self._start_answering(self.directory.get(contact_id))

    async def _call_first(self, contact_id=None, most=20 * 60):
        """
        While he's on a call, the model is the call's: other work (a text, a post in a group, an unprompted
        word, the pass after someone leaves) waits for it to end — a text reply taken mid-call made his next
        line wait behind it and then read the whole call again. Work for someone on the call goes ahead.
        """
        waited = 0.0
        while getattr(self, "current_id", None) and (contact_id is None or contact_id not in self._members()) \
                and waited < most:
            await asyncio.sleep(2.0)
            waited += 2.0

    async def _write_text(self, contact, body):
        """
        The contact writes a text reply, held to the same lock as call turns.
        Returns the reply and everything the turn decided — answer later, act
        in a group, take a case, react — read while the lock is still held:
        read afterwards, a call turn in between had already reset them, and
        a reply held "for later" came back as nothing at all.
        """
        await self._call_first(contact.id)
        session = self.session_for(contact.id)
        loop = asyncio.get_running_loop()
        result = {"text": ""}

        def run():
            for event in session.ask(body, via="text"):
                if event["type"] == "reply_end" and not event.get("interim"):
                    result["text"] = event["text"]

        async with self.turn_lock:
            try:
                await loop.run_in_executor(None, run)
            except Exception as exc:
                log.warning("%s text FAILED: %s", contact.id, str(exc)[:160])
            result.update(choice=getattr(session, "_reply_choice", None),
                          deferred=getattr(session, "_deferred", None),
                          group_task=getattr(session, "_group_task", None),
                          meant=getattr(session, "_meant_group", None),
                          take=getattr(session, "_take_case", None),
                          join=getattr(session, "_join", None),
                          react=getattr(session, "_text_react", None),
                          quote=getattr(session, "_text_quote", None))
            session._deferred = session._take_case = session._text_react = session._text_quote = None
            session._join = None
        return result

    async def _text_into_call(self, contact_id, body):
        """A text to someone on the line: they see it, and answer on the call."""
        epoch = self.interrupt()
        session = self.session_for(contact_id)
        async with self.turn_lock:
            if epoch != self.turn_epoch or contact_id not in self._members():
                # Something newer came first. The text was still read: it
                # rides along with their next turn instead of vanishing.
                session.unseen_texts.append(body)
                return
            if self.call and self.call.is_group:
                turn = self.call.text_turn(session, body)
            else:
                turn = session.ask(body, via="text_on_call")
            await self.drive(turn, self.contact or session.contact, epoch)

    async def _release_after(self, contact):
        """
        Free the model a while after the call ends. A 26B is 15 GB; resident
        all evening it pushed a 24 GB Mac into swap. A call loads it while the
        line rings (the boot reads the prefix anyway), so releasing between
        calls costs the next greeting a few seconds and nothing else.
        """
        try:
            await asyncio.sleep(config.HANG_UP_RELEASE)
        except asyncio.CancelledError:
            return
        if self.current_id is None:
            await asyncio.to_thread(_release_model, contact.model)

    # --- the console's clock ---------------------------------------------

    async def pulse(self):
        """
        Every half minute: whose dot has changed, which promises have fallen
        due, and whether anyone has a reason to get in touch. This is what
        makes the directory a set of people getting on with their evenings
        rather than seven phones waiting for him.
        """
        await asyncio.sleep(5)
        self._resume_unread()
        self._resume_group_reads()
        while True:
            try:
                await self._tick()
            except Exception as exc:
                log.warning("pulse: %s", str(exc)[:200])
            await asyncio.sleep(PULSE_SECONDS)

    def _resume_unread(self):
        """Texts still unread from before a restart are waiting to be read."""
        for contact in self.directory:
            waiting = TextLog(contact.id).unread()
            pending = self._pending_texts.setdefault(contact.id, [])
            known = {m["id"] for m in pending}
            pending[:0] = [m for m in waiting if m["id"] not in known]
            if pending:
                self._start_answering(contact)
            else:
                self._pending_texts.pop(contact.id, None)

    async def _tick(self):
        now = time.time()
        for contact in self.directory:
            await self._presence_changed(contact)
            if not contact.shares_status and places.is_patrol(presence.of(contact).now(now)["doing"]) \
                    and random.random() < 0.6 * PULSE_SECONDS / 3600:
                # Out on the rooftops, someone who keeps their whereabouts to
                # themselves gets seen now and then: a sighting on the scanner.
                where, _ = presence.of(contact).whereabouts(now)
                presence.of(contact).seen_at(where, "a sighting on the scanner", now)
        # Each step on its own: one that keeps failing (the mail, the papers) no longer takes the cases and
        # comms down with it every pulse.
        await self._step("diary", asyncio.to_thread(self._observe_all, now))
        await self._step("upkeep", asyncio.to_thread(_daily_upkeep))
        # On a call, the model is the call's: nothing that writes with it starts until he hangs up — day plans,
        # the papers, the mail, someone texting first, the group chats talking among themselves. They catch up
        # after. (A first reply took seventeen seconds behind a day plan.)
        calling = bool(self.current_id)
        if not calling:
            for name, step in (("status lines", self._write_status_lines), ("keep up", self._keep_up),
                               ("day plans", self._write_day_plans), ("gazette", self._write_gazette),
                               ("mail", self._write_mail), ("invites", self._write_invites),
                               ("dispatches", self._write_dispatches), ("sounds", self._warm_sounds)):
                await self._step(name, step())
        if not self.clients:
            # Nobody at the console to hear about it — but the night goes on: cases still end when they end, and his
            # day still runs. Without the diary's own watch an evening he was at was settled as missed — letdowns,
            # Jim's trust, the papers — because nothing had looked to see he was there.
            await self._step("cases", self._advance_cases(now))
            await self._step("diary", self._diary_tick(now, quiet=True))
            return
        if not calling:
            for contact in self.directory:
                for intent in presence.of(contact).due(now):
                    # Spawned, not awaited: one contact typing out a long reply
                    # stalled every dot and every other promise behind it.
                    presence.of(contact).postpone(intent["id"], 120, count=False)  # claimed
                    self._spawn(self._carry_out(contact, intent))
            await self._step("reaching out", self._maybe_reach_out(now))
        await self._step("sightings", self._sightings(now))
        await self._step("rooms", self._rooms_arrive(now))
        await self._step("calls in", self._calls_in(now))
        await self._step("announcements", self._announce(now))
        await self._step("diary", self._diary_tick(now))
        if not calling:
            await self._step("groups", self._group_tick(now))
        await self._step("cases", self._case_tick(now))
        # Comms runs on a call too: on a case's comms live, that's where its updates come from (_comms_live);
        # every other channel waits for the call to end (see _comms_tick).
        await self._step("comms", self._comms_tick(now))

    async def _step(self, name, awaitable):
        try:
            await awaitable
        except Exception as exc:
            log.warning("pulse (%s): %s", name, str(exc)[:200])

    async def _advance_cases(self, now):
        """Cases reaching their scenes and their ends with nobody watching — closed in the odds' own words."""
        from ..engine import cases
        for case in cases.advance(now, arrived=self._on_scene):
            if case["id"] not in self._closing:
                self._closing.add(case["id"])
                self._spawn(self._close_case(case, write=False))

    def _observe_all(self, now):
        """Each of them: whatever's changed since the last pulse goes in their diary (see engine.diary)."""
        from ..engine import diary
        for contact in self.directory:
            try:
                diary.observe(presence.of(contact), now)
            except Exception as exc:
                log.warning("diary %s: %s", contact.id, str(exc)[:160])

    async def _write_status_lines(self):
        """
        Anyone whose situation has changed writes themselves a new status line —
        but only while the model is already loaded for something else. Loading
        fifteen gigabytes to write "on patrol 🦇" is how a laptop gets hot.
        """
        # Never in the middle of anything: a status line generated during a call
        # queues the next spoken reply behind it at the model.
        if (self._writing_lines or not self.clients or self.current_id or self._texters
                or self.turn_lock.locked()):
            return
        stale = [c for c in self.directory if not presence.of(c).has_current_line()]
        if not stale or not await asyncio.to_thread(_model_loaded, stale[0].model):
            return
        self._writing_lines = True
        try:
            for contact in stale[:3]:
                whereabouts = presence.of(contact)
                key = whereabouts.line_key()
                line = await asyncio.to_thread(initiative.status_line, contact, whereabouts.now())
                if line:
                    whereabouts.set_line(key, line)
                    await self._presence_changed(contact)
        finally:
            self._writing_lines = False

    async def _write_dispatches(self):
        """The scanner's calls get their dispatch text, two at a time, while the model's idle."""
        from ..engine import incidents
        if (self._writing_lines or not self.clients or self.current_id or self._texters
                or self.turn_lock.locked()):
            return
        waiting = incidents.unwritten()[:2]
        model = next(iter(self.directory)).model if waiting else None
        if not waiting or not await asyncio.to_thread(_model_loaded, model):
            return
        self._writing_lines = True
        try:
            options = next(iter(self.directory)).options
            for report in waiting:
                await asyncio.to_thread(incidents.write_dispatch, report, model, options)
            await self.broadcast({"type": "scanner"})
        finally:
            self._writing_lines = False

    # --- cases ------------------------------------------------------------------

    async def _take_by_name(self, contact, named):
        """'[take: Diamond District]' — the open report they meant, by place or kind."""
        import difflib

        # Open or already being worked: with teams, "back Cass up" is a case to join — not one that's closed.
        from ..engine import cases, incidents
        open_ = sorted((r for r in incidents.at() if r["status"] != "resolved"
                        and (cases.for_report(r["id"]) or {}).get("status") != "closed"),
                       key=lambda r: (-r["severity"], -r["at"]))
        if not open_:
            return
        said = named.lower().strip(" .")
        if said in ("", "the place", "its place", "it", "that", "this", "one"):
            return              # the instruction's own placeholder, or nothing to go on
        # By what it names — the place, the district, the rogue, the kind of call —
        # first; only then a close match. At a cutoff of 0.1, "the docks" took a
        # mugging at the cathedral.
        report = next((r for r in open_ if r["place"].lower() in said or said in r["place"].lower()), None) \
            or next((r for r in open_ if r.get("suspect") and (r["suspect"].lower().replace("the ", "") in said)), None) \
            or next((r for r in open_ if r["area"].lower() in said or said in r["area"].lower()), None) \
            or next((r for r in open_ if r["kind"].lower() in said), None)
        if report is None:
            labels = {f"{r['place']} {r['area']} {r['kind']}".lower(): r for r in open_}
            hit = difflib.get_close_matches(said, list(labels), 1, 0.6)
            report = labels[hit[0]] if hit else None
        if report:
            await self.assign_case(report["id"], contact.id, by="him", tell=False)

    async def _join_by_name(self, contact, named, by="him"):
        """
        '[join: Dick]' — they go to one of the family and stay with them. Dick on a
        case: they're on it too, heading for the scene and counted in the odds when
        they get there. Dick out on the roofs or at home: to wherever he is, the
        fastest way they've got, and with him after — where he goes, they go. Bruce:
        to where he is. Nobody walks off a scene they're in the middle of to do it.
        Returns what it did, or None.
        """
        from ..engine import batman, cases, diary, incidents
        said = (named or "").strip().strip(" .\"'")
        if said.lower() in ("", "name", "their name", "them", "him", "her"):
            return None
        mine = cases.active(contact.id)
        if mine and ((mine.get("members") or {}).get(contact.id) or {}).get("status") == "on scene":
            log.info("%s can't join %s: in the middle of %s", contact.id, said, mine["id"])
            return None
        whereabouts = presence.of(contact)
        if said.lower() in ("bruce", "batman", "b", "me", "you"):
            theirs = cases.active("bruce")
            if theirs and theirs["status"] != "closed":
                return await self.assign_case(theirs["id"], contact.id, by=by, tell=False)
            there = batman.position()
            if there is None:
                return None
            name = incidents._nearest_place(*there)[0] or batman.state()["where"]
            target, label = None, "Bruce"
        else:
            target = self.directory.find(said)
            if target is None or target.id == contact.id:
                return None
            theirs = cases.active(target.id)
            if theirs and theirs["status"] != "closed":
                if mine and mine["id"] == theirs["id"]:
                    return mine                              # already on it with them
                return await self.assign_case(theirs["id"], contact.id, by=by, tell=False)
            if not getattr(target, "shares_location", True):
                return None                                  # nowhere to go: they keep where they are to themselves
            if whereabouts.owned() == "follow" and (whereabouts.get("activity") or {}).get("follow") == target.id:
                return None                                  # on their way already
            there = presence.of(target).position()
            if there is None:
                return None
            name, label = presence.of(target).whereabouts()[0], target.name
        if mine:
            cases.leave(mine["id"], contact.id)              # off the last one: Bruce asked for them elsewhere
        here = whereabouts.position() or there
        trip = await asyncio.to_thread(travel.fastest, contact.id, here, there, name, None, False)
        minutes = max(1, int((trip["end"] - time.time()) / 60))
        if target is not None:
            # With them from now on: where they go — the next roof, home, a case — this one goes too.
            whereabouts.set_activity(f"with {label}", presence.BUSY, max(90, minutes + 60), where=name,
                                     company=[target.id], follow=target.id, owner="follow")
        else:
            whereabouts.set_activity(f"with {label}", presence.BUSY, max(60, minutes + 45), where=name, xy=there,
                                     owner="follow")
        whereabouts.set_trip(name, {**trip, "left": time.time()})
        diary.write(contact.id, "joining", f"went to join {label}" + (" — Bruce asked" if by == "him" else ""))
        log.info("%s joining %s (%s, %d min)", contact.id, label, by, minutes)
        await self._presence_changed(contact)
        return {"joining": label, "minutes": minutes}

    async def assign_case(self, report_id, contact_id, by="him", tell=True):
        """
        Put someone on a report: they head there — the further away, the longer
        it takes — it's what they're doing, and if he did it from the map they
        text him back about it. From the map, asleep, they get it as a text
        from him to read when they wake; awake, they take it as readily as
        they take orders (`takes_orders`: Dick nearly always, Randy rarely).
        Returns the case, or {"texted": True} / {"declined": True}.
        """
        from ..engine import cases, incidents
        report = incidents.get(report_id) or cases.for_report(report_id)
        if contact_id == "bruce" and report is not None:
            return await self._bruce_takes(report)
        contact = self.directory.get(contact_id)
        if contact is None or report is None or contact_id not in cases.FIELD:
            return None
        done = cases.for_report(report_id)
        if done and done["status"] == "closed":
            return None             # over already: nobody's sent to an empty scene
        about = f"{report['kind'].lower()} at {report['place']}" + (
            f" — dispatch said: {report['dispatch']}" if report.get("dispatch") else "")
        if by == "him" and tell and contact_id not in self._members():
            if presence.of(contact).now()["status"] == presence.OFFLINE:
                await self.text(contact_id, f"{report['kind']} at {report['place']}. Can you take it?")
                return {"texted": True}
            if random.random() > (contact.initiative or {}).get("takes_orders", 0.95):
                self._spawn(self._later(random.uniform(8, 40), self._send_unprompted(contact, about, "case_declined")))
                return {"declined": True}
        current = cases.active(contact_id)
        if current and current["id"] != report_id:
            if ((current.get("members") or {}).get(contact_id) or {}).get("status") == "on scene":
                log.info("%s stays on %s: in the middle of it", contact_id, current["id"])
                return None                             # in the thick of one: it's finished first
            cases.leave(current["id"], contact_id)      # off the last one, on to this
        # From exactly where they are, the fastest way they have in the suit — the roofs,
        # the bike, Randy flying, the Batwing if it's near — and the map shows them on
        # their way. A getaway is cut off where they can get ahead of it, not chased.
        whereabouts = presence.of(contact)
        here = whereabouts.position() or (0.0, 0.0)
        goal, cut = self._heading_for(report, here, contact_id)
        trip = await asyncio.to_thread(travel.fastest, contact_id, here, goal, None if cut else report["place"],
                                       None, True)
        minutes = max(0.5, (trip["end"] - time.time()) / 60)
        case = cases.assign({**report, "x": goal[0], "y": goal[1]}, contact_id, by=by, travel=minutes)
        case = self._chase_after(case, report, cut, contact_id, trip["end"]) or case
        await self._case_comms(case)
        if report.get("suspect"):
            from ..engine import codex
            codex.encounter(report["suspect"], f"{report['kind'].lower()} at {report['place']}")
        whereabouts.set_activity(f"on the way to the {report['kind'].lower()} at {report['place']}",
                                 presence.BUSY, max(90, int(minutes) + 60), where=report["place"],
                                 xy=goal if report.get("route") else None, owner="case")
        whereabouts.set_trip(report["place"], {**trip, "left": time.time()})
        if not contact.shares_status:
            whereabouts.seen_at(report["place"], "on a case")
        log.info("%s on case %s (%s at %s, %s)", contact_id, report_id, report["kind"], report["place"], by)
        from ..engine import diary
        others = [self.directory.get(m).name if self.directory.get(m) else "Bruce" for m in cases.team(case) if m != contact_id]
        diary.write(contact_id, "case", (f"joined {' and '.join(others)} on" if others
                                         else "took" if by == "self" else "was put on")
                    + f" the {report['kind'].lower()} at {report['place']}" + (" — Bruce asked" if by == "him" else ""))
        await self._presence_changed(contact)
        await self.broadcast({"type": "cases"})
        if cases.known(case):
            await self._system("case_taken", contact_id)
        if tell and contact_id not in self._members():
            why = "case_assigned" if by == "him" else "case_taken"
            chatty = min(1.0, (contact.initiative or {}).get("per_day", 0.4))
            if by == "him":
                self._spawn(self._later(random.uniform(8, 40), self._send_unprompted(contact, about, why)))
            elif self._may_reach_out(time.time()) and random.random() < 0.4 * chatty:
                self._count_initiative()
                self._spawn(self._later(random.uniform(8, 40), self._send_unprompted(contact, about, why)))
        return case

    async def _bruce_takes(self, report):
        """
        He goes himself — as Batman, whatever the hour: the roofs, the Batmobile or the
        Batwing, whichever's fastest — and he's on the case with whoever's there. A
        getaway he cuts off where he can get ahead of it.
        """
        from ..engine import batman, cases
        if batman.committed():
            return None                                 # in the middle of one: it's finished first
        mine = cases.active("bruce")
        if mine and mine["id"] != report["id"]:
            batman.leave_case_for()                     # on his way to one, he's changed his mind
        here = batman.position() or (0.0, 30.0)
        goal, cut = self._heading_for(report, here, "bruce")
        trip = await asyncio.to_thread(batman.go, report["place"], goal[0], goal[1], report["id"])
        minutes = (trip["end"] - time.time()) / 60 if trip else 10.0
        case = cases.assign({**report, "x": goal[0], "y": goal[1]}, "bruce", by="him", travel=minutes)
        case = self._chase_after(case, report, cut, "bruce", trip["end"] if trip else time.time() + 600) or case
        await self._case_comms(case)
        self._warn_of_the_hunt(report)
        await self.broadcast({"type": "cases"})
        await self.broadcast({"type": "bruce", "bruce": batman.state()})
        log.info("bruce on case %s (%s at %s), %.0f min out", report["id"], report["kind"], report["place"], minutes)
        return case

    async def unassign_case(self, report_id, contact_id):
        """
        He pulls one of them off a case — on the way there or already in it. They stop
        where they are and go back to their night; they may well say something about
        it. Pulled off, the last one on it takes the case with them.
        """
        from ..engine import batman, cases
        case = cases.for_report(report_id)
        if case is None or case["status"] == "closed" or contact_id not in cases.team(case):
            return None
        there = ((case.get("members") or {}).get(contact_id) or {}).get("status") == "on scene"
        cases.leave(report_id, contact_id)
        if contact_id == "bruce":
            batman.halt()
            await self.broadcast({"type": "bruce", "bruce": batman.state()})
        else:
            contact = self.directory.get(contact_id)
            if contact is not None:
                whereabouts = presence.of(contact)
                here = whereabouts.position()
                whereabouts.clear_activity()
                back = whereabouts.whereabouts()[0] or ""
                spot = whereabouts.spot(back)
                if here is not None and spot:
                    # Back to their night from where they'd got to, still suited — not from the scene's door.
                    trip = travel.fastest(contact_id, here, (spot["x"], spot["y"]), spot.get("name"), jet=False)
                    whereabouts.set_trip(back, {**trip, "left": time.time()})
                whereabouts.put("case_rest", time.time() + 20 * 60)
                await self._presence_changed(contact)
                if contact_id not in self._members() and random.random() < (0.75 if there else 0.4):
                    about = (f"the {case['kind'].lower()} at {case['place']}"
                             + (" — you were already there, in it" if there else " — you were on your way"))
                    self._spawn(self._later(random.uniform(10, 50), self._send_unprompted(contact, about, "case_pulled")))
        log.info("%s pulled off case %s", contact_id, report_id)
        if contact_id != "bruce":
            from ..engine import diary
            diary.write(contact_id, "case", f"Bruce pulled them off the {case['kind'].lower()} at {case['place']}"
                        + (" — they were already in it" if there else " on the way there"))
        await self.broadcast({"type": "cases"})
        return cases.for_report(report_id) or {"gone": True}

    async def give_lift(self, contact_id, to=None):
        """
        He gives one of them a lift in the Batwing: they stay put, he comes down for
        them, and drops them where they need to be — their case, or wherever they
        were headed (or `to`: {"name", "x", "y"}). Returns his state, or {"error"}.
        """
        from ..engine import batman, cases, jet
        contact = self.directory.get(contact_id)
        if contact is None or contact_id not in cases.FIELD:
            return {"error": "Not one for a lift"}
        if not batman.state()["suit"]:
            return {"error": "Not in daylight"}
        if batman.committed():
            return {"error": batman.committed()}
        working = cases.active(contact_id)
        if working and ((working.get("members") or {}).get(contact_id) or {}).get("status") == "on scene":
            return {"error": f"{contact.name}'s in the middle of the {working['kind'].lower()}"}
        whereabouts = presence.of(contact)
        them = whereabouts.position()
        mine = batman.position()
        if them is None or mine is None:
            return {"error": "Can't see where they are"}
        case = cases.active(contact_id)
        if to:
            drop, name = (float(to["x"]), float(to["y"])), str(to.get("name") or "where he dropped them")
        elif case:
            drop, name = (case["x"], case["y"]), case["place"]
        else:
            going = whereabouts.whereabouts()[0]
            trip = whereabouts.trip(going, whereabouts.spot(going)) if going else None
            if not trip:
                return {"error": "Nowhere they need to be"}
            drop, name = tuple(trip["pts"][-1]), trip.get("to") or going
        # On his way to a case of his own: drop them, then on to his — he stays in the jet.
        own = cases.active("bruce")
        onward = (own["x"], own["y"]) if own and own["id"] != (case or {}).get("id") else None
        flights = await asyncio.to_thread(jet.lift, contact_id, mine, them, drop, None, onward)
        if flights is None:
            return {"error": "The Batwing's too far off, or mid-flight"}
        his, theirs = flights
        now = time.time()
        place = case["place"] if case else name
        if case:
            whereabouts.set_activity(f"on the way to the {case['kind'].lower()} at {case['place']}, on the Batwing with him",
                                     presence.BUSY, 90, where=case["place"], xy=drop, owner="lift")
            cases.retime(case["id"], contact_id, (theirs["end"] - now) / 60)
        else:
            whereabouts.set_activity(f"getting a lift from him in the Batwing, to {name}", presence.BUSY, 60,
                                     where=place, xy=drop, owner="lift")
        whereabouts.set_trip(place, {**theirs, "left": now})
        if onward:
            batman.ferry(own["place"], onward, his, now)
            cases.retime(own["id"], "bruce", (his["end"] - now) / 60)
        else:
            batman.ferry(name, drop, his, now)
        log.info("bruce giving %s a lift to %s", contact_id, name)
        await self._presence_changed(contact)
        await self.broadcast({"type": "bruce", "bruce": batman.state()})
        await self.broadcast({"type": "cases"})
        return batman.state()

    @staticmethod
    def _heading_for(report, here, who):
        """
        Where to go for a report: the scene — or, for one on the move, the first
        point along its way they can get to ahead of it (and True), or where it'll
        end up, for all the good that'll do, when nowhere's soon enough (and False).
        """
        from ..engine import incidents
        if not (report.get("route") and report.get("moving")):
            return (report["x"], report["y"]), False
        found = incidents.intercept(report, here, who)
        if found:
            return found[0], True
        return tuple(report["route"]["pts"][-1]), False

    def _chase_after(self, case, report, cut, who, arrive):
        """
        Someone gets ahead of a getaway: whether they stop it there is decided when
        they do — the first of them to cut it off decides it — and the case ends soon
        after. Nobody can get ahead of it: it ends when it's clean away.
        """
        from ..engine import cases, outcomes
        route = report.get("route") if report.get("moving") else None
        if not route:
            return None
        start = round(route["start"])
        held = case.get("chase") or {}
        if cut and (held.get("start") != start or held.get("at", 1e18) > arrive):
            caught = outcomes.catches(case, who)
            x, y = case["x"], case["y"]
            return cases.mark(case["id"], chase={"start": start, "x": x, "y": y, "at": arrive, "caught": caught, "by": who},
                              due=arrive + (6 if caught else 2) * 60)
        if not cut and held.get("start") != start:
            return cases.mark(case["id"], due=max(case.get("due", 0), route["end"] + 120))
        return None

    def _turned(self, case, report, now):
        """
        A call they're on has turned into something else — the robbery's a getaway
        now. The case follows it; whoever's on scene goes after it, and whether they
        catch it is decided as they go; whoever's still on the way heads it off.
        """
        from ..engine import cases, incidents, outcomes
        if report["kind"] in (case.get("was") or []) or report["kind"] in (case.get("kinds") or []):
            # Back to something it already was — a getaway run out to "suspects fled", a turn un-happening:
            # a case only moves forward (going back re-scored a clean getaway as a robbery "caught"). A
            # chase that's over ends now, as a chase.
            if case["kind"] in incidents.MOVING and now < case.get("due", now):
                cases.mark(case["id"], due=now, fled=bool(report.get("fled")))
            return
        changes = {"kind": report["kind"], "severity": report["severity"], "was": report.get("was", []),
                   "kinds": list(dict.fromkeys(list(case.get("kinds") or []) + [case["kind"]]))}
        route = report.get("route") if report.get("moving") else None
        held = case.get("chase") or {}
        if route and held.get("start") != round(route["start"]):
            landed = [m for m, v in (case.get("members") or {}).items() if v.get("status") == "on scene"]
            if landed:
                trial = {**case, **changes}
                caught = outcomes.catches(trial)
                f = 0.35 + 0.5 * outcomes._roll(trial, "caught-at") if caught else 1.0
                at = route["start"] + (route["end"] - route["start"]) * f
                x, y = travel.position(route, at)
                changes.update(chase={"start": round(route["start"]), "x": round(x, 2), "y": round(y, 2), "at": at,
                                      "caught": caught, "by": landed[0]},
                               x=round(x, 2), y=round(y, 2), due=at + (6 if caught else 2) * 60)
                # Off after it, right behind: along its way to wherever they stop it, or lose it.
                legs = [math.dist(route["pts"][i], route["pts"][i + 1]) for i in range(len(route["pts"]) - 1)]
                upto, walked, pts = sum(legs) * f, 0.0, [route["pts"][0]]
                for i, leg in enumerate(legs):
                    if walked + leg >= upto:
                        pts.append([round(x, 2), round(y, 2)])
                        break
                    pts.append(route["pts"][i + 1])
                    walked += leg
                for member in landed:
                    contact = self.directory.get(member)
                    if contact is None:
                        continue
                    whereabouts = presence.of(contact)
                    whereabouts.set_activity(f"after the {report['kind'].lower()} from {case['place']}", presence.BUSY,
                                             90, where=case["place"], xy=(x, y), owner="case")
                    whereabouts.set_trip(case["place"], {"pts": pts, "start": route["start"], "end": at,
                                                         "by": "in pursuit", "left": route["start"]})
        case = cases.mark(case["id"], **changes) or case
        if route and not (case.get("chase") or {}).get("by"):
            # Nobody there when it took off: whoever's on the way heads it off instead.
            for member, mine in (case.get("members") or {}).items():
                contact = self.directory.get(member)
                if contact is None or mine.get("status") != "assigned":
                    continue
                whereabouts = presence.of(contact)
                here = whereabouts.position(now) or (case["x"], case["y"])
                goal, cut = self._heading_for(report, here, member)
                trip = travel.fastest(member, here, goal, None, now, book=False, jet=False)
                whereabouts.set_activity(f"after the {report['kind'].lower()} from {case['place']}", presence.BUSY, 90,
                                         where=case["place"], xy=goal, owner="case")
                whereabouts.set_trip(case["place"], {**trip, "left": now})
                case = self._chase_after(case, report, cut, member, trip["end"]) or case
        log.info("case %s turned: %s -> %s", case["id"], case.get("was", ["?"])[-1:], report["kind"])

    # Where each of the two who work comms from their screens has to be for it: Alfred at home, Barbara in the
    # Clocktower.
    _SCREENS = {"alfred": "Wayne Manor", "batgirl": "The Clocktower"}

    def _comms_crew(self, case, crew):
        """
        Who's on a case's comms when it opens: whoever's on the case — and Alfred if he's at home, Barbara if she's
        at the Clocktower working. Anyone else is his to add (see _called_onto_comms).
        """
        out = list(crew)
        for screens in self._SCREENS:
            if screens not in out and self._at_their_screens(screens):
                out.append(screens)
        return out

    def _at_their_screens(self, cid):
        """Alfred at home and up; Barbara in the Clocktower, working — and neither out on a case of their own."""
        from ..engine import cases
        contact = self.directory.get(cid)
        if contact is None or cid not in self._SCREENS or cases.active(cid):
            return False
        whereabouts = presence.of(contact)
        state = whereabouts.now()
        if state["status"] == presence.OFFLINE or whereabouts.whereabouts()[0] != self._SCREENS[cid]:
            return False
        return cid == "alfred" or bool(state.get("terminal"))

    def _called_onto_comms(self, group, cid):
        """
        He's put someone on a case's comms himself. Alfred or Barbara drop whatever they were doing for their
        screens, and what they left goes with them into what they say; anyone else is being asked in — onto the
        case if they'll go, listening if they won't (see ContactSession._comms_note).
        """
        from ..engine import cases
        meta = group.meta() or {}
        case = cases.for_report(meta.get("case", "")) if meta.get("comms") else None
        contact = self.directory.get(cid)
        if case is None or case["status"] == "closed" or contact is None:
            return
        whereabouts = presence.of(contact)
        left = (whereabouts.now().get("doing") or "").strip()
        if cid in self._SCREENS:
            minutes = max(30, int((case.get("due", time.time()) - time.time()) / 60) + 20)
            whereabouts.set_activity(f"on comms for the {case['kind'].lower()} at {case['place']}", presence.BUSY,
                                     minutes, owner="comms", terminal=True)
            self._spawn(self._presence_changed(contact))
        self.session_for(cid)._called_onto_comms = {"group": group.id, "left": left,
                                                    "asked_in": cid not in self._SCREENS and cid not in cases.team(case)}

    def _comms_for(self, cid):
        """The live comms channel they're on for a case that's still going, or None."""
        from ..engine import cases
        mine = cases.active(cid)
        for group in group_store.all_groups():
            meta = group.meta() or {}
            if not meta.get("comms") or meta.get("archived") or cid not in group.members:
                continue
            case = cases.for_report(meta.get("case", ""))
            if case is None or case["status"] == "closed":
                continue
            if mine is None or case["id"] == mine["id"]:
                return group
        return None

    @staticmethod
    def _comms_say(about, why):
        """What would have been a text to him about a case, as something to say on its comms instead."""
        return {"case_assigned": f"he's just put you on this one — {about}. Say on comms that you're on it, your way, "
                                 "a few words",
                "case_taken": f"you've just taken this one yourself — {about}. Say so on comms, briefly",
                "case_arrived": f"you've just got to the scene — {about}. Tell comms you're there and what you see as "
                                "you get there, a few words",
                "case_trouble": f"it's gone bad — {about}. Call it on comms, now: a few words, what you need",
                "case_closed": f"it's over — {about}. Call it on comms, your way, a line",
                "case_hurt": f"you got hurt — {about}. Say it on comms if you would; if it's only his to know, end "
                             "with [dm: what you'd text him]; if you'd keep it to yourself, reply with exactly SKIP",
                "case_watch": f"{about}. A word on comms if you'd give one — what the feeds show, GCPD's position, a "
                              "warning — or nothing"}.get(why, about)

    async def _comms_word(self, group, contact, about, why):
        """What would have been a text to him about a case, said on its comms instead — where the others hear it."""
        await self._post_as(group, contact, opening=self._comms_say(about, why), must=why == "case_trouble")

    # --- comms, live: the case's channel as a call -----------------------------------------------------

    def _live_on_comms(self, cid, case):
        """Who's actually on comms right now, earpiece in: whoever's on the case — Alfred, Barbara, at their screens."""
        from ..engine import cases
        contact = self.directory.get(cid)
        if contact is None:
            return False
        if cid in cases.team(case):
            return True
        if cid in self._SCREENS:
            activity = presence.of(contact).get("activity") or {}
            return self._at_their_screens(cid) or (activity.get("owner") == "comms" and activity.get("until", 0) > time.time())
        return False

    async def join_comms(self, group_id):
        """
        He comes onto a case's comms, live. Whoever's on it is on the line at once — earpieces in, no ring — with
        Alfred in the cave and Barbara at her screens if they're on it; someone gives him where it's at, and from
        then the case comes down the line as it moves (see _comms_live). Off it, the channel carries it as
        messages; what's said on the call is kept there too, so it's one story either way.
        """
        from ..engine import cases
        group = group_store.of(group_id)
        meta = (group.meta() or {}) if group else {}
        case = cases.for_report(meta.get("case", "")) if meta.get("comms") else None
        if self.call and getattr(self.call, "comms", None) and self.call.comms.get("group") == group_id:
            return
        live = [m for m in (group.members if group else []) if case and self._live_on_comms(m, case)][:MAX_CONTACTS]
        if case is None or case["status"] == "closed" or not live:
            await self.broadcast({"type": "comms_joined", "group": group_id, "members": []})
            return await self.broadcast(events.notice(
                "That case is over — comms is closed." if case is not None and case["status"] == "closed"
                else "Nobody's on comms for this one right now.", "info"))
        if self.current_id or self.call:
            await self.disconnect()        # off whatever he was on: one line at a time
        await self.broadcast({"type": "comms_joined", "group": group_id, "members": live})
        async with self.turn_lock:
            self.current_id = live[0]
            self.call = Call()
            self.call.comms = {"group": group_id, "case": case["id"]}
            for cid in live:
                session = self.session_for(cid)
                session._comms = dict(self.call.comms)
                self.call.join(session)
            await self.broadcast(events.contact_changed(live[0]))
            await self.broadcast(events.party(live))
            for cid in live:
                await self.broadcast({"type": "picked_up", "speaker": cid})
        line = group.system("You came onto comms, live")
        await self.broadcast({"type": "group_message", "group": group.id, "message": line})
        log.info("on comms for %s with %s", case["id"], live)
        # Whoever's there gives him where it's at; with nobody there yet, whoever's on the way, or the screens.
        landed = [m for m in live if ((case.get("members") or {}).get(m) or {}).get("status") == "on scene"]
        first = (landed or [m for m in live if m in cases.team(case)] or live)[0]
        group.update_meta(lambda m: m.update({"heard": None, "next_live": time.time() + random.uniform(60, 100)}))
        await self._say_on_call(first, "He's just come onto comms, live. Give him where it's at — a line or two, the "
                                       "way you would on comms; a word of hello at most.")

    async def _say_on_call(self, contact_id, instruction):
        """One of them says something on the call that nobody asked for — an update, a call, how it ended — or nothing."""
        member = self.sessions.get(contact_id)
        if member is None or contact_id not in self._members():
            return None
        epoch = self.turn_epoch
        async with self.turn_lock:
            if epoch != self.turn_epoch or contact_id not in self._members() or not self.call:
                return None
            turn = self.call.update(member, instruction) if self.call.is_group else member.speak_up(instruction)
            line = await self._drive_for(turn, member.contact, epoch)
        if self.call and not self.call.is_group and getattr(member, "_hanging_up", False):
            member._hanging_up = False
            await self.broadcast(events.call_ending())       # the last one on comms signed off: the line closes
        await self._hang_ups()
        return line

    def _comms_record(self, sender, text):
        """What's said on a live comms call, kept in the case's channel too — read later, it's the same story."""
        comms = getattr(self.call, "comms", None) if self.call else None
        group = group_store.of(comms["group"]) if comms else None
        text = (text or "").strip()
        if group is None or not text:
            return
        message = group.add(sender, text, origin="call")
        if message is None:
            return
        for member in self._members() + ["me"]:
            group.mark_read(member, message["at"])          # they heard it said
        self._spawn(self.broadcast({"type": "group_message", "group": group.id, "message": message}))

    async def _onto_live_comms(self, cid):
        """Someone just on the case — or at their screens — while he's on its comms live: on the line too, no ring."""
        if not self.call or not getattr(self.call, "comms", None) or cid in self._members():
            return
        session = self.session_for(cid)
        async with self.turn_lock:
            if not self.call or cid in self._members() or not self.call.join(session):
                return
            session._comms = dict(self.call.comms)
        await self.broadcast(events.party(self._members(), added=cid, room=True))
        await self.broadcast({"type": "picked_up", "speaker": cid})
        self.call.event(f"{self.directory.get(cid).name} is on comms now")

    async def _comms_live(self, group, case, now, meta):
        """
        The case coming down the line while he's on its comms: the end of it said there, and signed off; someone
        getting there, it going bad, it settling — said as it happens; and between, now and then, a word from the
        scene or the screens. Never over him: a turn in hand, or him just having spoken, and it waits.
        """
        from ..engine import cases
        on = [m for m in self._members() if self.directory.get(m)]
        crew_on = [m for m in on if m in cases.team(case)]
        if case["status"] == "closed":
            if not meta.get("signed_off"):
                group.update_meta(lambda m: m.update({"signed_off": True, "close_at": now + COMMS_LINGER}))
                result = (case.get("result") or {}).get("how", "")
                line = group.system(f"Case closed{': ' + result if result else ''}")
                await self.broadcast({"type": "group_message", "group": group.id, "message": line})
                lead = (crew_on or on or [None])[0]
                if lead:
                    self._spawn(self._say_on_call(lead, (
                        f"It's over: {case.get('outcome') or 'done'}. Say it on comms — how it ended, in a line or two, "
                        "true to what happened — then sign off your way and end with [hang up].")))
            return
        if self.turn_lock.locked() or now - getattr(self, "_him_at", 0) < 8:
            return
        phase, how = cases.phase(case, now)
        landed = sorted(m for m, v in (case.get("members") or {}).items() if v.get("status") == "on scene")
        heard = f"{phase}|{','.join(landed)}"
        changed = heard != meta.get("heard")
        if not changed and now < meta.get("next_live", 0):
            return
        group.update_meta(lambda m, heard=heard: m.update({"heard": heard, "next_live": now + random.uniform(75, 160)}))
        before = (meta.get("heard") or "|").split("|")[-1].split(",")
        fresh = [m for m in landed if m not in before and m in on]
        screens = [m for m in on if m not in cases.team(case)]
        if fresh:
            speaker, say = fresh[0], ("You've just got to the scene. Tell comms you're there and what you see as you "
                                      "get there — a few words.")
        elif phase == "gone wrong" and crew_on:
            speaker, say = crew_on[0], "It's gone bad — call it on comms now: a few words, what you need."
        elif screens and (not crew_on or random.random() < 0.3):
            speaker, say = random.choice(screens), ("You're on the screens: what do the feeds or the scanner show now — "
                                                    "GCPD's position, a warning, something they can't see? A line.")
        elif crew_on:
            mine = (case.get("members") or {}).get(crew_on[0]) or {}
            where = ("on your way to join them" if mine.get("status") == "assigned" and case["status"] == "on scene"
                     else how)
            speaker, say = random.choice(crew_on), (f"Comms update — right now you're {where}. What's new: what you "
                                                    "see, what's changed, what you need — a line or two, to whoever "
                                                    "it's for.")
        else:
            return
        self._spawn(self._say_on_call(speaker, say))


    async def _case_comms(self, case):
        """
        More than one of them on a case — or him and one of them — and there's a
        comms channel: everyone on it in a group chat while it lasts, talking the
        way people do mid-op, with Alfred in the cave (see _comms_crew). Whoever
        joins later is added — Barbara too, if she sits down at her screens.
        """
        from ..engine import cases
        crew = [m for m in cases.team(case) if self.directory.get(m)]
        found = next((g for g in group_store.all_groups() if (g.meta() or {}).get("case") == case["id"]), None)
        if found is None and "bruce" in cases.team(case) and not crew:
            # Him on it alone: no channel unless he opens one (see open_comms). Alfred may
            # still have something to say — a word in his ear, as a text, now and then.
            alfred = self.directory.get("alfred")
            if alfred is not None and random.random() < 0.45 and not case.get("alfred_told"):
                cases.mark(case["id"], alfred_told=True)
                self._spawn(self._later(random.uniform(40, 160), self._send_unprompted(
                    alfred, f"he's on the {case['kind'].lower()} at {case['place']} alone, and you're watching it on the "
                            "cave's screens", "case_watch")))
            return None
        if case["status"] == "closed" or not (len(crew) >= 2 or ("bruce" in cases.team(case) and crew) or found):
            return None
        # Jason and Randy working one between them, him not on it: their own channel, not his to read.
        sharers = [m for m in crew if getattr(self.directory.get(m), "shares_location", True)]
        if not sharers and "bruce" not in cases.team(case):
            return None
        if found is None:
            found = group_store.create(f"Comms · {case['kind']} · {case['place']}"[:60], self._comms_crew(case, crew))
            found.update_meta(lambda meta: meta.update({"case": case["id"], "comms": True,
                                                        "next_post": time.time() + random.uniform(15, 45)}))
            found.system(f"Comms open: {case['kind'].lower()} at {case['place']}")
            await self.broadcast({"type": "group_created", "group": self.group_payload(found)})
            return found
        kept_out = set((found.meta() or {}).get("kept_out") or ())
        joining = crew + [s for s in self._SCREENS if s not in kept_out and self._at_their_screens(s)]
        live = getattr(self.call, "comms", None) if self.call else None
        changed = False
        for member in joining:
            if member not in found.members:
                found.add_member(member, backlog=8)
                line = found.system(f"{self.directory.get(member).name} joined comms")
                await self.broadcast({"type": "group_message", "group": found.id, "message": line})
                changed = True
            if live and live.get("group") == found.id and member not in self._members():
                self._spawn(self._onto_live_comms(member))
        if changed:
            await self.broadcast({"type": "group_updated", "group": self.group_payload(found)})
        return found

    async def open_comms(self, report_id):
        """
        He opens a comms channel on a case himself: whoever's on it, and Alfred in the cave
        (Barbara too, if she's at her screens in the Clocktower and not out herself — see
        _comms_crew). His; nobody opens another.
        """
        from ..engine import cases
        case = cases.for_report(report_id)
        if case is None or case["status"] == "closed":
            return None
        found = next((g for g in group_store.all_groups() if (g.meta() or {}).get("case") == case["id"]), None)
        if found is not None:
            if (found.meta() or {}).get("archived"):
                found.update_meta(lambda m: m.update({"archived": False}))
            return found
        crew = self._comms_crew(case, [m for m in cases.team(case) if self.directory.get(m)])
        if not crew:
            return None
        found = group_store.create(f"Comms · {case['kind']} · {case['place']}"[:60], crew)
        found.update_meta(lambda meta: meta.update({"case": case["id"], "comms": True, "by": "him",
                                                    "next_post": time.time() + random.uniform(15, 45)}))
        line = found.system(f"You opened comms: {case['kind'].lower()} at {case['place']}")
        await self.broadcast({"type": "group_created", "group": self.group_payload(found)})
        await self.broadcast({"type": "group_message", "group": found.id, "message": line})
        return found

    async def archive_group(self, group_id, archived=True):
        """He puts a group chat away (or brings it back): it folds into the archived list, and goes quiet."""
        group = group_store.of(group_id)
        if group is None:
            return None
        group.update_meta(lambda m: m.update({"archived": bool(archived), "archived_at": time.time(), "kept": True}))
        if archived:
            for key, task in list(self._group_readers.items()):
                if key[0] == group.id and key not in self._group_writing:
                    task.cancel()
        await self.broadcast({"type": "group_updated", "group": self.group_payload(group)})
        return group

    async def _comms_tick(self, now):
        """
        On comms while it's live: a line every minute or two from whoever's on it,
        about where it's got to; when it's over, a sign-off, and the channel closes
        a little later as everyone goes their own way.
        """
        from ..engine import cases
        for group in group_store.all_groups():
            meta = group.meta() or {}
            if not meta.get("comms"):
                continue
            if meta.get("archived"):
                # Put away when it ended; gone for good a few days on, so they don't pile up — unless
                # he archived it himself, when it's his to delete.
                if not meta.get("kept") and now - meta.get("archived_at", now) > 3 * 86400:
                    await self.group_delete(group.id)
                continue
            case = cases.for_report(meta["case"])
            if case is not None and case["status"] != "closed":
                await self._case_comms(case)          # whoever's since on it, or at their screens: on comms too
                meta = group.meta() or {}
            live = getattr(self.call, "comms", None) if self.call else None
            if case is not None and live and live.get("group") == group.id:
                await self._comms_live(group, case, now, meta)
                continue
            last = (group.summary().get("last") or {}).get("at", 0)
            if case is None or (case["status"] == "closed" and now > meta.get("close_at", now + 1)
                                and now - last > COMMS_QUIET):
                group.update_meta(lambda m: m.update({"archived": True, "archived_at": now}))
                for key, task in list(self._group_readers.items()):
                    if key[0] == group.id and key not in self._group_writing:
                        task.cancel()       # put away: nobody's still reading it to answer
                await self.broadcast({"type": "group_updated", "group": self.group_payload(group)})
                continue
            if case["status"] == "closed":
                if not meta.get("signed_off"):
                    # The end of it, said once: the case line, a sign-off from whoever led
                    # it — and a few minutes on, the channel's put away as they go their ways.
                    group.update_meta(lambda m: m.update({"signed_off": True, "close_at": now + COMMS_LINGER}))
                    result = (case.get("result") or {}).get("how", "")
                    line = group.system(f"Case closed{': ' + result if result else ''}")
                    await self.broadcast({"type": "group_message", "group": group.id, "message": line})
                    speaker = next((self.directory.get(m) for m in cases.team(case) if self.directory.get(m)
                                    and m in group.members), None)
                    if speaker is not None and speaker.id not in self._members():
                        self._spawn(self._post_as(group, speaker, opening=f"it's over: {case.get('outcome', 'done')} — "
                                                  "sign off on comms your way, a few words; nothing more to sort out"))
                continue
            # Comms is bursts, not a feed: a line when something changes — someone gets
            # there, it goes bad, it's settling — and only now and then between. A line
            # every minute or two for an hour and a half was a chat that never ended.
            phase, how = cases.phase(case, now)
            landed = sorted(m for m, v in (case.get("members") or {}).items() if v.get("status") == "on scene")
            heard = f"{phase}|{','.join(landed)}"
            changed = heard != meta.get("heard")
            if not changed and (now < meta.get("next_post", 0) or meta.get("lines", 0) >= COMMS_LINES):
                continue
            if self.current_id or self.turn_lock.locked():
                continue
            on_it = [self.directory.get(m) for m in group.members if self.directory.get(m) and m not in self._members()]
            if not on_it:
                continue
            spoken = meta.get("lines", 0) + (0 if changed else 1)
            group.update_meta(lambda m, heard=heard, spoken=spoken: m.update(
                {"heard": heard, "lines": spoken, "next_post": now + random.uniform(4, 9) * 60}))
            # Whoever just got there says so; otherwise whoever's on it.
            fresh = [self.directory.get(m) for m in landed if m not in (meta.get("heard") or "").split("|")[-1].split(",")
                     and self.directory.get(m) and m not in self._members()]
            speaker = fresh[0] if fresh else random.choice(on_it)
            mine = (case.get("members") or {}).get(speaker.id) or {}
            if speaker.id not in cases.team(case):
                # In his ear, not on the scene: the feeds, the scanner, GCPD's cars — and a word, dry or worried.
                watching = "the cave's screens" if speaker.id == "alfred" else "your screens at the clock tower"
                crew = [self.directory.get(m).name if self.directory.get(m) else "Bruce" for m in cases.team(case)]
                theirs = how
                for mine_, them in (("you're", "they're"), ("your", "their"), ("you", "them")):
                    theirs = re.sub(rf"\b{mine_}\b", them, theirs)
                self._spawn(self._post_as(group, speaker, opening=(
                    f"you're on comms from {watching} — {' and '.join(crew)} on the {case['kind'].lower()} at "
                    f"{case['place']}, {theirs}. A quick line, the way you'd say it: what the feeds or the scanner show, "
                    "where GCPD are, a warning, a dry word — short, no greetings")))
                continue
            if mine.get("status") == "assigned" and case["status"] == "on scene":
                how = "on your way to join them"
            from ..engine import outcomes
            names = {c.id: c.name for c in self.directory} | {"bruce": "Bruce"}
            parts = outcomes.roles(cases.team(case), case["kind"], names)
            on_way = [names.get(m, m) for m, v in (case.get("members") or {}).items()
                      if v.get("status") != "on scene" and m != speaker.id]
            self._spawn(self._post_as(group, speaker, opening=(
                f"you're on comms for the {case['kind'].lower()} at {case['place']} — right now you're {how}. "
                + (f"{' and '.join(on_way)} {'is' if len(on_way) == 1 else 'are'} still on the way, not there yet. "
                   if on_way else "")
                + f"Each of you is on what you're best at ({parts}). A quick line to the team, the way people talk on "
                "comms mid-op, from different ends of the same scene: where you are, what you see, a call, a warning, "
                "the clever move — short, no greetings")))

    async def _case_tick(self, now):
        """
        Patrols take what's near them; cases reach the scene, and run their course.

        Anyone out on patrol is expected to pick up a serious call close by —
        not the instant it comes over the scanner, but within a few minutes, the
        worse it is and the nearer, the sooner. A case gone bad, or the worst
        kind, pulls in whoever's near as backup. Jason goes for the violent ones
        and the crews; nobody tells Randy what's his.
        """
        from ..engine import cases, incidents, places
        openly = [c for c in cases.everything() if c["status"] != "closed"]
        busy = {cid for c in openly for cid in cases.team(c)}
        taken = {c["id"] for c in cases.everything()}
        open_ = [r for r in incidents.at(now) if r["id"] not in taken and r["severity"] >= 2
                 and r["status"] in ("reported", "units responding", "backup requested")]
        violent = ("shoot", "stab", "machete", "gang", "turf", "drive-by", "robbery", "hostage", "assault", "attack")
        hour = time.localtime(now).tm_hour
        dark = hour >= 19 or hour < 5
        for cid in cases.FIELD:
            contact = self.directory.get(cid)
            if contact is None or cid in busy or cid in self._members():
                continue
            whereabouts = presence.of(contact)
            state = whereabouts.now()
            if state["status"] == presence.OFFLINE or now < (whereabouts.get("case_rest") or 0):
                continue        # asleep, out of reach, or just back from one
            # What they're actually doing — not "" because they've just texted him (on their phone, still out).
            patrolling = places.is_patrol(whereabouts._situation(now)[0].get("doing"))
            # From exactly where they are — the roof they're watching from, the road they're on.
            here = whereabouts.position(now)
            sector = whereabouts.whereabouts(now)[0] or ""
            areas = {(places.resolve(b) or {}).get("area") for b in contact.beat} | {
                (places.resolve(sector) or {}).get("area")}
            # Trouble like a case they worked lately — the same crew, the same rogue — is
            # theirs before anyone's: that's a lead, and they'd cross town for it.
            worked = [c for c in cases.everything() if cid in cases.team(c) and now - c.get("opened_at", 0) < 48 * 3600]
            leads = {c.get("suspect") for c in worked if c.get("suspect")} | {c.get("gang") for c in worked if c.get("gang")}
            leads.discard(None)

            def near(x, y, here=here):
                return math.dist(here, (x, y)) if here else 99.0

            choices = []
            for r in open_:
                far = near(r["x"], r["y"])
                lead = bool(leads & {r.get("suspect"), r.get("gang")})
                if patrolling:
                    if r["area"] not in areas and far > (12 if lead else 6):
                        continue
                elif far > (8 if lead else 3.5 if dark else 2.5) or (r["severity"] < (3 if dark else 4) and not lead):
                    continue        # off duty: only something bad, and close — then the game can wait
                if cid == "redhood" and not any(v in r["kind"].lower() for v in violent):
                    continue        # Jason doesn't do break-ins
                # A tick is half a minute: the worst on their doorstep in a minute or two, a
                # minor one across the district maybe never.
                duty = 1.0 if patrolling else 0.6 if dark else 0.35
                choices.append((0.05 * r["severity"] * duty * (1.4 if far < 2.5 else 1.0 if far < 6 else 0.5)
                                * (2.5 if lead else 1.0), r, "take"))
            for c in openly:
                kind, _how = cases.phase(c, now)
                far = near(c["x"], c["y"])
                if (kind == "gone wrong" or c["severity"] >= 4) and far <= 8:
                    choices.append((0.12 if kind == "gone wrong" else 0.05, c, "join"))
                elif patrolling and c["severity"] >= 3 and far <= 4.5 and len(cases.team(c)) < 3:
                    # Someone's already on it, round the corner from them: they back them up.
                    choices.append((0.03, c, "join"))
            if not choices:
                continue
            odds, what, _how = max(choices, key=lambda ch: ch[0])
            if random.random() < odds * (contact.initiative or {}).get("self_dispatch", 1.0):
                whereabouts.put("case_rest", now + random.uniform(25, 70) * 60)
                company = whereabouts.whereabouts(now)[1]       # before the case takes them off on their own
                await self.assign_case(what["id"], cid, by="self")
                busy.add(cid)
                # Whoever they're with comes too: Dick and Tim drop the controllers together — unless they're
                # hurt, or just back from one.
                for friend in company:
                    pal = self.directory.get(friend)
                    resting = pal is not None and (now < (presence.of(pal).get("case_rest") or 0)
                                                   or (presence.of(pal).get("hurt") or {}).get("until", 0) > now)
                    if friend in cases.FIELD and friend not in busy and friend not in self._members() and not resting:
                        await self.assign_case(what["id"], friend, by="self")
                        busy.add(friend)
        # A case gone bad: whoever's in it may call for help — a text, or for the
        # worst of them, a ring — once, and as themselves. Jason never does.
        for case in cases.board(now):
            if case["status"] != "on scene" or case.get("cried_for_help") is not None:
                continue
            if cases.phase(case, now)[0] != "gone wrong":
                continue
            contact = self.directory.get(case["assignee"])
            if contact is None or contact.id in self._members():
                continue
            asks = random.random() < (contact.initiative or {}).get("asks_for_help", 0.4)
            cases.mark(case["id"], cried_for_help=asks)
            if not (asks and case["severity"] >= 4 and not self.current_id and not self._incoming):
                await self._system("case_trouble", contact.id)      # the screens see it — unless they ring and say so
            if not asks:
                continue
            about = f"{case['kind'].lower()} at {case['place']}"
            if case["severity"] >= 4 and not self.current_id and not self._incoming:
                await self._ring(contact, f"backup — the {about} has gone bad")
            else:
                self._spawn(self._send_unprompted(contact, about, "case_trouble"))
        # Calls they're on that have turned — a getaway now, a hostage-taking — and ones gone clean away.
        live = {r["id"]: r for r in incidents.at(now)}
        for case in openly:
            report = live.get(case["id"])
            if report is not None and report["kind"] != case["kind"]:
                self._turned(case, report, now)
            elif report is None and case["kind"] in incidents.MOVING and now < case.get("due", now):
                held = case.get("chase") or {}
                if not held.get("caught") and now > held.get("at", 0):
                    cases.mark(case["id"], due=now)          # it's gone: nothing left to do but say so
        for case in cases.advance(now, arrived=self._on_scene):
            if case["id"] not in self._closing:
                # On a call, or nobody at the console: it still ends when it ends — in a line of the odds'
                # own rather than written up, so the model stays with the call. Waiting, overdue cases sat on
                # "any moment" taking new members who were never in the result.
                self._closing.add(case["id"])
                self._spawn(self._close_case(case, write=not self.current_id and bool(self.clients)))

    def _on_scene(self, case, who=None):
        """
        They've got there: now they're working it, where it is. Whether he
        hears that they've arrived is theirs — Tim and Barbara say so, Cass
        might send a word, Jason doesn't — and never mid-call, where he'd hear it anyway.
        """
        contact = self.directory.get(who or case["assignee"])
        mine = (case.get("members") or {}).get(who or case["assignee"]) or {"by": case.get("by")}
        if contact is not None:
            # Working it till it's done: an hour and a half that ran out first had them driving
            # home from a case the board still had them on.
            left = max(20, int((case.get("due", time.time()) - time.time()) / 60) + 15)
            presence.of(contact).set_activity(f"working the {case['kind'].lower()} at {case['place']}",
                                              presence.BUSY, left, where=case["place"],
                                              xy=(case["x"], case["y"]) if case.get("chase") else None, owner="case")
            self._spawn(self._presence_changed(contact))
            from ..engine import cases as board
            if board.known(case):
                self._spawn(self._system("case_scene", contact.id))
            odds = (contact.initiative or {}).get("case_updates", 0.4)
            if mine.get("by") == "him" and contact.id not in self._members() and random.random() < odds:
                self._spawn(self._send_unprompted(contact, f"the {case['kind'].lower()} at {case['place']}",
                                                  "case_arrived"))

    def _case_bonds(self, closed, result):
        """
        What a case does between them and him: he came himself, and it's remembered; it went bad with them
        alone and nobody sent, and that's remembered too. Those on it together are a little closer. And the
        department keeps its own count: lives saved, cases lost, a chase through the city's streets.
        """
        from ..engine import cases
        team = cases.team(closed)
        what = f"the {closed['kind'].lower()} at {closed['place']}"
        grade = result.get("grade", "")
        went_bad = grade == "failure" or bool(result.get("hurt"))
        family = [m for m in team if m != "bruce" and self.directory.get(m)]
        for member in family:
            if "bruce" in team:
                bonds.nudge(member, bonds.ME, warmth=2, trust=2.5, why=f"he came himself on {what}", weight=2)
            elif went_bad and len(family) == 1 and cases.known(closed):
                bonds.nudge(member, bonds.ME, trust=-2.5, tension=1.5, why=f"nobody came when {what} went bad",
                            weight=2)
            for other in family:
                if other != member:
                    bonds.nudge(member, other, warmth=1.0)
        if cases.known(closed):
            standing = {"success": 1.5, "partial": 0.3, "failure": -1.5}.get(grade, 0.0)
            if closed.get("severity", 2) >= 4 and grade == "success":
                standing += 1.5
            if closed.get("chase"):
                standing -= 1.0           # half a district's cars, and the insurer's letter
            if standing:
                bonds.nudge(bonds.GCPD, bonds.ME, warmth=standing, trust=standing * 0.6,
                            why=(f"{what}: {grade}" + (", with a chase through the streets" if closed.get("chase") else ""))
                            if abs(standing) >= 1.5 else "", weight=2 if abs(standing) >= 2.5 else 1)

    async def _close_case(self, case, write=True):
        """
        It's run its course: how it went is decided by the odds (who was on it,
        what it was, who was behind it — see engine.outcomes), and whoever led it
        writes it up in their own words. They may tell him — as one of the day's
        unprompted texts, not on top of them, and not in his quiet hours or about
        a case that ended hours ago while the console was shut. The hurt carry it;
        the ones who were there remember it; the rest of the family hear.
        """
        from ..engine import cases, outcomes
        try:
            result = outcomes.decide(case)
            writer = next((self.directory.get(m) for m in cases.team(case) if self.directory.get(m)), None)
            outcome = ""
            if writer is not None and write:
                session = self.session_for(writer.id)
                async with self.turn_lock:
                    loop = asyncio.get_running_loop()
                    outcome = await loop.run_in_executor(None, session.case_outcome, case, result)
                    # The story of it, told once, the way they'd tell it later — the one everyone on it remembers.
                    result["story"] = await loop.run_in_executor(None, session.case_story, case, result)
            # His own case, alone, has nobody to write it up — but it ends the same way as anyone's: the rogue's
            # run, the letters, him off it (skipped, his suit stayed on in daylight and Gordon never wrote).
            closed = cases.close(case["id"], outcome or (result["line"][:1].upper() + result["line"][1:] + "."), result)
            if not closed:
                return
            if "bruce" in cases.team(closed):
                from ..engine import batman
                batman.off_case(closed["id"])
            from ..engine import arcs, inbox
            back = arcs.record(closed, result)        # one of them got away: they'll be back, worse
            if cases.known(closed):
                # Letters about it — Gordon, a thank-you, the insurer — only about what he'd know of.
                inbox.queue({"kind": "case", "id": closed["id"], "how": result.get("how", ""),
                             "caught": result.get("caught", ""), "suspect": closed.get("suspect", ""),
                             "place": closed["place"], "what": closed["kind"].lower(),
                             "severity": closed.get("severity", 2), "chase": bool(closed.get("chase"))})
            if back:
                log.info("%s will be back: %s at %s", back["suspect"], back["kind"], back["place"])
            now = time.time()
            from ..engine import diary
            for member in cases.team(closed):
                contact = self.directory.get(member)
                if contact is None:
                    continue
                diary.write(member, "case", f"the {closed['kind'].lower()} at {closed['place']} ended: {result['line']}"
                            + f" ({result.get('grade', 'over')})"
                            + (f"; came out of it with {result['hurt'][member]}" if member in result["hurt"] else ""))
                whereabouts = presence.of(contact)
                whereabouts.clear_activity()
                if member in result["hurt"]:
                    whereabouts.put("hurt", {"how": result["hurt"][member], "until": now + 36 * 3600,
                                             "on": f"the {closed['kind'].lower()} at {closed['place']}"})
                    whereabouts.put("case_rest", now + random.uniform(90, 180) * 60)     # home, and patched up
                else:
                    # Done here: what next is theirs — back someone up, take the next call, or a breather.
                    await self._after_case(contact, closed, now)
                await self._presence_changed(contact)
            self._remember_case(closed, result)
            self._case_bonds(closed, result)
            await self.broadcast({"type": "cases"})
            # Said on the console as it happens — a case ending was silent unless someone texted about it.
            await self.broadcast({"type": "case_closed", "id": closed["id"], "kind": closed["kind"],
                                  "place": closed["place"], "grade": result.get("grade", ""), "how": result.get("how", ""),
                                  "line": outcome or result["line"], "team": cases.team(closed)})
            log.info("%s closed case %s (%s, p=%.2f): %s", writer.id if writer else "bruce", case["id"], result["how"],
                     result["chance"], outcome)
            # Hurt on it: they may tell him — a text, or a ring if it's bad and he's free. Or not.
            for member, injury in result["hurt"].items():
                contact = self.directory.get(member)
                if contact is None or member in self._members():
                    continue
                if cases.known(closed):
                    await self._system("case_hurt", member)
                about = f"{injury}, on the {closed['kind'].lower()} at {closed['place']}"
                bad = any(w in injury for w in ("concussion", "bullet", "knife", "dislocated"))
                if bad and not self.current_id and not self._incoming and random.random() < 0.35:
                    await self._ring(contact, f"hurt — {about}")
                elif random.random() < (contact.initiative or {}).get("asks_for_help", 0.4) + 0.2:
                    self._spawn(self._later(random.uniform(60, 600), self._send_unprompted(contact, about, "case_hurt")))
            chatty = min(1.0, (writer.initiative or {}).get("per_day", 0.4)) if writer else 0.0
            if (writer is not None and writer.id not in self._members() and now - case.get("due", now) < 2 * 3600
                    and self._may_reach_out(now) and random.random() < (0.3 + 0.12 * case["severity"]) * chatty):
                self._count_initiative()
                await self._send_unprompted(writer, f"{case['kind'].lower()} at {case['place']} — {outcome}",
                                            "case_closed")
        finally:
            self._closing.discard(case["id"])

    async def _after_case(self, contact, closed, now):
        """
        A case over, one of them free again where it ended: what they do next is
        theirs. Him on something close by — they'd go to him; anyone else of theirs
        in the thick of it nearby, worse if it's gone wrong — backup; another call
        round the corner — they take it; otherwise a breather, and back to the night.
        """
        from ..engine import cases, incidents
        here = (closed["x"], closed["y"])
        drive = (contact.initiative or {}).get("self_dispatch", 1.0)
        choices = []
        for c in cases.board(now):
            if c["status"] == "closed" or c["id"] == closed["id"] or contact.id in cases.team(c):
                continue
            far = math.dist(here, (c["x"], c["y"]))
            if far > 7:
                continue
            weight = 0.25 + (0.35 if "bruce" in cases.team(c) else 0) + (0.4 if cases.phase(c, now)[0] == "gone wrong" else 0)
            weight += 0.1 * max(0, c["severity"] - 2) - 0.03 * far
            if contact.id == "redhood" and "bruce" in cases.team(c):
                weight *= 0.5                         # Jason will back him up — he just won't hurry
            choices.append((weight, c["id"], "backup"))
        taken = {c["id"] for c in cases.everything()}
        for r in incidents.at(now):
            if r["id"] in taken or r["severity"] < 2 or r["status"] == "resolved" or r.get("done"):
                continue
            far = math.dist(here, (r["x"], r["y"]))
            if far <= 5:
                choices.append((0.12 * r["severity"] - 0.02 * far, r["id"], "next"))
        best = max(choices, default=None)
        if best and random.random() < min(0.9, best[0] * drive):
            presence.of(contact).put("case_rest", 0)
            log.info("%s, done at %s, going straight to %s (%s)", contact.id, closed["place"], best[1], best[2])
            await self.assign_case(best[1], contact.id, by="self")
            return
        presence.of(contact).put("case_rest", now + random.uniform(10, 30) * 60)

    def _remember_case(self, case, result):
        """
        The nights that matter stay: everyone who was there keeps it ("I was
        there when the Joker went down"), and the rest of the family hear it.
        """
        from ..engine import cases, grapevine
        from ..memory.vault import Vault
        big = bool(result.get("caught")) or case["severity"] >= 4 or result["how"] in ("lost", "killed")
        if not big:
            return
        team = cases.team(case)
        names = {m: (self.directory.get(m).name if self.directory.get(m) else "Bruce" if m == "bruce" else m) for m in team}
        when = time.strftime("%-d %B %Y")
        for member in team:
            if self.directory.get(member) is None:
                continue
            others = [names[m] for m in team if m != member]
            try:
                Vault(member).memorize(f"On {when} you were on the {case['kind'].lower()} at {case['place']}"
                                       + (f" with {' and '.join(others)}" if others else "")
                                       + f": {result.get('story') or result['line']}")
            except Exception:
                log.exception("couldn't remember case %s for %s", case["id"], member)
        if result.get("caught"):
            told = [c.id for c in self.directory if c.id not in team]
            grapevine.spread(names[team[0]] if team else "the scanner",
                             f"{result['caught']} was caught tonight — " + " and ".join(names.values())
                             + f" brought them in at {case['place']}", told, delay=(10 * 60, 3 * 3600))

    async def _keep_up(self):
        """
        One contact catches up on what they follow — searched as of today, read
        by the model — while it's loaded and nothing else is happening. A day
        later they catch up again, so they're never months behind.
        """
        if (self._writing_lines or not self.clients or self.current_id or self._texters
                or self.turn_lock.locked()):
            return
        behind = [c for c in self.directory if culture.stale(c)]
        if not behind or not await asyncio.to_thread(_model_loaded, behind[0].model):
            return
        self._writing_lines = True
        try:
            contact = random.choice(behind)
            items = await asyncio.to_thread(culture.refresh, contact)
            if items:
                log.info("%s caught up on what they follow: %d things", contact.id, len(items))
                from ..engine import tastes
                if tastes.due(contact):
                    # A week of it: anything they've got into, or gone off — from what's real, as themselves.
                    moved = await asyncio.to_thread(tastes.evolve, contact, items)
                    if moved.get("into") or moved.get("over"):
                        log.info("%s's tastes moved: into %s; over %s", contact.id,
                                 [i["title"] for i in moved.get("into", [])], [i["title"] for i in moved.get("over", [])])
            else:
                # Nothing came back (offline, rate-limited): try someone else
                # next time rather than hammering the same searches.
                culture.mark_tried(contact)
        finally:
            self._writing_lines = False

    async def _write_mail(self):
        """
        His mail: the night report at six and the morning briefing whenever they're
        due (put together, not written), then — while the model's loaded and idle —
        one more piece of whatever life would send him.
        """
        from ..engine import engagements, inbox, incidents
        now = time.time()
        engagements.settle(now)                 # the evenings that are over: went, or didn't — notes to come
        for made in (inbox.write_night_report(now), inbox.write_briefing(now), inbox.write_market_close(now)):
            if made:
                await self.broadcast({"type": "mail", "message": made, "unread": inbox.unread()})
        for r in incidents.at(now):
            if r["kind"] == "Breakout" and r["id"] not in self._mail_seen:
                self._mail_seen.add(r["id"])
                inbox.queue({"kind": "breakout", "rogue": r.get("suspect", ""), "how": f"broke out of {r['place']}",
                             "id": r["id"]})
        if (self._writing_lines or not self.clients or self.current_id or self._texters or self.turn_lock.locked()
                or now < self._mail_next):
            return
        writer = self.directory.get("alfred") or next(iter(self.directory), None)
        if writer is None or not await asyncio.to_thread(_model_loaded, writer.model):
            return
        self._writing_lines = True
        try:
            made = await asyncio.to_thread(inbox.write_one, writer.model, writer.options, now)
            self._mail_next = now + random.uniform(4, 12) * 60
            if made:
                log.info("mail: %s", made.get("kind"))
                await self.broadcast({"type": "mail", "message": made, "unread": inbox.unread()})
        finally:
            self._writing_lines = False

    async def _write_gazette(self):
        """This morning's Gotham news, once a day, while the model's loaded and nothing else is happening."""
        from ..engine import gazette
        local = time.localtime()
        if local.tm_hour < 5 or (local.tm_hour == 5 and local.tm_min < 30):
            return          # the morning's papers go to bed after the night's over, not at midnight
        if (self._writing_lines or not self.clients or self.current_id or self._texters
                or self.turn_lock.locked() or gazette.written()):
            return
        writer = self.directory.get("alfred") or next(iter(self.directory), None)
        if writer is None or not await asyncio.to_thread(_model_loaded, writer.model):
            return
        self._writing_lines = True
        try:
            items = await asyncio.to_thread(gazette.write, writer.model, writer.options)
            if items:
                log.info("the morning news: %d items", len(items))
        finally:
            self._writing_lines = False

    async def _write_day_plans(self):
        """
        Each contact sketches their own day, once, while the model is already
        loaded and nothing else is happening — one contact a tick, so it never
        holds anything up.
        """
        if (self._writing_lines or not self.clients or self.current_id or self._texters
                or self.turn_lock.locked()):
            return
        now = time.time()
        if await self._write_bruces_day(now):
            return
        backoff = self._plan_backoff
        waiting = [c for c in self.directory if not presence.of(c).has_plan() and backoff.get(c.id, 0) <= now]
        if not waiting or not await asyncio.to_thread(_model_loaded, waiting[0].model):
            return
        self._writing_lines = True
        try:
            contact = waiting[0]
            plan = await asyncio.to_thread(initiative.day_plan, contact, now,
                                           self._plans_so_far(contact), list(self.directory))
            if plan:
                # For the day it was sketched on: begun at 23:59, it was filed under tomorrow when it finished.
                presence.of(contact).set_plan(plan, t=now)
                log.info("%s planned the day: %d blocks", contact.id, len(plan))
                await self._presence_changed(contact)
            else:
                backoff[contact.id] = now + 10 * 60      # try again later, and the others meanwhile
        finally:
            self._writing_lines = False

    async def _write_bruces_day(self, now):
        """His own day, sketched once each morning (engine.engagements.write_day). True if it was written now."""
        import datetime as _dt

        from ..engine import engagements
        today = _dt.date.fromtimestamp(now)
        if time.localtime(now).tm_hour < 5 or engagements.has_day(today) or \
                getattr(self, "_day_tried", (None, 0))[0] == today and now - self._day_tried[1] < 1800:
            return False
        writer = self.directory.get("alfred")
        if writer is None or not await asyncio.to_thread(_model_loaded, writer.model):
            return False
        self._day_tried = (today, now)
        self._writing_lines = True
        try:
            made = await asyncio.to_thread(engagements.write_day, writer.model, writer.options, today, now)
        finally:
            self._writing_lines = False
        if made:
            log.info("Bruce's day: %d things", len(made))
        return bool(made)

    def _plans_so_far(self, contact):
        """
        What the others who share his secret have planned today, for someone
        sketching theirs — so two who'd patrol together can, and say so both
        sides. Nobody outside the secret sees anyone's nights, or shows theirs.
        """
        from .. import operator
        known = set((operator.profile().get("secrets") or {}).get("known_by") or [])
        if contact.id not in known:
            return ""
        lines = []
        for other in self.directory:
            if other.id == contact.id or other.id not in known:
                continue
            for block in presence.of(other).plan_today():
                if block.get("where"):
                    lines.append(f"{other.name}: {block['doing']} — {block['where']}, "
                                 f"{_clock(block['from'])}–{_clock(block['to'])}")
        return "\n".join(lines[:14])

    async def _presence_changed(self, contact):
        # Worked out on a thread: a journey not yet worked out costs a road route or two, and nine of them on
        # the event loop at the turn of a watch held a call's voice up for most of a second.
        shown = await asyncio.to_thread(presence.of(contact).public)
        # Where they are counts: a patrol moving along its beat is news to the map — and who they're with.
        key = (shown["status"], shown["doing"], shown.get("line", ""), shown.get("where"),
               tuple(shown.get("with") or ()))
        if self._shown_presence.get(contact.id) != key:
            self._shown_presence[contact.id] = key
            await self.broadcast({"type": "presence", "speaker": contact.id, "presence": shown})

    async def _carry_out(self, contact, intent):
        """A promise falls due: they text, or they ring."""
        whereabouts = presence.of(contact)
        if contact.id in self._members():
            whereabouts.done(intent["id"])   # on the line with him; they'll just say it
            return
        if whereabouts.now()["status"] == presence.OFFLINE:
            whereabouts.postpone(intent["id"], 600, count=False)
            return
        callback = intent["origin"] == "callback"
        if (intent["origin"] == "relayed" and whereabouts.now()["status"] == presence.BUSY
                and intent.get("tries", 0) < 6):
            # "When he's free": word passed on is got round to between things, not in the middle of them.
            whereabouts.postpone(intent["id"], 900)
            return
        if intent["action"] == "call":
            if not (self.current_id or self._incoming or self._connecting):
                whereabouts.done(intent["id"])
                about = (f"calling him back — he rang you earlier and you didn't pick up "
                         f"({intent['about']})") if callback else intent["about"]
                return await self._ring(contact, about)
            if intent.get("tries", 0) < 2:
                whereabouts.postpone(intent["id"], 300)   # he's on another call
                return
        whereabouts.done(intent["id"])
        why = ("callback" if callback else "worry" if intent.get("origin") == "worry"
               else "relayed" if intent.get("origin") == "relayed" else "promise")
        await self._send_unprompted(contact, intent["about"], why)

    def _quiet(self, now):
        try:
            start, end = (int(h) for h in config.QUIET_HOURS.split("-"))
        except ValueError:
            return False
        hour = time.localtime(now).tm_hour
        return start <= hour < end if start <= end else (hour >= start or hour < end)

    def _initiative_log(self):
        """When each unprompted text was decided on, the last day's worth (the document "initiative")."""
        found = store.doc("initiative", [])
        return found if isinstance(found, list) else []

    async def _maybe_reach_out(self, now):
        """
        The unprompted text: chasing a question he left hanging, or something
        on their mind. Budgeted — a few a day across everyone, never two
        within the hour, none while he sleeps, none from someone already
        waiting on him — so it reads as people, not notifications.
        """
        if not self._may_reach_out(now):
            return
        chases, impulses = [], []
        for contact in self.directory:
            if contact.id in self._members() or contact.id in self._texters:
                continue
            whereabouts = presence.of(contact)
            if whereabouts.now()["status"] not in (presence.ONLINE, presence.IDLE):
                continue
            leaning = contact.initiative or {}
            last = TextLog(contact.id).last()
            if last and last["from"] == "me":
                # He's waiting on them, not the other way round — until enough time
                # has gone that they'd just text about something else as if
                # nothing happened (Dick in hours, Jason a day on, Randy never).
                after = leaning.get("resurface_after", 8)
                if after is None or now - last["at"] < after * 3600:
                    continue
            if last and last["from"] == "them":
                # Their question, unanswered: chase it — once, if they're the type.
                chase = whereabouts.get("chase") or {}
                if (last["text"].rstrip().endswith("?") and chase.get("id") != last["id"]
                        and last.get("origin") != "chase"):
                    lo, hi = leaning.get("chase_after", [30, 180])
                    chase = {"id": last["id"], "at": last["at"] + random.uniform(lo, hi) * 60
                             if random.random() < leaning.get("double_text", 0.3) else None}
                    whereabouts.put("chase", chase)
                if chase.get("id") == last["id"] and chase.get("at") and now >= chase["at"]:
                    chases.append(contact)
                    continue
                # They spoke last: it's his turn — for a while. How long a
                # while is theirs: Dick's onto the next thing in hours, Randy
                # never texts into a silence he didn't break.
                if now - last["at"] < 12 * 3600 * (1 - leaning.get("double_text", 0.3)) + 3600:
                    continue
            # Something on their mind: a few times a day for the chatty ones.
            # Warmer, they text first more; sore, less — or only to have it out (see initiative.impulse).
            if random.random() < leaning.get("per_day", 0.4) * bonds.effects(contact)["reach"] * PULSE_SECONDS / (16 * 3600):
                impulses.append(contact)
        if chases:
            contact = random.choice(chases)
            presence.of(contact).put("chase", {"id": (presence.of(contact).get("chase") or {}).get("id")})
            self._count_initiative()
            last = TextLog(contact.id).last() or {}
            # Their phone shows whether he's opened it, as anyone's does.
            seen = f"read {describe_gap(now - last['seen_at'])}" if last.get("seen_at") else "not opened"
            if last.get("seen_at"):
                # Read and left: noticed — and a second time today, remembered (engine.bonds).
                chased = sum(1 for m in TextLog(contact.id).page(limit=30)
                             if m.get("origin") == "chase" and now - m["at"] < 86400)
                bonds.turned_away(contact, "on_read", chased + 1)
            self._spawn(self._send_unprompted(contact, seen, "chase"))
        elif impulses:
            contact = random.choice(impulses)
            about = initiative.impulse(self.session_for(contact.id))
            last = TextLog(contact.id).last()
            if about and last and last["from"] == "them":
                about += (f" (he never answered your last text — {'he read it' if last.get('seen_at') else 'never even opened it'}; "
                          "mention that, or don't, whatever you'd actually do)")
            if about:
                self._count_initiative()
                self._spawn(self._send_unprompted(contact, about, "impulse"))

    def _may_reach_out(self, now):
        """
        Room in the day for one more unprompted text: under the daily budget,
        not within INITIATIVE_GAP of the last, not in his quiet hours — the
        one rule for every text nobody asked for, whatever prompted it.
        """
        if config.INITIATIVE_PER_DAY <= 0 or self._quiet(now) or not self.clients:
            return False
        sent = [t for t in self._initiative_log() if now - t < 86400]
        return len(sent) < config.INITIATIVE_PER_DAY and not (sent and now - max(sent) < INITIATIVE_GAP)

    def _count_initiative(self):
        """Counted when decided, not when sent — or the next tick decides again."""
        entries = [t for t in self._initiative_log() if time.time() - t < 86400]
        store.save_doc("initiative", entries + [time.time()])

    # What they'd tell him about a case: said on its comms channel, if there is one and they're on it.
    _COMMS_TALK = {"case_assigned", "case_taken", "case_arrived", "case_trouble", "case_closed", "case_hurt", "case_watch"}

    async def _send_unprompted(self, contact, about, why):
        """They text first: written, then typed, then sent — or, about a case with comms open, said there."""
        if why in self._COMMS_TALK:
            if getattr(self.call, "comms", None) and contact.id in self._members():
                return await self._say_on_call(contact.id, self._comms_say(about, why))   # live: said on the call
            group = self._comms_for(contact.id)
            if group is not None:
                return await self._comms_word(group, contact, about, why)
        await self._call_first(contact.id)
        session = self.session_for(contact.id)
        loop = asyncio.get_running_loop()
        async with self.turn_lock:
            if contact.id in self._members():
                return      # he's rung them meanwhile; they'll say it, not text it
            text = await loop.run_in_executor(None, session.reach_out, about, why)
        if not text:
            return
        log.info("%s texted first (%s), %d chars", contact.id, why, len(text))
        presence.of(contact).touch()
        await self._presence_changed(contact)
        await self._deliver(contact, text, loop.time(), origin=why)
        self._release_later(contact)

    # --- calls to him -------------------------------------------------------

    async def _system(self, event, contact_id=""):
        """A moment for the Batcomputer to say out loud — the page plays it, never over a call (see system_voice)."""
        await self.broadcast({"type": "system", "event": event, "contact": contact_id or ""})

    async def host(self, what, where, at, until, invited, notes=""):
        """
        He's hosting — a party at the Manor, a dinner — and asks them: a plan (engine.plans), so it's in everyone's
        week and they answer it their own way, and the invitation in a chat of its own, where they say yes, no or
        maybe and talk about it. Returns his diary row.
        """
        from ..engine import engagements, plans
        until = until or at + engagements.LENGTH
        when = plans.when(at)
        line = (f"{what}" + (f" at {where}" if where else "") + f", {when} — I'm hosting. "
                + ("You're invited." if len(invited) == 1 else "You're all invited.") + (f" {notes}" if notes else ""))
        if len(invited) == 1:
            # One guest: asked in their own thread, not a chat of one.
            plan = plans.add({"what": what, "where": where, "at": at, "until": until, "who": invited, "host": "me"},
                             made_in="")
            await self.text(invited[0], line)
        else:
            group = group_store.create(f"{what}"[:60], invited)
            await self.broadcast({"type": "group_created", "group": self.group_payload(group)})
            plan = plans.add({"what": what, "where": where, "at": at, "until": until, "who": invited, "host": "me"},
                             made_in=f"g:{group.id}")
            await self.group_text(group.id, line)
        return next((e for e in engagements.all_engagements() if e.get("plan") == plan["id"]), None)

    async def cancel_engagement(self, engagement_id, why=""):
        """
        He calls something off from the diary — saying why, or not. An invitation: Alfred sends his regrets. A
        meeting made by letter: Alfred writes to say so, and they answer as they would. A plan with the family, or
        their evening he'd said yes to: he says so where it was made — his words, if he gave any — and they take it
        their way: the rest still go without him (unless it was his party, or just the two of them), they can ask
        what came up, and it stays with them a little (bonds.let_down — days ahead is nothing much; the last minute
        is felt; a case is a reason, to anyone who knows). His own entry just goes. Returns the row, or None.
        """
        from .. import identity as wayne_identity
        from ..engine import batman, bonds, cases, engagements, inbox, plans
        case = cases.active(batman.PILOT)
        row = engagements.cancel(engagement_id, why, case=f"{case['kind']} at {case['place']}" if case else "")
        if row is None:
            return None
        ahead = row["at"] - time.time()
        timing = 0.0 if ahead > 86400 else 1.0 if ahead > 3 * 3600 else 1.6
        said = (why or "").strip()

        def hurt(cid, what):
            if not timing:
                bonds.nudge(cid, bonds.ME, warmth=-0.3)                  # a day or more ahead: plans change
                return
            understood = bool(case) and not wayne_identity.outside(cid)
            bonds.let_down(cid, f"he called off {what}" + (" at the last minute" if timing > 1 else ""),
                           warmth=-1.5 * timing, trust=-0.5 * timing, tension=0.8 * timing, understood=understood)

        if row.get("source") in ("invitation", "mail") and row.get("thread"):
            note = inbox.call_off(row)
            if note:
                await self.broadcast({"type": "mail", "message": note, "unread": inbox.unread()})
        elif row.get("plan"):
            plan = plans.he_cant(row["plan"], said) or {}
            made = str(plan.get("made_in") or "")
            line = said or (f"I'm going to have to call off {row['what']} — something's come up. Sorry."
                            if plans.off(plan) else f"Something's come up — I can't make {row['what']}. Sorry.")
            if made.startswith("g:"):
                await self.group_text(made[2:], line)
            for cid in plan.get("who") or []:
                if self.directory.get(cid) is None or not plans.going(plan, cid):
                    continue
                if not made.startswith("g:"):
                    await self.text(cid, line)
                hurt(cid, row["what"])
        elif row.get("inviter") and self.directory.get(row["inviter"]) is not None:
            await self.text(row["inviter"], said or f"Something's come up — I can't make {row['what']} after all. Sorry.")
            hurt(row["inviter"], f"your {row['what']}")
        return row

    async def plan_moved(self, row):
        """
        He's moved a plan in his diary: said where it was made — the family chat, or each of them who's going —
        in a plain line of his, and their week moves with it (plans.move did that).
        """
        from ..engine import plans
        plan = next((p for p in plans.all_plans() if p["id"] == row.get("plan")), None)
        if plan is None:
            return
        line = (f"Change of plan — {row['what']} is now {plans.when(row['at'])}"
                + (f" at {row['where']}" if row.get("where") else "") + ".")
        made = str(plan.get("made_in") or "")
        if made.startswith("g:"):
            await self.group_text(made[2:], line)
            return
        for cid in plan.get("who") or []:
            if self.directory.get(cid) is not None and plans.going(plan, cid):
                await self.text(cid, line)

    async def answer_invite(self, engagement_id, yes, why=""):
        """
        His yes or no to one of them asking him along (engine.invites), from the diary: in the diary either way,
        and said to them in their thread — his words, or a plain yes or no — and they answer as they would.
        """
        from ..engine import engagements
        row = engagements.answer_invite(engagement_id, yes)
        if row is None:
            return None
        if self.directory.get(row.get("inviter", "")) is not None:
            await self.text(row["inviter"], (why or "").strip() or
                            ("I'll be there." if yes else f"I can't make {row['what']} — sorry."))
        return row

    def _after_called_off(self, e):
        """
        Something he called off, or turned down, with them is over: whoever went without him might say so — how it
        went, a dig, what came up, that it's fine — or let it be (session.reach_out, "letdown"). One of them, now
        and then, a little after; a two-person plan that fell through, less often.
        """
        from .. import identity as wayne_identity
        from ..engine import bonds, engagements, plans
        if e.get("plan"):
            plan = next((p for p in plans.all_plans() if p["id"] == e["plan"]), None)
            if plan is None:
                return
            fell_through = plans.off(plan)
            went = [c for c in plan.get("who") or [] if plans.going(plan, c)]
        else:
            fell_through, went = False, [e["inviter"]]
        went = [c for c in went if self.directory.get(c) is not None]
        chance = 0.3 if fell_through else 0.35 if e["status"] == "declined" else 0.6
        if not went or random.random() > chance:
            return
        contact = self.directory.get(random.choice(went))
        others = [self.directory.get(c).name for c in went if c != contact.id]
        times = bonds.letdowns(contact.id)
        said = e.get("cancel_why") or ""
        case = e.get("cancel_case") if not wayne_identity.outside(contact.id) else ""
        what = e["what"] if e.get("plan") else f"your {e['what']}"
        if fell_through:
            about = (f"{what} was meant to be tonight — he called it off" + (f" (he said: \u201c{said}\u201d)" if said else "")
                     + ", so it didn't happen; you did something else with the evening.")
        else:
            about = (f"{what} was tonight — he " + ("said he couldn't come" if e["status"] == "declined" else "called it off")
                     + (f" (he said: \u201c{said}\u201d)" if said else "") + (f" — the {engagements.lower_kind(case)}" if case else "")
                     + ", and you went without him" + (f", with {', '.join(others)}" if others else "") + ". It's over now.")
        about += (" He's let you down like this a few times lately." if times >= 3 else
                  " It's one evening — you know him, and it's not the end of anything.")
        self._spawn(self._later(random.uniform(120, 1500), self._send_unprompted(contact, about, "letdown")))

    async def _warm_sounds(self):
        """
        Now and then, off a call, the sound of where they're about to be is made ahead of anyone ringing them —
        a couple of clips at a time, within the day's allowance (ambience.warm) — so a first call there isn't quiet.
        """
        from ..audio import ambience
        now = time.time()
        if self.current_id or getattr(self, "_ringing_out", None) or now < getattr(self, "_warm_next", 0):
            return
        self._warm_next = now + random.uniform(4, 8) * 60
        made = await asyncio.to_thread(ambience.warm, list(self.directory), now)
        if made:
            log.info("sounds made ahead: %s", ", ".join(made))

    async def _write_invites(self):
        """
        Now and then one of them asks him along to something of theirs (engine.invites) — while the model's loaded
        and idle: a text from them, and a line in his diary to say yes or no to, from there or just by answering.
        """
        from ..engine import invites
        now = time.time()
        if (self._writing_lines or not self.clients or self.current_id or self._texters or self.turn_lock.locked()
                or now < self._invite_next):
            return
        self._invite_next = now + random.uniform(25, 70) * 60
        if random.random() > 0.35:
            return
        writer = self.directory.get("alfred") or next(iter(self.directory), None)
        if writer is None or not await asyncio.to_thread(_model_loaded, writer.model):
            return
        self._writing_lines = True
        try:
            found = await asyncio.to_thread(invites.make, self.directory, writer.model, writer.options, now)
        finally:
            self._writing_lines = False
        if not found:
            return
        contact, row, about = found
        log.info("%s asked him along to %s", contact.id, row["what"])
        await self.broadcast({"type": "diary"})
        self._spawn(self._send_unprompted(contact, about, "invite"))

    def _warn_of_the_hunt(self, report):
        """
        The department's turned on the masks — the Mayor's task force out, orders to bring them in on sight (see
        outcomes.hunted): him heading out to a call, Barbara at her screens or Alfred says so, once a night.
        """
        from ..engine import outcomes
        night = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 3600))
        if not outcomes.hunted() or getattr(self, "_hunt_warned", None) == night:
            return
        self._hunt_warned = night
        who = self.directory.get("batgirl") if self._at_their_screens("batgirl") else self.directory.get("alfred")
        if who is not None and who.id not in self._members():
            self._spawn(self._later(random.uniform(20, 60), self._send_unprompted(who, (
                f"he's heading out to the {report['kind'].lower()} at {report['place']} — and GCPD's task force is out "
                "tonight with orders to bring the masks in on sight; warn him, your way"), "impulse")))

    async def _diary_tick(self, now, quiet=False):
        """
        His engagements: while one's on, whether he's there (engine.engagements.watch); before one starts, the
        reminder — on the console, with the way to go — and, as often as not, Alfred's word about it. `quiet`:
        nobody at the console — his day still runs (being there, setting off, home after), but nothing that's for
        a page to show, and no reminder spent unseen.
        """
        from ..engine import batman, engagements
        engagements.watch(now)
        if not quiet:
            for e in engagements.called_off_over(now):
                self._after_called_off(e)
        # The map follows the diary: off to the next thing in time — unless he's in the middle of a case, or on his
        # way somewhere he chose — and home when the day's done.
        state = batman.state(now)
        free = not batman.committed(now) and not state.get("route")
        for e in engagements.due_to_go(now):
            if free:
                if await asyncio.to_thread(engagements.go, e["id"], now):
                    engagements.went(e["id"], now)
                    log.info("off to %s (%s), as the diary has it%s", e["what"], e["where"],
                             " — late" if now > e["at"] else "")
        if free and engagements.day_done(now) and state.get("where") != batman.HOME and \
                time.localtime(now).tm_hour < 20 and getattr(self, "_went_home", "") != time.strftime("%Y-%m-%d"):
            self._went_home = time.strftime("%Y-%m-%d")
            await asyncio.to_thread(batman.go, batman.HOME, None, None, "", now, False)
        if quiet:
            return
        # Now and then — rarely — trouble comes to the evening he's at: on the scanner, his to slip out for.
        for e in engagements.all_engagements():
            if e["status"] == "accepted" and e.get("present") and not e.get("crime_rolled") and e["at"] <= now < e["until"]:
                report = engagements.crime_at(e, now)
                if report:
                    log.info("trouble at %s: %s", e["what"], report["kind"])
                    await self.broadcast({"type": "scanner"})
                    await self.broadcast({"type": "cases"})
                    await self._system("case_trouble")
        for e in engagements.reminders(now):
            await self.broadcast({"type": "engagement_soon", "engagement": e})
            alfred = self.directory.get("alfred")
            if alfred is not None and random.random() < 0.6 and alfred.id not in self._members():
                at = time.strftime("%-I:%M %p", time.localtime(e["at"])).lower()
                where_now = batman.state(now).get("where") or "the Manor"
                self._spawn(self._send_unprompted(alfred, (
                    f"{'Batman’s ' if e.get('as') == 'batman' else 'his '}{e['what']}" + (f" at {e['where']}" if e["where"] else "")
                    + f" is at {at}, and he's at {where_now} — remind him, your way"), "impulse"))

    async def _announce(self, now):
        """
        The Batcomputer's moments nobody asked for: he's arrived, the Batwing's
        overhead waiting for him, something big has landed on the scanner, a
        breakout. Each once, as it happens — nothing from before the console came up.
        """
        from ..engine import batman, incidents, jet
        seen = self.__dict__.setdefault("_announced", {"route": None, "jet": None, "calls": None})
        route = bool(batman.state(now).get("route"))
        if seen["route"] and not route:
            await self._system("arrived")
        seen["route"] = route
        waiting = jet.state(now).get("status") == "waiting overhead for you"
        if waiting and seen["jet"] is False:
            await self._system("jet_overhead")
        seen["jet"] = waiting
        calls = incidents.at(now)
        if seen["calls"] is None:
            seen["calls"] = {r["id"] for r in calls}
            return
        for r in calls:
            if r["id"] in seen["calls"]:
                continue
            seen["calls"].add(r["id"])
            if "breakout" in (r.get("kind") or "").lower():
                await self._system("breakout")
            elif r["severity"] >= 4:
                await self._system("scanner_major")

    async def _rooms_arrive(self, now):
        """
        Someone he's on the phone to has company now — Barbara got to Cass's —
        and they're in the room: heard in the background, on the host's speaker,
        as anyone there would be. Not if the host stepped away from them.
        """
        if not self.current_id or self._ringing_out or getattr(self, "_adding", False):
            return
        # Someone on a host's speaker who isn't with them any more — off somewhere, their day moved on — says
        # goodbye and goes; the one they've left may say something about it.
        for gid, hid in list(self.call.speakerphone.items()) if self.call else []:
            host = self.directory.get(hid)
            _, company = presence.of(host).whereabouts(now) if host else ("", [])
            if host is not None and gid not in company and gid in self._members():
                await self._room_leaves(gid, host)
        hosts = [m.contact for m in self.call.members if m.contact.id not in self.call.speakerphone] if self.call \
            else [self.directory.get(self.current_id)]
        for host in [h for h in hosts if h is not None]:
            apart = self.__dict__.get("_stepped_away", {}).get(host.id, set())
            _, company = presence.of(host).whereabouts(now)
            fresh = [cid for cid in company if cid not in self._members() and cid not in apart
                     and cid not in getattr(self, "_adding", ()) and cid != self.current_id
                     and self.directory.get(cid) is not None]
            if fresh:
                log.info("%s has company now on the call: %s", host.id, ", ".join(fresh))
                self._spawn(self._bring_the_room(host, arriving=True))

    async def _room_leaves(self, gid, host):
        """A guest in the room heads off: a goodbye down the host's phone, gone from the call, maybe a word after."""
        epoch = self.interrupt()
        async with self.turn_lock:
            if not self.call or gid not in self._members() or epoch != self.turn_epoch:
                return
            member = self.sessions[gid]
            log.info("%s left the room %s is calling from", gid, host.id)
            await self.drive(self.call.farewell(member, why=f"You're heading off, leaving where {host.name} is."),
                             member.contact, epoch)
            if not self.call or member not in self.call.members:
                return
            self.call.speakerphone.pop(gid, None)
            self.call.leave(member)
            self.call.event(f"{member.contact.name} left — headed off from where {host.name} is")
            self._wrap_up(member, {gid} | set(self._members()))
            await self.broadcast(events.party(self._members(), removed=gid))
            await self.broadcast({"type": "room", "with": dict(self.call.speakerphone)})
        if self.call and host.id in self._members() and random.random() < 0.5:
            await self._react(f"{member.contact.name} has just headed off, out of where you are.", who=host.id)

    async def _sightings(self, now):
        """
        Where he'd know someone is without their telling him: Jim's car on the scanner, Jim on the news — every
        half hour or so while he's out and about, never at home; and when something big lands on GCPD's
        scanner, sometimes Jim himself goes, and that's where he is (and what he'll say he's doing).
        """
        from ..engine import incidents
        considered = self.__dict__.setdefault("_scenes_considered", set())
        for contact in self.directory:
            rule = getattr(contact, "sightings", None) or {}
            if not rule:
                continue
            whereabouts = presence.of(contact)
            scene = rule.get("scene") or {}
            if scene:
                worst = [r for r in incidents.at(now) if r["id"] not in considered and now - r["at"] < 15 * 60
                         and r.get("status") != "resolved" and r["severity"] >= scene.get("severity", 4)]
                considered.update(r["id"] for r in worst)
                asleep = whereabouts.now(now)["status"] == presence.OFFLINE
                if worst and random.random() < scene.get("chance", 0.5) * (0.4 if asleep else 1.0):
                    report = max(worst, key=lambda r: (r["severity"], r["at"]))
                    whereabouts.set_activity(f"on scene at {report['place']} — {report['kind'].lower()}",
                                             presence.BUSY, scene.get("minutes", 75), t=now,
                                             where=report["place"], owner="scene")
                    whereabouts.seen_at(report["place"], scene.get("how", "on scene"), now)
                    await self._presence_changed(contact)
                    continue
            where, company = whereabouts.whereabouts(now)
            # With someone on the family's app — Barbara at his for Sunday lunch — he's where her dot is.
            beside = next((self.directory.get(c) for c in company if self.directory.get(c) is not None
                           and getattr(self.directory.get(c), "shares_location", True)
                           and getattr(self.directory.get(c), "family_app", True)), None)
            if beside is not None and where:
                seen = whereabouts.get("seen") or {}
                if seen.get("where") != (places.resolve(where) or {}).get("name") or now - seen.get("at", 0) > 600:
                    whereabouts.seen_at(where, f"with {beside.name} — her dot's there" if beside.pronoun == "she"
                                        else f"with {beside.name} — their dot's there", now)
                    await self._presence_changed(contact)
                continue
            home = (getattr(contact, "home", "") or "").lower()
            if not where or where.lower() == home or where.lower().startswith("home"):
                continue
            seen = whereabouts.get("seen") or {}
            moved = seen.get("where") != (places.resolve(where) or {}).get("name")
            stale = now - seen.get("at", 0) > rule.get("every", 1800) * random.uniform(0.8, 1.6)
            if stale or (moved and random.random() < 0.3):
                how = (rule.get("how") or {})
                whereabouts.seen_at(where, how.get(where) or how.get("default") or "seen", now)
                await self._presence_changed(contact)

    async def _calls_in(self, now):
        """
        The ones who'd ring him because of the night itself — Jim, when something
        big lands on GCPD's scanner and nobody's on it yet: what the signal would
        say, from his desk. Once per call off the scanner, at the odds his profile
        gives, never twice inside his gap, and never over a call already going.
        """
        from ..engine import cases, incidents
        if self.current_id or self._incoming or self._ringing_out or getattr(self, "_adding", False):
            return
        called = self.__dict__.setdefault("_called_in", {})
        heard = self.__dict__.setdefault("_called_about", set())
        for contact in self.directory:
            rule = getattr(contact, "calls_in", None) or {}
            if not rule or now - called.get(contact.id, 0) < rule.get("gap", 120) * 60:
                continue
            fresh = [r for r in incidents.at(now)
                     if r["id"] not in heard and now - r["at"] < 20 * 60 and r.get("status") != "resolved"
                     and cases.for_report(r["id"]) is None
                     and (r["severity"] >= rule.get("severity", 4)
                          or (rule.get("breakouts") and "breakout" in (r.get("kind") or "").lower()))]
            if not fresh:
                continue
            worst = max(fresh, key=lambda r: (r["severity"], r["at"]))
            heard.update(r["id"] for r in fresh)
            if random.random() >= rule.get("chance", 0.5):
                continue
            called[contact.id] = now
            toll = incidents.toll_text(worst.get("toll") or {})
            about = (f"{worst['kind'].lower()} at {worst['place']} ({worst['area']})"
                     + (f", {worst['suspect']} behind it" if worst.get("suspect") else "")
                     + (f" — {toll}" if toll else "") + f"; GCPD's status: {worst['status']}. You want him on it")
            log.info("%s calling in about %s", contact.id, worst["id"])
            return await self._ring(contact, about)

    async def _ring(self, contact, about):
        """They call him. The page rings; he answers, declines, or misses it — or, muted, it never rings at all."""
        self._incoming = {"id": contact.id, "about": about}
        if calls_muted(contact.id):
            log.info("%s rang him, silenced", contact.id)
            return await self._unanswered("missed")
        log.info("%s ringing him", contact.id)
        await self.broadcast({"type": "call_incoming", "speaker": contact.id})
        self._ring_timer = self._spawn(self._ring_out(contact.id))

    async def _ring_out(self, contact_id):
        await asyncio.sleep(RING_FOR)
        if self._incoming and self._incoming["id"] == contact_id:
            self._ring_timer = None
            await self._unanswered("missed")

    async def answer(self, contact_id):
        incoming = self._incoming
        if not incoming or incoming["id"] != contact_id:
            # Too late — it had already rung out. Tell the page, which is
            # otherwise left waiting on a line that isn't there.
            await self.broadcast({"type": "call_unanswered", "speaker": contact_id,
                                  "how": "missed", "message": None})
            return
        self._incoming = None
        if self._ring_timer:
            self._ring_timer.cancel()
            self._ring_timer = None
        await self.connect(contact_id, incoming=incoming["about"])

    async def decline(self, contact_id):
        if self._incoming and self._incoming["id"] == contact_id:
            if self._ring_timer:
                self._ring_timer.cancel()
                self._ring_timer = None
            await self._unanswered("declined")

    async def _unanswered(self, how):
        """A declined or missed call goes in the thread — and they may text instead."""
        incoming, self._incoming = self._incoming, None
        contact = self.directory.get(incoming["id"]) if incoming else None
        if contact is None:
            return
        log_ = TextLog(contact.id)
        entry = log_.add("them", "", kind=f"{how}_call")
        await self.broadcast({"type": "call_unanswered", "speaker": contact.id,
                              "how": how, "message": entry})
        # He didn't take it: once is nothing much; again today, it stings (engine.bonds).
        today = sum(1 for m in log_.page(limit=40) if m.get("kind") == f"{how}_call" and time.time() - m["at"] < 86400)
        bonds.turned_away(contact, how, today)
        if random.random() < (contact.initiative or {}).get("react", 0.6):
            await asyncio.sleep(random.uniform(6, 30) if how == "declined" else random.uniform(20, 80))
            await self._send_unprompted(contact, incoming["about"], how)

    async def nudge(self, kind="check_in"):
        """
        Break a long silence. Not a notification — a turn like any other, so
        what he says is generated in character and differs every time. It is
        never stored in history: a check-in that went unanswered shouldn't
        become part of what he remembers of the conversation.
        """
        if not self.current_id or self._ringing_out:
            return
        if self._adding:
            # Someone's being rung in: nobody asks if he's still there while the
            # line rings — they wait for the pick-up, or say something about it.
            return await self.broadcast(events.turn_complete())
        if self.call and self.call.is_group:
            if kind == "sign_off":
                # The quiet's run its course: the page rings off on this.
                return await self.broadcast(events.turn_complete())
            return await self._lull()
        # The epoch is taken before waiting, as submit does: bumping it after
        # the wait cancelled whatever he'd said in the meantime, and his words
        # were replaced by a check-in.
        epoch = self.interrupt()
        async with self.turn_lock:
            if epoch != self.turn_epoch or not self.current_id:
                return
            await self.drive(getattr(self.session_for(self.current_id), kind)(), self.contact, epoch)

    async def _lull(self):
        """
        A pause on a group call (the page says when: it knows when the last
        voice stopped). Someone may fill it — the chatty likelier, not whoever
        just spoke — at odds that fall with each line said into the quiet, so a
        call he's gone silent on drifts off rather than carrying on without him.
        """
        self._lulls += 1
        # While he's in the conversation, they don't run it without him: a line or two into a pause, then they leave
        # him room — a call where the two of them volleyed jabs for twenty lines was no call at all. Quiet for a
        # while, they carry it a little further, then let it drift.
        engaged = time.time() - getattr(self, "_him_at", 0) < 30
        most = 2 if engaged else min(4, MOST_UNPROMPTED)
        if (self.call.waiting_on_him() or self._lulls > most
                or random.random() >= self._lull_energy):
            # Nobody fills it — he's just been asked something and they give him a
            # moment, or the call's run down — and the page, waiting to hear, is
            # told so, or its timer stopped and the call never drifted to an end.
            return await self.broadcast(events.turn_complete())
        members = [m for m in self.call.members if m.contact.id not in self.call.muted]
        if not members:
            return await self.broadcast(events.turn_complete())
        weights = [(0.25 + (m.contact.initiative or {}).get("per_day", 0.5) * 0.4)
                   * (1 - (m.contact.texting_pace or {}).get("on_read", 0) * 0.7)
                   * (0.4 if m is self.call.last_speaker else 1.0)
                   # Whoever filled the last pause leaves the next to someone else, mostly.
                   * (0.25 if m.contact.id == self._last_lull else 1.0)
                   # In the background on someone's speaker: there, not running the call.
                   * (ROOM_GUEST if m.contact.id in self.call.speakerphone else 1.0) for m in members]
        member = random.choices(members, weights=weights)[0]
        # Now and then two of them go for the same pause at once.
        rest = [(m, w) for m, w in zip(members, weights, strict=True) if m is not member]
        second = (random.choices([m for m, _ in rest], weights=[w for _, w in rest])[0]
                  if rest and not engaged and random.random() < TALK_OVER else None)
        epoch = self.turn_epoch
        async with self.turn_lock:
            if epoch != self.turn_epoch or not self.call or member not in self.call.members:
                return await self.broadcast(events.turn_complete())
            turn = (self.call.collide(member, second, self._lulls)
                    if second is not None and second in self.call.members
                    else self.call.lull(member, self._lulls))
            line = await self._drive_for(turn, member.contact, epoch)
        if line:
            self._last_lull = member.contact.id
        self._lull_energy *= LULL_DECAY if line else 0.7
        await self._hang_ups()
        await self._call_moves()
        await self._call_adds()

    async def _drive_for(self, generator, contact, epoch):
        """Drive a generator and hand back what it returned."""
        result = {}

        def keep():
            result["value"] = yield from generator
        await self.drive(keep(), contact, epoch)
        return result.get("value")

    async def submit(self, text, spoken=False, confidence=1.0, talked_over="", overlapped=None, cut_in=False):
        """
        Run one turn. `spoken` marks input that came from a microphone, which is
        the only kind that can be an acoustic echo — typed text never is.
        """
        text = (text or "").strip()
        if not text or not self.current_id or self._ringing_out == self.current_id:
            return      # nobody on the line — it's ringing, and they won't answer
        # Only a take their voice could have got into can be their voice coming back (the page knows;
        # an older page that doesn't say is checked as before).
        if spoken and overlapped is not False and self._is_own_echo(text):
            await self.broadcast(events.state(events.IDLE))      # not left on "one moment" for nothing
            return
        muted = self.__dict__.get("_they_muted", {}).get(self.current_id)
        if muted and not (self.call and self.call.is_group):
            # They've muted themselves: they hear him, they'll answer when they're back.
            muted["missed"].append(text[:200])
            await self.broadcast(events.state(events.IDLE))
            return
        wanted = self._asked_to_add(text) if self.call else None
        if self.call:
            leaving = self._asked_to_drop(text)
            if leaving:
                return await self.drop(leaving)

        # Supersede first, then queue: the running turn sees a stale epoch,
        # stops, and releases the lock instead of making the new input wait for
        # a reply nobody is listening to any more.
        was_speaking = self.voice_busy
        self._lull_energy, self._lulls = 1.0, 0
        self._him_at = time.time()
        # Live mic: he paused mid-thought and they came in on it — theirs to know, so it sounds like a cut-in.
        for member in (self.call.members if self.call else []):
            member._came_in = cut_in
        if getattr(self.call, "comms", None):
            self._comms_record("me", text)
        epoch = self.interrupt()
        async with self.turn_lock:
            if epoch != self.turn_epoch:
                return             # something newer arrived while we waited
            contact = self.contact
            group = bool(self.call and self.call.is_group)
            if group:
                turn = self.call.turn(text, interrupted=was_speaking, confidence=confidence,
                                      talked_over=talked_over)
            else:
                session = self.session_for(self.current_id)
                turn = session.ask(text, interrupted=was_speaking, confidence=confidence)
            await self.drive(turn, contact, epoch)
        if group:
            await self._hang_ups()
        await self._call_moves()
        await self._call_adds()
        if wanted and self.call and wanted not in self._members():
            # He asked for them: whoever he asked has answered him; now it rings.
            await self.add(wanted)
        for member in (self.call.members if self.call else []):
            taking = getattr(member, "_take_case", None)
            if taking:
                member._take_case = None
                self._spawn(self._take_by_name(member.contact, taking))
            going = getattr(member, "_join", None)
            if going:
                member._join = None
                self._spawn(self._join_by_name(member.contact, going))
            await self._use_tools(member, "call")

    def _asked_to_add(self, text):
        """
        'Alfred, get Lucius on the line' — a contact not on the call, asked for
        plainly: ringing someone in, by name, onto the line. "Did Dick call you?"
        and "get me what Barbara found" aren't, and nor is "don't add Tim".
        """
        if re.search(r"(?i)\b(don'?t|do not|no need to|never mind)\b", text):
            return None
        if text.rstrip().endswith("?") and not re.match(r"(?i)\W*(\w+,\s*)?(can|could|would|will) you\b", text):
            return None
        for contact in self.directory:
            if contact.id in self._members():
                continue
            for n in {contact.name, contact.full_name}:
                name = re.escape(n)
                # On a call, "call Tim", "add Tim", "get Tim on", "bring Tim in",
                # "loop Tim in", "conference in Tim" — all ring him in. Not "did Tim
                # call?" or "Tim called" — and not "let me call Tim" or "I'll ring Tim later":
                # that's him doing it himself, when he's ready, not asking them to.
                ask = re.search(rf"(?i)\b(call|ring|phone|get|grab|bring|add|patch|loop|pull|conference)\b(?:\s+in)?"
                                rf"[^.?!]{{0,18}}\b{name}\b", text)
                if (ask and not _his_own_doing(text[:ask.start()])
                        and not re.search(rf"(?i)\b(did|has|have|was)\s+{name}\s+(call|ring|phone)|\b{name}\s+"
                                          rf"(called|rang|phoned)\b", text)):
                    return contact.id
                on = re.search(rf"(?i)\b{name}\b[^.?!]{{0,24}}\b(on|onto|into|in on)\s+(the |this )?(line|call)\b",
                               text)
                if on and not _his_own_doing(text[:on.start()]):
                    return contact.id
        return None

    def _asked_to_drop(self, text):
        """'Selina, you can drop off' — someone on the call, let go."""
        if not self.call or not self.call.is_group:
            return None
        if re.search(r"(?i)\b(don'?t|do not|no need to|not yet)\b", text) or text.rstrip().endswith("?"):
            return None         # "Tim, don't hang up yet" keeps Tim
        if not re.search(r"\b(drop (off|out)|drop|you can go|hang up|leave the call|let (her|him|them) go|sign off|"
                         r"head off|kick|remove|boot|get off the (line|call)|take (her|him|them) off|cut)\b", text, re.I):
            return None
        named = self.call.addressed(text)
        return named[0].contact.id if len(named) == 1 else None


console = Console()


# The reactions a phone offers at a tap, sent alone as a text when asked to react.
_TAPBACKS = {"👍", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿", "❤️", "😂", "‼️", "❓", "👎"}


_HIS_OWN = re.compile(
    r"(?i)\b(let me|lemme|let'?s|let us|i'?ll|i will|i'?m|i am|i'?d|i (?:need|have|want|wanna|gotta|got|ought|should|"
    r"could|can|might|may|must|guess|think|reckon|better)|we'?ll|we will|we'?re|we (?:need|have|should|could|can|might|"
    r"must))\b")


def _his_own_doing(before):
    """
    Whether what comes before "call Tim" makes it his own plan — "let me call Tim", "I'll ring Tim after this",
    "we should get Tim on" — rather than asking whoever's on the line to: "Alfred, call Tim", "can you get Tim
    on", "I need you to patch Tim in".
    """
    clause = re.split(r"[.?!;,:—–]", before)[-1]
    own = _HIS_OWN.search(clause)
    return bool(own) and not re.search(r"(?i)\byou\b", clause[own.end():])


def _quoting(thread, batch, said):
    """
    Which of his texts their reply quotes — the one of `batch` the words in
    [reply: …] are from. None if none is, or it's his last text anyway.
    """
    target = quoted_by(batch, said) if said else None
    last = thread.last()
    return target["id"] if target and last and last["id"] != target["id"] else None


def _just_a_typo(before, after):
    """Whether an edit only fixed the spelling — nothing anyone would answer."""
    import difflib
    return difflib.SequenceMatcher(None, before.lower(), after.lower()).ratio() >= 0.88


def _as_read(message):
    """One of his texts as they read it — with the one it answers, when he replied to a particular one."""
    quoted = message.get("reply_to")
    if not quoted:
        return message["text"]
    whose = "your" if quoted["from"] == "them" else "his own earlier"
    return f'(replying to {whose} text: "{quoted["text"]}") {message["text"]}'


def _clock(hours):
    return f"{int(hours) % 24:02d}:{int(round(hours % 1 * 60)) % 60:02d}"


def _model_loaded(model):
    """Whether Ollama has this model in memory right now."""
    try:
        import ollama
        return any(m.get("model") == model or m.get("name") == model
                   for m in ollama.ps().get("models", []))
    except Exception:
        return False


def _release_model(model):
    try:
        import ollama
        ollama.generate(model=model, prompt="", keep_alive=0)
    except Exception:
        pass


def _warm_model():
    """
    Nudge the default contact's model into memory while the operator is still
    reading the boot screen. Loading a 14B costs around 25 seconds, and paying
    that after they have already spoken is the difference between a console and
    a progress bar. Failures are silent: this is an optimisation, not a step.

    The contact's own options are passed rather than a bare `num_predict`.
    Ollama keys a resident model on its context size, so warming at the default
    4096 and then asking at 8192 unloads and reloads it — the first real turn
    paid fifteen seconds, and the warm-up was the reason.

    It sends the real prefix — persona, directives, samples and the history
    window — rather than a single character. Loading the weights is only half
    the cold start; the other half is reading a couple of thousand tokens of
    prompt, and on a model that caches its prefix this pays for that before
    anyone has spoken instead of on the first reply.
    """
    if console.directory.get(config.DEFAULT_CONTACT) is None:
        return
    try:
        console.session_for(config.DEFAULT_CONTACT).warm()
    except Exception:
        pass


def _warm_speech():
    """
    Load and exercise the Whisper model before anyone speaks.

    Resolving the backend and decoding the first clip costs about two seconds;
    every clip after that takes a tenth of one. Left to happen on demand, that
    two seconds lands on the first thing the operator ever says — which is
    exactly when the console is being judged, and it reads as a slow microphone
    rather than a one-off load. A short buffer of silence is enough to force the
    whole path: backend resolution, weights, and the first decode.
    """
    try:
        import numpy as np

        from ..audio import stt
        stt.transcribe_audio(np.zeros(stt.SAMPLE_RATE, dtype=np.float32))
    except Exception:
        pass


def _daily_upkeep():
    from ..store import upkeep
    upkeep.daily()


def _warm_store():
    """The day's upkeep, then the search index recall reads — off the loop, before anyone asks."""
    from ..store import index, upkeep
    try:
        upkeep.daily()
        index.warm()
    except Exception:
        logging.getLogger("wayne").exception("warming the store failed")


@asynccontextmanager
async def lifespan(_app):
    warm = [
        asyncio.create_task(asyncio.to_thread(_warm_model)),
        asyncio.create_task(asyncio.to_thread(_warm_speech)),
        asyncio.create_task(asyncio.to_thread(world.prime)),
        asyncio.create_task(asyncio.to_thread(_warm_store)),
        asyncio.create_task(asyncio.to_thread(
            system_voice.prime, console.voice, list(console.directory))),
        asyncio.create_task(console.pulse()),
    ]
    yield
    for task in warm:
        task.cancel()
    store.close_all()           # checkpoints the write-ahead log into the database file


app = FastAPI(title="WayneTech Console", docs_url=None, redoc_url=None,
              lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _contact_payload(contact):
    """What the page is told about a contact — who they are, not what runs them."""
    return {
        "id": contact.id,
        "name": contact.name,
        "full_name": contact.full_name,
        "role": contact.role,
        "tagline": contact.tagline,
        "accent": contact.accent,
        "pronoun": contact.pronoun,
        "available": contact.availability.is_available(),
        "portrait": codex.frame_for(contact),
        "portrait_v": codex.portrait_version(contact.id),
        "group": contact.group,
        "presence": presence.of(contact).public(),
        # Who they are to someone who knows only masks, whether they're on the family's app, and what they know
        # of him (see wayne.identity) — the Codex shows it, and lets him change it.
        "mask": contact.mask,
        "family_app": contact.family_app,
        "knows": wayne_identity.level(contact.id),
        # Where they live, by its place on the map: his to find ("Jim" finds Jim's apartment), nobody else's.
        "home": contact.home,
        # In live mic, how short a pause they'll come in on — Jason's breath, Alfred's full stop (milliseconds).
        "patience": contact.patience,
        # Where things stand between them, in words — never a number: what he'd sense, not a score.
        "standing": _standing_payload(contact),
    }


def _standing_payload(contact):
    overall, sore, romance = bonds.standing(contact.id, bonds.ME)
    lately = [e["text"] for e in bonds.memories(contact.id, bonds.ME, most=2)]
    out = {"overall": overall, "sore": sore, "romance": romance, "lately": lately}
    if getattr(contact, "sees_work", "") == "gcpd":
        out["department"] = bonds.standing(bonds.GCPD, bonds.ME)[0]
    return out


def _asset_version():
    """
    A token that changes whenever any front-end file does.

    The console runs in a Chrome window with its own persistent profile, which
    caches `/static/js/app.js` by URL and keeps serving the old one after an
    edit — so a code change appeared not to take, and the obvious next move is
    to rebuild the .app, which changes nothing because the bundle is only a
    launcher. Stamping the URLs makes an edit take effect on reload, full stop.

    Recomputed per request rather than at import: during development the whole
    point is that the file just changed, and the cost is a handful of stats.
    """
    stamp = max(
        (path.stat().st_mtime for path in WEB_DIR.rglob("*")
         if path.is_file() and path.suffix in (".js", ".css")),
        default=0.0,
    )
    return f"{stamp:.0f}"


@app.get("/")
async def index():
    # Rewritten in memory; index.html on disk stays clean and directly openable.
    html = (WEB_DIR / "index.html").read_text()
    html = re.sub(r'(/static/[\w./-]+\.(?:js|css))"',
                  rf'\1?v={_asset_version()}"', html)
    return Response(content=html, media_type="text/html",
                    headers={"Cache-Control": "no-store"})


def _restart_process():
    """
    The console started again in place — the same command, a fresh process — so everything's read anew from
    disk (and a reset asked for in Settings is done before anything opens the database: see wayne.store.reset).
    """
    import os
    import sys
    log.info("restarting the console")
    try:
        store.close_all()
    except Exception:
        pass
    os.environ["WAYNE_RESTARTED"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


@app.post("/api/admin/restart")
async def admin_restart():
    """Settings: restart the console. Nothing's lost; the page reconnects when it's back."""
    asyncio.get_running_loop().call_later(0.5, _restart_process)
    return JSONResponse({"ok": True})


@app.post("/api/admin/reset")
async def admin_reset(request: Request):
    """
    Settings: start over — "all", "chats" or "world". Written down, the console restarts, and the reset is done as
    it comes back up, with a backup first (see wayne.store.reset).
    """
    from ..store import reset
    body = await request.json()
    scope = str(body.get("scope", "all"))
    if scope not in reset.SCOPES:
        return JSONResponse({"error": "no such reset"}, status_code=400)
    reset.request(scope)
    log.info("reset (%s) asked for from Settings", scope)
    asyncio.get_running_loop().call_later(0.5, _restart_process)
    return JSONResponse({"ok": True, "scope": scope})


@app.get("/api/session")
async def session_info():
    """
    What the console needs to render itself.

    Deliberately spare. Which model answers, which speech backend is loaded and
    which synthesiser speaks are all facts about the machinery, and the console
    is not meant to feel like machinery — so they are not sent to the page at
    all. Resolving the speech backend also costs a model load, which is a poor
    thing to pay for a readout nobody needs.
    """
    from ..store import reset
    return JSONResponse({
        "operator": wayne_operator.full_name(),
        # A fresh start from Settings, said once: what went, and where the old data is.
        "reset_done": reset.done(),
        # His own face and framing, from the Codex — his marker on the map.
        "operator_portrait": codex.portrait_of("bruce"),
        "operator_frame": codex.frames().get(codex.frame_key("bruce")) or {},
        "contacts": [_contact_payload(c) for c in console.directory],
        "mutes": mutes(),
        "mail_unread": _mail_unread(),
        "current": console.current_id,
        "default": config.DEFAULT_CONTACT,
        # Gotham, for the little map on a status card.
        "map": {**{k: v for k, v in places.gazetteer().items() if k in ("places", "regions", "areas")},
                # When the map was last built: its files are fetched with it, so a rebuilt
                # city is never the browser's cached old one.
                "version": f"{(WEB_DIR / 'map' / 'gotham.geojson').stat().st_mtime:.0f}"
                if (WEB_DIR / "map" / "gotham.geojson").exists() else "0"},
    })


def _mail_unread():
    from ..engine import inbox
    return inbox.unread()


def mutes(now=None):
    """
    The threads he's muted: {thread: {"messages": until (0 none, -1 for good), "calls": bool}} —
    a contact's id, or "g:<group>". Lapsed mutes are left out. Kept in the space "mutes", a row a thread.
    """
    now = now or time.time()
    kept = store.space("mutes").dict()
    out = {}
    for thread, m in kept.items():
        until = m.get("messages", 0)
        live = {"messages": until if (until == -1 or until > now) else 0, "calls": bool(m.get("calls"))}
        if live["messages"] or live["calls"]:
            out[thread] = live
    return out


def calls_muted(contact_id):
    return bool((store.space("mutes").get(contact_id) or {}).get("calls"))


def _load_pins():
    """His pins on the map — the space "pins", a row a pin."""
    return store.space("pins").list()


@app.get("/api/mutes")
async def get_mutes():
    return JSONResponse({"mutes": mutes()})


@app.post("/api/mutes")
async def set_mute(request: Request):
    """Mute a thread's messages (an hour, till morning, for good — or not), and a contact's calls."""
    body = await request.json()
    thread = str(body.get("thread") or "")[:60]
    if not thread:
        return JSONResponse({"error": "which thread?"}, status_code=400)
    kept = mutes()
    entry = dict(kept.get(thread) or {"messages": 0, "calls": False})
    if "messages" in body:
        how, now = str(body["messages"]), time.time()
        if how == "hour":
            entry["messages"] = now + 3600
        elif how == "morning":
            local = time.localtime(now)
            morning = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 8, 0, 0, 0, 0, -1))
            entry["messages"] = morning if morning > now + 600 else morning + 86400
        elif how == "always":
            entry["messages"] = -1
        else:
            entry["messages"] = 0
    if "calls" in body:
        entry["calls"] = bool(body["calls"]) and not thread.startswith("g:")
    kept[thread] = entry
    store.space("mutes").replace({k: v for k, v in kept.items() if v.get("messages") or v.get("calls")})
    return JSONResponse({"mutes": mutes()})


@app.get("/api/map/pins")
async def map_pins():
    """His own pins on the map."""
    return JSONResponse({"pins": _load_pins()})


@app.post("/api/map/pins")
async def save_pin(request: Request):
    """Drop a pin, or rename or move one he dropped."""
    body = await request.json()
    try:
        x, y = float(body.get("x")), float(body.get("y"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "where?"}, status_code=400)
    pin = {"id": str(body.get("id") or uuid.uuid4().hex[:10]), "label": str(body.get("label") or "Pin")[:40],
           "x": max(-20.0, min(120.0, x)), "y": max(-20.0, min(120.0, y)), "at": time.time()}
    with store.get().tx():
        space = store.space("pins")
        space.drop(pin["id"])           # moved or renamed: it goes to the end, as the newest
        space.put(pin["id"], pin)
        space.trim(200)
    return JSONResponse({"pin": pin})


@app.delete("/api/map/pins/{pin_id}")
async def delete_pin(pin_id: str):
    store.space("pins").drop(pin_id)
    return JSONResponse({"ok": True})


@app.get("/api/map/incidents")
async def map_incidents():
    """What the scanner says is happening in the city right now — who's on what, and where the jet is."""
    # Worked out off the event loop: a quarter of a second of drawing the night's calls, every twenty
    # seconds per window and on every broadcast, was a quarter second a call's voice waited behind.
    return JSONResponse(await asyncio.to_thread(_map_incidents, time.time()))


def _map_incidents(now):
    from ..engine import cases, incidents, jet, outcomes
    by_id = {c["id"]: c for c in cases.board()}
    reports = []

    def with_case(r):
        case = by_id.get(r["id"])
        if not case:
            return r
        out = {**r, "assignee": case["assignee"], "team": cases.team(case), "case": case["status"],
               "outcome": case.get("outcome", ""), "result": (case.get("result") or {}).get("how", ""),
               "ok": (case.get("result") or {}).get("ok"), "grade": (case.get("result") or {}).get("grade", ""),
               **({"done": True, "done_at": case.get("closed_at", now)} if case["status"] == "closed" else {})}
        if case["status"] != "closed":
            # How far along it is, and each of them: on scene, or how far out.
            began = case.get("opened_at", now)
            out["phase"] = cases.phase(case, now)[0]
            out["progress"] = round(max(0.0, min(1.0, (now - began) / max(60, case.get("due", now) - began))), 3)
            out["due"] = case.get("due")
            out["members"] = {m: {"status": v.get("status"),
                                  "eta": max(0, round(v.get("joined", now) + v.get("travel", 9) * 60 - now))}
                              for m, v in (case.get("members") or {}).items()}
        return out
    for r in incidents.at(now):
        reports.append(with_case(r))
    # Tonight's that are over stay on the map a while, faded: cleared by GCPD, or the family's, closed.
    shown = {r["id"] for r in reports}
    for r in incidents.ended(now):
        if r["id"] in shown:
            continue
        shown.add(r["id"])
        item = with_case(r)
        if item.get("case") and item["case"] != "closed":
            # GCPD have cleared off, but the family are still on it: it isn't over till they are.
            reports.append({**item, "status": "contained"})
        elif not item.get("case"):
            # Nobody from the family on it: how GCPD did on their own.
            ended = outcomes.gcpd(r)
            reports.append({**item, "done": True, "ok": ended["ok"], "grade": ended["grade"], "result": ended["how"]})
        else:
            reports.append({**item, "done": True})
    # The family's own endings the scanner has lost — a getaway gone clean away, a breakout ended in a
    # cell — still belong to tonight: the most dramatic ones were the ones that vanished.
    tonight = incidents.night_began(now)
    for case in by_id.values():
        if case["id"] in shown or case["status"] != "closed" or case.get("closed_at", 0) < tonight:
            continue
        reports.append(with_case({"id": case["id"], "kind": case["kind"], "severity": case.get("severity", 2),
                                  "place": case["place"], "area": case.get("area", ""), "x": case["x"], "y": case["y"],
                                  "at": case.get("began", case.get("opened_at", now)), "status": "resolved",
                                  "suspect": case.get("suspect", ""), "dispatch": case.get("dispatch", "")}))
    return {"incidents": reports, "jet": jet.state()}


@app.get("/api/cases")
async def case_board():
    from ..engine import cases
    return JSONResponse({"cases": cases.board()})


@app.post("/api/cases/assign")
async def assign_case(request: Request):
    """He puts someone on a report, from the map."""
    from ..engine import batman, cases
    body = await request.json()
    report_id, contact_id = str(body.get("report", "")), str(body.get("contact", ""))
    # Busy is busy: on one already, they're pulled off it first — the map won't offer them, and nor will this.
    if contact_id == "bruce":
        why = batman.committed()
        if why:
            return JSONResponse({"error": why}, status_code=409)
    else:
        current = cases.active(contact_id)
        if current and current["id"] != report_id and current["status"] != "closed":
            found = console.directory.get(contact_id)
            there = ((current.get("members") or {}).get(contact_id) or {}).get("status") == "on scene"
            return JSONResponse({"error": f"{found.name if found else contact_id} is "
                                          f"{'in the middle of' if there else 'on the way to'} the "
                                          f"{current['kind'].lower()} at {current['place']} — pull them off it first"},
                                status_code=409)
    result = await console.assign_case(report_id, contact_id, by="him")
    if not result:
        return JSONResponse({"error": "can't assign that"}, status_code=400)
    return JSONResponse(result if ("texted" in result or "declined" in result) else {"case": result})


def _with_diary(messages):
    """
    Each letter with the evening it put in his diary, as the diary has it now — called off, changed, gone to,
    missed — not as it was when the letter was answered: the diary is the one truth for that.
    """
    from ..engine import engagements
    rows = {e["id"]: e for e in engagements.all_engagements()}
    out = []
    for m in messages:
        wanted = ((m.get("engagement") or {}).get("id") or "") if isinstance(m.get("engagement"), dict) else ""
        row = rows.get(wanted)
        if wanted:
            m = {**m, "diary": {"status": row["status"] if row else "gone", "at": row["at"] if row else None}}
        out.append(m)
    return out


@app.get("/api/mail")
async def get_mail(folder: str = "inbox"):
    """His mail, a folder at a time, newest first — and how much is unread."""
    from ..engine import inbox
    return JSONResponse({"mail": _with_diary(inbox.mail(folder if folder in inbox.FOLDERS else "inbox")),
                         "unread": inbox.unread(),
                         "counts": {f: sum(1 for m in inbox.mail(f) if not m.get("read")) for f in inbox.FOLDERS}})


@app.get("/api/mail/thread/{thread_id}")
async def mail_thread(thread_id: str):
    """A conversation, oldest first — what they wrote and what he answered — and whether the last can be answered."""
    from ..engine import inbox
    found = _with_diary(inbox.thread(thread_id))
    return JSONResponse({"thread": found, "answerable": [m["id"] for m in found if inbox.answerable(m)]})


@app.get("/api/mail/{message_id}/suggest")
async def suggest_mail(message_id: str):
    """A few ways he might answer — drafts to pick from and change, in his voice. Written by Alfred's model."""
    from ..engine import inbox
    writer = console.directory.get("alfred") or next(iter(console.directory), None)
    if writer is None:
        return JSONResponse({"options": []})
    picks = await asyncio.to_thread(inbox.suggest, message_id, writer.model, writer.options)
    return JSONResponse({"options": picks})


@app.post("/api/mail/{message_id}/reply")
async def reply_mail(message_id: str, request: Request):
    """He writes back: his reply in Sent, in the thread; theirs comes when they'd write it."""
    from ..engine import inbox
    body = await request.json()
    mine = inbox.reply(message_id, str(body.get("body", "")))
    if mine is None:
        return JSONResponse({"error": "that one can't be answered"}, status_code=400)
    return JSONResponse({"message": mine, "unread": inbox.unread()})


def _diary_when(date, clock):
    """'2026-10-16' and '19:30' as a moment, in local time — or None."""
    import datetime as _dt
    try:
        day = _dt.date.fromisoformat(str(date))
        hh, mm = (int(x) for x in str(clock or "20:00").split(":")[:2])
    except (ValueError, TypeError):
        return None
    return time.mktime((day.year, day.month, day.day, max(0, min(23, hh)), max(0, min(59, mm)), 0, 0, 0, -1))


@app.get("/api/diary")
async def diary(start: str = "", days: int = 14):
    """His diary, a day at a time — and the places he could write in, for the form."""
    import datetime as _dt

    from ..engine import engagements
    try:
        first = _dt.date.fromisoformat(start) if start else _dt.date.today() - _dt.timedelta(days=3)
    except ValueError:
        first = _dt.date.today() - _dt.timedelta(days=3)
    names = sorted({p["name"] for p in places.gazetteer().get("places", []) if p.get("kind") != "district"}
                   | {v["name"] for v in places.venues()})
    return JSONResponse({"days": engagements.days(first, max(1, min(days, 31))), "places": names,
                         "today": _dt.date.today().isoformat()})


@app.post("/api/diary")
async def diary_add(request: Request):
    """He writes something in his diary himself."""
    from ..engine import engagements
    body = await request.json()
    at = _diary_when(body.get("date"), body.get("from"))
    if at is None or not str(body.get("what") or "").strip():
        return JSONResponse({"error": "what and when, at least"}, status_code=400)
    until = _diary_when(body.get("date"), body.get("to")) if body.get("to") else None
    if until is not None and until <= at:
        until += 86400                    # past midnight
    invited = [c.id for c in (console.directory.find(n) for n in (body.get("with") or [])) if c] \
        if isinstance(body.get("with"), list) else []
    if body.get("host") and invited:
        row = await console.host(body.get("what"), body.get("where", ""), at, until, invited, body.get("notes", ""))
        return JSONResponse({"entry": engagements.entry(row)})
    row = engagements.add(body.get("what"), body.get("where", ""), at, until, body.get("as", "bruce"),
                          ", ".join(body.get("with") or []) if isinstance(body.get("with"), list) else body.get("with", ""),
                          body.get("notes", ""))
    return JSONResponse({"entry": engagements.entry(row)})


@app.post("/api/diary/{engagement_id}")
async def diary_edit(engagement_id: str, request: Request):
    """He changes something still to come: what, where, when."""
    from ..engine import engagements
    body = await request.json()
    at = _diary_when(body.get("date"), body.get("from")) if body.get("date") else None
    until = _diary_when(body.get("date"), body.get("to")) if body.get("date") and body.get("to") else None
    if at is not None and until is not None and until <= at:
        until += 86400
    was = next((e for e in engagements.all_engagements() if e["id"] == engagement_id), None)
    who = body.get("with")
    row = engagements.edit(engagement_id, what=body.get("what"), where=body.get("where"), at=at, until=until,
                           notes=body.get("notes"), as_=body.get("as"),
                           with_=", ".join(who) if isinstance(who, list) else who)
    if row is None:
        return JSONResponse({"error": "that one can't be changed"}, status_code=400)
    if was and row.get("plan") and (abs(was["at"] - row["at"]) > 60 or was.get("where") != row.get("where")):
        await console.plan_moved(row)                  # the family hear it where the plan was made
    return JSONResponse({"entry": engagements.entry(row)})


@app.post("/api/diary/{engagement_id}/cancel")
async def diary_cancel(engagement_id: str, request: Request):
    """He calls it off — saying why, or not — and whoever it was with is told."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    row = await console.cancel_engagement(engagement_id, str((body or {}).get("why") or "")[:300])
    if row is None:
        return JSONResponse({"error": "nothing to cancel"}, status_code=400)
    from ..engine import engagements
    return JSONResponse({"entry": engagements.entry(row)})


@app.post("/api/diary/{engagement_id}/answer")
async def diary_answer(engagement_id: str, request: Request):
    """His yes or no to one of them asking him along — and it's said to them."""
    body = await request.json()
    row = await console.answer_invite(engagement_id, bool(body.get("yes")), str(body.get("why") or "")[:300])
    if row is None:
        return JSONResponse({"error": "nothing waiting on an answer"}, status_code=400)
    from ..engine import engagements
    return JSONResponse({"entry": engagements.entry(row)})


@app.delete("/api/diary/{engagement_id}")
async def diary_delete(engagement_id: str):
    """
    Gone from the diary altogether — and if it's still to come and someone's expecting him (a plan, their
    invitation, a letter's evening), called off first, so they're told rather than left waiting.
    """
    from ..engine import engagements
    row = next((e for e in engagements.all_engagements() if e["id"] == engagement_id), None)
    if (row and row["status"] == "accepted" and row["until"] > time.time()
            and (row.get("plan") or row.get("inviter") or row.get("source") in ("invitation", "mail"))):
        await console.cancel_engagement(engagement_id)
    engagements.delete(engagement_id)
    return JSONResponse({"ok": True})


@app.get("/api/engagements")
async def list_engagements():
    """His diary: what's coming, and what became of the last few."""
    from ..engine import engagements
    now = time.time()
    return JSONResponse({"upcoming": engagements.upcoming(now, days=14), "recent": engagements.recent(now, days=7)})


@app.post("/api/engagements/{engagement_id}/go")
async def go_to_engagement(engagement_id: str):
    """He sets off for it — as Bruce Wayne to an evening, as Batman to a meeting — and the map follows."""
    from ..engine import batman, engagements
    if batman.committed():
        # A case first: he goes the moment it's done, on his own — late (see Console._diary_tick).
        return JSONResponse({"error": "after the case", "held": True}, status_code=409)
    trip = await asyncio.to_thread(engagements.go, engagement_id)
    if trip is None:
        return JSONResponse({"error": "nowhere to go"}, status_code=400)
    return JSONResponse(batman.state())


@app.post("/api/mail/{message_id}/rsvp")
async def rsvp_mail(message_id: str, request: Request):
    """He accepts an invitation, or declines it — Alfred answers for him, and it's in the diary."""
    from ..engine import inbox
    body = await request.json()
    done = inbox.rsvp(message_id, bool(body.get("accept")))
    if done is None:
        return JSONResponse({"error": "no event in that one"}, status_code=400)
    row, note = done
    return JSONResponse({"engagement": row, "message": note, "unread": inbox.unread()})


@app.post("/api/mail/{message_id}")
async def file_mail(message_id: str, request: Request):
    """Read, filed, binned: a message moved to a folder or marked."""
    from ..engine import inbox
    body = await request.json()
    if body.get("delete"):
        inbox.delete(message_id)
        return JSONResponse({"ok": True, "unread": inbox.unread()})
    found = inbox.update(message_id, **{k: body[k] for k in ("folder", "read") if k in body})
    if found is None:
        return JSONResponse({"error": "no such message"}, status_code=404)
    return JSONResponse({"message": found, "unread": inbox.unread()})


@app.get("/api/map/odds/{report_id}")
async def report_odds(report_id: str):
    """
    The odds on a report: as it stands (if anyone's on it), and with each of the
    family who's free added — how far out they are, and what it would make it.
    """
    from ..engine import batman, cases, incidents, outcomes

    def work():
        now = time.time()
        report = incidents.get(report_id) or cases.for_report(report_id)
        if report is None:
            return None
        case = cases.for_report(report_id)
        base = dict(case) if case and case["status"] != "closed" else {
            "id": report["id"], "kind": report["kind"], "severity": report["severity"], "suspect": report.get("suspect", ""),
            "crew": report.get("crew", 1), "began": report.get("at", now), "opened_at": now, "team": [], "members": {},
            "x": report["x"], "y": report["y"], "status": "assigned"}
        base.update({"kind": report["kind"], "severity": report["severity"], "crew": report.get("crew", base.get("crew", 1))})
        busy = {m for c in cases.everything() if c["status"] != "closed" for m in cases.team(c)}
        out = {"current": outcomes.estimate(base, now) if cases.team(base) else None,
               "projected": outcomes.estimate(base, now, projected=True) if cases.team(base) else None,
               "gcpd": outcomes.baseline(base), "threat": outcomes.threat(base), "with": {},
               # Whoever's on it got there after it was over: it's gone however GCPD's night went.
               "late": bool(cases.team(base) and (
                   (not base.get("chase")) if report["kind"] in incidents.MOVING
                   else outcomes.too_late(base) and outcomes.family(report["kind"]) in ("street", "petty")))}
        goal = (report["x"], report["y"])
        free = [m for m in cases.FIELD if m not in busy and console.directory.get(m)]
        if "bruce" not in busy and not batman.committed():
            free.append("bruce")
        for member in free:
            if member == "bruce":
                here = batman.position(now)
            else:
                contact = console.directory.get(member)
                if not getattr(contact, "shares_location", True) or presence.of(contact).now()["status"] == presence.OFFLINE:
                    continue
                here = presence.of(contact).position(now)
            if here is None:
                continue
            route = travel.fastest(member, here, goal, None, now, book=False, jet=member == "bruce")
            eta = max(0.5, (route["end"] - now) / 60)
            trial = {**base, "team": cases.team(base) + [member],
                     "members": {**(base.get("members") or {}), member: {"joined": now, "travel": eta, "status": "assigned"}}}
            moving = bool(report.get("moving") and report.get("route"))
            if moving:
                trial["chase"] = {"start": 0} if incidents.intercept(report, here, member, now) else None
            # Too late to change it: the getaway's gone past anywhere they could cut it off, the mugging's
            # over before they're there — it'll go however it goes for GCPD.
            late = (not trial.get("chase")) if moving else (
                outcomes.too_late(trial) and outcomes.family(report["kind"]) in ("street", "petty"))
            out["with"][member] = {"odds": outcomes.estimate(trial, now, projected=True), "eta": round(eta, 1),
                                   "by": route.get("by", ""), "late": bool(late)}
        return out
    found = await asyncio.to_thread(work)
    if found is None:
        return JSONResponse({"error": "no such report"}, status_code=404)
    return JSONResponse(found)


@app.post("/api/cases/comms")
async def case_comms(request: Request):
    """He opens a comms channel on a case."""
    body = await request.json()
    group = await console.open_comms(str(body.get("report", "")))
    if group is None:
        return JSONResponse({"error": "nobody to open it with"}, status_code=400)
    return JSONResponse({"group": console.group_payload(group)})


@app.post("/api/groups/{group_id}/archive")
async def archive_group(group_id: str, request: Request):
    """He archives a group chat, or brings it back."""
    body = await request.json()
    group = await console.archive_group(group_id, bool(body.get("archived", True)))
    if group is None:
        return JSONResponse({"error": "no such group"}, status_code=404)
    return JSONResponse({"group": console.group_payload(group)})


@app.post("/api/cases/unassign")
async def unassign_case(request: Request):
    """He pulls someone off a case, from the map."""
    body = await request.json()
    result = await console.unassign_case(str(body.get("report", "")), str(body.get("contact", "")))
    if result is None:
        return JSONResponse({"error": "they're not on it"}, status_code=400)
    return JSONResponse({"case": result})


@app.get("/api/bruce")
async def bruce_state():
    """Where he is — and where he's going, if he's on his way."""
    from ..engine import batman
    return JSONResponse(batman.state())


@app.post("/api/bruce/go")
async def bruce_go(request: Request):
    """He sets off: for a place on the map, or a report off the scanner (which makes it his case too)."""
    from ..engine import batman, cases, incidents
    body = await request.json()
    report_id = str(body.get("report") or "")
    if report_id:
        report = incidents.get(report_id) or cases.for_report(report_id)
        if report is None:
            return JSONResponse({"error": "no such report"}, status_code=404)
        held = batman.committed()
        if held:
            return JSONResponse({"error": held}, status_code=200)
        batman.leave_case_for()
        await console._bruce_takes(report)
        return JSONResponse(batman.state())
    held = batman.committed()
    if held:
        return JSONResponse({"error": held}, status_code=200)
    batman.leave_case_for()                 # on the way to one, off to somewhere else: he's not on it now
    person = str(body.get("person") or "")
    if person:
        # To one of them, and with them from then on.
        trip = await asyncio.to_thread(batman.join, person)
        if trip is None:
            return JSONResponse({"error": "can't see where they are"}, status_code=400)
        await console.broadcast({"type": "bruce", "bruce": batman.state()})
        return JSONResponse(batman.state())
    where = str(body.get("place") or "").strip()
    x, y = body.get("x"), body.get("y")
    if not where and x is None:
        return JSONResponse({"error": "somewhere to go"}, status_code=400)
    trip = await asyncio.to_thread(batman.go, where or "a spot on the map", x if x is None else float(x),
                                   y if y is None else float(y))
    if trip is None:
        return JSONResponse({"error": "can't get there"}, status_code=400)
    now_state = batman.state()
    await console.broadcast({"type": "bruce", "bruce": now_state})
    await console._system("route_suit" if now_state.get("suit") else "route")
    return JSONResponse(now_state)


@app.post("/api/bruce/lift")
async def bruce_lift(request: Request):
    """He gives one of them a lift in the Batwing, to their case or wherever they're going."""
    body = await request.json()
    found = await console.give_lift(str(body.get("person") or ""), body.get("to"))
    if found and not found.get("error"):
        await console._system("lift", str(body.get("person") or ""))
    return JSONResponse(found)


@app.post("/api/jet/home")
async def jet_home():
    """Done with it: the Batwing takes itself back to the hangar."""
    from ..engine import jet
    found = await asyncio.to_thread(jet.send_home)
    await console.broadcast({"type": "cases"})
    if found:
        await console._system("jet_home")
    return JSONResponse(found or {"error": "Mid-flight"})


@app.post("/api/jet/summon")
async def jet_summon():
    """He calls the Batwing to him: it comes, and waits overhead for wherever he goes next. Batman's, not Bruce Wayne's."""
    from ..engine import batman, jet
    if not batman.state()["suit"]:
        return JSONResponse({"error": "Not in daylight"}, status_code=200)
    here = batman.position()
    if here is None:
        return JSONResponse({"error": "Can't find you"}, status_code=200)
    found = await asyncio.to_thread(jet.summon, "bruce", here)
    if found is None:
        await console._system("jet_busy")
        return JSONResponse({"error": "On someone else's job"}, status_code=200)
    await console.broadcast({"type": "cases"})
    await console._system("jet_summoned")
    return JSONResponse(found)


@app.get("/api/map/trail/{contact_id}")
async def map_trail(contact_id: str, hours: float = 2.0):
    """Where someone has been lately, for the map — nothing for those who don't share."""
    contact = console.directory.get(contact_id)
    if contact is None or not contact.shares_location:
        return JSONResponse({"trail": []})
    hours = max(0.25, min(hours, 12))
    whereabouts = presence.of(contact)
    trail = await asyncio.to_thread(whereabouts.trail, hours)
    # And the roads they took between those places, as they took them — and the roofs, on patrol.
    since = time.time() - hours * 3600
    legs = await asyncio.to_thread(whereabouts.patrol_legs, since)
    return JSONResponse({"trail": trail, "trips": whereabouts.trips(since) + legs})


@app.get("/api/codex")
async def codex_all():
    """The Codex: people, rogues and places (see wayne.engine.codex)."""
    from ..engine import codex
    return JSONResponse({"people": codex.people(console.directory), "rogues": codex.rogues(),
                         "places": codex.places()})


@app.post("/api/codex/note")
async def codex_note(request: Request):
    """His own note on someone or somewhere in the Codex."""
    from ..engine import codex
    body = await request.json()
    kind, name = str(body.get("kind") or ""), str(body.get("name") or "").strip()
    if kind not in ("people", "rogues", "places") or not name:
        return JSONResponse({"error": "what note?"}, status_code=400)
    codex.write_note(kind, name, str(body.get("note") or ""))
    return JSONResponse({"ok": True})


_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@app.post("/api/codex/portrait/{entry_id}")
async def codex_portrait(entry_id: str, request: Request):
    """
    A face for someone in the Codex — dropped or chosen on their card. A
    contact's goes where the directory looks (web/portraits/<id>), anyone
    else's in web/portraits/codex/<id>; either way it stays out of git.
    """
    from ..engine import codex
    if not re.fullmatch(r"[a-z0-9-]{1,40}", entry_id):
        return JSONResponse({"error": "bad id"}, status_code=400)
    ext = _IMAGE_TYPES.get((request.headers.get("content-type") or "").split(";")[0].strip())
    if not ext:
        return JSONResponse({"error": "png, jpeg or webp"}, status_code=415)
    data = await request.body()
    if not data or len(data) > 8_000_000:
        return JSONResponse({"error": "too big"}, status_code=413)
    contact = request.query_params.get("contact") or None
    if contact and console.directory.get(contact) is None:
        return JSONResponse({"error": "no such contact"}, status_code=404)
    url = await asyncio.to_thread(codex.save_portrait, entry_id, data, ext, contact)
    return JSONResponse({"portrait": url})


@app.post("/api/groups/{group_id}/picture")
async def group_picture(group_id: str, request: Request):
    """A picture for a group chat — dropped or chosen in its info panel. Kept out of git, like the faces."""
    from ..engine import codex
    group = group_store.of(group_id)
    if group is None or not re.fullmatch(r"[a-z0-9]{6,40}", group_id):
        return JSONResponse({"error": "no such group"}, status_code=404)
    ext = _IMAGE_TYPES.get((request.headers.get("content-type") or "").split(";")[0].strip())
    if not ext:
        return JSONResponse({"error": "png, jpeg or webp"}, status_code=415)
    data = await request.body()
    if not data or len(data) > 8_000_000:
        return JSONResponse({"error": "too big"}, status_code=413)
    url = await asyncio.to_thread(codex.save_group_picture, group_id, data, ext)
    await console.broadcast({"type": "group_updated", "group": console.group_payload(group)})
    return JSONResponse({"picture": url})


@app.delete("/api/groups/{group_id}/picture")
async def group_picture_gone(group_id: str):
    from ..engine import codex
    group = group_store.of(group_id)
    if group is None or not re.fullmatch(r"[a-z0-9]{6,40}", group_id):
        return JSONResponse({"error": "no such group"}, status_code=404)
    await asyncio.to_thread(codex.save_group_picture, group_id, None, "")
    await console.broadcast({"type": "group_updated", "group": console.group_payload(group)})
    return JSONResponse({"picture": ""})


@app.post("/api/codex/place")
async def codex_place(request: Request):
    """He rewrites what Gotham knows of a place."""
    from ..engine import places
    body = await request.json()
    name = str(body.get("name") or "").strip()
    known = {p["name"] for p in places.gazetteer()["places"]} | {v["name"] for v in places.venues()}
    if name not in known:
        return JSONResponse({"error": "no such place"}, status_code=404)
    places.edit(name, str(body.get("bio") or ""))
    return JSONResponse({"ok": True})


@app.post("/api/codex/frame/{entry_id}")
async def codex_frame(entry_id: str, request: Request):
    """How a face sits in its circle — the part of the picture, how close — set from the Codex."""
    from ..engine import codex
    body = await request.json()
    size, position = str(body.get("size") or ""), str(body.get("position") or "")
    if (size or position) and not (re.fullmatch(r"\d{2,3}(\.\d)?%", size)
                                   and re.fullmatch(r"-?\d{1,3}(\.\d)?% -?\d{1,3}(\.\d)?%", position)):
        return JSONResponse({"error": "bad frame"}, status_code=400)
    contact = request.query_params.get("contact") or None
    if contact and console.directory.get(contact) is None:
        return JSONResponse({"error": "no such contact"}, status_code=404)
    if not re.fullmatch(r"[a-z0-9-]{1,40}", entry_id):
        return JSONResponse({"error": "bad id"}, status_code=400)
    await asyncio.to_thread(codex.set_frame, codex.frame_key(entry_id, contact), size, position)
    found = console.directory.get(contact) if contact else None
    return JSONResponse({"frame": codex.frame_for(found) if found else codex.frames().get(codex.frame_key(entry_id), {})})


@app.post("/api/codex/bio")
async def codex_bio(request: Request):
    """He rewrites what's on file for someone in the Codex; empty puts the file back."""
    from ..engine import codex
    body = await request.json()
    kind, name = str(body.get("kind") or ""), str(body.get("name") or "").strip()
    known = ({p["name"] for p in codex._data()["people"]} if kind == "people"
             else set(codex._data()["rogues"]) | {r["name"] for r in codex.rogues()} if kind == "rogues" else set())
    if name not in known:
        return JSONResponse({"error": "no such entry"}, status_code=404)
    await asyncio.to_thread(codex.write_bio, kind, name, str(body.get("bio") or ""))
    return JSONResponse({"ok": True})


@app.get("/api/scanner/{report_id}/log")
async def report_log(report_id: str):
    """A report's incident log, as GCPD keeps it — written by the model."""
    from ..engine import incidents
    report = incidents.get(report_id)
    if report is None:
        return JSONResponse({"log": [], "suspect": ""}, status_code=404)
    # Written the first time it's opened, added to when it's opened further on.
    contact = next(iter(console.directory))
    entries = await asyncio.to_thread(incidents.write_log, report, contact.model, contact.options)
    return JSONResponse({"log": entries, "status": report["status"]})


@app.post("/api/unlock")
async def unlock(request: Request):
    """
    Checked here only so the page has something to call; this is stagecraft,
    not access control. The server does not gate any other route on it and the
    passcode sits in plain text in .env — see config.CONSOLE_PASSCODE.
    """
    body = await request.json()
    supplied = (body.get("passcode") or "").strip().lower()
    return JSONResponse({"ok": supplied == config.CONSOLE_PASSCODE.strip().lower()})


@app.get("/api/identity")
async def identity_levels():
    """What each contact knows of him — and the ways there are to know him."""
    return JSONResponse({"levels": [{"id": lv, "label": wayne_identity.LABELS[lv]} for lv in wayne_identity.LEVELS],
                         "contacts": {c.id: wayne_identity.level(c.id) for c in console.directory}})


@app.post("/api/contacts/{contact_id}/knows")
async def set_knows(contact_id: str, request: Request):
    """The story moved on — Jim found out, Selina learned who Nightwing is: what they know of him, from now."""
    if console.directory.get(contact_id) is None:
        return Response(status_code=404)
    body = await request.json()
    level = body.get("level")
    if level not in (*wayne_identity.LEVELS, None):
        return JSONResponse({"error": "no such level"}, status_code=400)
    wayne_identity.set_level(contact_id, level)
    log.info("%s now knows: %s", contact_id, wayne_identity.level(contact_id))
    return JSONResponse({"id": contact_id, "knows": wayne_identity.level(contact_id)})


@app.get("/api/contacts/{contact_id}/bio")
async def read_bio(contact_id: str):
    """
    The relationship in the operator's own words. Falls back to whatever the
    profile shipped with, so the file reads as written rather than empty.
    """
    contact = console.directory.get(contact_id)
    if contact is None:
        return Response(status_code=404)
    stored = store.space("bio").get(contact_id)
    return JSONResponse({"bio": stored or contact.bio})


@app.post("/api/contacts/{contact_id}/bio")
async def write_bio(contact_id: str, request: Request):
    if console.directory.get(contact_id) is None:
        return Response(status_code=404)
    body = await request.json()
    bio = (body.get("bio") or "").strip()
    if bio:
        store.space("bio").put(contact_id, bio)
    else:
        store.space("bio").drop(contact_id)
    return JSONResponse({"ok": True})


@app.get("/api/system/{event}")
async def system_line(event: str, contact: str = ""):
    """
    The Batcomputer's line for a moment, as mp3, with its text in a header so
    the page can show what was said. 204 when there is no system voice.
    """
    if not system_voice.available():
        return Response(status_code=204)
    found = console.directory.get(contact) if contact else None
    text = system_voice.line(event, system_voice.name_for(event, found))
    if not text:
        return Response(status_code=404)
    try:
        clip = await asyncio.to_thread(system_voice.audio, text, console.voice, event)
    except Exception:
        return Response(status_code=204)
    return Response(content=clip, media_type="audio/mpeg",
                    headers={"X-Line": text, "Cache-Control": "no-store"})


@app.get("/api/contacts/{contact_id}/messages")
async def messages(contact_id: str, before: float = 0, limit: int = 40):
    """
    A page of the text thread, oldest first: the newest `limit` messages older
    than `before` (a timestamp), so the page can load further back on scroll.
    """
    if console.directory.get(contact_id) is None:
        return Response(status_code=404)
    page = TextLog(contact_id).page(before or None, max(1, min(limit, 200)))
    return JSONResponse({"messages": page, "typing": ("text", contact_id) in console._typing_now})


@app.get("/api/groups")
async def list_groups():
    return JSONResponse({"groups": [console.group_payload(g) for g in group_store.all_groups()]})


@app.post("/api/groups")
async def create_group(request: Request):
    body = await request.json()
    group = await console.group_create(body.get("name", ""), body.get("members", []))
    if group is None:
        return JSONResponse({"error": "a group needs at least two contacts"}, status_code=400)
    return JSONResponse({"group": console.group_payload(group)})


@app.patch("/api/groups/{group_id}")
async def update_group(group_id: str, request: Request):
    body = await request.json()
    group = await console.group_update(group_id, body.get("name"), body.get("add") or [],
                                       body.get("remove") or [])
    if group is None:
        return Response(status_code=404)
    return JSONResponse({"group": console.group_payload(group)})


@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: str):
    await console.group_delete(group_id)
    return JSONResponse({"ok": True})


@app.get("/api/groups/{group_id}/messages")
async def group_messages(group_id: str, before: float = 0, limit: int = 40):
    group = group_store.of(group_id)
    if group is None:
        return Response(status_code=404)
    page = group.page(before or None, max(1, min(limit, 200)))
    meta = group.summary()
    typing = [cid for (gid, cid) in console._typing_now if gid == group_id]
    return JSONResponse({"messages": page, "reads": meta["reads"], "members": meta["members"],
                         "name": meta["name"], "typing": typing})


@app.get("/api/audio/{clip_id}")
async def audio(clip_id: str):
    clip = console.audio_clips.get(clip_id)
    if clip is None:
        return Response(status_code=404)
    return Response(content=clip, media_type="audio/mpeg")


@app.get("/api/ambience/{contact_id}")
async def ambience_for(contact_id: str):
    """The kind of place someone on the call is in — the room their voice comes from (see wayne.audio.ambience)."""
    from ..audio import ambience
    contact = console.directory.get(contact_id)
    if contact is None or not ambience.available():
        return JSONResponse({"scene": "", "beds": [], "cues": [], "cuts": [], "now": time.time()})
    # The next couple of minutes there — which loops, which sounds when — laid out here so what they hear is
    # what he hears (see ambience.heard).
    return JSONResponse(await asyncio.to_thread(ambience.plan, contact))


@app.get("/api/ambience/file/{scene}")
async def ambience_file(scene: str):
    """A place's loop, or one of its sounds from a distance — made the first time anyone's there, kept after."""
    from ..audio import ambience
    made = await asyncio.to_thread(ambience.clip, scene)
    if made is None:
        return Response(status_code=404)
    return Response(content=made.read_bytes(), media_type="audio/mpeg",
                    headers={"Cache-Control": "max-age=86400"})


def _speech_hint():
    """
    Words this particular conversation is likely to contain, fed to the decoder
    so it stops inventing spellings for them. Whoever is on the line, whoever is
    speaking, and any proper nouns from the last thing said.

    Names only — deliberately. Whisper's `initial_prompt` is not a glossary, it
    is a prefix the decoder continues from, so it biases output toward *whatever
    is in it*. This used to pass the contact's entire last sentence, which meant
    every take was nudged toward the words he had just spoken: his phrasing
    appeared in transcripts of things the operator never said, the reply
    answered that, and the conversation walked off in a direction nobody had
    asked for. It also gave acoustic echo a helping hand, since the hint agreed
    with whatever the microphone picked up off the speakers.

    Capitalised words survive because a name is what Whisper actually gets wrong
    and what a hint genuinely fixes. Prose is what does the damage.
    """
    # The configured hints too. Building this list per request used to replace
    # them entirely, so names added to WAYNE_WHISPER_HINTS were never used.
    parts = wayne_operator.whisper_hints() + [
        h.strip() for h in config.WHISPER_HINT_PROMPT.split(",") if h.strip()]
    contact = console.contact
    if contact:
        parts += [contact.name, contact.full_name]
    if console.recent_speech:
        spoken = console.recent_speech[-1][1] or ""
        # Mid-sentence capitals: proper nouns, minus whatever starts a sentence.
        parts += re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z'’-]{2,}", spoken)[:6]
    seen, unique = set(), []
    for part in parts:
        key = (part or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(part.strip())
    return ", ".join(unique)[:200]


# Hearing him has a thread of its own. On the shared pool it queued behind every model call waiting its turn
# (two of them on a group call, the afterthoughts, a text) — 30 and 97 seconds to hear four seconds of speech.
_STT_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
_HEARING = {"waiting": 0}            # his turns waiting to be heard: a live caption gives way to them
_CAPTION_SECONDS = 6                 # a caption only needs what he's saying now, not the whole take again


@app.post("/api/transcribe")
async def transcribe(request: Request):
    """Raw little-endian float32 PCM at stt.SAMPLE_RATE, captured in the page."""
    body = await request.body()
    began = time.monotonic()
    partial = bool(request.query_params.get("partial") or request.query_params.get("wake"))
    hint = _speech_hint()
    if request.query_params.get("wake"):
        # Listening for "Hey Alfred" with no call on: their names are what it's listening for. Never logged.
        hint = ", ".join(["Hey"] + [c.name for c in console.directory])[:200]
    if partial:
        if _HEARING["waiting"] or stt.busy():
            return JSONResponse({"text": "", "confidence": 1.0, "skipped": True})
        body = body[-_CAPTION_SECONDS * stt.SAMPLE_RATE * 4:]
    else:
        _HEARING["waiting"] += 1

    def hear():
        if partial and _HEARING["waiting"]:
            return "", 1.0                       # his turn came in while this waited: it goes first
        return stt.transcribe_pcm(body, hint)
    try:
        text, confidence = await asyncio.get_running_loop().run_in_executor(_STT_POOL, hear)
    finally:
        if not partial:
            _HEARING["waiting"] -= 1
    if partial:
        return JSONResponse({"text": text, "confidence": round(confidence, 2)})     # a live caption, not a turn
    # How long hearing him took, against how long he spoke: slow here is the GPU shared with a reply being written.
    log.info("heard %.1fs of speech in %.2fs%s", len(body) / 4 / stt.SAMPLE_RATE, time.monotonic() - began,
             " (while the model was busy)" if console.voice_busy or console.turn_lock.locked() else "")
    return JSONResponse({"text": text, "confidence": round(confidence, 2)})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    console.clients.add(websocket)
    try:
        while True:
            try:
                payload = await websocket.receive_json()
            except (ValueError, TypeError):
                continue        # one malformed message must not close the socket
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if kind == "prompt":
                try:
                    confidence = float(payload.get("confidence") or 1.0)
                except (TypeError, ValueError):
                    confidence = 1.0
                console._spawn(console.submit(
                    payload.get("text", ""), spoken=bool(payload.get("spoken")),
                    confidence=confidence, talked_over=str(payload.get("talked_over") or ""),
                    overlapped=payload.get("overlapped"), cut_in=bool(payload.get("cut_in"))))
            elif kind == "connect":
                console._spawn(console.connect(payload.get("id", "")))
            elif kind == "disconnect":
                console._spawn(console.disconnect())
            elif kind == "interrupt":
                # He cut in: the turn stops here too, not only on his speakers.
                try:
                    console._heard_upto = int(payload.get("heard_upto") or 0)
                except (TypeError, ValueError):
                    console._heard_upto = 0
                console.interrupt()
                if not console.voice_busy and getattr(console, "_last_said", None):
                    console._remember_heard(*console._last_said)     # the reply was written; he heard part of it
                    console._last_said = None
            elif kind == "comms_call":
                console._spawn(console.join_comms(payload.get("group", "")))
            elif kind == "caught":
                console.caught(payload.get("speaker", ""), payload.get("lines"))
            elif kind == "nudge":
                console._spawn(console.nudge())
            elif kind == "signoff":
                console._spawn(console.nudge("sign_off"))
            elif kind == "text":
                console._spawn(console.text(payload.get("id", ""), payload.get("text", ""), payload.get("reply_to")))
            elif kind == "answer":
                console._spawn(console.answer(payload.get("id", "")))
            elif kind == "decline":
                console._spawn(console.decline(payload.get("id", "")))
            elif kind == "group_text":
                console._spawn(console.group_text(payload.get("id", ""), payload.get("text", ""), payload.get("reply_to")))
            elif kind == "text_react":
                console._spawn(console.text_react(payload.get("id", ""), payload.get("message", ""),
                                                       payload.get("emoji", "")))
            elif kind == "text_edit":
                console._spawn(console.text_edit(payload.get("id", ""), payload.get("message", ""),
                                                      str(payload.get("text", ""))[:2000]))
            elif kind == "group_edit":
                console._spawn(console.group_edit(payload.get("id", ""), payload.get("message", ""),
                                                       str(payload.get("text", ""))[:2000]))
            elif kind == "seen":
                TextLog(str(payload.get("id", ""))).mark_seen()
            elif kind == "group_seen":
                seen = group_store.of(str(payload.get("id", "")))
                if seen is not None:
                    seen.mark_read("me")
            elif kind == "group_react":
                console._spawn(console.group_react(payload.get("id", ""), payload.get("message", ""),
                                                        payload.get("emoji", "")))
            elif kind == "call_group":
                console._spawn(console.call_many(payload.get("ids") or []))
            elif kind == "add":
                console._spawn(console.add(payload.get("id", ""), by_him=True))
            elif kind == "mute":
                console.set_muted(bool(payload.get("on")))
            elif kind == "listening":
                console._spawn(console.read_ahead())
            elif kind == "drop":
                console._spawn(console.drop(payload.get("id", "")))
            elif kind == "resume":
                console._spawn(console.nudge("resume"))
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("the console's socket failed")
    finally:
        console.clients.discard(websocket)


def serve():
    import uvicorn
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT,
                log_level="warning", access_log=False)
