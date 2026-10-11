"""
Sealing what's stored: encrypted with the memory key when one's set (see
wayne.memory.store), plain UTF-8 when not — per value, so a database written
before the key was turned on still reads, and is sealed row by row as each row
is next written.

A value that won't open — the key changed, or was mistyped — is copied into
the quarantine table before it's treated as missing, the way an undecryptable
JSON file used to be moved aside: whatever overwrites it later, the original is
still there to recover with the right key.
"""
import hashlib
import json
import logging
import time

from ..memory import store as files

_log = logging.getLogger("wayne")
_warned = set()


def dumps(value):
    """The one JSON spelling everything's stored in — compact, so equal values compare equal as text."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def seal(text):
    return files.encrypt(text)


def unseal(blob, where=None, conn=None):
    """
    The text in a sealed value, or None if it can't be opened (and it's been
    quarantined, given `where` = (table, key) and a connection to keep it on).
    """
    if blob is None:
        return None
    raw = bytes(blob)
    cipher = files._cipher()
    if cipher is not None and files._looks_encrypted(raw):
        try:
            return cipher.decrypt(raw).decode()
        except Exception:
            pass
    if files._looks_encrypted(raw):
        _quarantine(raw, where, conn)
        return None
    try:
        return raw.decode()
    except UnicodeDecodeError:
        _quarantine(raw, where, conn)
        return None


def _quarantine(raw, where, conn):
    if where is None or conn is None:
        return
    table, key = where
    sha = hashlib.sha256(raw).hexdigest()
    try:
        conn.execute("INSERT OR IGNORE INTO quarantine(tbl, pk, sha, blob, at) VALUES (?, ?, ?, ?, ?)",
                     (table, str(key), sha, raw, time.time()))
    except Exception:
        pass
    if (table, str(key)) not in _warned:
        _warned.add((table, str(key)))
        _log.warning("stored value %s/%s could not be decrypted (wrong WAYNE_MEMORY_KEY?); a copy is kept in "
                     "the quarantine table", table, key)
