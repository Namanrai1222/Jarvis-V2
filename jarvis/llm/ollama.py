"""Talks to the local Ollama server only."""
import httpx

from ..config import OLLAMA_URL

SYSTEM = (
    "You are Jarvis, Naman's private assistant running locally on his MacBook. "
    "Reply in one to three short sentences that sound natural when spoken aloud. "
    "You understand English and Hinglish. You cannot take actions on the Mac yet, "
    "so never claim you did something; say what Naman can do instead."
)


async def chat(model: str, text: str) -> str:
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=120) as c:
        r = await c.post(
            "/api/chat",
            json={
                "model": model,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
                "stream": False,
                "think": False,
            },
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()


def installed_models() -> set[str] | None:
    """Model names Ollama has, or None if Ollama isn't reachable."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    return {m["name"] for m in r.json().get("models", [])}
