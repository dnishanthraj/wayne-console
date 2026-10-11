"""The database: spaces, threads, events, sealing at rest, the import of the old files, and many writers at once."""
import json
import sqlite3
import threading
import time

import pytest

from wayne import paths, store
from wayne.memory import store as files
from wayne.store import events, legacy, threads, transcript


def _with_key(monkeypatch, key):
    monkeypatch.setattr("wayne.config.MEMORY_KEY", key)
    monkeypatch.setattr(files, "_fernet", None)
    monkeypatch.setattr(files, "_fernet_failed", False)


def _db_bytes():
    out = b""
    for suffix in ("", "-wal", "-shm"):
        path = paths.db_file().with_name("wayne.db" + suffix)
        if path.exists():
            out += path.read_bytes()
    return out


# --- spaces ---------------------------------------------------------------------------

class TestSpaces:
    def test_entries_keep_the_order_they_were_first_written_in(self):
        space = store.space("cases")
        for key in ("b", "a", "c"):
            space.put(key, {"id": key})
        space.put("a", {"id": "a", "status": "closed"})          # changed, not moved
        assert space.keys() == ["b", "a", "c"]
        assert [c["id"] for c in space.list()] == ["b", "a", "c"]
        assert list(space.dict()) == ["b", "a", "c"] and space.get("a")["status"] == "closed"

    def test_every_reader_gets_its_own_copy(self):
        space = store.space("plans")
        space.put("p", {"who": ["robin"]})
        space.get("p")["who"].append("nightwing")
        space.list()[0]["who"].append("batgirl")
        assert space.get("p") == {"who": ["robin"]}

    def test_saving_the_whole_board_writes_only_what_changed(self):
        space = store.space("cases")
        space.replace({str(i): {"id": str(i), "n": i} for i in range(50)})
        db = store.get()
        before = db.conn.total_changes
        board = space.dict()
        board["7"]["n"] = 700
        del board["9"]
        space.replace(board)
        assert db.conn.total_changes - before == 2           # one row changed, one deleted
        assert space.get("7")["n"] == 700 and "9" not in space

    def test_trim_keeps_the_newest(self):
        space = store.space("dispatch")
        for i in range(10):
            space.put(f"r{i}", f"call {i}")
        space.trim(3)
        assert space.keys() == ["r7", "r8", "r9"]

    def test_a_rolled_back_change_is_not_left_in_memory(self):
        space = store.space("doc")
        space.put("bruce", {"where": "Wayne Manor"})
        with pytest.raises(RuntimeError), store.get().tx():
            space.put("bruce", {"where": "Crime Alley"})
            raise RuntimeError("halfway")
        assert space.get("bruce") == {"where": "Wayne Manor"}

    def test_another_process_writing_is_noticed(self):
        space = store.space("doc")
        space.put("jet", {"legs": []})
        assert space.get("jet") == {"legs": []}
        other = sqlite3.connect(str(paths.db_file()))
        other.execute("UPDATE kv SET value = ? WHERE space = 'doc' AND key = 'jet'", (b'{"legs":[1]}',))
        other.commit()
        other.close()
        store.get().look(force=True)
        assert space.get("jet") == {"legs": [1]}

    def test_update_reads_changes_and_writes_in_one_step(self):
        space = store.space("doc")
        space.update("initiative", lambda log: log.append(1), default=[])
        space.update("initiative", lambda log: log + [2], default=[])
        assert space.get("initiative") == [1, 2]

    def test_tests_never_open_the_real_database(self, monkeypatch):
        monkeypatch.setattr(paths, "DATA_DIR", paths.ROOT_DIR / "data")
        with pytest.raises(RuntimeError, match="real database"):
            store.get()


# --- threads ---------------------------------------------------------------------------

