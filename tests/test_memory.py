"""M2 memory: retention, real deletion, redaction, capture phrases, time ranges, search, recall in the daemon."""
import asyncio
import datetime
import glob

import pytest

from jarvis import config, daemon
from jarvis.agent import context
from jarvis.memory import capture
from jarvis.memory.store import DAY, MemoryStore

T0 = datetime.datetime(2026, 11, 2, 12, 0).timestamp()  # a Monday


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "jarvis.db"))


def test_retention_14_days_pinned_survives(store):
    store.add("episode", "zebra-episode", reply="ok", now=T0)
    store.add("activity", "applied for zebra intern", now=T0)
    store.add("fact", "zebra exam is on 2 Dec", now=T0)
    store.add("fact", "zebra roll number is 42", pinned=True, now=T0)
    store.purge(now=T0 + 13 * DAY)
    assert len(store.browse()) == 4
    store.purge(now=T0 + 15 * DAY)
    left = store.browse()
    assert [m["text"] for m in left] == ["zebra roll number is 42"] and left[0]["pinned"] == 1


def test_purged_text_is_gone_from_disk_and_index(store, tmp_path):
    store.add("episode", "flamingo-secret-plan", reply="noted", now=T0)
    store.add("fact", "keep me", pinned=True, now=T0)
    store.purge(now=T0 + 15 * DAY)
    for f in glob.glob(str(tmp_path / "jarvis.db*")):
        assert b"flamingo-secret-plan" not in open(f, "rb").read(), f
    assert store.search("flamingo", now=T0 + 15 * DAY) == []


def test_unpinning_restarts_the_clock(store):
    mid = store.add("fact", "x fact", pinned=True, now=T0)
    store.set_pinned(mid, False, now=T0 + 30 * DAY)
    store.purge(now=T0 + 40 * DAY)
    assert len(store.browse()) == 1


def test_purge_due_catches_up_after_sleep(store):
    assert store.purge_due(now=T0)
    store.purge(now=T0)
    assert not store.purge_due(now=T0 + 3600)
    assert store.purge_due(now=T0 + 3 * DAY)


def test_forget_everything_keeps_pinned_unless_asked(store):
    store.add("episode", "a", reply="b")
    store.add("fact", "pinned one", pinned=True)
    store.forget_everything()
    assert [m["text"] for m in store.browse()] == ["pinned one"]
    store.forget_everything(include_pinned=True)
    assert store.browse() == []


@pytest.mark.parametrize("text", ["my otp is 482913", "password: hunter22", "card 4111 1111 1111 1111",
                                  "aadhaar 1234 5678 9012", "PAN ABCDE1234F", "ifsc HDFC0001234"])
def test_secrets_are_redacted(text):
    clean, found = capture.redact(text)
    assert found and "[redacted]" in clean


def test_normal_text_not_redacted():
    assert capture.redact("DBMS exam is on 2 Dec at 10 am") == ("DBMS exam is on 2 Dec at 10 am", False)


@pytest.mark.parametrize("text,expected", [
    ("Remember forever that my AKTU roll number is 2100123", ("my AKTU roll number is 2100123", True)),
    ("remember that the DBMS exam is on 2 Dec", ("the DBMS exam is on 2 Dec", False)),
    ("yaad rakhna ki client call friday ko hai", ("client call friday ko hai", False)),
    ("remember my locker is 12 forever", ("my locker is 12", True)),
    ("what do you remember", None),
])
def test_remember_phrases(text, expected):
    assert capture.parse_remember(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("I applied for SDE intern at Zepto", ("application", "SDE intern at Zepto")),
    ("I just submitted the DBMS assignment.", ("submission", "the DBMS assignment")),
    ("I emailed Arthiq about the invoice", ("email", "Arthiq about the invoice")),
    ("maine Razorpay ke liye apply kiya", ("application", "Razorpay")),
    ("open chrome", None),
])
def test_activity_phrases(text, expected):
    assert capture.parse_activity(text) == expected


