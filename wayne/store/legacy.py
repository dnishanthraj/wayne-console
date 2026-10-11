"""
The JSON files a console kept before the database: brought in once, and — for
going back to an older version — written out again.

Bringing them in (`adopt`) happens the first time a database is opened in a
data directory that has them. Everything goes in one transaction, read back
through the same code the console reads with, and compared with what the files
said; anything that doesn't match rolls the lot back and nothing has changed.
Only once it's all in and checked are the files moved, untouched, to
DATA_DIR/_imported-<when>/ — a backup, and out of the way, so nothing ever
mistakes a stale file for the live one.

A file that can't be read — encrypted with a key that's gone, or not JSON —
is left where it is, said so in the log, and skipped, exactly as the console
used to treat one: as empty.

`export` writes the database back out as the files, in the old shapes, for a
console from before it (scripts/export_db_to_json.py).
"""
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from ..memory import store as files

_log = logging.getLogger("wayne")

# Under data/<contact>/.
EACH = ("history.json", "texts.json", "vault.txt", "story.txt", "presence.json", "diary.json", "hearsay.json",
        "bio.txt")
# Under data/: files that were {key: value} and are now a space a row per key.
SPACES = {"_rogue_status.json": "rogue_status", "_encountered.json": "encountered",
          "_codex_notes.json": "codex_notes", "_portrait_frames.json": "frames", "_codex_places.json": "place_edits",
          "_culture.json": "culture", "_gazette.json": "gazette", "_dispatch.json": "dispatch",
          "_reportlog.json": "reportlog", "_mutes.json": "mutes"}
# ... single documents.
DOCS = {"_arcs.json": "arcs", "_jet.json": "jet", "_bruce.json": "bruce", "_initiative.json": "initiative"}
# ... lists of things with ids, now a row each.
LISTS = {"_cases.json": "cases", "_pins.json": "pins"}
OTHERS = ("_plans.json", "_inbox.json")


class Mismatch(RuntimeError):
    """What went in didn't read back the same: nothing is kept."""


# --- finding and reading the files -------------------------------------------------

def _people(root):
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
                  and p.name != "ambience" and any((p / n).exists() for n in EACH))


def found(root):
    """Every file there is to bring in, as paths."""
    root = Path(root)
    if not root.exists():
        return []
    out = [root / n for n in (*SPACES, *DOCS, *LISTS, *OTHERS) if (root / n).is_file()]
    for person in _people(root):
        out += [person / n for n in EACH if (person / n).is_file()]
    groups = root / "_groups"
    if groups.is_dir():
        for folder in sorted(groups.iterdir()):
            out += [folder / n for n in ("group.json", "messages.json") if (folder / n).is_file()]
    return out