class TestThreads:
    def _message(self, i, sender="me", **kw):
        return {"id": f"m{i}", "from": sender, "text": f"text {i}", "at": 1000.0 + i, **kw}

    def test_pages_back_from_the_newest(self):
        thread = threads.Thread("dm:robin")
        for i in range(10):
            thread.add(self._message(i))
        assert [m["id"] for m in thread.tail(3)] == ["m7", "m8", "m9"]
        assert [m["id"] for m in thread.tail(3, before=1005.0)] == ["m2", "m3", "m4"]

    def test_read_marks_live_in_columns_and_come_back_in_the_message(self):
        thread = threads.Thread("dm:robin")
        thread.add(self._message(1, "them"))
        thread.add(self._message(2, "them", kind="missed_call"))
        assert thread.mark("seen_at", 5.0, "sender = 'them' AND seen_at IS NULL AND kind IS NULL") == 1
        first, second = thread.all()
        assert first["seen_at"] == 5.0 and "seen_at" not in second

    def test_only_the_newest_are_kept_past_the_cap(self):
        thread = threads.Thread("g:abc")
        for i in range(12):
            thread.add(self._message(i), cap=5)
        assert [m["id"] for m in thread.all()] == ["m7", "m8", "m9", "m10", "m11"]

    def test_the_last_of_every_group_in_one_query(self):
        for gid in ("g:a", "g:b"):
            for i in range(3):
                threads.Thread(gid).add(self._message(i, text=gid))
        threads.Thread("dm:robin").add(self._message(9))
        lasts = threads.lasts("g:")
        assert set(lasts) == {"g:a", "g:b"} and lasts["g:a"]["id"] == "m2"

    def test_the_same_id_twice_in_a_thread_is_refused(self):
        thread = threads.Thread("dm:robin")
        thread.add(self._message(1))
        with pytest.raises(sqlite3.IntegrityError):
            thread.add(self._message(1))


# --- events ----------------------------------------------------------------------------

class TestEvents:
    def test_news_is_known_only_from_when_it_arrives(self):
        now = time.time()
        events.add("hearsay", "Bruce is selling the Batmobile", actor="Dick Grayson",
                   audience=[("batgirl", now + 3600, "Dick told you")])
        got = events.known_to("batgirl", "hearsay")
        assert got[0]["known_at"] == now + 3600 and got[0]["how"] == "Dick told you"
        assert events.known_to("robin", "hearsay") == []

    def test_what_it_is_about_is_queryable(self):
        first = events.add("case.closed", "the robbery at Ralli's", links=[("contact", "robin"), ("rogue", "The Joker")])
        events.add("case.closed", "the fire at Ace", links=[("contact", "nightwing")])
        about = store.get().rows("SELECT event_id FROM event_links WHERE type = 'rogue' AND ref = 'The Joker'")
        assert [r[0] for r in about] == [first]
        assert sorted(events.links([first])[first]) == [("contact", "robin"), ("rogue", "The Joker")]

    def test_an_actors_lines_by_kind(self):
        for i, kind in enumerate(("diary.trip", "diary.arrived", "hearsay", "diary.left")):
            events.add(kind, f"line {i}", at=100.0 + i, actor="robin")
        assert [e["text"] for e in events.of("robin", "diary.")] == ["line 0", "line 1", "line 3"]
        assert [e["text"] for e in events.of("robin", "diary.", limit=2)] == ["line 1", "line 3"]

    def test_forgetting_what_they_heard_leaves_what_others_heard(self):
        events.add("hearsay", "one", audience=["robin", "batgirl"])
        events.add("hearsay", "two", audience=["robin"])
        events.forget_audience("robin", "hearsay")
        assert events.known_to("robin", "hearsay") == []
        assert [e["text"] for e in events.known_to("batgirl", "hearsay")] == ["one"]
        assert store.get().row("SELECT COUNT(*) FROM events")[0] == 1


# --- sealed at rest ---------------------------------------------------------------------

