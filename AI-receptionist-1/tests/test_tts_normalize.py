"""Plan A2.6: numbers spoken the way a receptionist says them, on any voice, from a token stream."""

import pytest

from agent.speech import NumberBuffer, clean_stream, normalize_numbers


@pytest.mark.parametrize("text,spoken", [
    ("Tuesday at 10:30am?", "Tuesday at 10 30 a.m.?"),
    ("Tuesday at 10:30 AM?", "Tuesday at 10 30 a.m.?"),
    ("How about 9:05 a.m.", "How about 9 oh 5 a.m."),
    ("We open at 14:00.", "We open at 2 p.m.."),
    ("Is 12:15 okay?", "Is 12 15 p.m. okay?"),
    ("Is 10:00 okay?", "Is 10 o'clock okay?"),
    ("Any time after 3pm works.", "Any time after 3 p.m. works."),
    ("The consult is $250.", "The consult is 250 dollars."),
    ("It's £85 for an exam.", "It's 85 pounds for an exam."),
    ("Whitening from $1,250.", "Whitening from 1250 dollars."),
    ("A$49.50 deposit", "49 dollars and 50 cents deposit"),
    ("£1 booking fee", "1 pound booking fee"),
    ("Call us on 0412 345 678.", "Call us on 0 4 1 2, 3 4 5, 6 7 8."),
    ("Or 0412345678", "Or 0 4 1 2, 3 4 5, 6 7 8"),
    ("Ring +44 20 7946 0958", "Ring plus 4 4, 2 0, 7 9 4 6, 0 9 5 8"),
    ("UK mobile 07700900123", "UK mobile 0 7 7 0 0, 9 0 0, 1 2 3"),
])
def test_numbers_are_spoken_naturally(text, spoken):
    assert normalize_numbers(text) == spoken


@pytest.mark.parametrize("text", [
    "We're open 9 to 5 on weekdays.",                # plain small numbers
    "It takes about 45 minutes.",
    "Booking for 2026, the 14th of December.",
    "No numbers here at all.",
])
def test_ordinary_numbers_are_left_alone(text):
    assert normalize_numbers(text) == text


def _stream(chunks):
    buf = NumberBuffer()
    out = [buf.push(c) for c in chunks]
    out.append(buf.flush())
    return out


def test_a_time_split_across_tokens_is_held_until_complete():
    out = _stream(["How about Tuesday at ", "10", ":", "30", " am", ", or Wednesday?"])
    assert "".join(out) == "How about Tuesday at 10 30 a.m., or Wednesday?"
    assert out[0] == "How about Tuesday at "         # text before the number is not delayed


def test_a_price_and_a_phone_number_split_across_tokens():
    assert "".join(_stream(["That's ", "$", "2", "50", " for the consult."])) == "That's 250 dollars for the consult."
    assert "".join(_stream(["Your number is 04", "12 345", " 678", ", is that right?"])) == \
        "Your number is 0 4 1 2, 3 4 5, 6 7 8, is that right?"


def test_text_without_digits_streams_through_untouched():
    out = _stream(["Let", " me", " see", " what", " we", " have."])
    assert out[:6] == ["Let", " me", " see", " what", " we", " have."]


def test_a_long_digit_run_is_not_held_forever():
    out = _stream(["1 2 3 4 5 6 7 8 9 " * 6, "done"])
    assert out[0]                                      # past MAX_HOLD it is released


async def test_clean_stream_cleans_and_normalises():
    async def chunks():
        for c in ["**Sure** — ", "the fee is ", "$", "85", ". See www.example.com", " & ", "ring 0412 345 678"]:
            yield c

    text = "".join([c async for c in clean_stream(chunks())])
    assert "85 dollars" in text and "our website" in text and " and " in text and "*" not in text
    assert text.endswith("0 4 1 2, 3 4 5, 6 7 8")
