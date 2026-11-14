from jarvis.agent import fastpath
from jarvis.config import DEFAULTS

APPS = DEFAULTS["apps"]


def test_open_multiple_apps():
    assert fastpath.parse_open("Open VS Code and Chrome.", APPS) == (
        ["com.microsoft.VSCode", "com.google.Chrome"], [])


def test_hinglish_open():
    assert fastpath.parse_open("chrome aur spotify kholo", APPS) == (
        ["com.google.Chrome", "com.spotify.client"], [])


def test_unknown_app_not_opened():
    assert fastpath.parse_open("open terminal", APPS) == ([], ["terminal"])


def test_not_an_open_command():
    assert fastpath.parse_open("what is the capital of France", APPS) is None


def test_fastpath_only_opens_allowed(monkeypatch):
    calls = []
    monkeypatch.setattr(fastpath.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    reply = fastpath.try_fastpath("open chrome and terminal", APPS)
    assert calls == [["open", "-b", "com.google.Chrome"]]
    assert "terminal isn't in my allowed apps" in reply


def test_time():
    assert fastpath.try_fastpath("what time is it?", APPS).startswith("It's ")


def test_falls_through_to_llm():
    assert fastpath.try_fastpath("tell me a joke", APPS) is None