class TestSealedAtRest:
    def test_nothing_personal_is_readable_in_the_files(self, monkeypatch):
        from wayne.memory import History, Vault
        from wayne.memory.texts import TextLog
        _with_key(monkeypatch, files.generate_key())
        History("alfred").record_exchange("I live at Wayne Manor", "Indeed you do, sir.")
        Vault("alfred").memorize("remember that the Kord deal closes Friday")
        TextLog("robin").add("me", "Meet me at the Wayne Manor gates")
        store.space("cases").put("c1", {"place": "Wayne Manor"})
        events.add("diary.arrived", "got to Wayne Manor", actor="alfred")
        store.get().conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        raw = _db_bytes()
        assert raw and b"Wayne Manor" not in raw and b"Kord" not in raw
        assert History("alfred").messages[0]["content"] == "I live at Wayne Manor"

    def test_what_was_written_before_the_key_still_reads(self, monkeypatch):
        store.space("doc").put("bruce", {"where": "the cave"})
        store.close_all()
        _with_key(monkeypatch, files.generate_key())
        assert store.doc("bruce") == {"where": "the cave"}

    def test_a_wrong_key_quarantines_rather_than_loses(self, monkeypatch):
        _with_key(monkeypatch, files.generate_key())
        store.space("doc").put("bruce", {"where": "the cave"})
        store.close_all()
        _with_key(monkeypatch, files.generate_key())
        assert store.doc("bruce") is None
        db = store.get()
        kept = db.row("SELECT blob FROM quarantine WHERE tbl = 'kv'")
        assert kept and bytes(kept[0]).startswith(b"gAAAAA")

    def test_the_transcript_and_events_are_sealed_too(self, monkeypatch):
        _with_key(monkeypatch, files.generate_key())
        transcript.keep("robin", "user", time.time(), "the docks at midnight")
        events.add("hearsay", "the docks at midnight", audience=["robin"])
        store.get().conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert b"docks" not in _db_bytes()
        assert transcript.lines("robin")[0]["text"] == "the docks at midnight"


# --- the old files ---------------------------------------------------------------------

def _old_world(root, write=files.atomic_write):
    """A data directory as a console before the database left it: one of everything."""
    now = time.time()
    dump = json.dumps
    write(root / "_cases.json", dump([{"id": "r1", "kind": "Robbery", "status": "closed", "place": "Ralli's",
                                       "area": "Diamond District", "team": ["robin"], "members": {}, "opened_at": now - 900,
                                       "closed_at": now - 60, "outcome": "cuffed", "log": [{"at": now, "text": "closed"}]},
                                      {"id": "r2", "kind": "Arson", "status": "assigned", "place": "Ace", "area": "Otisburg",
                                       "team": ["nightwing"], "opened_at": now}]))
    write(root / "_plans.json", dump({"plans": [{"id": "p1", "what": "dinner", "where": "", "at": now + 3600,
                                                 "until": now + 7200, "who": ["robin"], "answers": {}}]}))
    write(root / "_inbox.json", dump({"mail": [{"id": "m2", "at": now, "folder": "inbox", "subject": "b", "read": False},
                                               {"id": "m1", "at": now - 10, "folder": "secure", "subject": "a", "read": True}],
                                      "keys": ["night:2026-10-10"], "events": [{"id": "r1", "kind": "case_closed"}]}))
    write(root / "_bruce.json", dump({"where": "Wayne Manor", "x": 1.0, "y": 2.0}))
    write(root / "_initiative.json", dump([now - 100]))
    write(root / "_dispatch.json", dump({"r1": "Shots fired", "r0": "A car on fire"}))
    write(root / "_rogue_status.json", dump({"The Joker": {"status": "in custody", "held": "GCPD", "since": now, "how": "x"}}))
    write(root / "_pins.json", dump([{"id": "pin1", "label": "Here", "x": 1, "y": 2, "at": now}]))
    write(root / "_groups" / "g1" / "group.json", dump({"name": "Family", "members": ["robin", "nightwing"],
                                                       "created_at": now - 50, "reads": {"robin": now}}))
    write(root / "_groups" / "g1" / "messages.json", dump([{"id": "x1", "from": "robin", "text": "hey", "at": now - 5},
                                                          {"id": "x2", "from": "me", "text": "hi", "at": now - 1,
                                                           "reactions": {"robin": "👍"}}]))
    person = root / "robin"
    write(person / "history.json", dump([{"role": "user", "content": "[link]", "at": now - 30, "marker": True},
                                         {"role": "assistant", "content": "Hey B.", "at": now - 29},
                                         {"role": "user", "content": "Where are you?", "at": now - 20},
                                         {"role": "assistant", "content": "The docks.", "at": now - 19, "aside": "Cold."}]))
    write(person / "texts.json", dump([{"id": "t1", "from": "me", "text": "home?", "at": now - 9, "read_at": now - 8},
                                       {"id": "t2", "from": "them", "text": "soon", "at": now - 7, "seen_at": now - 6},
                                       {"id": "t3", "from": "me", "text": "", "at": now - 5, "kind": "missed_call",
                                        "ignored": True}]))
    write(person / "vault.txt", "- [2026-10-01] Allergic to cats\n- [2026-10-02] Hates the docks\n")
    write(person / "story.txt", "- Bruce and Selina have a son\n")
    write(person / "bio.txt", "My son, in all but name.")
    write(person / "presence.json", dump({"activity": None, "last_active": now, "intents": [],
                                          "trips": [{"to": "Docks", "pts": [[1, 2]], "start": now, "end": now}]}))
    write(person / "diary.json", dump({"entries": [{"at": now - 600, "kind": "trip", "text": "set off for the docks"},
                                                   {"at": now - 300, "kind": "with", "text": "with Dick", "who": ["nightwing"]}],
                                       "last": {"doing": "patrol", "where": "Docks"}}))
    write(person / "hearsay.json", dump([{"from": "Dick Grayson", "text": "Bruce is selling the car", "at": now + 600}]))
    return now


