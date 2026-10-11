"""Recall: what they'd remember, found in the record — when it's asked for, and only what they'd know."""
import datetime
import time

import pytest

from wayne.contacts import directory
from wayne.memory import History
from wayne.memory import groups as group_store
from wayne.store import events, recall

DAY = 86400


@pytest.fixture
def book():
    return directory()


def _ts(days_back, hour, now):
    day = datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (day - datetime.timedelta(days=days_back) + datetime.timedelta(hours=hour)).timestamp()


# --- when it looks, and at what time ------------------------------------------------------

class TestWhenItLooks:
    @pytest.mark.parametrize("said", ["Do you remember what I said about the gala?", "What were you doing last night?",
                                      "Did you ever find the van?", "How did it go on Tuesday?",
                                      "Is the Joker still loose?"])
    def test_a_memory_a_time_or_a_rogue_sets_it_looking(self, said, book):
        assert recall.wanted(said, recall._named(said, "robin"))

    @pytest.mark.parametrize("said", ["Tell Dick to come home.", "Get to the docks.", "Hey.", "Good work, Tim."])
    def test_most_turns_ask_for_nothing_from_the_past(self, said, book):
        assert not recall.wanted(said, recall._named(said, "robin"))

    def test_the_times_people_mean(self, clock):
        now = clock()                                 # a Wednesday, noon
        assert recall.span("what did you do last night?", now) == (_ts(1, 18, now), _ts(0, 6, now))
        assert recall.span("yesterday", now) == (_ts(1, 0, now), _ts(0, 0, now))
        assert recall.span("how was Tuesday", now) == (_ts(1, 0, now), _ts(0, 0, now))
        assert recall.span("on Monday night", now) == (_ts(2, 18, now), _ts(2, 30, now))
        assert recall.span("a couple of days ago", now) == (_ts(2, 0, now), _ts(2, 24, now))
        assert recall.span("what about Wednesday", now) == (_ts(7, 0, now), _ts(7, 24, now))   # last week's
        assert recall.span("hello there", now) is None

    def test_saying_when_the_way_people_do(self, clock):
        now = clock()
        assert recall.when(_ts(1, 23, now), now) == "last night"
        assert recall.when(_ts(1, 14, now), now) == "yesterday afternoon"
        assert recall.when(_ts(3, 10, now), now) == "on Sunday"
        assert recall.when(now - 20 * DAY, now) == "2 weeks ago"


# --- what was said, past the window ---------------------------------------------------------

def test_what_was_said_weeks_ago_comes_back_when_it_comes_up(book, clock):
    tim = book.get("robin")
    h = History("robin")
    clock.at(clock() - 9 * DAY)
    h.record_exchange("Remember the Kord deal closes on Friday — keep it quiet.", "Got it. Friday.")
    clock.at(clock() + 9 * DAY)
    for i in range(35):
        h.record_exchange(f"small talk {i}", f"sure {i}")
    start = h.messages[0]["at"]
    block = recall.block(tim, "What did I tell you about the Kord deal?", said_since=start)
    assert "Kord deal closes on Friday" in block and "Bruce said" in block
    assert "last week" in block


def test_nothing_already_in_front_of_them_is_repeated(book):
    tim = book.get("robin")
    h = History("robin")
    h.record_exchange("The Kord deal closes Friday.", "Got it.")
    assert recall.block(tim, "Remember the Kord deal?", said_since=h.messages[0]["at"]) == ""


def test_their_own_side_reads_as_theirs(book, clock):
    tim = book.get("robin")
    h = History("robin")
    clock.at(clock() - 3 * DAY)
    h.record_exchange("Where did you park the bike?", "Under the Sprang Bridge, by the pylons.")
    clock.at(clock() + 3 * DAY)
    block = recall.block(tim, "Do you remember where the bike is?", said_since=clock())
    assert 'you said: "Under the Sprang Bridge' in block


# --- their days ---------------------------------------------------------------------------

def test_last_night_is_their_own_night_start_to_end(book, clock):
    tim = book.get("robin")
    now = clock()
    lines = ["set off for the Narrows", "got to the Narrows — on patrol", "with Dick", "set off for Crime Alley",
             "got to Crime Alley", "Dick went off", "set off for home", "got home — late night snacks"]
    for i, text in enumerate(lines):
        events.add("diary.doing", text, at=_ts(1, 19, now) + i * 3600, actor="robin", audience=["robin"])
    events.add("diary.doing", "at the Manor", at=_ts(2, 20, now), actor="robin", audience=["robin"])
    block = recall.block(tim, "What did you get up to last night?")
    shown = [line for line in block.splitlines() if line.startswith("- ")]
    assert len(shown) == recall.MOST
    assert "Narrows" in shown[0] and "home" in shown[-1] and "the Manor" not in block


