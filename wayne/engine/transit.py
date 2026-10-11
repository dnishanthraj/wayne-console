"""
Gotham's trains, as anyone takes them: the subway, the Narrows monorail, Blüdhaven's metro, and the main-line
rail between the cities — one network, and the same one the map draws (scripts/build_map.py builds its lines from
here), so a station is on its line and someone riding is on the track.

gotham.json's "transit" holds each station once ({name: [x, y]}) and each line as the stations it calls at in
order, with an [x, y] bend wherever the track shouldn't run straight. A stretch two lines both run is one stretch
of track — the map draws them side by side. The main lines come from the map's rail: its roads of class "rail",
smoothed as the map smooths them, and their stations (gotham.json's "rail_stations"); a subway station that's
also a rail station sits exactly on it, and changing there is changing trains.

A journey (ride) is the quickest way by train: a walk to a station, the ride — changing lines where that's
quicker — and a walk out; with how long each part takes, so a dot on the map walks, waits on the platform, and
then moves at the train's pace.
"""
import heapq
import json
import math
from functools import lru_cache
from pathlib import Path

SPEED = {"subway": 3.3, "monorail": 3.6, "rail": 6.0}     # map units a minute, stops and all (one is ~150 m)
WAIT = {"subway": 4.0, "monorail": 4.0, "rail": 9.0}       # minutes on the platform for the first train
DWELL = 0.4               # at each stop on the way
CHANGE = 3.0              # changing trains: the stairs, the corridor, the other platform
RELUCTANCE = 4.0          # and what a change is worth avoiding, beyond its minutes: one train beats three
WALKING = 0.53            # on foot, ~5 km/h
ACCESS = 5.0              # the furthest anyone walks to a station, or from one (~10 minutes)
INTERCHANGE = 2.2         # two stations this close are one change, on foot

_GOTHAM = Path(__file__).with_name("gotham.json")


def smooth(points, iterations=3):
    """Corner cutting (Chaikin), its ends kept — the map's curves (build_map.chaikin), so the same ones."""
    pts = [tuple(p) for p in points]
    for _ in range(iterations):
        out = [pts[0]]
        for a, b in zip(pts, pts[1:], strict=False):
            if math.hypot(b[0] - a[0], b[1] - a[1]) > 60:
                out += [a, b]
                continue
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        out.append(pts[-1])
        pts = out
    return pts


def length(pts):
    return sum(math.dist(p, q) for p, q in zip(pts, pts[1:], strict=False))


def _along(pts, d):
    """The point `d` along a polyline, and the index of the leg it's on."""
    for i, (p, q) in enumerate(zip(pts, pts[1:], strict=False)):
        leg = math.dist(p, q)
        if d <= leg or i == len(pts) - 2:
            k = 0.0 if not leg else max(0.0, min(1.0, d / leg))
            return (p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k), i
        d -= leg
    return tuple(pts[-1]), max(0, len(pts) - 2)


def _between(pts, d0, d1):
    """The stretch of a polyline from `d0` along it to `d1`."""
    a, i = _along(pts, d0)
    b, j = _along(pts, d1)
    return [a] + [tuple(p) for p in pts[i + 1:j + 1]] + [b]


@lru_cache(maxsize=1)
def network():
    """
    {"stations": {name: (x, y)}, "lines": [{name, short, color, kind, calls: [names], path, stops (shares of the
    path at each call)}], "edges": {(a, b): {"pts" (from a to b), "lines": [names]}}} — a < b, one per stretch of
    track whoever runs it. Built once.
    """
    src = json.loads(_GOTHAM.read_text())
    spec = src.get("transit") or {}
    stations = {name: (float(p[0]), float(p[1])) for name, p in (spec.get("stations") or {}).items()}
    lines, edges = [], {}

    def stretch(a, b, bends, line):
        key = (a, b) if a < b else (b, a)
        if key not in edges:
            run = [stations[a], *bends, stations[b]]
            pts = smooth(run, 2) if bends else run
            edges[key] = {"pts": pts if key[0] == a else pts[::-1], "lines": []}
        if line not in edges[key]["lines"]:
            edges[key]["lines"].append(line)
        pts = edges[key]["pts"]
        return pts if key[0] == a else pts[::-1]

    for spec_line in spec.get("lines") or []:
        calls, path, stops, bends = [], [], [], []
        for stop in spec_line["stops"]:
            if not isinstance(stop, str):
                bends.append((float(stop[0]), float(stop[1])))
                continue
            if calls:
                run = stretch(calls[-1], stop, bends, spec_line["name"])
                path += run[1:]
            else:
                path.append(stations[stop])
            stops.append(length(path))
            calls.append(stop)
            bends = []
        total = length(path) or 1.0
        lines.append({"name": spec_line["name"], "short": spec_line.get("short") or spec_line["name"],
                      "color": spec_line.get("color", "#cccccc"), "kind": spec_line.get("kind", "subway"),
                      "calls": calls, "path": path, "stops": [round(s / total, 4) for s in stops]})

    # The main lines, from the map's rail: its stations where the map puts them, its track between them.
    for road in src.get("roads") or []:
        if road.get("class") != "rail":
            continue
        called = src.get("rail_stations", {}).get(road["name"]) or []
        if len(called) < 2:
            continue
        track = smooth(road["line"], 3)
        total = length(track)
        calls, path = [], []
        for name, share in called:
            at, _ = _along(track, share * total)
            stations.setdefault(name, (round(at[0], 2), round(at[1], 2)))
            if calls:
                run = _between(track, called[len(calls) - 1][1] * total, share * total)
                key = (calls[-1], name) if calls[-1] < name else (name, calls[-1])
                edges.setdefault(key, {"pts": run if key[0] == calls[-1] else run[::-1], "lines": []})
                edges[key]["lines"].append(road["name"])
                path += run[1:]
            else:
                path.append(at)
            calls.append(name)
        lines.append({"name": road["name"], "short": road["name"], "color": "#e8f6ff", "kind": "rail",
                      "calls": calls, "path": path, "stops": [round(s, 4) for _, s in called]})
    return {"stations": stations, "lines": lines, "edges": edges}


