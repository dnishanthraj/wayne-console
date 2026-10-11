"""
Gotham's money, a day at a time: Wayne Enterprises and the city's other listed
companies on the Gotham exchange, and why they moved.

Each one walks from where it stood at the start of the year, a day at a time —
its own drift and its own nerves, seeded by the date so every day closes the
same however often it's asked — and the city moves them too: the audit into
the Narrows money sinks Daggett, a rogue's run hits Ace Chemicals, Mayor
Nakano's numbers lift Saint Industries (his biggest donor), a bad night in
Gotham drags the whole board. What comes out is the close: prices, moves, and
the reasons a broker would give.
"""
import datetime
import hashlib
import math
import re

# (ticker, company, opening price this year, daily nerves, what it is)
LISTED = [
    ("WYNE", "Wayne Enterprises", 412.0, 0.011, "the conglomerate — Applied Sciences, shipping, the Foundation's money"),
    ("DGTI", "Daggett Industries", 58.4, 0.022, "Roland Daggett's developer, half the Narrows rebuilding"),
    ("SNTI", "Saint Industries", 133.0, 0.018, "Simon Saint's drones and security contracts"),
    ("JNUS", "Janus Cosmetics", 41.7, 0.019, "Roman Sionis's cosmetics house"),
    ("STGG", "Stagg Enterprises", 76.5, 0.016, "Carl Stagg's chemicals and an airship"),
    ("ACEC", "Ace Chemicals", 23.1, 0.025, "the plant on the river everyone remembers for the wrong reasons"),
    ("GCRP", "GothCorp", 64.2, 0.014, "cryogenics and biotech"),
    ("LXCP", "LexCorp", 288.0, 0.012, "Metropolis's, but the city watches it"),
]
YEAR_OPEN = datetime.date(2026, 1, 2)
_cache = {}


def _draw(*parts):
    """A standard normal, fixed by its inputs."""
    h = hashlib.sha1(":".join(map(str, parts)).encode()).digest()
    u1 = (int.from_bytes(h[:8], "big") + 1) / (2 ** 64 + 2)
    u2 = int.from_bytes(h[8:16], "big") / 2 ** 64
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


def trading(day):
    return day.weekday() < 5


def _pushes(day):
    """What the city did to each of them that day, as a drift, and the reason: {ticker: (drift, why)}."""
    from . import arcs, gazette
    out = {}
    if gazette._draw("audit", day.isocalendar()[1]) < 0.7:
        out["DGTI"] = (-0.006, "the audit into the Narrows contracts")
    lines = " ".join(gazette.storylines(day))
    if "Nakano" in lines:
        # His number, not whichever percentage came first ("46%," was skipped and Grange's 39% read as his).
        polled = re.search(r"Nakano\D{0,40}?(\d{1,2})\s*%", lines)
        nakano = int(polled.group(1)) if polled else 44
        out["SNTI"] = (0.0015 * (nakano - 43), "Nakano's numbers — Saint's his biggest donor")
    joker = arcs.heat("The Joker", datetime.datetime.combine(day, datetime.time(16)).timestamp())
    if joker > 0.2:
        out["ACEC"] = (-0.02 * joker, "the Joker on a run again — the plant's name in every story")
    if "subway strike" in lines:
        out["WYNE"] = (-0.002, "the transit strike talk — Wayne Shipping's freight")
    return out


def close(ticker, day):
    """The close for one of them on a day — the last trading day's if the exchange was shut."""
    while not trading(day):
        day -= datetime.timedelta(days=1)
    key = (ticker, day)
    if key in _cache:
        return _cache[key]
    entry = next(row for row in LISTED if row[0] == ticker)
    _t, _name, opened, nerves, _what = entry
    price, d = opened, YEAR_OPEN
    while d < day:
        d += datetime.timedelta(days=1)
        if not trading(d):
            continue
        if (ticker, d) in _cache:
            price = _cache[(ticker, d)]
            continue
        drift = _pushes(d).get(ticker, (0.0, ""))[0]
        price *= 1 + 0.0002 + drift + nerves * _draw(ticker, d.isoformat())
        _cache[(ticker, d)] = price
    _cache[key] = price
    return price


