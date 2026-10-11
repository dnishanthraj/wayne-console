"""Gotham's trains: every station on its line, shared track side by side, journeys that change — and know where they are."""
import math

from wayne.engine import places, transit, travel


def _off_path(p, path):
    def gap(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        k = 0.0 if not (dx or dy) else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
        return math.dist(p, (a[0] + dx * k, a[1] + dy * k))
    return min(gap(a, b) for a, b in zip(path, path[1:], strict=False))


def test_every_station_is_on_its_line_and_where_the_line_stops():
    net = transit.network()
    for line in net["lines"]:
        assert len(line["calls"]) >= 2, line["name"]
        assert line["stops"] == sorted(line["stops"]) and line["stops"][0] == 0 and line["stops"][-1] == 1
        for name, share in zip(line["calls"], line["stops"], strict=True):
            at = net["stations"][name]
            assert _off_path(at, line["path"]) < 0.02, (line["name"], name)
            reached = transit._along(line["path"], share * transit.length(line["path"]))[0]
            assert math.dist(reached, at) < 0.05, (line["name"], name)           # a train stops at the station


def test_a_station_two_systems_share_is_one_place_and_every_station_is_on_land():
    net = transit.network()
    graph = travel._load()
    for name, (x, y) in net["stations"].items():
        assert travel._on_land(graph, x, y), name
    rail = {n for line in net["lines"] if line["kind"] == "rail" for n in line["calls"]}
    subway = {n for line in transit.drawn() for n in line["calls"]}
    assert {"Union Station", "Burnley", "Robinsville", "Blüdhaven Central", "Waterloo", "Coventry Junction"} <= rail & subway


def test_track_two_lines_share_is_drawn_side_by_side():
    slots = transit.slots()
    assert slots[("Bowery", "Park Row")] == {"Robinson Line": -0.5, "Finger Line": 0.5}
    assert slots[("Cherry Hills", "Knightsdome")] == {"Kane Line": 0.0}
    assert len(transit.drawn()) == 9 and all(line["kind"] != "rail" for line in transit.drawn())


def test_a_journey_changes_lines_and_knows_where_it_is_all_the_way():
    selinas, university = (80.4, 101.9), (25.6, 67.0)
    went = transit.ride(selinas, university)
    assert went["lines"] == ["Robinson Line", "Burnside Line"] and went["by"] == "on the subway"
    assert went["pts"][0] == list(selinas) and went["pts"][-1] == list(university)
    assert went["times"] == sorted(went["times"]) and went["times"][-1] == 1.0 and len(went["times"]) == len(went["pts"])
    legs = went["legs"]
    said = [transit.where_on(legs, share, "Gotham University") for share in (0.02, 0.15, 0.3, 0.55, 0.75, 0.97)]
    assert said[0].startswith("walking to East End station")
    assert said[1].startswith("on the platform at East End")
    assert "on the Robinson Line" in said[2] and "next stop" in said[2] and "change at Museum Mile for the Burnside Line" in said[2]
    assert "changing at Museum Mile" in said[3] or "Burnside Line" in said[3]
    assert "on the Burnside Line" in said[4] and "get off at Gotham University" in said[4]
    assert said[5].startswith("off the train")


def test_no_station_near_or_walking_quicker_is_no_train():
    assert transit.ride((1.0, 10.0), (-40.0, 30.0)) is None              # the Manor's lanes, nowhere near a line
    assert transit.ride((62.0, 25.2), (62.6, 25.8)) is None              # round the corner: walk it


def test_how_they_get_about_decides_the_train(monkeypatch):
    a, b = (80.4, 101.9), (25.6, 67.0)
    assert travel.way(a, b, mode="subway")["by"] == "on the subway"
    assert travel.way(a, b, mode="drive")["by"] == "driving"
    monkeypatch.setattr(travel, "_leaning", lambda who, a, b: 100.0)     # happy to take the train however slow
    assert travel.way(a, b, mode="mixed")["by"] == "on the subway"
    monkeypatch.setattr(travel, "_leaning", lambda who, a, b: 0.01)      # never, if the car's quicker at all
    assert travel.way(a, b, mode="mixed")["by"] == "driving"
    assert travel.way(a, (a[0] + 1, a[1]), mode="subway")["by"] == "on foot"


def test_blüdhavens_waterfront_is_blüdhavens_and_every_place_is_itself():
    assert places.resolve("Waterloo Docks")["area"] == "Blüdhaven"      # not Gotham Docks, a city away
    assert all(places.resolve(p["name"])["name"] == p["name"] for p in places.gazetteer()["places"])


def test_on_the_way_they_know_the_road_or_the_line_or_that_its_nowhere(clock):
    from wayne.contacts import directory
    from wayne.engine import presence
    tim = presence.of(directory().get("robin"))
    went = transit.ride((80.4, 101.9), (25.6, 67.0))
    trip = {"to": "Gotham University", "from": "", "pts": went["pts"], "times": went["times"], "legs": went["legs"],
            "start": clock() - went["minutes"] * 60 * 0.3, "end": clock() + went["minutes"] * 60 * 0.7, "by": went["by"]}
    line = tim._en_route(trip, "Gotham University", "", clock())
    assert "on the Robinson Line" in line and "next stop" in line
    drive = {"to": "Crime Alley", "from": "", "pts": [[60.0, 45.0], [72.0, 46.5]], "start": clock() - 60,
             "end": clock() + 60, "by": "driving"}
    assert "on Park Row" in tim._en_route(drive, "Crime Alley", "", clock())
    nowhere = {"to": "Bristol", "from": "", "pts": [[-22.0, 80.0], [-18.0, 80.0]], "start": clock() - 60,
               "end": clock() + 60, "by": "driving"}
    assert "nowhere with a name" in tim._en_route(nowhere, "Bristol", "", clock())
