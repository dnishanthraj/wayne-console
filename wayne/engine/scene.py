"""
A call with company, written as one scene.

Four of them on the line used to be four conversations taking turns with one
model. Every line was a different character's whole prompt read afresh — five
thousand tokens, three seconds, before a word — so a pause was five to ten
seconds of nothing before anyone spoke into it, someone joining took ten, and
nobody wrote with the others' lines in view, so the same shapes came round and
round ("Honestly, Bruce?", "And Tim?").

Here the call is one prompt the model keeps hold of: the rules of the line, a
brief for each of them in their own profile's words (how they talk on a call,
what they call everyone, what he is to them, where they are now, what they
mustn't say in front of whom), and the call so far. Each time he speaks, the
next few lines are written in one stream — whoever he spoke to first, then
whoever would come in — and voiced as they arrive, each in their own voice.
When he's quiet, they carry on; when he cuts in, the stream stops where he
stopped hearing it, and the next starts from where the model already is: a
fraction of a second, not three.

Generated, never scripted: the scene decides who speaks and what they say.
Code keeps it a call he's on — him first when he speaks, a handful of lines a
round, nobody saying what they wouldn't know or wouldn't say in this company —
and each line is said through its speaker's own session (ContactSession.scene_line):
their markers acted on, their forms of address guarded, remembered as theirs.
"""
import logging
import re
import time

import ollama

from .. import config, events, identity
from . import guards, model, prompting

log = logging.getLogger("wayne")

# Lines a round, at most: answering him (whoever he spoke to, then a couple more), carrying on in a pause, someone
# joining or going, something that just happened on the call.
MOST = {"him": 4, "carry": 3, "greet": 2, "event": 2}
# Words of each brief, by how many are on the line: the whole call has to fit the window with room to answer.
_BRIEF_WORDS = {2: 520, 3: 430, 4: 340, 5: 280, 6: 230, 7: 200}
# The paragraphs of a profile that say how someone sounds with company, in the order they're worth the words.
_PARTS = ("How you talk on a call", "How you talk on the line", "What you call people", "What you call him",
          "What he is to you", "Your voice", "Your temperament", "How words work for you", "Who you are")
_NOTHING = re.compile(r"^\W*\(?\s*(nothing|silence|no one|nobody)\b", re.I)


def _paragraphs(contact):
    return [p.strip() for p in (contact.system or "").split("\n\n") if p.strip()]


def brief(contact, words):
    """
    Who they are on this call, in their own profile's words (written to them, as "you"): how they talk on a call
    and the tics in it, what they call everyone, what he is to them, their voice — cut to `words`.
    """
    paras = _paragraphs(contact)
    picked = []
    for part in _PARTS:
        found = next((p for p in paras if p.startswith(part)), None)
        if found and found not in picked:
            picked.append(found)
    lines = [paras[0].split(". ")[0].strip().rstrip(".") + "."] if paras else []
    if getattr(contact, "speaking", ""):
        lines.append(f"Your habits of speech: {contact.speaking}.")
    budget = words - sum(len(x.split()) for x in lines)
    for para in picked:
        n = len(para.split())
        if n <= budget:
            lines.append(para)
            budget -= n
        elif budget > 40:
            lines.append(" ".join(para.split()[:budget]).rstrip(",;:") + "…")
            budget = 0
    if getattr(contact, "voice_range", ""):
        lines.append(f"Delivery cues your voice can do: {contact.voice_range}.")
    if contact.forbidden_address:
        lines.append("Never call anyone: " + ", ".join(f"'{a}'" for a in contact.forbidden_address) + ".")
    return "\n".join(lines)


