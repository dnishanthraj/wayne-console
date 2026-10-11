"""
News travels.

Tell Dick something on Tuesday and Barbara may know it by Thursday — "Dick
told me you…" — because that is how a family works. After a call ends, the one
or two things said that someone might pass on are noted; then, with a chance
set by how close two people are and after a delay a person would take, they
reach others as hearsay, with who it came from.

Kept honest three ways. Only what was actually said is noted, and nothing he
asked to keep quiet. News that touches a secret (see the operator profile's
`secrets`) never reaches anyone outside it — Selina cannot learn who Batwing is
from gossip. And hearsay is handed over as secondhand, to raise only when it
comes up naturally, so it stays an occasional surprise rather than a habit.
"""
import random
import re
import threading
import time

from .. import identity, operator
from ..store import events
from . import model

# How long news takes to travel, in seconds: from half an hour to most of a day.
_DELAY = (30 * 60, 14 * 3600)
_KEEP = 12            # hearsay entries kept per contact
_FRESH_DAYS = 10      # older than this and it's no longer worth mentioning
_lock = threading.Lock()


def _load(contact_id):
    """
    What's been passed on to them, the latest passed on first — {from, text, at}, `at` when it reaches them.
    Each is an event (kind hearsay) with them in its audience from that moment (see wayne.store.events).
    """
    return [{"from": teller(e, contact_id), "text": e["text"], "at": e["known_at"]}
            for e in events.known_to(contact_id, "hearsay", limit=_KEEP)]


def teller(event, viewer_id):
    """
    Who passed it on, as the one hearing it knows them: "Randy Wayne told you…" reached Jim, who knows a Batwing
    and no Randy. The name it was kept under for anyone the record can't place (news from outside the cast).
    """
    source = (event.get("data") or {}).get("source")
    if not source:
        from ..contacts import directory
        found = next((c for c in directory() if c.full_name == event.get("actor")), None)
        source = found.id if found else ""
    return identity.label(source, viewer_id, full=True) if source else event.get("actor", "")


def _pass_on(source, text, reaching, source_id=""):
    """`source` (a name) tells `text` to everyone in `reaching` — [(contact id, when it reaches them)]."""
    if reaching:
        events.add("hearsay", text, actor=source, links=[("contact", source_id)] + [("contact", c) for c, _ in reaching],
                   audience=[(c, at, f"{identity.label(source_id, c, full=True) if source_id else source} told you")
                             for c, at in reaching],
                   data={"source": source_id} if source_id else None, secret=False)


def closeness(a, b):
    net = operator.profile().get("network", {})
    return float(net.get(f"{a}|{b}", net.get(f"{b}|{a}", 0.0)))


# The night's work, whatever it's called: the masks' names alone let "Bruce
# asked Dick to stake out the docks for Black Mask's shipment" and "Tim got
# hurt on patrol" through to Selina.
_THE_WORK = re.compile(
    r"(?i)\b(patrol\w*|stake ?outs?|staking (it )?out|suits?|cowls?|capes?|masks?|gear|grapple\w*|"
    r"rooftops?|vigilante\w*|bat-?signal|batmobile|utility belt|cases?|the scanner|"
    r"joker|riddler|penguin|two-face|scarecrow|bane|poison ivy|ivy|mr\.? freeze|harley|killer croc|"
    r"hush|black mask|ra'?s al ghul|talia|deathstroke|zsasz|mad hatter|clayface|firefly|"
    r"arkham|blackgate)\b")


def _crosses_secret(text, contact_id):
    """
    Whether news would tell this contact more than they know (see wayne.identity): anything of the work, or a
    word of the secret's, to anyone who doesn't know everything. `contact_id` None: as if to someone outside.
    """
    from .. import identity
    if contact_id is not None and not identity.outside(contact_id):
        return False
    secrets = operator.profile().get("secrets", {})
    return (any(re.search(rf"\b{re.escape(t)}\b", text, re.I) for t in secrets.get("terms", []))
            or bool(_THE_WORK.search(text)))


def heard(contact_id, now=None):
    """What this contact has heard secondhand and can now mention, newest first."""
    now = now or time.time()
    items = [h for h in _load(contact_id)
             if h["at"] <= now and now - h["at"] < _FRESH_DAYS * 86400]
    return sorted(items, key=lambda h: -h["at"])[:3]


def block(contact_id):
    items = heard(contact_id)
    if not items:
        return ""
    now = time.time()

    def ago(t):
        hours = (now - t) / 3600
        return "earlier today" if hours < 12 else "yesterday" if hours < 36 else f"{int(hours // 24)} days ago"

    return ("Heard secondhand — raise one only if it comes up naturally, and rarely; you may say "
            "who told you:\n" + "\n".join(f"- {h['from']} told you {ago(h['at'])}: {h['text']}" for h in items))


