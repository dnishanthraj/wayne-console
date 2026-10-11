"""
The console's own voice — the Batcomputer — for the moments that belong to
the machine rather than to anyone on the line: unlocking, locking, placing a call and ending one, the Batwing coming and going,
a route set and arrived at, a case taken, reached, gone wrong, closed, the
scanner's worst, a message on the secure line, the link dropping.

These lines are written in advance on purpose. Everywhere a character speaks,
what they say is generated, because a person never says the same thing twice;
a system is the one voice that should sound like a system. A few variants per
moment keep it from sounding like a recording.

Every line is synthesised once and kept: as an mp3 under
DATA_DIR/_system_voice/<voice>/<moment>/<the line>.mp3, readable and
playable as files. Everything that could be said — every moment, for everyone
in the directory — is rendered at start in the background (see prime), so
nothing the console says waits on the network or spends a credit twice; a new
contact, or a new line here, is rendered the next time it starts (or by
scripts/voice_lines.py, now).

Optional: set BATCOMPUTER_VOICE_ID. Without it the console is silent at these
moments, as it always was.
"""
import hashlib
import logging
import os
import random
import re
import threading
import time

from .. import config, paths

_log = logging.getLogger("wayne")

LINES = {
    # Getting in, by the passcode — and nothing it says claims more than that (no "voice print").
    "unlock": [
        "Passcode accepted. Good {period}.",
        "Access granted. Good {period}.",
        "Authentication complete. Welcome back.",
    ],
    "denied": [
        "Access denied.",
        "Passcode rejected.",
    ],
    "lockout": [
        "Too many attempts. Terminal locked.",
        "Lockout engaged.",
    ],
    "lock": [
        "Console secured.",
        "Session locked.",
        "Locking down. Goodbye.",
    ],
    # Calls.
    "call": [
        "Establishing secure line to {name}.",
        "Opening encrypted channel to {name}.",
        "Routing call to {name}.",
    ],
    "end": [
        "Line closed.",
        "Channel terminated.",
        "Connection ended.",
    ],
    "add": [
        "Adding {name} to the call.",
        "Patching {name} in.",
        "Bringing {name} onto the line.",
    ],
    "drop": [
        "{name} has left the call.",
        "{name} is off the line.",
    ],
    "incoming": [
        "Incoming call. {name}.",
        "{name} is calling.",
        "Secure line request from {name}.",
    ],
    "declined": [
        "{name} declined the call.",
        "Call rejected by {name}.",
        "{name} can't take the call.",
    ],
    "unavailable": [
        "{name} is not available.",
        "No answer from {name}'s line.",
    ],
    # The Batwing.
    "jet_summoned": [
        "Batwing inbound.",
        "The Batwing is on its way.",
        "Batwing dispatched to your position.",
    ],
    "jet_overhead": [
        "Batwing overhead.",
        "The Batwing is holding above you.",
    ],
    "jet_home": [
        "Batwing returning to the hangar.",
        "The Batwing is heading home.",
    ],
    "jet_busy": [
        "The Batwing is on another job.",
    ],
    "lift": [
        "Batwing en route to {name}.",
        "Picking up {name}.",
    ],
    # Getting about.
    "route": [
        "Route plotted.",
        "Course set.",
    ],
    "route_suit": [
        "Suit systems online. Route plotted.",
        "Course set. Suit systems nominal.",
    ],
    "arrived": [
        "Destination reached.",
        "You've arrived.",
    ],
    # Cases, by the masks — the Batcomputer's names for them.
    "case_taken": [
        "{name} has taken the case.",
        "Case assigned to {name}.",
    ],
    "case_scene": [
        "{name} is on scene.",
        "{name} has arrived on scene.",
    ],
    "case_trouble": [
        "{name} needs backup.",
        "Situation deteriorating. {name} is in trouble.",
    ],
    "case_hurt": [
        "{name} is hurt.",
        "{name} has been injured.",
    ],
    "case_closed": [
        "Case closed.",
        "Case resolved.",
    ],
    # The city.
    "scanner_major": [
        "Priority call on the scanner.",
        "Major incident reported.",
    ],
    "breakout": [
        "Breakout reported.",
        "Escape in progress. Checking the files.",
    ],
    "mail_secure": [
        "Secure message received.",
        "New message on the secure line.",
    ],
    "night_report": [
        "The night report is ready.",
    ],
    # The line to the console itself.
    "link_lost": [
        "Connection lost.",
        "Link down. Reconnecting.",
    ],
    "link_back": [
        "Link restored.",
        "Connection re-established.",
    ],
}

# The moments that name someone by the Batcomputer's name for them — their mask, if they have one.
_BY_MASK = {"case_taken", "case_scene", "case_trouble", "case_hurt", "lift"}

_lock = threading.Lock()


