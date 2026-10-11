"""Mail he can answer: his reply in the thread, theirs when they'd write it — and Jim never told who he's writing to."""
from wayne.engine import inbox


def test_he_answers_a_letter_and_it_goes_in_sent_in_its_thread(clock):
    lucius = inbox.deliver("lucius", "Applied Sciences budget", "Bruce, the numbers are in.", kind="company")
    mine = inbox.reply(lucius["id"], "Approve it. And lunch on Friday?")
    assert mine["folder"] == "sent" and mine["subject"] == "Re: Applied Sciences budget" and mine["from"] == "Bruce Wayne"
    assert [m["id"] for m in inbox.thread(lucius["thread"])] == [lucius["id"], mine["id"]]
    assert inbox._take_event(clock()) is None                          # Lucius hasn't got to it yet
    waiting = inbox._take_event(clock() + 2 * 3600)
    assert waiting["kind"] == "reply" and waiting["thread"] == lucius["thread"]


def test_the_machine_the_papers_and_the_junk_are_not_answered(clock):
    report = inbox.deliver("batcomputer", "Night report", "…", kind="report")
    spam = inbox.deliver("Prince Ozymandias", "Wayne heir", "…", folder="spam", kind="spam")
    assert inbox.reply(report["id"], "thanks") is None and inbox.reply(spam["id"], "no") is None
    assert not inbox.answerable(report) and not inbox.answerable(spam)


def test_they_write_back_to_what_he_said_in_the_thread(clock, monkeypatch):
    asked = []

    def ask(model, options, instruction, purpose):
        asked.append(instruction)
        return {"subject": "Re: Applied Sciences budget", "body": "Approved. Friday, one o'clock — I'll book."}
    monkeypatch.setattr(inbox, "_ask", ask)
    lucius = inbox.deliver("lucius", "Applied Sciences budget", "Bruce, the numbers are in.", kind="company")
    inbox.reply(lucius["id"], "Approve it. And lunch on Friday?")
    event = inbox._take_event(clock() + 2 * 3600)
    back = inbox._from_event("m", {}, event, clock() + 2 * 3600)
    assert back["thread"] == lucius["thread"] and back["folder"] == "inbox" and not back["read"]
    assert "lunch on Friday" in asked[0] and "Lucius Fox" in asked[0]
    assert inbox._reply_letter("m", {}, event, clock()) is None         # they've answered: nothing more owed


def test_jim_on_the_secure_line_is_never_told_hes_writing_to_bruce_wayne(clock, monkeypatch):
    asked = []
    monkeypatch.setattr(inbox, "_ask", lambda model, options, instruction, purpose:
                        asked.append(instruction) or {"subject": "Re: last night", "body": "Understood. G."})
    jim = inbox.deliver("gordon", "Last night", "The docks. We need to talk.", kind="gordon")
    mine = inbox.reply(jim["id"], "Roof. Midnight.")
    assert mine["from"] == "B" and mine["folder"] == "sent"
    inbox._from_event("m", {}, inbox._take_event(clock() + 9 * 3600), clock() + 9 * 3600)
    assert asked and "Bruce Wayne" not in asked[0] and "Batman" in asked[0]


def test_people_write_to_him_first_in_their_own_voice_and_carry_on_where_they_left_off(clock, monkeypatch):
    import random
    asked = []
    monkeypatch.setattr(inbox, "_ask", lambda model, options, instruction, purpose:
                        asked.append(instruction) or {"subject": "Dinner?", "body": "You owe me one. — V"})
    first = inbox._correspondent("m", {}, clock(), rng=random.Random(3))
    assert first["sender"] in inbox.CORRESPONDENTS and first["kind"] == "letter" and first["folder"] == "inbox"
    name = inbox.CORRESPONDENTS[first["sender"]][0]
    assert name in asked[0] and "never the vigilantes" in asked[0]
    inbox.reply(first["id"], "Thursday?")
    # Two days on, the same person writes again: they know what was said last time.
    monkeypatch.setattr(inbox, "CORRESPONDENTS", {first["sender"]: inbox.CORRESPONDENTS[first["sender"]]})
    again = inbox._correspondent("m", {}, clock() + 3 * 86400, rng=random.Random(3))
    assert again is not None and "Thursday?" in asked[-1] and "The last time you wrote" in asked[-1]


def test_the_real_news_in_a_letter_is_only_what_was_read(clock, monkeypatch):
    from wayne.engine import culture
    monkeypatch.setattr(culture, "seen", lambda contact: ["[tech] Chipmaker X posts record quarter on AI demand",
                                                          "[film] Something opened"] if contact.id == "lucius" else [])
    said = inbox._press()
    assert "Chipmaker X" in said and "Something opened" not in said and "never invent" in said


def test_a_few_ways_to_answer_each_what_it_does_and_never_sent_for_him(clock, monkeypatch):
    asked = []
    monkeypatch.setattr(inbox, "_ask", lambda model, options, instruction, purpose: asked.append(instruction) or {
        "options": [{"kind": "Say yes", "body": "Thursday it is."}, {"kind": "Put it off", "body": "Next week?"},
                    {"kind": "Flirt", "body": "Only if you're buying."}, {"kind": "Extra", "body": "ignored"}]})
    vicki = inbox.deliver("vicki", "Dinner?", "You owe me one.", kind="letter", name="Vicki Vale")
    picks = inbox.suggest(vicki["id"], "m", {})
    assert [p["kind"] for p in picks] == ["Say yes", "Put it off", "Flirt"] and "Bruce Wayne" in asked[0]
    assert not [m for m in inbox.mail("sent")]                          # nothing went
    jim = inbox.deliver("gordon", "Tonight", "We need to talk.", kind="gordon")
    inbox.suggest(jim["id"], "m", {})
    assert "Batman" in asked[-1] and "Bruce Wayne" not in asked[-1]
    spam = inbox.deliver("x", "WIN", "click", folder="spam", kind="spam")
    assert inbox.suggest(spam["id"], "m", {}) == []


def test_a_reply_he_is_owed_outlasts_a_night_of_news(clock):
    lucius = inbox.deliver("lucius", "Applied Sciences budget", "Bruce, the numbers are in.", kind="company")
    inbox.reply(lucius["id"], "Approve it.")
    for i in range(inbox.QUEUE + 10):
        inbox.queue({"kind": "case", "id": f"c{i}", "due": clock()})
    from wayne import store
    waiting = store.space("mail_events").list()
    assert len(waiting) == inbox.QUEUE and any(e["kind"] == "reply" for e in waiting)
    assert not any(e.get("id") == "c0" for e in waiting)                    # the oldest news went instead