def _read(path, skipped):
    """A file's text, decrypted — without moving it aside, as read_text would — or None, noted in `skipped`."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        skipped[str(path)] = f"unreadable: {exc}"
        return None
    text = files.decrypt(raw)
    if raw and not text and files._looks_encrypted(raw):
        skipped[str(path)] = "encrypted with a key that isn't this one"
        return None
    return text


def _json(path, skipped, kind):
    text = _read(path, skipped)
    if text is None or not text.strip():
        return None
    try:
        value = json.loads(text)
    except ValueError:
        skipped[str(path)] = "not JSON"
        return None
    if not isinstance(value, kind):
        skipped[str(path)] = f"not a {kind.__name__}"
        return None
    return value


def _unique_ids(messages):
    """Every message with an id, and no two the same (the UNIQUE the database keeps) — fixed in the source too."""
    seen = set()
    for m in messages:
        if not m.get("id") or m["id"] in seen:
            m["id"] = uuid.uuid4().hex[:12]
        seen.add(m["id"])
    return messages


def _lines(text):
    return [line.lstrip("- ").strip() for line in (text or "").splitlines() if line.strip()]


def _same(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def _words(text):
    return " ".join((text or "").split())


# --- bringing them in ---------------------------------------------------------------

def adopt(db):
    """Bring a data directory's JSON files into this (new) database. Returns {"imported": n, "skipped": {...}}."""
    root = db.path.parent
    paths_found = found(root)
    if not paths_found:
        db.set_meta("created_at", time.time())
        return {"imported": 0, "skipped": {}}
    started = time.time()
    skipped, checks = {}, []
    with db.tx():
        _bring_in(db, root, skipped, checks)
        wrong = [what for what, ok in ((what, check()) for what, check in checks) if not ok]
        if wrong:
            raise Mismatch("these didn't read back as the files had them: " + ", ".join(wrong))
        manifest = {str(p.relative_to(root)): {"bytes": p.stat().st_size,
                                               "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in paths_found if str(p) not in skipped}
        db.set_meta("imported_at", started)
        db.set_meta("imported", json.dumps(manifest))
        if skipped:
            db.set_meta("import_skipped", json.dumps(skipped))
    aside = _set_aside(root, [p for p in paths_found if str(p) not in skipped], started)
    if aside:
        db.set_meta("imported_to", str(aside.relative_to(root)))
    for path, why in skipped.items():
        _log.warning("not imported, left where it is: %s (%s)", path, why)
    _log.info("brought %d file(s) into %s in %.2fs%s", len(paths_found) - len(skipped), db.path.name,
              time.time() - started, f"; the originals are in {aside.name}" if aside else "")
    return {"imported": len(paths_found) - len(skipped), "skipped": skipped, "aside": str(aside or "")}


def _bring_in(db, root, skipped, checks):
    from ..engine import diary
    from ..memory.history import History
    from . import events, transcript
    from .threads import Thread

    space = db.space

    # Documents, and {key: value} files.
    for name, doc in DOCS.items():
        value = _json(root / name, skipped, (dict, list)) if (root / name).is_file() else None
        if value is not None:
            space("doc").put(doc, value)
            checks.append((name, lambda doc=doc, value=value: _same(space("doc").get(doc), value)))
    for name, kept in SPACES.items():
        value = _json(root / name, skipped, dict) if (root / name).is_file() else None
        if value:
            space(kept).replace(value)
            checks.append((name, lambda kept=kept, value=value: _same(space(kept).dict(), value)
                           and list(space(kept).keys()) == list(value)))
    for name, kept in LISTS.items():
        value = _json(root / name, skipped, list) if (root / name).is_file() else None
        if value:
            rows = {}
            for row in value:
                if isinstance(row, dict) and row.get("id"):
                    rows[str(row["id"])] = row
            space(kept).replace(rows)
            checks.append((name, lambda kept=kept, rows=rows: _same(space(kept).list(), list(rows.values()))))

    plans = _json(root / "_plans.json", skipped, dict) if (root / "_plans.json").is_file() else None
    if plans and plans.get("plans"):
        rows = {p["id"]: p for p in plans["plans"] if isinstance(p, dict) and p.get("id")}
        space("plans").replace(rows)
        checks.append(("_plans.json", lambda rows=rows: _same(space("plans").list(), list(rows.values()))))

    inbox = _json(root / "_inbox.json", skipped, dict) if (root / "_inbox.json").is_file() else None
    if inbox:
        mail = sorted((m for m in inbox.get("mail") or [] if isinstance(m, dict) and m.get("id")), key=lambda m: m["at"])
        space("mail").replace({m["id"]: m for m in mail})
        space("mail_keys").replace({str(k): time.time() for k in inbox.get("keys") or []})
        space("mail_events").replace({uuid.uuid4().hex[:12]: e for e in inbox.get("events") or []})
        checks.append(("_inbox.json", lambda mail=mail, inbox=inbox: _same(space("mail").list(), mail)
                       and set(space("mail_keys").keys()) == {str(k) for k in inbox.get("keys") or []}
                       and _same(space("mail_events").list(), list(inbox.get("events") or []))))

    groups = root / "_groups"
    if groups.is_dir():
        for folder in sorted(p for p in groups.iterdir() if (p / "group.json").is_file()):
            meta = _json(folder / "group.json", skipped, dict)
            if meta is None:
                continue
            said = _unique_ids(_json(folder / "messages.json", skipped, list) or []) \
                if (folder / "messages.json").is_file() else []
            space("groups").put(folder.name, meta)
            thread = Thread(f"g:{folder.name}")
            for m in said:
                thread.add(m)
            checks.append((f"_groups/{folder.name}", lambda gid=folder.name, meta=meta, said=said, thread=thread:
                           _same(space("groups").get(gid), meta) and _same(thread.all(), said)))

    for person in _people(root):
        cid = person.name
        if (person / "history.json").is_file():
            window = _json(person / "history.json", skipped, list)
            if window is not None:
                space("window").put(cid, window)
                for message in window:
                    line = History._line(message) if isinstance(message, dict) and message.get("role") else None
                    if line:
                        transcript.keep(cid, message["role"], message["at"], line, message.get("via"))
                checks.append((f"{cid}/history.json", lambda cid=cid, window=window: _same(space("window").get(cid), window)))
        if (person / "texts.json").is_file():
            said = _unique_ids(_json(person / "texts.json", skipped, list) or [])
            thread = Thread(f"dm:{cid}")
            for m in said:
                thread.add(m)
            checks.append((f"{cid}/texts.json", lambda thread=thread, said=said: _same(thread.all(), said)))
        for kind in ("vault", "story"):
            path = person / f"{kind}.txt"
            if path.is_file():
                text = _read(path, skipped)
                if text is not None:
                    facts = _lines(text)
                    space(f"{kind}/{cid}").replace({uuid.uuid4().hex[:12]: f for f in facts})
                    checks.append((f"{cid}/{kind}.txt", lambda kind=kind, cid=cid, facts=facts:
                                   space(f"{kind}/{cid}").list() == facts))
        if (person / "bio.txt").is_file():
            text = _read(person / "bio.txt", skipped)
            if text and text.strip():
                space("bio").put(cid, text.strip())
                checks.append((f"{cid}/bio.txt", lambda cid=cid, text=text: space("bio").get(cid) == text.strip()))
        if (person / "presence.json").is_file():
            state = _json(person / "presence.json", skipped, dict)
            if state:
                space(f"presence/{cid}").replace(state)
                checks.append((f"{cid}/presence.json", lambda cid=cid, state=state:
                               _same(space(f"presence/{cid}").dict(), state)))
        if (person / "diary.json").is_file():
            book = _json(person / "diary.json", skipped, dict) or {}
            lines = [e for e in book.get("entries") or [] if isinstance(e, dict) and _words(e.get("text"))]
            for entry in lines:
                diary._record(cid, {**entry, "text": _words(entry["text"])})
            if book.get("last"):
                space("diary_last").put(cid, book["last"])

            def diary_ok(cid=cid, lines=lines, last=book.get("last")):
                back = [{"at": e["at"], "kind": e["kind"][len("diary."):], "text": e["text"], **e["data"]}
                        for e in events.of(cid, "diary.")]
                want = sorted(({**e, "text": _words(e["text"])} for e in lines), key=lambda e: e["at"])
                return _same(sorted(back, key=lambda e: e["at"]), want) and (not last or _same(space("diary_last").get(cid), last))
            checks.append((f"{cid}/diary.json", diary_ok))
        if (person / "hearsay.json").is_file():
            heard = [h for h in _json(person / "hearsay.json", skipped, list) or []
                     if isinstance(h, dict) and _words(h.get("text"))]
            for h in heard:
                events.add("hearsay", h["text"], at=h.get("at"), actor=h.get("from", ""),
                           audience=[(cid, h.get("at"), f"{h.get('from', 'someone')} told you")])

            def heard_ok(cid=cid, heard=heard):
                back = {(e["actor"], e["text"], e["known_at"]) for e in events.known_to(cid, "hearsay")}
                return back == {(h.get("from", ""), _words(h["text"]), h.get("at")) for h in heard}
            checks.append((f"{cid}/hearsay.json", heard_ok))

    # How each closed case went, as the record keeps it from now on.
    from ..engine import cases
    for case in space("cases").list():
        if case.get("status") == "closed":
            try:
                cases._remember(case)
            except Exception:
                _log.exception("couldn't record how case %s went", case.get("id"))


def _set_aside(root, moved, stamp):
    """The files, untouched, into _imported-<when>/ — keeping where each was. Returns the folder."""
    if not moved:
        return None
    aside = root / time.strftime("_imported-%Y%m%d-%H%M%S", time.localtime(stamp))
    for path in moved:
        target = aside / path.relative_to(root)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
        except OSError:
            _log.exception("couldn't move %s aside; it's ignored from now on", path)
    # Folders the move emptied.
    for folder in sorted({p.parent for p in moved}, key=lambda p: -len(p.parts)):
        try:
            if folder != root and not any(folder.iterdir()):
                folder.rmdir()
                if folder.parent.name == "_groups" and not any(folder.parent.iterdir()):
                    folder.parent.rmdir()
        except OSError:
            pass
    return aside


# --- writing them out again --------------------------------------------------------

def export(root):
    """
    The database written out as the JSON files an older console reads, under
    `root` (which must not be the live data directory while the console runs).
    Returns the paths written. Only what those files ever held: what came after
    the database — his diary (engagements), the bonds between them, tastes, who
    knows the secret, the papers' view of him, the city's ambience — was never
    in a file an older console reads, and stays in the database (and its daily
    backups) alone.
    """
    from ..engine import diary
    from . import db as database
    from . import events
    from .threads import Thread
    store = database.get()
    root = Path(root)
    written = []

    def write(path, text):
        files.atomic_write(path, text)
        written.append(path)

    def dump(value):
        return json.dumps(value, ensure_ascii=False)

    for name, doc in DOCS.items():
        value = store.space("doc").get(doc)
        if value is not None:
            write(root / name, dump(value))
    for name, kept in SPACES.items():
        if len(store.space(kept)):
            write(root / name, dump(store.space(kept).dict()))
    for name, kept in LISTS.items():
        if len(store.space(kept)):
            write(root / name, dump(store.space(kept).list()))
    if len(store.space("plans")):
        write(root / "_plans.json", dump({"plans": store.space("plans").list()}))
    if len(store.space("mail")) or len(store.space("mail_events")):
        write(root / "_inbox.json", dump({"mail": store.space("mail").list(), "keys": store.space("mail_keys").keys(),
                                          "events": store.space("mail_events").list()}))
    for gid, meta in store.space("groups").dict().items():
        write(root / "_groups" / gid / "group.json", json.dumps(meta, indent=1))
        write(root / "_groups" / gid / "messages.json", dump(Thread(f"g:{gid}").all()))

    people = set()
    for name in store.space("window").keys():
        people.add(name)
    for row in store.rows("SELECT DISTINCT space FROM kv WHERE space LIKE 'presence/%' OR space LIKE 'vault/%' "
                          "OR space LIKE 'story/%'"):
        people.add(row[0].split("/", 1)[1])
    people |= set(store.space("bio").keys()) | set(store.space("diary_last").keys())
    people |= {r[0][3:] for r in store.rows("SELECT DISTINCT thread FROM messages WHERE thread LIKE 'dm:%'")}
    people |= {r[0] for r in store.rows("SELECT DISTINCT actor FROM events WHERE kind LIKE 'diary.%'")}
    people |= {r[0] for r in store.rows("SELECT DISTINCT contact_id FROM event_audience a JOIN events e "
                                        "ON e.id = a.event_id WHERE e.kind = 'hearsay'")}
    for cid in sorted(p for p in people if p):
        folder = root / cid
        window = store.space("window").get(cid)
        if window:
            write(folder / "history.json", json.dumps(window, indent=2))
        said = Thread(f"dm:{cid}").all()
        if said:
            write(folder / "texts.json", dump(said))
        for kind in ("vault", "story"):
            facts = store.space(f"{kind}/{cid}").list()
            if facts:
                write(folder / f"{kind}.txt", "".join(f"- {f}\n" for f in facts))
        bio = store.space("bio").get(cid)
        if bio:
            write(folder / "bio.txt", bio)
        state = store.space(f"presence/{cid}").dict()
        if state:
            write(folder / "presence.json", json.dumps(state, indent=1))
        lines = [{"at": e["at"], "kind": e["kind"][len("diary."):], "text": e["text"], **e["data"]}
                 for e in events.of(cid, "diary.", since=time.time() - diary.KEEP, limit=diary.MOST)]
        last = store.space("diary_last").get(cid)
        if lines or last:
            write(folder / "diary.json", dump({"entries": lines, "last": last or {}}))
        heard = [{"from": e["actor"], "text": e["text"], "at": e["known_at"]}
                 for e in events.known_to(cid, "hearsay", limit=12)][::-1]
        if heard:
            write(folder / "hearsay.json", json.dumps(heard, indent=1))
    return written


def copy_out(src_dir, dst_dir, ignore=("_backup-*", "_imported-*", "ambience", "console.log")):
    """
    A data directory copied — the database by SQLite's backup, so a copy taken
    while the console runs is still whole; everything else as files.
    """
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    shutil.copytree(src_dir, dst_dir, ignore=shutil.ignore_patterns(*ignore, "wayne.db", "wayne.db-wal", "wayne.db-shm",
                                                                     "wayne.failed-*"))
    source = src_dir / "wayne.db"
    if source.exists():
        import sqlite3
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(str(dst_dir / "wayne.db"))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    return dst_dir