class TestTheOldFiles:
    def _check(self, now):
        from wayne.engine import cases, diary, grapevine, inbox, plans
        from wayne.memory import History, Story, Vault
        from wayne.memory import groups as group_store
        from wayne.memory.texts import TextLog
        assert [c["id"] for c in cases.everything()] == ["r1", "r2"]
        assert cases.active("nightwing")["id"] == "r2"
        assert plans.all_plans()[0]["id"] == "p1"
        assert [m["id"] for m in inbox.mail()] == ["m2", "m1"] and inbox.unread() == 1
        assert inbox._delivered("night:2026-10-10") and inbox._take_event()["kind"] == "case_closed"
        assert store.doc("bruce")["where"] == "Wayne Manor"
        group = group_store.of("g1")
        assert group.name == "Family" and [m["id"] for m in group.messages()] == ["x1", "x2"]
        assert group.messages()[1]["reactions"] == {"robin": "👍"}
        h = History("robin")
        assert [m["content"] for m in h.messages] == ["[link]", "Hey B.", "Where are you?", "The docks."]
        assert [s["text"] for s in transcript.lines("robin")] == ["Hey B.", "Where are you?", "The docks. Cold."]
        log = TextLog("robin")
        texts = log.page()
        assert [t["id"] for t in texts] == ["t1", "t2", "t3"] and texts[0]["read_at"] == now - 8
        assert texts[2]["ignored"] is True and log.last()["id"] == "t2" and log.unread() == []
        assert Vault("robin").entries() == ["[2026-10-01] Allergic to cats", "[2026-10-02] Hates the docks"]
        assert Story("robin").entries() == ["Bruce and Selina have a son"]
        assert store.space("bio").get("robin") == "My son, in all but name."
        assert store.space("presence/robin").get("trips")[0]["to"] == "Docks"
        assert [e["text"] for e in diary.entries("robin", since=now - 3600)] == ["set off for the docks", "with Dick"]
        assert diary.entries("robin", since=now - 3600)[1]["who"] == ["nightwing"]
        assert grapevine.heard("robin", now + 700)[0]["from"] == "Dick Grayson"
        assert grapevine.heard("robin", now) == []
        assert [e["kind"] for e in events.query("e.kind = 'case.closed'")] == ["case.closed"]

    def test_everything_comes_in_and_reads_back_the_same(self, own_world):
        now = _old_world(own_world)
        db = store.get()
        assert db.meta("imported_at") and db.meta("import_skipped") is None
        self._check(now)
        aside = own_world / db.meta("imported_to")
        assert (aside / "robin" / "texts.json").exists() and (aside / "_groups" / "g1" / "group.json").exists()
        assert not (own_world / "robin").exists() and not (own_world / "_cases.json").exists()

    def test_encrypted_files_come_in_too(self, own_world, monkeypatch):
        _with_key(monkeypatch, files.generate_key())
        now = _old_world(own_world)
        self._check(now)

    def test_a_file_that_wont_open_is_left_where_it_is(self, own_world):
        _old_world(own_world)
        (own_world / "robin" / "vault.txt").write_bytes(b"gAAAAA" + b"x" * 60)
        db = store.get()
        assert "vault.txt" in db.meta("import_skipped")
        assert (own_world / "robin" / "vault.txt").exists()
        from wayne.memory import Vault
        assert Vault("robin").entries() == []

    def test_once_in_never_again(self, own_world):
        _old_world(own_world)
        store.get()
        store.close_all()
        (own_world / "_bruce.json").write_text(json.dumps({"where": "elsewhere"}))
        assert store.doc("bruce")["where"] == "Wayne Manor"

    def test_nothing_is_kept_if_anything_reads_back_wrong(self, own_world, monkeypatch):
        _old_world(own_world)
        monkeypatch.setattr(legacy, "_same", lambda a, b: False)
        with pytest.raises(legacy.Mismatch):
            store.get()
        assert (own_world / "_cases.json").exists() and not (own_world / "wayne.db").exists()
        assert list(own_world.glob("wayne.failed-*.db"))

    def test_written_back_out_it_comes_in_again_the_same(self, own_world, tmp_path, monkeypatch):
        now = _old_world(own_world)
        store.get()
        out = tmp_path / "exported"
        legacy.export(out)
        store.close_all()
        monkeypatch.setattr(paths, "DATA_DIR", out)
        self._check(now)


