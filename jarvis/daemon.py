"""jarvisd: wires the agent together and serves the API on 127.0.0.1 (browser) and a Unix socket (CLI, menu bar)."""
import asyncio
import re
import uuid
from pathlib import Path

import httpx
import uvicorn
from fastapi.responses import HTMLResponse

from . import config
from .activity import Activity
from .agent import fastpath, planner
from .agent.executor import Executor
from .api import ActionId, Message, create_app
from .llm import ollama
from .policy.pending import PendingStore
from .tools import STOP

REQUEST_TIMEOUT = 60
_CONFIRM = re.compile(r"^(?:confirm|yes|haan|ha|ok|do it|kar do)(?:\s+(a\d+))?$")
_CANCEL = re.compile(r"^(?:cancel|no|nahi|mat karo)(?:\s+(a\d+))?$")


class Jarvis:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.pending = PendingStore()
        self.activity = Activity(str(config.APP_DIR / "jarvis.db"))
        self.executor = Executor(cfg, self.activity, self.pending)
        self.running: set[asyncio.Task] = set()

    # --- models ---
    def _model(self) -> str | None:
        models = ollama.installed_models() or set()
        for role in ("main", "router"):
            if self.cfg["models"][role] in models:
                return self.cfg["models"][role]
        return None

    # --- control words (trusted: only Naman types or speaks to Jarvis) ---
    def _control(self, t: str) -> str | None:
        if m := _CONFIRM.match(t):
            return self._resolve(m.group(1), self.executor.confirm)
        if m := _CANCEL.match(t):
            return self._resolve(m.group(1), self.executor.cancel)
        if t in {"undo", "undo that", "undo last"}:
            return self.executor.undo_last()
        if t in {"resume", "unpause"}:
            return self.resume()
        if t in {"pause"}:
            self.executor.paused = True
            return "Paused. I'll answer but won't take actions until you say resume."
        return None

    def _resolve(self, pid: str | None, fn) -> str:
        items = self.pending.list()
        if pid:
            return fn(pid)
        if len(items) == 1:
            return fn(items[0].id)
        if not items:
            return "There's nothing waiting for confirmation."
        return "Which one? " + "; ".join(f"{p.id}: {p.summary}" for p in items)

    # --- main entry ---
    async def respond(self, text: str) -> dict:
        t = re.sub(r"[.!?]+$", "", text.strip().lower())
        if t in {"stop", "stop everything"}:
            return {"reply": await self.stop(), "source": "control", "pending": []}
        if (reply := await asyncio.to_thread(self._control, t)) is not None:
            return {"reply": reply, "source": "control", "pending": self._pending_public()}

        task = asyncio.create_task(self._handle(text))
        self.running.add(task)
        try:
            return await asyncio.wait_for(task, REQUEST_TIMEOUT)
        except asyncio.CancelledError:
            return {"reply": "Stopped.", "source": "control", "pending": []}
        except asyncio.TimeoutError:
            return {"reply": "That took too long, so I stopped.", "source": "timeout", "pending": []}
        finally:
            self.running.discard(task)

    async def _handle(self, text: str) -> dict:
        rid = uuid.uuid4().hex[:8]
        fast = fastpath.match(text, self.cfg)
        if fast is not None:
            replies, pending = [fast.reply] if fast.reply else [], []
            for name, args in fast.calls:
                r, p = await asyncio.to_thread(self.executor.call, name, args, tainted=False, request_id=rid)
                replies.append(r)
                if p:
                    pending.append(p)
            reply = " ".join(replies) + planner.describe_pending(pending)
            return {"reply": reply.strip(), "source": "fastpath", "pending": [p.public() for p in pending]}

        model = await asyncio.to_thread(self._model)
        if model is None:
            return {"reply": "My language model is offline. Simple commands still work.", "source": "offline",
                    "pending": []}

        async def chat(messages, tools):
            return await ollama.chat_message(model, messages, tools)

        try:
            out = await planner.plan(text, chat=chat, executor=self.executor, cfg=self.cfg, request_id=rid)
        except httpx.HTTPError:
            return {"reply": "My language model stopped responding.", "source": "offline", "pending": []}
        # Small models often claim a pending action is already done, so when something waits for
        # approval the reply is ours, not the model's.
        reply = planner.describe_pending(out["pending"]).strip() if out["pending"] else out["reply"]
        return {"reply": reply, "source": "llm",
                "pending": [p.public() for p in out["pending"]]}

    def _pending_public(self) -> list[dict]:
        return [p.public() for p in self.pending.list()]

    # --- kill switch ---
    async def stop(self) -> str:
        STOP.set()  # long file loops stop between items
        for task in list(self.running):
            task.cancel()
        self.pending.clear()
        self.executor.paused = True
        for role in ("main", "router"):
            await ollama.unload(self.cfg["models"][role])
        return "Stopped everything. I'm paused; say resume when you want me back."

    def resume(self) -> str:
        STOP.clear()
        self.executor.paused = False
        return "Resumed."

    async def health(self) -> dict:
        models = await asyncio.to_thread(ollama.installed_models)
        return {"status": "paused" if self.executor.paused else "ready",
                "llm": models is not None and self.cfg["models"]["router"] in models,
                "pending": len(self.pending.list())}


def build_app():
    config.ensure_app_dir()
    j = Jarvis(config.load())
    token = config.new_token()
    app = create_app(token)
    page = (Path(__file__).parent / "ui/index.html").read_text().replace("__JARVIS_TOKEN__", token)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return page

    @app.get("/api/health")
    async def health():
        return await j.health()

    @app.post("/api/message")
    async def message(msg: Message):
        return await j.respond(msg.text)

    @app.get("/api/pending")
    async def pending():
        return j._pending_public()

    @app.post("/api/confirm")
    async def confirm(a: ActionId):
        return {"reply": await asyncio.to_thread(j.executor.confirm, a.id)}

    @app.post("/api/cancel")
    async def cancel(a: ActionId):
        return {"reply": j.executor.cancel(a.id)}

    @app.post("/api/undo")
    async def undo():
        return {"reply": await asyncio.to_thread(j.executor.undo_last)}

    @app.post("/api/stop")
    async def stop():
        return {"reply": await j.stop()}

    @app.post("/api/resume")
    async def resume():
        return {"reply": j.resume()}

    @app.get("/api/activity")
    async def activity():
        return j.activity.recent()

    return app


async def _serve() -> None:
    app = build_app()
    config.SOCK_PATH.unlink(missing_ok=True)
    # uvicorn makes the socket 0666; the 0700 app dir and the token still keep other users out
    tcp = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config.PORT, log_level="warning"))
    uds = uvicorn.Server(uvicorn.Config(app, uds=str(config.SOCK_PATH), log_level="warning"))
    await asyncio.gather(tcp.serve(), uds.serve())


def serve() -> None:
    asyncio.run(_serve())
