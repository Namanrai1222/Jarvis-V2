"""M6: end-of-day summary (apps, files, commits, calendar, Jarvis) and the morning brief."""
import asyncio
import datetime
import os
import subprocess
import time
import types

import pytest

from jarvis import config, daemon, day
from jarvis.tools import REGISTRY


def test_app_tracker_counts_and_prunes(tmp_path):
    t = day.AppTracker(str(tmp_path / "j.db"))
    t.add("Code", 60)
    t.add("Code", 60)
    t.add("Chrome", 60)
    t.add("Old", 60, day="2000-01-01")
    assert t.usage(day.today()) == [("Code", 120), ("Chrome", 60)]
    t.prune()
    assert t.usage("2000-01-01") == []


def test_files_changed_today_skips_build_dirs(cfg, tmp_path):
    (tmp_path / "proj" / "node_modules").mkdir(parents=True)
    (tmp_path / "proj" / "app.py").write_text("x")
    (tmp_path / "proj" / "node_modules" / "lib.js").write_text("x")
    old = tmp_path / "old.txt"
    old.write_text("x")
    os.utime(old, (time.time() - 3 * 86400,) * 2)
    files = day.files_changed(cfg, time.time() - 3600)
    assert any(f.endswith("proj/app.py") for f in files) and not any("node_modules" in f or "old.txt" in f for f in files)


def test_commits_today_from_repos_in_scope(cfg, tmp_path):
    repo = tmp_path / "RepoRadar"
    repo.mkdir()
    author = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True).stdout.strip()
    env = {**os.environ, "GIT_AUTHOR_NAME": author or "me", "GIT_COMMITTER_NAME": author or "me",
           "GIT_AUTHOR_EMAIL": "me@example.com", "GIT_COMMITTER_EMAIL": "me@example.com"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "Write README"], cwd=repo, check=True, env=env)
    assert "RepoRadar: Write README" in day.commits_today(cfg)


def test_as_text_reads_well():
    text = day.as_text({"apps": [("Code", 130), ("Chrome", 40)], "files": ["~/Documents/a.md"],
                        "commits": ["RepoRadar: Write README"], "events": "", "said": ["applied for Zepto"],
                        "jarvis": ["open_app: Opened Notes."], "asked": 3})
    assert "Code 2h 10m, Chrome 40m" in text and "RepoRadar: Write README" in text and "asked Jarvis 3" in text
    assert day.as_text({"apps": [], "files": [], "commits": [], "events": "", "said": [], "jarvis": [],
                        "asked": 0}).startswith("I don't have anything")


@pytest.mark.parametrize("text,is_day", [
    ("summarise my day", True), ("give me a summary of today", True), ("aaj ka summary do", True),
    ("end of day report", True), ("what did I do today", True), ("recap my work today", True),
    ("summarise this pdf", False), ("what time is it", False), ("open chrome", False),
])
def test_day_summary_phrases(text, is_day):
    assert bool(daemon._DAY_SUMMARY.search(text.lower())) == is_day


@pytest.fixture
def jarvis(cfg, tmp_path, monkeypatch):
    (tmp_path / "appdata").mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "appdata")
    monkeypatch.setattr(daemon.ollama, "installed_models", lambda: {"qwen3:8b"})
    monkeypatch.setitem(REGISTRY, "list_events", types.SimpleNamespace(
        run=lambda a, c: types.SimpleNamespace(text="Standup — 10:00")))
    monkeypatch.setitem(REGISTRY, "list_reminders", types.SimpleNamespace(
        run=lambda a, c: types.SimpleNamespace(
            text=f"Submit DBMS assignment (due {datetime.date.today():%a %b %d %Y} 18:00:00 GMT+0530)\nOld thing")))
    monkeypatch.setattr(day, "commits_today", lambda cfg: ["RepoRadar: Write README"])
    j = daemon.Jarvis(cfg)
    j.apps.add("Code", 7200)
    return j


def test_day_summary_uses_main_brain_on_marked_data(jarvis, monkeypatch):
    seen = {}

    async def chat_message(model, messages, tools=None):
        seen["data"] = messages[1]["content"]
        return {"content": "<untrusted>Main focus: RepoRadar.</untrusted>\n- Coded 2h in VS Code"}

    monkeypatch.setattr(daemon.ollama, "chat_message", chat_message)
    out = asyncio.run(jarvis.respond("summarise my day"))
    assert out["source"] == "day" and out["reply"].startswith("Main focus: RepoRadar.")
    assert seen["data"].startswith('<untrusted source="laptop activity">') and "Code 2h 0m" in seen["data"]


def test_day_summary_falls_back_to_plain_list(jarvis, monkeypatch):
    monkeypatch.setattr(daemon.ollama, "installed_models", lambda: set())
    out = asyncio.run(jarvis.respond("summarise my day"))
    assert "Time in apps: Code 2h 0m" in out["reply"] and "RepoRadar: Write README" in out["reply"]


def test_brief_lists_calendar_and_due_today_only(jarvis):
    text = jarvis.brief()
    assert "Standup — 10:00" in text and "- Submit DBMS assignment" in text and "Old thing" not in text


def test_brief_sent_once_a_day(jarvis):
    sent = []
    jarvis.notifier.notify = lambda text, urgency="normal", **k: sent.append(urgency)
    jarvis.cfg["day"]["daily_brief"] = "00:00"
    asyncio.run(daemon._maybe_brief(jarvis))
    asyncio.run(daemon._maybe_brief(jarvis))
    assert sent == ["brief"]