# --- many at once ----------------------------------------------------------------------

def test_many_writers_at_once_lose_nothing():
    from wayne.memory import groups as group_store
    from wayne.memory.texts import TextLog
    group = group_store.create("Family", ["robin", "nightwing"])
    errors = []

    def write(who):
        try:
            for i in range(40):
                TextLog(who).add("me", f"{who} {i}")
                group.add(who, f"{who} {i}")
                store.space(f"presence/{who}").put("last_active", i)
        except Exception as exc:          # noqa: BLE001 — any failure is the finding
            errors.append(exc)

    workers = [threading.Thread(target=write, args=(who,)) for who in ("robin", "nightwing", "batgirl", "orphan")]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert errors == []
    assert len(group.messages()) == 160
    assert all(len(TextLog(w).page(limit=100)) == 40 for w in ("robin", "nightwing", "batgirl", "orphan"))
    assert all(store.space(f"presence/{w}").get("last_active") == 39 for w in ("robin", "nightwing"))


def test_readers_that_only_look_leave_the_board_as_it_is(clock):
    """The shared views (cases.peek, the dispatches, the plans) are read hundreds of times a pulse — never changed."""
    from wayne.contacts import directory
    from wayne.engine import cases, incidents, patrols, plans, presence
    report = (incidents.at(clock()) or [{"id": "r1", "kind": "Robbery", "severity": 3, "place": "Ralli's",
                                         "area": "Diamond District", "x": 50.0, "y": 50.0, "at": clock()}])[0]
    cases.assign(report, "robin", travel=5)
    plans.add({"what": "dinner", "where": "", "at": clock() + 600, "until": clock() + 7200, "who": ["robin"]})
    plans.answer(plans.all_plans()[0]["id"], "robin", "yes")
    for t in (clock(), clock() + 900, clock() + 3600):
        incidents.at(t)
        incidents.ended(t)
        patrols._related_areas("robin", t)
        for contact in directory():
            presence.of(contact).whereabouts(t)
    for name in ("cases", "plans", "dispatch"):
        assert store.space(name).view() == store.space(name).list()


def test_upkeep_clears_what_is_past_keeping_and_keeps_a_weeks_copies(clock):
    from wayne.store import upkeep
    now = clock()
    transcript.keep("robin", "user", now - 200 * 86400, "long ago")
    transcript.keep("robin", "user", now - 1 * 86400, "yesterday")
    events.add("diary.doing", "two years back", at=now - 800 * 86400, actor="robin")
    events.add("diary.doing", "last week", at=now - 7 * 86400, actor="robin")
    for day in range(10):
        upkeep.run(now - (9 - day) * 86400)
    assert [line["text"] for line in transcript.lines("robin")] == ["yesterday"]
    assert [e["text"] for e in events.of("robin", "diary.")] == ["last week"]
    copies = sorted((paths.DATA_DIR / "_backup-daily").glob("wayne-*.db"))
    assert len(copies) == upkeep.BACKUPS
    kept = sqlite3.connect(str(copies[-1]))
    assert kept.execute("SELECT COUNT(*) FROM transcript").fetchone()[0] == 1
    kept.close()
    assert not upkeep.due(now)
