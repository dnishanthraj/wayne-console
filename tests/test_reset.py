"""Starting over from Settings: asked for, done as the console comes back up, backed up first — and only what goes, goes."""
from wayne import paths, store
from wayne.store import events, reset


def _world():
    store.space("bonds").put("robin|me", {"warmth": 40})
    store.space("tastes").put("robin", {"likes": []})
    store.space("engagements").put("e1", {"what": "a gala"})
    store.space("mail").put("m1", {"subject": "hi"})
    store.space("frames").put("robin", {"x": 1})              # his photo framing: kept
    store.space("identity").put("gordon", "masks")            # who knows what: kept
    events.add("relayed", "Bruce asked Dick to pass on: text him", actor="Dick", audience=["robin"])
    events.add("hearsay", "Dick told you Bruce is in Paris", actor="Dick", audience=["robin"])


def test_a_reset_asked_for_is_done_at_start_up_with_a_backup_first():
    _world()
    reset.request("all")
    assert (paths.DATA_DIR / "_reset-pending.json").exists()
    done = reset.pending()
    assert done["scope"] == "all" and (paths.DATA_DIR / done["backup"] / "wayne.db").exists()
    for space in ("bonds", "tastes", "engagements", "mail"):
        assert not store.space(space).list(), space
    assert store.space("frames").get("robin") == {"x": 1} and store.space("identity").get("gordon") == "masks"
    assert not events.known_to("robin", "relayed") and not events.known_to("robin", "hearsay")
    assert reset.pending() is None                               # done once
    said = reset.done()
    assert said["backup"] == done["backup"] and reset.done() is None   # and said once


def test_conversations_only_leaves_the_world():
    _world()
    reset.request("chats")
    reset.pending()
    assert not store.space("bonds").list() and not store.space("tastes").list()
    assert store.space("mail").get("m1") and store.space("engagements").get("e1")     # the world goes on


def test_there_is_no_such_reset_as_anything_else():
    import pytest
    with pytest.raises(ValueError):
        reset.request("everything-and-the-kitchen-sink")