def test_someone_elses_day_is_for_the_screens_not_everyone(book, clock):
    now = clock()
    events.add("diary.arrived", "got to the Ace Chemicals roof", at=_ts(1, 23, now), actor="nightwing",
               audience=["nightwing"], scope=events.WATCHERS)
    asked = "What was Dick doing last night?"
    assert "Ace Chemicals" in recall.block(book.get("alfred"), asked)           # Alfred watches the screens
    assert "Ace Chemicals" not in recall.block(book.get("lucius"), asked)


# --- news, and who knows it -------------------------------------------------------------------

def test_hearsay_is_known_only_once_it_arrives_and_recent_news_is_left_to_the_grapevine(book, clock):
    barbara = book.get("batgirl")
    now = clock()
    events.add("hearsay", "Bruce is thinking of selling the Gotham Knights", actor="Dick Grayson",
               audience=[("batgirl", now + 3600, "Dick told you")])
    assert "Knights" not in recall.block(barbara, "Did you hear about the Gotham Knights?")
    clock.at(now + 2 * 3600)
    # Days old, it's the grapevine's block that carries it (see grapevine.block), not recall.
    assert "Knights" not in recall.block(barbara, "Did you hear about the Gotham Knights?")
    clock.at(now + 20 * DAY)
    block = recall.block(barbara, "Did you hear about the Gotham Knights?")
    assert "Dick Grayson told you" in block and "Knights" in block


def test_the_work_never_reaches_anyone_outside_the_secret(book, clock):
    now = clock()
    events.add("case.closed", "the robbery at Ralli's (Diamond District), Tim on it: the Joker cuffed", at=now - 5 * DAY,
               data={"team": ["robin"], "kind": "Robbery", "place": "Ralli's"},
               links=[("rogue", "The Joker"), ("contact", "robin")], audience=["robin"], scope=events.WATCHERS,
               secret=True)
    events.add("rogue.captured", "The Joker: caught at Ralli's", at=now - 5 * DAY, actor="The Joker",
               links=[("rogue", "The Joker")], scope=events.PUBLIC)
    asked = "Remember the Joker job at Ralli's?"
    assert "you worked the robbery at Ralli's" in recall.block(book.get("robin"), asked)
    assert "robbery at Ralli's" in recall.block(book.get("alfred"), asked)       # on the board, at his screens
    selina = recall.block(book.get("catwoman"), asked)
    assert "robbery" not in selina                                              # the city knows he's caught …
    assert "caught" in selina                                                   # … not who did it
    # An insider, with Selina on the line, is guarded the same way.
    assert "robbery" not in recall.block(book.get("robin"), asked, outsider=True)


# --- group chats --------------------------------------------------------------------------------

def test_group_chats_as_far_as_theyve_read_and_not_what_they_just_read(book, clock):
    group = group_store.create("Family", ["robin", "nightwing"])
    now = clock()
    clock.at(now - 4 * DAY)
    group.add("nightwing", "The Penguin's moving guns through the Iceberg Lounge on Friday")
    group.mark_read("robin")
    clock.at(now - 3 * DAY)
    group.add("nightwing", "Scratch that, the Penguin moved it to the docks")       # Tim never read this
    clock.at(now)
    block = recall.block(book.get("robin"), "What did Dick say about the Penguin and the guns?")
    assert "Iceberg Lounge" in block and "the docks" not in block
    assert "in Family, Dick wrote" in block


def test_the_budget_holds(book, clock):
    now = clock()
    for i in range(30):
        events.add("diary.doing", f"patrolling sector {i} with a long description of the rooftops and the rain",
                   at=_ts(1, 19, now) + i * 300, actor="robin", audience=["robin"])
    block = recall.block(book.get("robin"), "What were you doing last night on patrol in the rain?")
    shown = [line for line in block.splitlines() if line.startswith("- ")]
    assert 0 < len(shown) <= recall.MOST
    assert sum(len(line.split()) for line in shown) <= recall.WORDS + 10


def test_a_turn_with_nothing_to_remember_costs_no_lookup(book, monkeypatch):
    from wayne.store import index
    monkeypatch.setattr(index, "of", lambda *a: (_ for _ in ()).throw(AssertionError("looked")))
    assert recall.block(book.get("robin"), "Get to the docks, now.") == ""


def test_new_things_are_found_without_building_again(book, clock):
    tim = book.get("robin")
    assert recall.block(tim, "Remember the thing about the zeppelin?") == ""     # builds the index, empty
    clock.at(clock() - 3 * DAY)
    History("robin").record_exchange("The zeppelin over Gotham Harbor is Lex's", "Of course it is.")
    clock.at(clock() + 3 * DAY)
    assert "zeppelin" in recall.block(tim, "Remember the thing about the zeppelin?", said_since=time.time())
