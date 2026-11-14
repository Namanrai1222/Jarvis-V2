"""Talks to the local Ollama server only."""
import json
import re

import httpx

from ..config import OLLAMA_URL

CHAT_SYSTEM = (
    "You are Jarvis, Naman's private assistant running locally on a MacBook. "
    "Reply in one to three short sentences that sound natural when spoken aloud. "
    "You understand English and Hinglish; if Naman writes Hinglish in English letters, reply the same way, "
    "never in Devanagari. In this mode you cannot take actions, so never claim you did something."
)


async def chat_message(model: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """One chat turn; returns the assistant message (may contain tool_calls)."""
    # cap length: small models sometimes loop forever without it
    body = {"model": model, "messages": messages, "stream": False, "think": False,
            "options": {"num_predict": 1024 if tools else 300}}
    if tools:
        body["tools"] = tools
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=120) as c:
        r = await c.post("/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]


ROUTER_PROMPT = """Classify the user's message.
"task" = needs to do something on the Mac or read live personal data: apps, websites, files, folders, reminders, \
calendar, notes, email drafts, shortcuts, battery, disk.
"chat" = anything answerable from general knowledge or conversation: greetings, thanks, jokes, facts, explanations, advice.
Examples:
"open spotify" -> task
"what's on my calendar friday" -> task
"draft a reply to the client" -> task
"what is photosynthesis" -> chat
"who won the 2011 world cup" -> chat
"good morning" -> chat
"kal 5 baje remind karna" -> task
"ek motivational quote batao" -> chat"""

# Missing a task is worse than a slow chat reply, so these words always go to the main brain.
TASK_WORDS = re.compile(
    r"\b(open|launch|quit|close|start|run|play|search|google|find|move|copy|rename|trash|delete|folder|files?|"
    r"downloads?|desktop|documents|pdfs?|remind\w*|reminders?|calendar|event|schedule|block|meeting|notes?|"
    r"draft|email|mail|shortcut|battery|disk|screenshot|undo|kholo|dikhao|karo|chalao)\b", re.I)


async def needs_tools(model: str, text: str) -> bool:
    """Quick brain: decides if the main brain (with tools) is needed. Errs toward yes."""
    if TASK_WORDS.search(text):
        return True
    body = {"model": model, "stream": False, "think": False, "options": {"temperature": 0, "num_predict": 20},
            "messages": [{"role": "system", "content": ROUTER_PROMPT}, {"role": "user", "content": text}],
            "format": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["chat", "task"]}},
                       "required": ["kind"]}}
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=30) as c:
        r = await c.post("/api/chat", json=body)
        r.raise_for_status()
    try:
        return json.loads(r.json()["message"]["content"])["kind"] != "chat"
    except (KeyError, ValueError, TypeError):
        return True  # unreadable answer: let the main brain handle it


async def chat(model: str, text: str) -> str:
    msg = await chat_message(model, [{"role": "system", "content": CHAT_SYSTEM}, {"role": "user", "content": text}])
    return msg["content"].strip()


async def unload(model: str) -> None:
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=10) as c:
            await c.post("/api/generate", json={"model": model, "keep_alive": 0})
    except httpx.HTTPError:
        pass


def installed_models() -> set[str] | None:
    """Model names Ollama has, or None if Ollama isn't reachable."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    return {m["name"] for m in r.json().get("models", [])}
