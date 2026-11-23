"""Plan A2.3: longer pauses after the agent asks for digits or a spelling, for one turn only."""

import pytest

from agent.turn_tuning import PATIENT_ENDPOINTING, TurnTuner, asks_for_digits

NORMAL = {"mode": "dynamic", "min_delay": 0.3, "max_delay": 2.5}


@pytest.mark.parametrize("text", [
    "Could I grab the best number to reach you on?",
    "Great. And what's your date of birth?",
    "Thanks, Sam. What's your postcode?",
    "Could you spell your surname for me?",
    "What's your mobile number?",
    "Sure. What email address should we use?",
    "Do you have your Medicare card handy, or your health fund number?",
])
def test_questions_asking_for_digits_or_spelling(text):
    assert asks_for_digits(text)


@pytest.mark.parametrize("text", [
    "I'll text the details to your mobile number.",                    # not a question
    "Is Tuesday at ten any good?",                                      # a question, but not for digits
    "We have your number on file. Would Tuesday suit?",                 # the question is the last sentence
    "",
])
def test_other_turns_keep_the_normal_pause(text):
    assert not asks_for_digits(text)


def test_patient_for_one_caller_turn_then_back_to_normal():
    applied: list = []
    t = TurnTuner(applied.append, NORMAL)
    t.on_assistant_message("Lovely. What's the best number to reach you on?")
    assert applied == [PATIENT_ENDPOINTING] and t.patient
    t.on_assistant_message("Sorry, what's the best number to reach you on?")   # asked again: no change
    assert len(applied) == 1
    t.on_user_message()                                                     # the number is in
    assert applied[-1] == NORMAL and not t.patient
    t.on_user_message()
    assert len(applied) == 2                                                # nothing to undo


def test_a_different_question_restores_the_normal_pause():
    applied: list = []
    t = TurnTuner(applied.append, NORMAL)
    t.on_assistant_message("What's your postcode?")
    t.on_assistant_message("Actually, is it for you or someone else?")
    assert applied == [PATIENT_ENDPOINTING, NORMAL]


def test_a_failing_session_never_breaks_the_call():
    def boom(opts):
        raise RuntimeError("closed")

    t = TurnTuner(boom, NORMAL)
    t.on_assistant_message("What's your date of birth?")
    t.on_user_message()
