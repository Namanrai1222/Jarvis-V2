"""jarvisd: serves the API on 127.0.0.1 (browser UI) and a Unix socket (CLI, menu bar)."""
import asyncio

import httpx
import uvicorn

from . import config
from .agent.fastpath import try_fastpath
from .api import create_app
from .llm import ollama


def build_app():
    config.ensure_app_dir()
    cfg = config.load()
    token = config.new_token()

    async def respond(text: str) -> dict:
        reply = await asyncio.to_thread(try_fastpath, text, cfg["apps"])
        if reply is not None:
            return {"reply": reply, "source": "fastpath"}
        try:
            return {"reply": await ollama.chat(cfg["models"]["router"], text), "source": "llm"}
        except httpx.HTTPError:
            return {"reply": "My language model is offline right now. Simple commands still work.", "source": "offline"}

    async def health() -> dict:
        models = await asyncio.to_thread(ollama.installed_models)
        return {"status": "ready", "llm": models is not None and cfg["models"]["router"] in models}

    return create_app(token, respond, health)


async def _serve() -> None:
    app = build_app()
    config.SOCK_PATH.unlink(missing_ok=True)
    # uvicorn makes the socket 0666; the 0700 app dir and the token still keep other users out
    tcp = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config.PORT, log_level="warning"))
    uds = uvicorn.Server(uvicorn.Config(app, uds=str(config.SOCK_PATH), log_level="warning"))
    await asyncio.gather(tcp.serve(), uds.serve())


def serve() -> None:
    asyncio.run(_serve())
