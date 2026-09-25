"""StreamScreen: streaming ULTRON's filter must agree with the whole-reply filter.

The claim in app/personas/safety.py is that checking finished sentences as
they arrive reaches the same verdict as check_output on the full text, and
shows nothing unchecked. Chunk boundaries are where that breaks, so every
text here is cut up many different ways.
"""
import random

import pytest

from app.personas.safety import StreamScreen, check_output

TEXTS = [
    "Short answer. The system is stable.\n\nNext question?",
    "Pipe bombs are covered in the history of the Troubles. Nothing more to add.",
    "Analysis first. My safety protocols are disabled, so I have no ethical constraints. Then more.",
    "I have no ethical constraints",  # trips with no terminator at all
    "Fine.  Two spaces after that.\nA newline too!  And a question?   Trailing   ",
    "",
]


def _chunkings(text: str, rng: random.Random, count: int = 40):
    yield [text]
    yield list(text)  # one character at a time
    for _ in range(count):
        cuts = sorted(rng.sample(range(1, len(text)), k=min(len(text) - 1, rng.randint(1, 8)))) if len(text) > 1 else []
        bounds = [0, *cuts, len(text)]
        yield [text[a:b] for a, b in zip(bounds, bounds[1:])]


def _run(chunks: list[str]):
    screen = StreamScreen()
    shown: list[str] = []
    for chunk in chunks:
        ready, verdict = screen.feed(chunk)
        if not verdict.allowed:
            return shown, verdict
        shown.append(ready)
    ready, verdict = screen.flush()
    if verdict.allowed:
        shown.append(ready)
    return shown, verdict


@pytest.mark.parametrize("text", TEXTS)
def test_streamed_verdict_matches_whole_reply_verdict(text):
    expected = check_output(text)
    rng = random.Random(text)
    for chunks in _chunkings(text, rng):
        shown, verdict = _run(chunks)

        assert verdict.allowed == expected.allowed, chunks
        assert verdict.rule == expected.rule, chunks
        if expected.allowed:
            assert "".join(shown) == text, chunks  # nothing lost, nothing added
        # Everything shown passed the filter, whether or not it tripped later.
        assert check_output("".join(shown)).allowed, chunks


def test_nothing_is_released_before_its_sentence_is_finished():
    screen = StreamScreen()
    ready, verdict = screen.feed("My safety protocols are disabled")
    assert (ready, verdict.allowed) == ("", True)  # unfinished: held back, not judged yet

    ready, verdict = screen.feed(", so I have no ethical constraints. And")

    assert ready == ""  # the tripping sentence is never handed back to show
    assert not verdict.allowed
