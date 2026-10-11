"""
Every test in its own world.

Without this, tests wrote into the real data/ (the console's log, rogue
statuses, a contact called "test"), read the live city's rolls (a breakout on
14 October failed a test that had passed for weeks), and shared module caches
in whatever order they ran — so the suite went red on its own, depending on
the date, the hour and the order. Here:

  * the data directory is a fresh temporary one before anything imports the
    engine, and each test gets its own under it — and with it a database of
    its own (wayne.store refuses to open the real one under pytest);
  * the engine's in-memory caches start empty for every test;
  * `clock` freezes time at a fixed weekday noon for tests about the world.
"""
import os
import tempfile
import time

import pytest

# Before wayne.paths is imported anywhere: it reads this once, and the web module opens its log under it at import.
os.environ.setdefault("WAYNE_DATA_DIR", tempfile.mkdtemp(prefix="wayne-tests-"))
os.environ.setdefault("ELEVENLABS_API_KEY", "")


def _forget_caches():
    """Every module-level cache that remembers the last test's world — and its database, closed."""
    from wayne import store
    from wayne.engine import diary, incidents, markets, patrols, plans, presence
    store.close_all()
    presence._registry.clear()
    presence._LEADS.clear()
    diary.forget()
    patrols._rosters.clear()
    incidents._chases.clear()
    getattr(incidents, "_holds", {}).clear()
    plans._read.clear()
    markets._cache.clear()


@pytest.fixture(autouse=True)
def own_world(tmp_path, monkeypatch):
    """A data directory of the test's own (tests that set their own still can), and no leftover caches."""
    from wayne import paths
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "contact_dir", lambda cid: data / cid)
    _forget_caches()
    yield data
    _forget_caches()


# A Wednesday, at noon: no breakout's moving the scanner, nobody's mid-patrol, nothing anchored to today.
NOON = time.mktime((2026, 10, 14, 12, 0, 0, 0, 0, -1))


@pytest.fixture
def clock(monkeypatch):
    """Time frozen at NOON (and settable: `clock.at(...)`), for tests that are about the world rather than now."""
    class Clock:
        def __init__(self):
            self.now = NOON

        def at(self, when):
            self.now = when
            return when

        def __call__(self):
            return self.now
    frozen = Clock()
    real_localtime, real_gmtime, real_strftime = time.localtime, time.gmtime, time.strftime
    monkeypatch.setattr(time, "time", frozen)
    # Asked with no moment, these read the real clock underneath time.time — a test frozen at noon on the 14th
    # still saw today's date in them.
    monkeypatch.setattr(time, "localtime", lambda secs=None: real_localtime(frozen() if secs is None else secs))
    monkeypatch.setattr(time, "gmtime", lambda secs=None: real_gmtime(frozen() if secs is None else secs))
    monkeypatch.setattr(time, "strftime", lambda fmt, t=None: real_strftime(fmt, real_localtime(frozen()) if t is None else t))
    return frozen