class Scene:
    """The other end of a group call, written as one: see the module's docstring."""

    def __init__(self, call):
        self.call = call
        self.messages = []          # the call so far, as the model reads it: his lines (and what happened), theirs
        self.notes = []             # what's happened on the call since the last round: "(Tim didn't pick up)"
        self._client = None
        self._cancelled = 0
        self._cast = None           # who the system prompt was written for
        self._system = ""
        self._round = None          # this round's lines, as said: [(member, [sentences])]
        self._growing = None        # the line being written: (member, how many of its sentences are said)
        self._stop = False
        self._greeting = self._farewell = None
        self._committed = []
        self.seed = ""              # what was said before it became a scene: told once, in the first round

    # --- who's who -------------------------------------------------------------------------------------

    def _listeners(self):
        return [m.contact.id for m in self.call.members]

    def label(self, member):
        """
        What a line of theirs is tagged with: their name — or, with someone on the line who knows only the
        masks, their mask (a cover's noun for those who have none: "Engineer"), so the scene never hands Jim a face.
        """
        listeners = self._listeners()
        if identity.strict(listeners) and identity.level(member.contact.id) == "all":
            mask = getattr(member.contact, "mask", "")
            if mask:
                return mask
            cover = getattr(member.contact, "cover", "")
            if cover:
                return re.sub(r"^(?:a|an)\s+", "", cover).split(" on ")[0].title()
        return member.contact.name

    def him(self):
        return identity.handle("me", self._listeners())

    def _who(self, tag):
        """The member a line's tag means — their label, or any name they go by if nobody's guarding one."""
        tag = (tag or "").strip().lower()
        for member in self.call.members:
            if self.label(member).lower() == tag:
                return member
        if identity.strict(self._listeners()):
            return None
        for member in self.call.members:
            if tag in {n.lower() for n in identity.names_of(member.contact)}:
                return member
        return None

    # --- the prompt --------------------------------------------------------------------------------------

    def system(self):
        """The rules of the line and everyone's brief — rewritten only when who's on it changes."""
        members = list(self.call.members)
        cast = tuple(sorted(m.contact.id for m in members))
        if cast == self._cast and self._system:
            return self._system
        him = self.him()
        labels = [self.label(m) for m in members]
        listeners = self._listeners()
        words = _BRIEF_WORDS.get(len(members), 200)
        briefs = []
        for m in members:
            parts = [f"=== {self.label(m)} ===", brief(m.contact, words)]
            guard = identity.guard_note(m.contact.id, [x for x in listeners if x != m.contact.id])
            if guard:
                parts.append(guard)
            from . import bonds, presence
            for extra in (bonds.note(m.contact), presence.of(m.contact).note()):
                if extra:
                    parts.append(extra)
            room = self.call.in_the_room(m)
            if room:
                parts.append(f"Right there with you, not just on the line: {', '.join(room)}.")
            # What only they know of the city's own additions (see wayne.lore): what only Dick, say, would know.
            from .. import lore
            from .. import operator as wayne_operator
            mine = [f["text"] for f in lore.facts()
                    if wayne_operator._knows(f, m.contact.id, identity.level(m.contact.id))]
            if mine:
                parts.append("Only you know, of the people below: " + " ".join(mine))
            briefs.append("\n".join(parts))
        names = ", ".join(labels[:-1]) + (" and " if len(labels) > 1 else "") + labels[-1]
        from .. import lore
        gotham = []
        for person in lore.people():
            line = person.get("public") or f"{person['name']} — {person.get('occupation', '')}"
            strangers = [self.label(m) for m in members if m.contact.id in (person.get("unknown_to") or ())]
            gotham.append(f"- {line}" + (f" (news to {', '.join(strangers)})" if strangers else ""))
        self._system = (
            f"You're writing the other end of a phone call as it happens: a group call. {him} — never write a line "
            f"for him — is on the line with {names}. Each of them has a brief below, written to them as \"you\".\n\n"
            "How to write it:\n"
            f"- Only their lines, one to a line: \"Name: what they say\", with the names exactly as here ({names}). "
            f"No narration, no stage directions outside a line, and nothing for {him}.\n"
            f"- {him} comes first. When he's just spoken, whoever he spoke to answers him — what he actually asked, "
            "plainly — before anyone else does; said to everyone, whoever would answer first does. Then one or two "
            "others come in, if they would. Nobody talks about him as if he weren't on the line.\n"
            "- When he's quiet, there's no dead air: they carry on the way a family on a call does — pick a thread "
            "back up, react to each other, rib someone, ask each other something, bring him back in now and then.\n"
            "- It's speech, on phones: short, unpolished — half-sentences, a laugh in the middle, \"—wait\", trailing "
            "off. One thought a line, usually; never more than three sentences.\n"
            "- Everyone sounds like themselves, from their brief — not like each other, and not like a script. No "
            "stock lines, no picking up a phrase someone just used, no ending a line by handing it to someone "
            "(\"…And Tim?\"), no talking to two people in one line out of habit.\n"
            "- A delivery cue in [brackets] at the start of a line now and then — only ones their brief says their "
            "voice can do; most lines need none.\n"
            "- Stop when it's naturally his turn: a question's been put to him, or the moment's said — usually after "
            "one to three lines. If nobody would say anything, write only: (nothing)\n"
            "- Someone who has to go ends their line with [hang up]. Asked by him to get someone else on the line, "
            "they end their line with [add: that person's first name].\n"
            + ("\nPeople around Gotham, as real as anyone — named, everyone on the line knows exactly who's meant (but "
               "for whoever it's news to):\n" + "\n".join(gotham) + "\n" if gotham else "")
            + "\n" + "\n\n".join(briefs))
        self._cast = cast
        return self._system

    def _payload(self, ask):
        member = self.call.members[0]
        num_ctx = (member.contact.options or {}).get("num_ctx") or 8192
        messages = [{"role": "system", "content": self.system()}] + self.messages + [{"role": "user", "content": ask}]
        return prompting.fit_to_context(messages, num_ctx, reserve=360, label="the call's scene")

    # --- a round -------------------------------------------------------------------------------------------

    def respond(self, prompt, interrupted=False, talked_over=""):
        """
        He's said something: the next few lines, as events — his line first, then theirs as they're written.
        Returns how many lines were said, or None if the scene couldn't be written at all (the call falls back
        to a turn per person, this once).
        """
        call = self.call
        yield events.message("user", prompt)
        call._last_operator_line = prompt
        call.transcript.append(("me", prompt))
        for member in call.members:
            member.heard.append(call.heard_as("me", prompt, member))
        notes = list(self.notes)
        addressed = [m for m in call.addressed(prompt) if m.contact.id not in call.muted]
        if len(addressed) == 1:
            notes.append(f"(He's talking to {self.label(addressed[0])}.)")
        over = next((m for m in call.members if m.contact.id == talked_over), None) if interrupted else None
        if over is not None:
            notes.insert(0, f"(He cut in on {self.label(over)} mid-line.)")
        ask = f"{self.him()}: {prompt}" + ("\n" + "\n".join(notes) if notes else "")
        return (yield from self._round_of(ask, MOST["him"], first=addressed[0] if len(addressed) == 1 else None))

    def carry_on(self, quiet_for=1):
        """A pause: whoever would speak into it does — a line or two, or nobody."""
        tail = (" He's been quiet a while: someone might check he's still there, or they let it wind down."
                if quiet_for >= 4 else "")
        ask = ("\n".join(self.notes) + "\n" if self.notes else "") + \
              f"(A pause — nobody's said anything for a few seconds.{tail}) Carry on: a line or two."
        return (yield from self._round_of(ask, MOST["carry"]))

    def greet(self, newcomer, note=""):
        """Someone's just been patched in: they say hello to a call already going; someone may answer."""
        ask = ("\n".join(self.notes) + "\n" if self.notes else "") + \
              f"({self.label(newcomer)} has just joined the call{f' — {note}' if note else ''}.) " \
              f"{self.label(newcomer)} speaks first: a line, the way they'd join a call already going."
        return (yield from self._round_of(ask, MOST["greet"], first=newcomer, greeting=newcomer))

    def farewell(self, member, why=""):
        """Someone's going: a quick goodbye down the line."""
        ask = ("\n".join(self.notes) + "\n" if self.notes else "") + \
              f"({self.label(member)} is going — {why or 'he is letting them drop off'}.) " \
              f"{self.label(member)} says a quick goodbye: one line."
        return (yield from self._round_of(ask, 1, first=member, farewell=member))

    def react(self, what):
        """Something happened on the call — someone didn't pick up, someone's being rung in: a line about it, or not."""
        ask = ("\n".join(self.notes) + "\n" if self.notes else "") + \
              f"({what}) If anyone would say something about it, a line — or (nothing)."
        return (yield from self._round_of(ask, MOST["event"]))

    def note(self, what):
        """Something that happened on the call, for the next round to know: "(Tim didn't pick up)"."""
        self.notes.append(f"({what})" if not what.startswith("(") else what)
        self.notes = self.notes[-6:]

    def _round_of(self, ask, most, first=None, greeting=None, farewell=None):
        """
        One stream of lines: parsed as it comes ("Name: …"), each sentence said through its speaker's session the
        moment it's finished, and the call's record — the model's, the call's, theirs — kept to what was said.
        Returns how many lines were said, or None if nothing could be written at all.
        """
        call = self.call
        self.notes = []
        if self.seed:
            ask, self.seed = self.seed + "\n\n" + ask, ""
        muted = [self.label(m) for m in call.members if m.contact.id in call.muted]
        if muted:
            ask += f"\n(On mute for now, and can't speak: {', '.join(muted)}.)"
        worn = guards.worn_out(call.transcript[-10:])
        if worn:
            ask += "\n(Worn thin on this call already: " + "; ".join(worn) + " — say it some other way, or not at all.)"
        payload = self._payload(ask)
        member0 = call.members[0]
        options = member0._options(temperature=0.9, num_predict=340)
        him = self.him()
        options["stop"] = [f"\n{him}:", f"\n{him.upper()}:"]
        for m in call.members:
            m._shows, m._reminds, m._diaries = [], [], []
            m._take_case = m._join = None
            m._hanging_up = False
            m._call_add = []
        mine = self._cancelled
        self._round, self._growing, self._stop = [], None, False
        self._greeting, self._farewell = greeting, farewell
        lines = []
        started = time.monotonic()
        failed = False
        try:
            client = ollama.Client() if ollama.chat is prompting_chat() else None
            self._client = client
            chat = client.chat if client is not None else ollama.chat
            stream = chat(model=member0.contact.model, messages=payload, stream=True, options=options,
                          **member0._extra())
            buffer = ""
            busy = model.working()
            busy.__enter__()
            self._busy = busy
            for part in stream:
                if self._cancelled != mine:
                    break
                # Ollama's parts are its own dict-like objects, not dicts: read as the sessions read them.
                piece = part.get("message", {}).get("content", "") if hasattr(part, "get") else ""
                if not piece:
                    continue
                buffer += piece
                # Whole lines first; then, of the line still being written, any sentence that's done.
                while "\n" in buffer and len(lines) < most and not self._stop:
                    text, buffer = buffer.split("\n", 1)
                    yield from self._line_done(text, lines)
                if len(lines) >= most or self._stop:
                    break
                yield from self._line_growing(buffer)
            else:
                if buffer.strip() and len(lines) < most and not self._stop:
                    yield from self._line_done(buffer, lines)
        except GeneratorExit:
            # Cut off — he talked over it: what was said so far is the round, cut where it stopped (the console
            # trims it further to what he actually heard: see heard_only).
            self._close()
            self._commit(ask, cut=True)
            raise
        except Exception as exc:
            if self._cancelled == mine:
                log.warning("the call's scene failed: %s", str(exc)[:200])
                failed = not self._said_lines()
        finally:
            self._close()
            busy, self._busy = getattr(self, "_busy", None), None
            if busy is not None:
                busy.__exit__(None, None, None)
        if failed:
            self._round = None
            return None
        self._commit(ask, cut=self._cancelled != mine)
        said = len(self._committed)
        log.info("scene: %d line%s in %.2fs", said, "" if said == 1 else "s", time.monotonic() - started)
        return said

    def _line_growing(self, text):
        """The line still being written: once it's clear whose it is, any sentence of it that's finished is said."""
        member, said = self._tag(text)
        if member is None:
            return
        voiced = self._growing[1] if self._growing and self._growing[0] is member else 0
        done = [x for x in guards.split_sentences(said) if x.strip()][:-1]        # the last may still be growing
        if len(done) > voiced:
            yield from self._voice(member, done[voiced:])
        self._growing = (member, max(voiced, len(done)))

    def _line_done(self, text, lines):
        """A whole line: whatever of it hasn't been said yet is; a line for him, or nobody, ends the round."""
        text = text.strip()
        if not text:
            return
        if _NOTHING.match(text):
            self._stop = True
            return
        tag = re.match(r"^\W*([^:\n]{1,40}?)\s*:", text)
        if tag and _plain_tag(tag.group(1)).lower() in {self.him().lower(), "bruce", "batman", "him", "me"}:
            self._stop = True                                  # writing his line for him: the round's his now
            return
        member, said = self._tag(text)
        voiced = self._growing[1] if self._growing and self._growing[0] is member else 0
        self._growing = None
        if member is None:
            return
        rest = [x for x in guards.split_sentences(said) if x.strip()][voiced:]
        if not rest and not voiced:
            return
        lines.append(member)
        yield from self._voice(member, rest, end=True)

    def _tag(self, text):
        """(the member, what they say) for "Name: words" — (None, "") if it isn't anyone's line."""
        found = re.match(r"^\W*([^:\n]{1,40}?)\s*:\s*(.*)$", text.strip())
        if not found:
            return None, ""
        member = self._who(_plain_tag(found.group(1)))
        if member is None or member.contact.id in self.call.muted:
            return None, ""
        return member, re.sub(r"^[*_\s]+|[*_\s]+$", "", found.group(2))

    def _voice(self, member, sentences, end=False):
        """
        Sentences of someone's line, said through their session (markers acted on, address guarded), as events in
        their voice — and noted for the record. Three sentences a line at most: it's a call, not a speech.
        """
        if not self._round or self._round[-1][0] is not member:
            self._round.append((member, []))
            yield {**events.reply_start(), "speaker": member.contact.id}
        said = self._round[-1][1]
        for sentence in sentences:
            if len(said) >= 3:
                break
            clean = member.scene_line(sentence, greeting=member is self._greeting,
                                      farewell=member is self._farewell)
            for part in (x for x in guards.split_sentences(clean or "") if x.strip()):
                said.append(part)
                yield {**events.sentence(len(said) - 1, part), "speaker": member.contact.id}
        if end and said:
            yield {**events.reply_end(" ".join(said)), "speaker": member.contact.id}

    def _said_lines(self):
        return [(m, " ".join(s)) for m, s in (self._round or []) if s]

    def _commit(self, ask, cut=False):
        """
        The round, as said: into the model's record of the call (exactly as written, so it carries on from there
        without reading it again), the call's transcript, and each of theirs — what they heard, what they said.
        """
        from .session import capped
        call = self.call
        said = self._said_lines()
        written = "\n".join(f"{self.label(m)}: {t}" for m, t in said) or "(nothing)"
        if cut and said:
            written += " —"
        self.messages += [{"role": "user", "content": ask}, {"role": "assistant", "content": written}]
        for member, text in said:
            call.last_speaker = member
            call.transcript.append((member.contact.id, text))
            heard_all = "\n".join(capped(member.heard))
            member.heard = []
            if heard_all:
                member.history.record_exchange(heard_all, text)
            else:
                member.history.record_aside(text)
            for other in call.others(member):
                other.heard.append(call.heard_as(member.contact.id, text, other))
            call._notice_asks(text)
        self._committed = said
        self._round = None

    def heard_only(self, sentences):
        """
        He cut in: of the last round, only the first `sentences` were heard — the rest was written, never said.
        The model's record of the call says so ("…—"), and the call's transcript keeps only what he heard.
        """
        said = getattr(self, "_committed", None)
        if not said or not self.messages or self.messages[-1]["role"] != "assistant":
            return
        kept, left = [], sentences
        for member, text in said:
            parts = [s for s in guards.split_sentences(text) if s.strip()]
            if left <= 0:
                break
            if len(parts) <= left:
                kept.append((member, text, False))
                left -= len(parts)
            else:
                kept.append((member, " ".join(parts[:left]), True))
                left = 0
        if len(kept) == len(said) and not any(c for _, _, c in kept):
            return
        written = "\n".join(f"{self.label(m)}: {t}" + (" —" if cut else "") for m, t, cut in kept) or "(nothing)"
        self.messages[-1] = {"role": "assistant", "content": written}
        unheard = said[len(kept):]
        for member, text in unheard:
            for i in range(len(self.call.transcript) - 1, -1, -1):
                if self.call.transcript[i] == (member.contact.id, text):
                    del self.call.transcript[i]
                    break
        self._committed = [(m, t) for m, t, _ in kept]

    def keep_warm(self):
        """Read the call so far, up to the last line said, so the next round starts from it."""
        if not self.messages or not self.call.members:
            return
        member0 = self.call.members[0]
        payload = ([{"role": "system", "content": self.system()}] + self.messages)
        with model.working():
            ollama.chat(model=member0.contact.model, messages=payload, stream=False,
                        options=member0._options(num_predict=1), **member0._extra())

    def cancel(self):
        """He's cut in: whatever's being written stops now — the connection cut, mid-read or mid-line."""
        self._cancelled += 1
        self._close()

    def _close(self):
        client, self._client = self._client, None
        if client is not None:
            try:
                client._client.close()
            except Exception:
                pass


def _plain_tag(tag):
    """A line's tag as the model may dress it — "**Dick**", "Dick (Nightwing)" — to the name inside."""
    return re.sub(r"\s*\([^)]*\)\s*", " ", re.sub(r"[*_]+", "", tag or "")).strip()


def prompting_chat():
    """The real ollama.chat — a test's stand-in is called as it is, and has no connection to cut."""
    from .session import _OLLAMA_CHAT
    return _OLLAMA_CHAT


def enabled():
    return config.GROUP_SCENE
