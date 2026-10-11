"""
A phone's record of calls, in the thread: one that happened says how long, and who else was on it; a missed one
is something waiting for him; and what's said about the call names people as Jim knows them, with Jim on it.
"""
from wayne import identity
from wayne.contacts import directory
from wayne.engine import party
from wayne.engine.session import ContactSession
from wayne.memory.texts import TextLog


def _last_call(cid):
    return [m for m in TextLog(cid).page(limit=10) if m.get("kind")][-1]


def _console():
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    console._spawn = lambda coroutine: coroutine.close()
    return console


def test_a_call_that_happened_is_in_the_thread_with_how_long(clock):
    console = _console()
    tim = ContactSession(directory().get("robin"))
    console._log_call(tim, clock() - 240, {"robin"})
    logged = _last_call("robin")
    assert logged["kind"] == "call" and logged["text"] == "4 min"
    console._log_call(tim, clock() - 30, {"robin", "nightwing"})
    logged = _last_call("robin")
    assert logged["kind"] == "group_call" and logged["text"] == "30 sec · with Dick"
    console._log_call(tim, clock() - 1, {"robin"})                 # a ring that never really connected
    assert _last_call("robin")["text"] == "30 sec · with Dick"


def test_whats_said_about_the_call_names_people_as_jim_knows_them(clock):
    call = party.Call(operator="Bruce Wayne")
    for cid in ("gordon", "nightwing"):
        call.join(ContactSession(directory().get(cid)))
    assert call.for_scene(f"({identity.mention('batgirl')} declined)") == "(Batgirl declined)"
    assert call.for_scene(f"({identity.mention('gordon')} is on the call now)") == "(Jim is on the call now)"
