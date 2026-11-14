"""Routing in jarvisd: control words, fast path, quick brain vs main brain (Ollama faked)."""
import asyncio

import pytest

from jarvis import config, daemon


@pytest.fixture
def jarvis(cfg, tmp_path, monkeypatch):
    (tmp_path / "appdata").mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "appdata")  # Jarvis's own data is always off-limits
    models = {cfg["models"]["router"], cfg["models"]["main"]}
    monkeypatch.setattr(daemon.ollama, "installed_models", lambda: models)
    return daemon.Jarvis(cfg)


def ask(j, text):
    return asyncio.run(j.respond(text))


def test_chat_goes_to_quick_brain(jarvis, monkeypatch):
    async def needs_tools(model, text):
        return False

    async def chat(model, text, memory=""):
        return f"{model} says hi"

    monkeypatch.setattr(daemon.ollama, "needs_tools", needs_tools)
    monkeypatch.setattr(daemon.ollama, "chat", chat)
    out = ask(jarvis, "tell me a joke")
    assert out["source"] == "quick" and out["reply"] == "qwen3:1.7b says hi"


def test_tasks_go_to_main_brain(jarvis, monkeypatch):
    used = []

    async def needs_tools(model, text):
        return True

    async def chat_message(model, messages, tools=None):
        used.append(model)
        return {"content": "done"}

    monkeypatch.setattr(daemon.ollama, "needs_tools", needs_tools)
    monkeypatch.setattr(daemon.ollama, "chat_message", chat_message)
    out = ask(jarvis, "what's on my calendar tomorrow")
    assert out["source"] == "llm" and used == ["qwen3:8b"]


def test_pending_reply_is_written_by_jarvis(jarvis, monkeypatch, tmp_path):
    turns = iter([{"content": "", "tool_calls": [
        {"function": {"name": "create_folder", "arguments": {"path": str(tmp_path / "x")}}}]},
        {"content": "Folder created!"}])

    async def needs_tools(model, text):
        return True

    async def chat_message(model, messages, tools=None):
        return next(turns)

    monkeypatch.setattr(daemon.ollama, "needs_tools", needs_tools)
    monkeypatch.setattr(daemon.ollama, "chat_message", chat_message)
    out = ask(jarvis, "make folder x")
    assert out["reply"].startswith("Nothing changed yet") and not (tmp_path / "x").exists()


@pytest.mark.parametrize("text", ["draft an email to the client", "kal ka calendar dikhao",
                                  "move my screenshots", "remind me to call mom"])
def test_task_words_never_go_to_quick_brain(text):
    async def boom(*a, **k):
        raise AssertionError("router model should not be asked")
    daemon.ollama.httpx.AsyncClient, orig = boom, daemon.ollama.httpx.AsyncClient
    try:
        assert asyncio.run(daemon.ollama.needs_tools("m", text)) is True
    finally:
        daemon.ollama.httpx.AsyncClient = orig


def test_control_words_skip_models(jarvis):
    assert ask(jarvis, "undo")["reply"] == "Nothing to undo."
    assert ask(jarvis, "confirm")["reply"] == "There's nothing waiting for confirmation."