def _line(session, message):
    """
    One line of the transcript, said by whoever said it: on a group call his side holds everyone else's
    labelled lines too, and every one of them went down as his — "Bruce said…" hearsay in Tim's words.
    His name is the one this contact knows him by: Batman, to Jim.
    """
    cid = session.contact.id
    if message["role"] != "user":
        return f"{session.contact.name}: {message['content']}"
    him = {operator.full_name(), operator.name(), operator.full_name(cid), operator.name(cid)}
    lines = []
    for part in message["content"].splitlines():
        labelled = re.match(r"^\(?([A-Z][\w.'-]*(?:\s[A-Z][\w.'-]*){0,2})\)?:\s*(.*)$", part.strip())
        if labelled and labelled.group(1) not in him:
            lines.append(f"{labelled.group(1)}: {labelled.group(2)}")
        else:
            lines.append(f"{operator.name(cid)}: {labelled.group(2) if labelled else part}")
    return "\n".join(lines)


def _notable(session, exchanges):
    """One model pass: what from this call might be passed on. [] if nothing."""
    transcript = "\n".join(_line(session, m) for m in exchanges)
    cid = session.contact.id
    him = operator.name(cid)            # as this contact knows him: what they'd pass on is "Batman said…", from Jim
    instruction = (
        f"Here is a conversation between {operator.full_name(cid)} and {session.contact.full_name}.\n\n"
        f"{transcript}\n\n"
        f"List at most two notable things {him} said or that happened in it that "
        f"{session.contact.name} might later mention to someone close to them. Only what was actually "
        f"said. Leave out anything {him} asked to keep private or quiet. Write each as a "
        f"short line beginning with '{him}'. If nothing is worth passing on, write NONE.")
    try:
        reply = model.ask(session.contact.model, [{"role": "user", "content": instruction}],
                          {**session.contact.options, "temperature": 0, "num_predict": 120},
                          think=model.thinking(session.contact), purpose="the grapevine")
    except Exception:
        return []
    if re.search(r"\bNONE\b", reply):
        return []
    lines = [re.sub(r"^[\s\-*•\d.]+", "", line).strip() for line in reply.splitlines()]
    return [line for line in lines if line.startswith(him)][:2]


def note_call(session, contacts, start_index, present=()):
    """After a call: note what might travel, and send it on its way. Background."""
    exchanges = [m for m in session.history.messages[start_index:] if m.get("content")]
    if len([m for m in exchanges if m["role"] == "user"]) < 2:
        return    # a hello and a goodbye carry no news
    secret_words = re.compile(r"\b(don'?t tell|keep (it|this) (quiet|between us)|between (us|you and me)|"
                              r"nobody (can|must) know|secret)\b", re.I)
    if any(secret_words.search(m["content"]) for m in exchanges if m["role"] == "user"):
        return

    def run():
        news = _notable(session, exchanges[-24:])         # the end of a long call is where the news is
        if not news:
            return
        source = session.contact
        for item in news:
            reaching = [(other.id, time.time() + random.uniform(*_DELAY)) for other in contacts
                        if other.id != source.id and other.id not in present     # they were there: not news
                        and not _crosses_secret(item, other.id)
                        and random.random() < closeness(source.id, other.id) * 0.6]
            with _lock:
                _pass_on(source.full_name, item, reaching, source.id)

    threading.Thread(target=run, daemon=True).start()


def note_said(source, item, contacts, present=()):
    """
    One thing he told `source` by text that they'd likely mention to someone close (read by the afterthought,
    see wayne.engine.initiative): on its way, as news from a call is — by closeness, in their own time.
    """
    him = operator.name(source.id)
    item = " ".join(str(item or "").split())[:200]
    if not item.startswith(him):
        return
    reaching = [(other.id, time.time() + random.uniform(*_DELAY)) for other in contacts
                if other.id != source.id and other.id not in present
                and not _crosses_secret(item, other.id)
                and random.random() < closeness(source.id, other.id) * 0.6]
    with _lock:
        _pass_on(source.full_name, item, reaching, source.id)


def spread(source, text, to, delay=_DELAY):
    """Something big travels: `source` (a name) tells each of `to` (contact ids), in their own time."""
    with _lock:
        _pass_on(source, text, [(other, time.time() + random.uniform(*delay)) for other in to])


def clear(contact_id):
    events.forget_audience(contact_id, "hearsay")
    events.forget_audience(contact_id, "relayed")


# --- word passed on for him ----------------------------------------------------------------------------------
# "Tell Tim to text me when he's free": Dick says he will, and does, when he gets round to it — and Tim, told,
# gets in touch ("Dick said you wanted me to text you?"), or doesn't, as he would. Read from the conversation
# by the afterthought (see wayne.engine.initiative), only when it was asked and agreed to.