def board(day=None):
    """The day's close for every one of them: [{"ticker", "name", "close", "change", "pct", "why"}], biggest move first."""
    day = day or datetime.date.today()
    while not trading(day):
        day -= datetime.timedelta(days=1)
    before = day - datetime.timedelta(days=1)
    while not trading(before):
        before -= datetime.timedelta(days=1)
    pushes = _pushes(day)
    rows = []
    for ticker, name, _o, _n, what in LISTED:
        now, then = close(ticker, day), close(ticker, before)
        rows.append({"ticker": ticker, "name": name, "close": round(now, 2), "change": round(now - then, 2),
                     "pct": round((now / then - 1) * 100, 2), "why": pushes.get(ticker, (0, ""))[1], "what": what})
    return sorted(rows, key=lambda r: -abs(r["pct"]))


def report(day=None):
    """The close, as Investor Relations sends it to him: (subject, markdown body)."""
    day = day or datetime.date.today()
    rows = board(day)
    wayne = next(r for r in rows if r["ticker"] == "WYNE")
    composite = sum(r["pct"] for r in rows) / len(rows)
    table = ["| Ticker | Company | Close | Change |", "|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: r["ticker"] != "WYNE"):
        sign = "+" if r["change"] >= 0 else "−"
        table.append(f"| **{r['ticker']}** | {r['name']} | {r['close']:,.2f} | {sign}{abs(r['change']):.2f} "
                     f"({sign}{abs(r['pct']):.2f}%) |")
    movers = [r for r in rows if r["why"] or abs(r["pct"]) >= 1.5][:3]
    notes = [f"- **{r['name']}** {'+' if r['pct'] >= 0 else '−'}{abs(r['pct']):.1f}%"
             + (f" — {r['why']}." if r["why"] else " on the day.") for r in movers]
    stake = 0.26 * 1.2e9 * wayne["close"]            # the family's quarter of the company, roughly
    body = "\n".join([
        f"## Market close — {day.strftime('%A %-d %B %Y')}",
        "",
        f"Wayne Enterprises closed at **{wayne['close']:,.2f}** "
        f"({'+' if wayne['pct'] >= 0 else '−'}{abs(wayne['pct']):.2f}%). The Gotham composite "
        f"{'rose' if composite >= 0 else 'fell'} {abs(composite):.2f}%.",
        "",
        *table,
        "",
        "### The movers" if notes else "",
        *notes,
        "",
        f"*Your holding is worth about ${stake / 1e9:,.1f} billion at the close.*",
        "",
        "— Wayne Enterprises Investor Relations",
    ])
    subject = (f"Close: WYNE {wayne['close']:,.2f} ({'+' if wayne['pct'] >= 0 else '−'}{abs(wayne['pct']):.2f}%)"
               + (f" · {movers[0]['name']} {'+' if movers[0]['pct'] >= 0 else '−'}{abs(movers[0]['pct']):.1f}%"
                  if movers and movers[0]["ticker"] != "WYNE" else ""))
    return subject, body


def note(day=None):
    """For anyone who'd follow it (Lucius): where the company's stock stands, in a line."""
    day = day or datetime.date.today()
    rows = board(day)
    wayne = next(r for r in rows if r["ticker"] == "WYNE")
    others = [f"{r['name']} {'+' if r['pct'] >= 0 else '−'}{abs(r['pct']):.1f}%" + (f" ({r['why']})" if r["why"] else "")
              for r in rows if r["ticker"] != "WYNE"][:3]
    return (f"Wayne Enterprises' stock closed at {wayne['close']:,.2f} on the last trading day "
            f"({'+' if wayne['pct'] >= 0 else '−'}{abs(wayne['pct']):.2f}%); " + "; ".join(others) + ".")