def drawn():
    """The lines the map draws as transit lines — the subway, the monorail, the metro (rail it draws as rail)."""
    return [line for line in network()["lines"] if line["kind"] != "rail"]


def slots():
    """
    Where each drawn line runs on each stretch of track: {(a, b): {line: offset}}, offsets in line-widths either
    side of the middle (-0.5 and 0.5 where two share it) — the map's side-by-side, in the order the lines are listed.
    """
    order = {line["name"]: i for i, line in enumerate(drawn())}
    out = {}
    for key, edge in network()["edges"].items():
        named = sorted((n for n in edge["lines"] if n in order), key=order.get)
        if named:
            out[key] = {n: i - (len(named) - 1) / 2 for i, n in enumerate(named)}
    return out


def served(name):
    """The lines that call at a station, in the order they're listed."""
    return [line for line in network()["lines"] if name in line["calls"]]


@lru_cache(maxsize=1)
def _links():
    """Stations close enough to change between on foot (different names, a short walk): {name: [(other, minutes)]}."""
    stations = network()["stations"]
    names = list(stations)
    links = {n: [] for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            d = math.dist(stations[a], stations[b])
            if d <= INTERCHANGE:
                links[a].append((b, d / WALKING))
                links[b].append((a, d / WALKING))
    return links


def ride(a, b):
    """
    The quickest way from a to b by train, as {"pts", "times" (the share of the journey reached at each point),
    "minutes", "by" ("on the subway", "on the train", "on the monorail"), "lines" (the ones ridden, in order)} —
    or None when there's no station within a walk of either end, or walking the whole way is as quick.
    """
    net = network()
    stations, lines = net["stations"], net["lines"]
    a, b = (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))
    first = {n: math.dist(a, p) / WALKING for n, p in stations.items() if math.dist(a, p) <= ACCESS}
    last = {n: math.dist(p, b) / WALKING for n, p in stations.items() if math.dist(p, b) <= ACCESS}
    if not first or not last:
        return None
    links = _links()
    # State: at a station, on a line (its index). The cost of getting there; how; the best way out so far.
    heap, best, back = [], {}, {}
    for name, walk in first.items():
        for i, line in enumerate(lines):
            if name in line["calls"]:
                cost = walk + WAIT[line["kind"]]
                if cost < best.get((name, i), math.inf):
                    best[(name, i)] = cost
                    back[(name, i)] = ("start", None, walk)
                    heapq.heappush(heap, (cost, name, i))
    done, finish = set(), None
    while heap:
        cost, name, i = heapq.heappop(heap)
        if (name, i) in done:
            continue
        done.add((name, i))
        if name in last:
            total = cost + last[name]
            if finish is None or total < finish[0]:
                finish = (total, name, i)
        if finish is not None and cost >= finish[0]:
            break
        line = lines[i]
        at = line["calls"].index(name)
        moves = []
        for step in (-1, 1):                                    # on to the next stop, either way
            k = at + step
            if 0 <= k < len(line["calls"]):
                other = line["calls"][k]
                key = (name, other) if name < other else (other, name)
                run = length(net["edges"][key]["pts"])
                moves.append(((other, i), run / SPEED[line["kind"]] + DWELL, ("ride", (name, i), None)))
        for j, there in enumerate(lines):                        # change here
            if j != i and name in there["calls"]:
                moves.append(((name, j), CHANGE + WAIT[there["kind"]] / 2 + RELUCTANCE, ("change", (name, i), None)))
        for other, walk in links.get(name, ()):                  # or a short walk to another station, and change
            for j, there in enumerate(lines):
                if other in there["calls"]:
                    moves.append(((other, j), walk + CHANGE + WAIT[there["kind"]] / 2 + RELUCTANCE,
                                  ("walk", (name, i), walk)))
        for state, extra, how in moves:
            if cost + extra < best.get(state, math.inf):
                best[state] = cost + extra
                back[state] = how
                heapq.heappush(heap, (cost + extra, state[0], state[1]))
    if finish is None or finish[0] >= math.dist(a, b) / WALKING:
        return None
    # Back along the way it came: the stations and lines, then the points and when each is reached.
    chain, state = [], (finish[1], finish[2])
    while True:
        how = back[state]
        chain.append((state, how))
        if how[0] == "start":
            break
        state = how[1]
    chain.reverse()
    pts, clock = [a], [0.0]
    start_station, start_line = chain[0][0]
    walk_in = chain[0][1][2]
    pts.append(stations[start_station])
    clock.append(walk_in)
    pts.append(stations[start_station])                               # on the platform
    clock.append(walk_in + WAIT[lines[start_line]["kind"]])
    # The same journey as its parts — for knowing where they are on it (see where_on).
    legs = [{"do": "walk", "to": start_station, "line": lines[start_line]["name"], "t": [0.0, walk_in]},
            {"do": "wait", "at": start_station, "line": lines[start_line]["name"], "t": [walk_in, clock[-1]]}]
    ridden, minutes_by = [lines[start_line]["name"]], {}
    for (station, i), how in chain[1:]:
        prev_station, prev_line = how[1]
        if how[0] == "ride":
            key = (prev_station, station) if prev_station < station else (station, prev_station)
            run = net["edges"][key]["pts"]
            run = run if key[0] == prev_station else run[::-1]
            pace = SPEED[lines[i]["kind"]]
            t0 = clock[-1]
            covered = 0.0
            for p, q in zip(run, run[1:], strict=False):
                covered += math.dist(p, q)
                pts.append(q)
                clock.append(t0 + covered / pace)
            clock[-1] += DWELL
            minutes_by[lines[i]["kind"]] = minutes_by.get(lines[i]["kind"], 0) + clock[-1] - t0
            if legs[-1]["do"] == "ride" and legs[-1]["line"] == lines[i]["name"]:
                legs[-1]["calls"].append(station)
                legs[-1]["when"].append(clock[-1])
                legs[-1]["t"][1] = clock[-1]
            else:
                legs.append({"do": "ride", "line": lines[i]["name"], "calls": [prev_station, station],
                             "when": [t0, clock[-1]], "t": [t0, clock[-1]]})
        else:
            t0 = clock[-1]
            if how[0] == "walk":
                pts.append(stations[station])
                clock.append(clock[-1] + how[2] + CHANGE)
            else:
                pts.append(stations[station])
                clock.append(clock[-1] + CHANGE)
            pts.append(stations[station])
            clock.append(clock[-1] + WAIT[lines[i]["kind"]] / 2)
            legs.append({"do": "change", "at": station, "from": prev_station, "line": lines[i]["name"],
                         "t": [t0, clock[-1]]})
            if lines[i]["name"] != ridden[-1]:
                ridden.append(lines[i]["name"])
    pts.append(b)
    legs.append({"do": "walk", "from": finish[1], "t": [clock[-1], clock[-1] + last[finish[1]]]})
    clock.append(clock[-1] + last[finish[1]])
    total = clock[-1] or 1.0
    for leg in legs:
        leg["t"] = [round(v / total, 4) for v in leg["t"]]
        if "when" in leg:
            leg["when"] = [round(v / total, 4) for v in leg["when"]]
    main = max(minutes_by, key=minutes_by.get) if minutes_by else "subway"
    by = {"rail": "on the train", "monorail": "on the monorail"}.get(main, "on the subway")
    return {"pts": [[round(x, 2), round(y, 2)] for x, y in pts], "times": [round(t / total, 4) for t in clock],
            "minutes": total, "by": by, "lines": ridden, "legs": legs}