_GETTING_ROUND = (3 * 60, 45 * 60)      # once they're looking at their phone, how long before they pass it on
_RELAY_FRESH_DAYS = 3


def seen_at(contact, t, rng=random):
    """When someone would see a message sent at `t`: as soon as they'd look — or once they're up, if they're not."""
    from . import presence
    whereabouts = presence.of(contact)
    for _ in range(36):                          # half-hours: a night's sleep and then some
        delay = presence.read_delay(contact, whereabouts.now(t), rng)
        if delay is not None:
            return t + delay
        t += 1800
    return None


def _carries(source, target, message):
    """
    Whether word from him can go this way without giving anyone away (see wayne.identity). To someone who
    knows only the masks it comes from a mask, with no name in it — Batgirl telling Jim that Batman wants a
    word, never Alfred; to someone outside the rest of it, nothing of the work.
    """
    from .. import identity
    known = identity.level(target.id)
    if known == "masks":
        if not getattr(source, "mask", ""):
            return False
        names = set(identity.guard_vocatives(source.id, [target.id])) | {operator.name(), operator.full_name()}
        return not any(re.search(rf"\b{re.escape(n)}\b", message, re.I) for n in names if n)
    if known in ("his", "none"):
        return not _crosses_secret(message, target.id)
    return True


def relay(source, asks, present=(), now=None, rng=random):
    """
    He asked `source` (a contact) to pass something on, and they said they would: each of `asks` —
    {"to": a first name, "message": what to tell them, "wants": "text" | "call" | None} — reaches its person in
    `source`'s own time, as word from them. When he wants that person to get in touch, they mean to (an
    intention, origin "relayed"; see presence.intend), from when they've heard. Someone who was on the call
    heard it for themselves. Returns [(target id, when it reaches them)].
    """
    from .. import identity
    from ..contacts import directory
    from . import bonds, presence
    now = now or time.time()
    book = directory()
    sent = []
    for ask in (asks if isinstance(asks, list) else [])[:2]:
        if not isinstance(ask, dict):
            continue
        target = book.find(str(ask.get("to") or "").strip()) if str(ask.get("to") or "").strip() else None
        message = " ".join(str(ask.get("message") or "").split())[:200].strip(" .")
        wants = ask.get("wants") if ask.get("wants") in ("text", "call") else None
        if (target is None or target.id == source.id or target.id in present or not message
                or message.lower() in ("null", "none", "nothing") or not _carries(source, target, message)):
            continue
        told = seen_at(source, now, rng)
        if told is None:
            continue
        told += rng.uniform(*_GETTING_ROUND)
        reaches = seen_at(target, told, rng)
        if reaches is None:
            continue
        sender = identity.label(source.id, target.id)          # Batgirl, to Jim; Barbara, to Dick
        him = operator.name(target.id)                           # Batman, to Jim
        line = f"{him} asked {sender} to pass on: {message}"
        events.add("relayed", line, actor=sender, links=[("contact", source.id), ("contact", target.id)],
                   audience=[(target.id, reaches, f"{sender} told you")], secret=False,
                   data={"wants": wants, "from": source.id})
        sent.append((target.id, reaches))
        if wants is None:
            continue
        # Told, they mean to — in their own time, and more slowly the less they want to: Randy sits on it.
        mood = bonds.effects(target, reaches)
        if rng.random() > max(0.35, min(0.97, 0.6 + 0.35 * mood["reach"])):
            continue
        due = reaches + rng.uniform(2, 25) * 60 + max(0.0, mood["quiet"] - 1) * rng.uniform(10, 50) * 60
        about = f"{sender} told you {him} wants you to {wants} him — the message: \"{message}\""
        presence.of(target).intend(wants, about, due, origin="relayed", arrives=reaches)
    return sent


def relayed(contact_id, now=None):
    """Word passed on to them from him that's reached them, newest first: [{from, text, at, wants}]."""
    now = now or time.time()
    found = [{"from": e["actor"], "text": e["text"], "at": e["known_at"]}
             for e in events.known_to(contact_id, "relayed", limit=6)]
    return sorted([r for r in found if r["at"] <= now and now - r["at"] < _RELAY_FRESH_DAYS * 86400],
                  key=lambda r: -r["at"])[:2]


def relayed_block(contact_id):
    """For their awareness: what's been passed on to them from him — theirs to act on, and to say who told them."""
    items = relayed(contact_id)
    if not items:
        return ""
    now = time.time()

    def ago(t):
        minutes = (now - t) / 60
        return ("just now" if minutes < 20 else "earlier today" if minutes < 12 * 60 else "yesterday"
                if minutes < 36 * 60 else f"{int(minutes // 1440)} days ago")
    return ("Passed on to you, from him — you know this; do what you'd do about it, and you may say who told you:\n"
            + "\n".join(f"- {r['from']} told you {ago(r['at'])}: {r['text']}" for r in items))
