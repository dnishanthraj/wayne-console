"""Who's on a case's comms: whoever's on the case and Alfred in the cave — Barbara only from her screens."""
from wayne.contacts import directory
from wayne.engine import cases, presence


def _console():
    import wayne.frontends.web as web
    console = web.Console.__new__(web.Console)
    console.directory = directory()
    return console


def test_comms_is_the_case_and_alfred_and_barbara_only_from_their_screens(monkeypatch):
    console = _console()
    case = {"id": "c1", "team": ["bruce", "robin"]}
    monkeypatch.setattr(console, "_at_their_screens", lambda cid: False)
    assert console._comms_crew(case, ["robin"]) == ["robin"]                         # neither of them is in
    monkeypatch.setattr(console, "_at_their_screens", lambda cid: cid == "batgirl")
    assert console._comms_crew(case, ["robin"]) == ["robin", "batgirl"]
    monkeypatch.setattr(console, "_at_their_screens", lambda cid: True)
    assert console._comms_crew(case, ["robin"]) == ["robin", "alfred", "batgirl"]


def test_alfred_counts_when_hes_home_and_up(monkeypatch):
    console = _console()
    monkeypatch.setattr(cases, "active", lambda cid: None)
    alfred = presence.of(directory().get("alfred"))
    monkeypatch.setattr(alfred, "whereabouts", lambda t=None: ("Wayne Manor", []))
    monkeypatch.setattr(alfred, "now", lambda t=None: {"status": presence.BUSY, "doing": "in the garden"})
    assert console._at_their_screens("alfred")                       # home: in on it
    monkeypatch.setattr(alfred, "now", lambda t=None: {"status": presence.OFFLINE, "doing": "asleep"})
    assert not console._at_their_screens("alfred")                   # home, asleep: not
    monkeypatch.setattr(alfred, "now", lambda t=None: {"status": presence.BUSY, "doing": "at the market"})
    monkeypatch.setattr(alfred, "whereabouts", lambda t=None: ("Gotham Night Market", []))
    assert not console._at_their_screens("alfred")                   # out: his to be asked onto it


def test_barbara_counts_as_at_her_screens_only_working_in_the_clocktower(monkeypatch):
    console = _console()
    monkeypatch.setattr(cases, "active", lambda cid: None)
    barbara = presence.of(directory().get("batgirl"))
    monkeypatch.setattr(barbara, "now", lambda t=None: {"status": presence.ONLINE, "doing": "at the clock tower, running comms",
                                                        "terminal": True})
    monkeypatch.setattr(barbara, "whereabouts", lambda t=None: ("The Clocktower", []))
    assert console._at_their_screens("batgirl")
    monkeypatch.setattr(barbara, "whereabouts", lambda t=None: ("Burnside College", []))
    assert not console._at_their_screens("batgirl")                          # her laptop at college isn't the Clocktower
    monkeypatch.setattr(barbara, "whereabouts", lambda t=None: ("The Clocktower", []))
    monkeypatch.setattr(barbara, "now", lambda t=None: {"status": presence.OFFLINE, "doing": "asleep"})
    assert not console._at_their_screens("batgirl")                          # home, asleep, isn't working
    monkeypatch.setattr(barbara, "now", lambda t=None: {"status": presence.ONLINE, "doing": "", "terminal": True})
    monkeypatch.setattr(cases, "active", lambda cid: {"id": "c9"} if cid == "batgirl" else None)
    assert not console._at_their_screens("batgirl")                          # out on a case of her own


def test_put_on_comms_alfred_drops_what_hes_doing_and_anyone_else_is_asked_in(clock, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    console = _console()
    console._tasks = set()
    console._presence_changed = lambda c: asyncio.sleep(0)
    case = {"id": "c5", "kind": "Child abduction", "place": "Waterloo Docks", "status": "on scene", "team": ["nightwing"],
            "due": clock() + 1800}
    monkeypatch.setattr(cases, "for_report", lambda rid: case)
    monkeypatch.setattr(cases, "team", lambda c: c["team"])
    group = SimpleNamespace(id="g1", meta=lambda: {"comms": True, "case": "c5"})
    sessions = {}
    console.session_for = lambda cid: sessions.setdefault(cid, SimpleNamespace())
    alfred = presence.of(directory().get("alfred"))
    alfred.set_activity("pruning the roses", presence.BUSY, 60, where="Wayne Manor")

    async def run():
        console._called_onto_comms(group, "alfred")
        console._called_onto_comms(group, "robin")
    asyncio.run(run())
    assert "on comms" in alfred.now()["doing"] and alfred.now().get("terminal")
    assert sessions["alfred"]._called_onto_comms["left"] == "pruning the roses"
    assert sessions["robin"]._called_onto_comms["asked_in"] and not sessions["alfred"]._called_onto_comms["asked_in"]


def test_whats_said_about_a_case_goes_on_its_comms_not_to_his_phone(clock, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    console = _console()
    posted, texted = [], []
    group = SimpleNamespace(id="g2")
    monkeypatch.setattr(console, "_comms_for", lambda cid: group if cid == "nightwing" else None)

    async def post_as(g, contact, opening=None, must=False, **kw):
        posted.append((g.id, contact.id, opening))
    console._post_as = post_as

    async def call_first(*a, **k):
        texted.append("text")
    console._call_first = call_first
    console.turn_lock = asyncio.Lock()
    console._members = lambda: []
    console.session_for = lambda cid: SimpleNamespace(reach_out=lambda about, why: "")
    dick = directory().get("nightwing")
    asyncio.run(console._send_unprompted(dick, "the child abduction at Waterloo Docks", "case_arrived"))
    assert posted and posted[0][0] == "g2" and "got to the scene" in posted[0][2] and not texted
    asyncio.run(console._send_unprompted(dick, "something on his mind", "impulse"))
    assert texted                                                     # anything else is still a text


def test_the_comms_brief_says_where_he_really_is_and_how_to_sound(clock, monkeypatch):
    from wayne.engine import batman
    from wayne.engine.session import ContactSession
    case = {"id": "c6", "kind": "Child abduction", "place": "Waterloo Docks", "area": "Blüdhaven", "status": "on scene",
            "severity": 3, "team": ["nightwing"], "members": {"nightwing": {"status": "on scene"}},
            "updated_at": clock() - 120, "due": clock() + 1200}
    monkeypatch.setattr(cases, "team", lambda c: c["team"])
    monkeypatch.setattr(batman, "state", lambda now=None: {"where": "Wayne Manor"})
    alfred = ContactSession.__new__(ContactSession)
    alfred.contact = directory().get("alfred")
    brief = alfred.comms_brief(case)
    assert "isn't on this one" in brief and "Wayne Manor" in brief           # not "already ahead of Dick"
    assert "no jokes, no emoji" in brief and "never \"copy that\" to nothing" in brief
    assert "in the cave" in brief and "nothing off it" in brief