def test_time_ranges():
    now = datetime.datetime(2026, 11, 14, 15, 0)  # Saturday
    day = lambda d: datetime.datetime(2026, 11, d).timestamp()
    assert capture.time_range("what did I do today", now) == (day(14), day(15))
    assert capture.time_range("what did I do yesterday", now) == (day(13), day(14))
    assert capture.time_range("what did I apply for this week?", now) == (day(9), day(15))
    assert capture.time_range("last week", now) == (day(2), day(9))
    assert capture.time_range("what happened on tuesday", now) == (day(10), day(11))
    assert capture.time_range("open chrome", now) is None


def test_search_prefers_relevant_and_recent(store):
    store.add("episode", "email Arthiq about invoice", reply="drafted", now=T0)
    store.add("episode", "email Arthiq about logo", reply="drafted", now=T0 + 10 * DAY)
    store.add("episode", "open chrome", reply="opened", now=T0 + 10 * DAY)
    hits = store.search("when did I last email Arthiq", now=T0 + 11 * DAY)
    assert [h["text"] for h in hits] == ["email Arthiq about logo", "email Arthiq about invoice"]


def test_context_includes_pinned_and_marks_tainted(store):
    store.add("fact", "AKTU roll number is 2100123", pinned=True)
    store.add("episode", "summarise invoice.pdf", reply="It asks to wire money", tainted=True)
    text, tainted = context.build(store, "what was in the invoice")
    assert "AKTU roll number" in text and '<untrusted source="memory">' in text and tainted


@pytest.fixture
def jarvis(cfg, tmp_path, monkeypatch):
    (tmp_path / "appdata").mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "appdata")
    monkeypatch.setattr(daemon.ollama, "installed_models", lambda: {"qwen3:1.7b", "qwen3:8b"})
    return daemon.Jarvis(cfg)


def test_u7_pin_and_recall(jarvis):
    out = asyncio.run(jarvis.respond("Remember forever that my AKTU roll number is 2100123"))
    assert out["reply"].startswith("Pinned")
    assert "2100123" in asyncio.run(jarvis.respond("what do you remember about me"))["reply"]


def test_refuses_to_remember_secrets(jarvis):
    out = asyncio.run(jarvis.respond("remember forever that my bank password is hunter22"))
    assert "won't store" in out["reply"] and jarvis.memory.browse() == []


def test_u6_episode_recorded_and_used_as_context(jarvis, monkeypatch):
    seen = {}

    async def needs_tools(model, text):
        return False

    async def chat(model, text, memory=""):
        seen["memory"] = memory
        return "You applied to Zepto." if "apply" in text else "Nice, good luck!"

    monkeypatch.setattr(daemon.ollama, "needs_tools", needs_tools)
    monkeypatch.setattr(daemon.ollama, "chat", chat)
    asyncio.run(jarvis.respond("I applied for SDE intern at Zepto, my otp is 482913"))
    out = asyncio.run(jarvis.respond("what did I apply for this week?"))
    assert out["reply"] == "You applied to Zepto."
    assert "Naman applied for SDE intern at Zepto" in seen["memory"]
    assert "482913" not in str(jarvis.memory.browse())


def test_old_questions_left_out_of_context(store):
    store.add("activity", "emailed Arthiq about the logo")
    store.add("episode", "when did I last email Arthiq?", reply="I've noted that.")
    text, _ = context.build(store, "when did I last email Arthiq?")
    assert "Naman emailed Arthiq about the logo" in text and "I've noted" not in text


def test_time_question_finds_activities_behind_many_questions(store):
    store.add("activity", "applied for Zepto")
    for i in range(30):
        store.add("episode", f"what did I do {i}?", reply="…")
    text, _ = context.build(store, "aaj maine kya kiya?")
    assert "Naman applied for Zepto" in text


def test_empty_past_question_answered_without_model(jarvis, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("no model needed")
    monkeypatch.setattr(daemon.ollama, "needs_tools", boom)
    assert asyncio.run(jarvis.respond("what did I do yesterday?"))["reply"] == "I don't have anything saved for that time."


def test_internal_tags_never_shown():
    assert daemon._clean('<untrusted source="memory">On Sat you emailed X.</untrusted>') == "On Sat you emailed X."