def where_on(legs, share, going_to=""):
    """
    Where someone is on a train journey, `share` of the way through it, in the words they'd know it in: walking
    to the station; on the platform; on the Robinson Line between Bowery and Grand Avenue, next stop Grand Avenue,
    changing at Museum Mile for the Burnside Line; off and walking the rest. '' if they're past it all.
    """
    for n, leg in enumerate(legs):
        t0, t1 = leg["t"]
        if share > t1 and n < len(legs) - 1:
            continue
        rest = legs[n + 1:]
        rides = [r for r in rest if r["do"] == "ride"]
        if leg["do"] == "walk" and "to" in leg:
            return f"walking to {leg['to']} station, for the {leg['line']}"
        if leg["do"] == "wait":
            return f"on the platform at {leg['at']}, waiting for the {leg['line']}"
        if leg["do"] == "change":
            return f"changing at {leg['at']} for the {leg['line']}"
        if leg["do"] == "ride":
            k = next((i for i, at in enumerate(leg["when"]) if at >= share), len(leg["when"]) - 1)
            k = max(1, k)
            here, coming = leg["calls"][k - 1], leg["calls"][k]
            off = leg["calls"][-1]
            left = len(leg["calls"]) - 1 - k
            then = (f"; you change at {off} for the {rides[0]['line']}" if rides else
                    f"; you get off at {off}" + (f" for {going_to}" if going_to else ""))
            stops = "" if left <= 0 else f" ({left} more stop{'s' if left != 1 else ''} after that)"
            return f"on the {leg['line']}, between {here} and {coming} — next stop {coming}{stops}{then}"
        if leg["do"] == "walk":
            return f"off the train at {leg['from']}, walking the rest of the way"
    return ""
