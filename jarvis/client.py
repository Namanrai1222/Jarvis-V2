"""Client for jarvisd over the Unix socket (used by the CLI and the menu bar)."""
import httpx

from .config import PORT, SOCK_PATH, TOKEN_PATH


def client(timeout: float = 130) -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=str(SOCK_PATH)),
        base_url=f"http://localhost:{PORT}",
        headers={"X-Jarvis-Token": TOKEN_PATH.read_text().strip()},
        timeout=timeout,
    )


def ask(text: str) -> str:
    with client() as c:
        r = c.post("/api/message", json={"text": text[:8000]})
        r.raise_for_status()
        return r.json()["reply"]


def health() -> dict | None:
    try:
        with client(timeout=2) as c:
            return c.get("/api/health").json()
    except (httpx.HTTPError, OSError):
        return None