def available():
    return bool(config.BATCOMPUTER_VOICE_ID and config.ELEVENLABS_API_KEY)


def _period(hour=None):
    hour = time.localtime().tm_hour if hour is None else hour
    return "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 18 else "evening"


def spoken_name(contact):
    """What the Batcomputer calls someone on a call: "Commissioner Gordon", or their full name."""
    return getattr(contact, "system_name", "") or contact.full_name


def name_for(event, contact):
    """Who someone is, said by the Batcomputer for this moment: Robin on a case, Tim Drake on a call."""
    if contact is None:
        return ""
    if event in _BY_MASK:
        return getattr(contact, "mask", "") or contact.name
    return spoken_name(contact)


def line(event, name=""):
    """A line for this moment, chosen at random, with the blanks filled — '' for a moment that needs a name it lacks."""
    options = LINES.get(event)
    if not options:
        return ""
    if not name:
        options = [o for o in options if "{name}" not in o]
        if not options:
            return ""
    return random.choice(options).format(name=name, period=_period())


# --- the clips on disk ------------------------------------------------------------------------

def _voice_dir():
    """Clips for this voice and model — a different voice, or a different model, is a different set."""
    tag = hashlib.sha1(f"{config.BATCOMPUTER_VOICE_ID}|{config.ELEVENLABS_MODEL}".encode()).hexdigest()[:8]
    return paths.DATA_DIR / "_system_voice" / tag


def _filename(text):
    """The line itself, as a file name a person can read: 'Batwing inbound.mp3'."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", text).strip().rstrip(". ")
    return (name[:150] or hashlib.sha1(text.encode()).hexdigest()[:12]) + ".mp3"


def _cache_path(text, event=""):
    return _voice_dir() / (event or "other") / _filename(text)


def _old_path(text):
    """Where a clip lived before they had names: a hash of the voice, the model and the line."""
    key = hashlib.sha1(f"{config.BATCOMPUTER_VOICE_ID}|{config.ELEVENLABS_MODEL}|{text}"
                       .encode()).hexdigest()[:20]
    return paths.DATA_DIR / "_system_voice" / f"{key}.mp3"


def _keep(path, clip):
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written aside and renamed, so a half-written clip is never served.
        tmp = path.with_suffix(".part")
        tmp.write_bytes(clip)
        os.replace(tmp, path)


def cached(text, event=""):
    """The clip for a line if it's on disk already (moving one kept under its old name), else None."""
    path = _cache_path(text, event)
    if path.exists():
        return path.read_bytes()
    old = _old_path(text)
    if old.exists():
        clip = old.read_bytes()
        _keep(path, clip)
        try:
            old.unlink()
        except OSError:
            pass
        return clip
    return None


def audio(text, voice_engine, event=""):
    """mp3 bytes for a line, from disk if it has been said before — synthesised once, then never again."""
    clip = cached(text, event)
    if clip is not None:
        return clip
    # Within the voices' concurrency cap: priming a hundred lines at startup
    # outside it took a slot from the first call's greeting, which then 429'd.
    clip = voice_engine.synthesize_capped(text, config.BATCOMPUTER_VOICE_ID)
    _keep(_cache_path(text, event), clip)
    return clip


def every_line(contacts):
    """[(moment, line)] for everything the Batcomputer could say about these contacts — what prime renders."""
    out = []
    for event, options in LINES.items():
        for option in options:
            if "{name}" in option:
                whom = [c for c in contacts if event not in _BY_MASK or getattr(c, "mask", "")]
                names = sorted({name_for(event, c) for c in whom})
            else:
                names = [""]
            periods = ("morning", "afternoon", "evening") if "{period}" in option else ("",)
            for name in names:
                for period in periods:
                    out.append((event, option.format(name=name, period=period)))
    return out


def status(contacts):
    """How much of it is on disk: (lines, cached, missing [(moment, line)])."""
    lines = every_line(contacts)
    missing = [(e, t) for e, t in lines if not _cache_path(t, e).exists() and not _old_path(t).exists()]
    return len(lines), len(lines) - len(missing), missing


def prime(voice_engine, contacts, quiet=True):
    """
    Synthesise every line ahead of time, so none waits on the network. Lines
    already on disk cost nothing; a failure is tried past, and only a run of
    them (no quota, no network) stops it — the rest are rendered when first
    asked for, or at the next start.
    """
    if not available():
        return 0
    made, failures = 0, 0
    for event, text in every_line(contacts):
        if cached(text, event) is not None:
            continue
        try:
            audio(text, voice_engine, event)
            made += 1
            failures = 0
        except Exception as exc:
            failures += 1
            if not quiet:
                _log.warning("system voice: couldn't render %r (%s)", text, exc)
            if failures >= 3:
                break
            time.sleep(2 * failures)
    if made:
        _log.info("system voice: rendered %d new line(s)", made)
    return made
