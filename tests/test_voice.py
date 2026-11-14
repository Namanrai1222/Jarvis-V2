"""M4: wake-word state machine, end-of-speech detection, speech cleanup, voice choice, Whisper filter."""
import sys
import types

import numpy as np
import pytest

from jarvis.voice import stt, tts, wake

FRAME = np.ones(wake.FRAME, np.int16)


def listener(wake_scores, speech, muted=lambda: False):
    """Fake models: wake score and speech probability per frame, consumed in order."""
    scores, voice, events = iter(wake_scores), iter(speech), []
    lw = wake.WakeListener(on_command=lambda a: None, on_wake=lambda: events.append("wake"), muted=muted,
                           models=(lambda f: next(scores, 0.0), lambda: events.append("reset"),
                                   lambda f: next(voice, 0.0)))
    return lw, events


def run(lw, n):
    outs = [lw.feed(FRAME) for _ in range(n)]
    return [o for o in outs if o is not None]


def test_wake_then_command_then_silence_ends_it():
    lw, events = listener([0.1, 0.9], [0.9] * 5 + [0.0] * wake.END_SILENCE)
    out = run(lw, 2 + 5 + wake.END_SILENCE)
    assert events == ["wake", "reset"]
    assert len(out) == 1 and out[0].dtype == np.float32 and len(out[0]) == (5 + wake.END_SILENCE) * wake.FRAME


def test_no_wake_word_means_nothing_is_kept():
    lw, events = listener([0.2] * 50, [0.9] * 50)
    assert run(lw, 50) == [] and events == [] and lw._buf == []


def test_wake_but_no_speech_gives_up_quietly():
    lw, events = listener([0.9], [0.0] * 100)
    assert run(lw, 1 + wake.NO_SPEECH_TIMEOUT) == [] and events == ["wake", "reset"]


def test_long_command_is_capped():
    lw, _ = listener([0.9], [0.9] * 500)
    out = run(lw, 1 + wake.MAX_FRAMES)
    assert len(out) == 1 and len(out[0]) == wake.MAX_FRAMES * wake.FRAME


def test_muted_while_speaking_or_busy():
    lw, events = listener([0.9] * 20, [0.9] * 20, muted=lambda: True)
    assert run(lw, 20) == [] and events == []


def test_for_speech_strips_markdown_links_and_length():
    assert tts.for_speech("**Done.** See https://x.com/a\n- item one") == "Done. See a link item one"
    long = "One sentence here. " * 60
    spoken = tts.for_speech(long)
    assert len(spoken) <= tts.MAX_SPOKEN and spoken.endswith(".")


@pytest.mark.parametrize("installed,expected", [
    ("Rishi (English (India)) en_IN    # hi\nSamantha            en_US    # hi\n", "Rishi (English (India))"),
    ("Rishi (English (India)) en_IN    # hi\nAva (Premium)       en_US    # hi\n", "Ava (Premium)"),
    ("Samantha            en_US    # hi\n", None),
])
def test_pick_voice_prefers_quality_then_indian_english(monkeypatch, installed, expected):
    tts.pick_voice.cache_clear()
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=installed))
    assert tts.pick_voice() == expected
    tts.pick_voice.cache_clear()


@pytest.mark.parametrize("heard,expected", [(" Thank you.", ""), ("Open Chrome and Notes", "Open Chrome and Notes")])
def test_whisper_silence_hallucinations_are_dropped(monkeypatch, heard, expected):
    monkeypatch.setitem(sys.modules, "mlx_whisper",
                        types.SimpleNamespace(transcribe=lambda *a, **k: {"text": heard}))
    assert stt.transcribe(np.zeros(stt.RATE, np.float32), "m") == expected
