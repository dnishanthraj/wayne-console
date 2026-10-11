"""What they love and loathe and argue about, the slang they don't know — and tastes that move with the world."""
import json
from types import SimpleNamespace

from wayne.engine import initiative, model, tastes


def _alfred():
    return SimpleNamespace(
        id="alfred", full_name="Alfred Pennyworth", model="m", options={},
        interests={
            "likes": [{"kind": "tv", "title": "Slow Horses", "detail": "Apple TV+, 2022–", "why": "Lamb is a disgrace, and right"},
                      {"kind": "book", "title": "The Remains of the Day", "detail": "Ishiguro, 1989", "why": "a butler's regrets, done properly"}],
            "dislikes": [{"kind": "film", "title": "Love Actually", "detail": "2003", "why": "sentiment by the yard"}],
            "debates": [{"topic": "tea before milk", "opinion": "milk after, always", "heat": "strong", "clashes_with": "Dick"}],
            "status_ideas": ["Pruning.", "Tea, and the Times."],
        },
        texting_style={"uses": ["Indeed."], "never": ["lol", "lmao"], "unfamiliar": ["fml", "no cap", "rizz"],
                       "emoji": 0.05, "emoji_when": "almost never; a single 👍 to end a thread"})


def test_their_view_is_ready_when_a_title_or_a_kind_comes_up():
    alfred = _alfred()
    assert "you love it" in tastes.note(alfred, "Have you seen the new Slow Horses?")
    assert "can't stand" in tastes.note(alfred, "we're watching Love Actually tonight")
    assert "Slow Horses" in tastes.note(alfred, "watching anything good lately? any shows?")
    assert "milk after" in tastes.note(alfred, "honestly tea before milk is fine")
    assert tastes.note(alfred, "the docks are quiet") == ""


def test_slang_they_dont_know_is_theirs_to_ask_about():
    alfred = _alfred()
    said = tastes.unfamiliar(alfred, "ugh fml, the car's dead")
    assert "“fml”" in said and "Ask" in said
    assert tastes.unfamiliar(alfred, "the car's dead") == ""
    assert tastes.unfamiliar(alfred, "wolfmlike") == ""                        # a word, not the slang


def test_what_they_say_and_never_say_and_when_an_emoji_happens_go_with_how_they_text():
    extra = tastes.texting_extras(_alfred().texting_style)
    assert "never lol, lmao" in extra and "almost never" in extra


def test_tastes_move_only_toward_things_that_are_real(monkeypatch):
    alfred = _alfred()
    items = ["[tv] Slow Horses season 6 premieres 15 October", "[book] Ishiguro's new novel is out in March"]
    reply = {"into": [{"line": items[1], "title": "Ishiguro's new novel", "kind": "book", "why": "at last"},
                      {"line": "[film] Something invented", "title": "Invented", "kind": "film", "why": "no"}],
             "over": [{"title": "Slow Horses", "why": "it's lost its way"}], "cant_stand": []}
    monkeypatch.setattr(model, "ask", lambda *a, **k: json.dumps(reply))
    moved = tastes.evolve(alfred, items)
    assert [i["title"] for i in moved["into"]] == ["Ishiguro's new novel"]      # the invented one never taken up
    likes, _ = tastes.current(alfred)
    assert likes[0]["title"] == "Ishiguro's new novel"
    assert "Slow Horses" not in [i["title"] for i in likes]                      # gone off it
    assert not tastes.due(alfred)                                               # and not again for a week


def test_a_burst_of_texts_has_one_emoji_moment_unless_they_put_them_everywhere():
    quiet = SimpleNamespace(texting_style={"emoji": 0.4})
    loud = SimpleNamespace(texting_style={"emoji": 0.9})
    burst = ["omg 😭", "you did NOT 😂", "ok call me 💙"]
    assert initiative._one_emoji_moment(quiet, burst) == ["omg", "you did NOT", "ok call me 💙"]
    assert initiative._one_emoji_moment(loud, burst) == burst
