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

from .. import operator
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
    return [{"from": e["actor"], "text": e["text"], "at": e["known_at"]}
            for e in events.known_to(contact_id, "hearsay", limit=_KEEP)]


def _pass_on(source, text, reaching, source_id=""):
    """`source` (a name) tells `text` to everyone in `reaching` — [(contact id, when it reaches them)]."""
    if reaching:
        events.add("hearsay", text, actor=source, links=[("contact", source_id)] + [("contact", c) for c, _ in reaching],
                   audience=[(c, at, f"{source} told you") for c, at in reaching], secret=False)


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
        news = _notable(session, exchanges)
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


def spread(source, text, to, delay=_DELAY):
    """Something big travels: `source` (a name) tells each of `to` (contact ids), in their own time."""
    with _lock:
        _pass_on(source, text, [(other, time.time() + random.uniform(*delay)) for other in to])


def clear(contact_id):
    events.forget_audience(contact_id, "hearsay")
