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
