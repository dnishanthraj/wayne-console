"""
Where the console keeps everything: one SQLite file, DATA_DIR/wayne.db.

    store.space("cases")          the case board, a row per case (wayne.store.kv)
    store.doc("bruce", {})        a single small document
    threads.Thread("dm:robin")    a text thread, a row per message
    transcript                    everything said, past the model's window
    events                        what happened, what it's about, who knows it
    recall.block(contact, said)   what they'd remember that bears on it

The JSON files an older console kept are imported the first time the database
is opened (wayne.store.legacy), and can be written back out with
scripts/export_db_to_json.py.
"""
from .db import close_all, get


def space(name):
    """A named space of small documents (see wayne.store.kv)."""
    return get().space(name)


def doc(name, default=None):
    """A single document — Bruce's state, the jet's, the rogues' runs — or `default` (a copy) if there's none."""
    return get().space("doc").get(name, default)


def save_doc(name, value):
    get().space("doc").put(name, value)


__all__ = ["close_all", "doc", "get", "save_doc", "space"]
