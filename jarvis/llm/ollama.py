"""Talks to the local Ollama server only."""
import httpx

from ..config import OLLAMA_URL

CHAT_SYSTEM = (
    "You are Jarvis, Naman's private assistant running locally on a MacBook. "
    "Reply in one to three short sentences that sound natural when spoken aloud. "
    "You understand English and Hinglish."
)


async def chat_message(model: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """One chat turn; returns the assistant message (may contain tool_calls)."""
    body = {"model": model, "messages": messages, "stream": False, "think": False}
    if tools:
        body["tools"] = tools
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=120) as c:
        r = await c.post("/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]


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
