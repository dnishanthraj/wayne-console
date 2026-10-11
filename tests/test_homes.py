"""Everyone's home, named and placed — and the evenings they keep together there."""
import datetime
import time

from wayne.contacts import directory
from wayne.engine import places, presence, week


def _sunday_lunch():
    """A Sunday, one o'clock-ish, when Jim and Barbara's lunch is on."""
    jim = directory().get("gordon")
    day = datetime.date(2026, 10, 4)
    for _ in range(40):
        t = time.mktime(datetime.datetime(day.year, day.month, day.day, 13, 45).timetuple())
        if any(h.get("together") == "gordon-sunday-lunch" for h in week.on(jim, t)):
            return t
        day += datetime.timedelta(days=7)
    raise AssertionError("no lunch in forty weeks")


def test_everyones_home_is_somewhere_on_the_map_and_none_is_the_scanners():
    from wayne.engine import incidents
    for contact in directory():
        spot = places.resolve(contact.home)
        assert spot and spot["name"] == contact.home, contact.id
    homes = {c.home for c in directory()}
    assert not any(r["place"] in homes for r in incidents.at(time.time()))


def test_jim_and_barbara_have_sunday_lunch_at_his_place_both_of_them():
    t = _sunday_lunch()
    jim, babs = presence.of(directory().get("gordon")), presence.of(directory().get("batgirl"))
    assert week.together_block(jim.contact, t)["together"] == week.together_block(babs.contact, t)["together"]
    jim_at, jim_with = jim.whereabouts(t + 3600)
    babs_at, babs_with = babs.whereabouts(t + 3600)
    assert jim_at == babs_at == "Jim's apartment"
    assert "batgirl" in jim_with and "gordon" in babs_with


def test_a_shared_evening_is_on_for_all_of_them_or_none():
    crew = [directory().get(c) for c in ("nightwing", "robin", "batgirl", "orphan")]
    day = datetime.date(2026, 10, 2)
    for _ in range(30):
        t = time.mktime(datetime.datetime(day.year, day.month, day.day, 20, 30).timetuple())
        on = {c.id for c in crew if any(h.get("together") == "dicks-movie-night" for h in week.on(c, t))}
        assert on in (set(), {c.id for c in crew})
        day += datetime.timedelta(days=7)


def test_home_somewhere_else_is_whoever_lives_there():
    tim = presence.of(directory().get("robin"))
    assert tim._home_is_home("Home, Blüdhaven") == "Dick's apartment"       # not the middle of Blüdhaven
    assert tim._home_is_home("Home") == "Tim's houseboat"
    dick = presence.of(directory().get("nightwing"))
    assert tim.label("Dick's apartment") == "Dick's apartment"
    assert tim._spoken_place("Dick's apartment") == "Dick's place (Dick's apartment)"
    assert dick._spoken_place("Dick's apartment").startswith("home")


def test_out_walking_the_dog_they_go_round_not_stand_still(clock):
    import math

    from wayne.engine import places
    dick = presence.of(directory().get("nightwing"))
    t = clock()
    dick.set_activity("walking Haley", presence.IDLE, 180, t=t, where="Port's Park")
    later = t + 3600                                                      # long since there, not on the way
    assert not dick.trip("Port's Park", places.resolve("Port's Park"), later)
    a, b = dick.position(later), dick.position(later + 300)
    port = places.resolve("Port's Park")
    assert a != b                                                         # moving
    assert max(math.dist(a, (port["x"], port["y"])), math.dist(b, (port["x"], port["y"]))) < 2.5
    cass = presence.of(directory().get("orphan"))
    cass.set_activity("a run", presence.IDLE, 180, t=t, where="Burnside")
    shown = cass.public(t + 3600)
    assert "Burnside Park" in shown["where"]                              # round the park that's there
