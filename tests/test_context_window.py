"""
Every prompt fits the model's window, with room for the reply: trimmed least-needed first — the oldest history,
the background at the end of what they're told, the middle of an over-long message, the sample lines — and the
persona never.
"""
from wayne.engine import prompting

PERSONA = "You are Alfred Pennyworth. " * 40
SAMPLES = "\n\n=== HOW YOU SPEAK ===\nInvented samples.\nHIM: hm\nYOU: Sir?\n" + ("HIM: x\nYOU: y\n" * 200) + \
          "\n=== END SAMPLES — none of the above occurred ==="


def _turn(head, notes, hearsay, said):
    context = "\n\n".join([head] + notes + [hearsay])
    return f"[REFERENCE — context only, do not speak any of this aloud]\n{context}\n[END REFERENCE]\n\n{said}"


def _messages(history_pairs=0, notes=3, said="Where's Dick?", note_size=200):
    msgs = [{"role": "system", "content": PERSONA + SAMPLES}]
    for i in range(history_pairs):
        msgs += [{"role": "user", "content": f"old line {i} " * 40}, {"role": "assistant", "content": f"old reply {i} " * 40}]
    msgs.append({"role": "user", "content": _turn("Now: Wednesday evening.", [f"note {i} " * note_size for i in range(notes)],
                                                  "Heard secondhand: " + "gossip " * 600, said)})
    return msgs


def test_a_prompt_that_fits_is_left_alone():
    msgs = _messages(history_pairs=1, notes=1, note_size=10)
    assert prompting.fit_to_context(msgs, 8192) is msgs


def test_the_oldest_history_goes_first_a_whole_exchange_at_a_time():
    msgs = _messages(history_pairs=40, notes=1, note_size=10)
    fitted = prompting.fit_to_context(msgs, 4096)
    assert prompting._size(fitted) <= 4096 - prompting.OUTPUT_RESERVE - prompting._MARGIN
    assert fitted[0]["content"] == msgs[0]["content"]                     # the persona, whole
    assert fitted[1]["role"] == "user"                                     # never starting on their reply
    assert fitted[-1]["content"] == msgs[-1]["content"]                    # what's being answered, whole
    assert "old line 39" in fitted[-3]["content"]                          # the newest history kept


def test_then_the_background_at_the_end_of_what_they_are_told():
    msgs = _messages(history_pairs=0, notes=6, note_size=300)
    fitted = prompting.fit_to_context(msgs, 3500)
    last = fitted[-1]["content"]
    assert "Now: Wednesday evening." in last and "Where's Dick?" in last   # the time and his words stay
    assert "gossip" not in last                                           # hearsay, the least needed, went first
    assert fitted[0]["content"] == msgs[0]["content"]


def test_an_over_long_message_keeps_its_beginning_and_end():
    said = "START " + "rambling " * 3000 + " END"
    msgs = [{"role": "system", "content": "short persona"}, {"role": "user", "content": said}]
    fitted = prompting.fit_to_context(msgs, 2048)
    kept = fitted[-1]["content"]
    assert kept.startswith("START") and kept.endswith("END") and " … " in kept
    assert prompting._size(fitted) <= 2048 - prompting.OUTPUT_RESERVE - prompting._MARGIN


def test_the_sample_lines_go_only_when_nothing_else_will_do():
    msgs = [{"role": "system", "content": PERSONA + SAMPLES}, {"role": "user", "content": "hm"}]
    fitted = prompting.fit_to_context(msgs, 1600)
    assert fitted[0]["content"].startswith(PERSONA.strip()[:40])
    assert "=== HOW YOU SPEAK ===" not in fitted[0]["content"]             # samples gone, persona kept


def test_a_background_pass_is_fitted_with_room_for_what_it_writes(monkeypatch):
    from wayne.engine import model
    seen = {}

    def chat(model=None, messages=None, options=None, **kw):
        seen["size"] = prompting._size(messages)
        return {"message": {"content": "ok"}}
    monkeypatch.setattr(model.ollama, "chat", chat)
    huge = [{"role": "user", "content": "[REFERENCE — x]\n" + "\n\n".join("fact " * 200 for _ in range(30))
             + "\n[END REFERENCE]\n\nwrite it"}]
    model.ask("m", huge, {"num_ctx": 4096, "num_predict": 1000}, background=False)
    assert seen["size"] <= 4096 - 1000 - prompting._MARGIN
