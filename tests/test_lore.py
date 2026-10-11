"""
Local lore: your own additions to Gotham, kept on this machine — a place on the map and in the Codex, a person, a
fact only some of them know, someone who writes — and nothing at all when there's no file, or a bad one.
"""
import json

from wayne import lore, operator, paths
from wayne.contacts import directory
from wayne.engine import codex, inbox, places

LORE = {
    "places": [{"name": "The Test Kitchen", "kind": "diner", "area": "Burnside", "x": 108.0, "y": 68.0,
                "bio": "Open all night.", "note": "Good pie."},
               {"name": "Nowhere Special", "kind": "spaceport", "x": 1, "y": 1}],          # not a kind the map draws
    "people": [{"id": "test-person", "name": "Testa Person", "group": "Gotham", "occupation": "Columnist"}],
    "facts": [{"known_by": ["robin"], "text": "The Test Kitchen's back booth is where Tim does his reading."}],
    "correspondents": {"testa": ["Testa Person", "testa@gothamgazette.com", 1, "a columnist with questions"]},
}


def _write(data):
    (paths.DATA_DIR / "lore.json").write_text(data if isinstance(data, str) else json.dumps(data))
    places._resolve.cache_clear()


def test_a_place_of_your_own_is_on_the_map_in_the_codex_and_somewhere_to_go(clock):
    _write(LORE)
    assert [v["name"] for v in lore.venues()] == ["The Test Kitchen"]
    assert places.resolve("the test kitchen")["x"] == 108.0
    entry = next(p for p in codex.places() if p["name"] == "The Test Kitchen")
    assert entry["bio"] == "Open all night." and entry["venue"] == "diner"


def test_people_facts_and_letters_reach_only_where_they_should(clock):
    _write(LORE)
    assert any(p["name"] == "Testa Person" for p in codex.people(directory()))
    assert "back booth" in operator.briefing("robin") and "back booth" not in operator.briefing("gordon")
    assert inbox._writers()["testa"][0] == "Testa Person"


def test_no_lore_or_bad_lore_changes_nothing(clock):
    assert lore.load() == {} and lore.venues() == []
    _write("{ not json")
    assert lore.load() == {} and lore.people() == []


def test_evelyn_lives_near_jason_and_the_family_knows_about_them(clock):
    places._resolve.cache_clear()
    flat = places.resolve("Evelyn's apartment")
    assert flat["area"] == "The Bowery"                       # near Jason's safehouse, not in Crime Alley
    assert "seeing Evelyn" in operator.briefing("nightwing") and "seeing Evelyn" not in operator.briefing("gordon")


def test_most_know_most_of_them_and_a_few_dont(clock):
    data = dict(LORE)
    data["people"] = [{"id": "test-person", "name": "Testa Person", "group": "Gotham", "occupation": "Columnist",
                       "public": "Testa Person writes the Gazette's puzzle page", "unknown_to": ["lucius"]}]
    _write(data)
    assert lore.known_to("robin") == ["Testa Person writes the Gazette's puzzle page"]
    assert lore.known_to("lucius") == []
    assert "puzzle page" in operator.briefing("robin") and "puzzle page" not in operator.briefing("lucius")


def test_a_call_with_company_knows_them_too_and_who_its_news_to(clock):
    from wayne.engine import party
    from wayne.engine.session import ContactSession
    data = dict(LORE)
    data["people"] = [{"id": "test-person", "name": "Testa Person", "public": "Testa Person writes the puzzle page",
                       "unknown_to": ["lucius"]}]
    _write(data)
    call = party.Call(operator="Bruce Wayne")
    for cid in ("robin", "lucius"):
        call.join(ContactSession(directory().get(cid)))
    system = call.writer().system()
    assert "Testa Person writes the puzzle page (news to Lucius)" in system
    assert "Only you know, of the people below: The Test Kitchen's back booth" in system      # Tim's own fact
