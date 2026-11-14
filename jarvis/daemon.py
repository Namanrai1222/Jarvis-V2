"""jarvisd: wires the agent together and serves the API on 127.0.0.1 (browser) and a Unix socket (CLI, menu bar)."""
import asyncio
import datetime
import itertools
import re
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from . import config, day, secrets
from .activity import Activity
from .agent import context, fastpath, guard, planner
from .agent.executor import Executor
from .api import ActionId, Forget, MemoryId, Message, Pin, create_app
from .llm import ollama
from .documents.extract import DOC_EXT
from .documents.index import DocIndex
from .memory import capture
from .memory.store import MemoryStore
from .notify import presence
from .notify.notifier import Notifier
from .notify.telegram import Telegram
from .policy.pending import PendingStore
from .tools import STOP
from .tools import docs as doc_tools

REQUEST_TIMEOUT = 120  # reading a PDF or a screenshot can take a while on battery
INBOX = Path.home() / "Jarvis/Inbox"
_CONFIRM = re.compile(r"^(?:confirm|yes|haan|ha|ok|do it|kar do)(?:\s+(a\d+))?$")
_CANCEL = re.compile(r"^(?:cancel|no|nahi|mat karo)(?:\s+(a\d+))?$")
_REMOTE_ANSWER = re.compile(r"^(a\d+)\s+(yes|y|ok|confirm|haan|ha|no|n|cancel|nahi|skip|stop)$")
_DAY_SUMMARY = re.compile(
    r"(summar\w*|recap|wrap[- ]?up|report)\b.{0,40}\b(day|today|aaj|din|work)\b|"
    r"\b(day|aaj|din)\b.{0,15}\b(summar\w*|recap|report)|^what (did|have) i (do|done|work(ed)? on) today|"
    r"\bend of (the )?day\b", re.I)
_BRIEF = re.compile(r"\b(daily|morning) brief\b|^brief( me)?$|aaj ka plan", re.I)
PHONE_TTL = 3600  # a decision sent to the phone gets an hour to be answered (DESIGN §9.4)


def mac_notification(text: str) -> None:
    """Notification Center banner; text passed as an argument so it can't inject script."""
    import subprocess

    subprocess.run(["osascript", "-l", "JavaScript", "-e",
                    "function run(a){var s=Application.currentApplication();s.includeStandardAdditions=true;"
                    "s.displayNotification(a[0],{withTitle:'Jarvis'})}", text[:300]], capture_output=True)


def _clean(reply: str) -> str:
    """Models sometimes echo our internal tags; never show them."""
    return re.sub(r"</?(?:untrusted|memory)\b[^>]*>", "", reply).strip() or "Done."


class Jarvis:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.pending = PendingStore(on_add=self._on_pending)
        token, chat = secrets.get("telegram_token"), secrets.get("telegram_chat_id")
        self.telegram = Telegram(token) if token and chat else None
        self.chat_id = int(chat) if chat else None
        away = cfg["notify"]["away_after_min"] * 60
        self.notifier = Notifier(cfg, mac=mac_notification, present=lambda: presence.present(away),
                                 remote=(lambda text, buttons: self.telegram.send(self.chat_id, text, buttons))
                                 if self.telegram else None)
        self.bad_tokens: list[float] = []
        self.apps = day.AppTracker(str(config.APP_DIR / "jarvis.db")) if cfg["day"]["track_apps"] else None
        self.activity = Activity(str(config.APP_DIR / "jarvis.db"))
        self.memory = MemoryStore(str(config.APP_DIR / "jarvis.db"))
        embed_model = cfg["models"]["embed"]
        has_embed = embed_model in (ollama.installed_models() or set())
        self.docs = DocIndex(str(config.APP_DIR / "jarvis.db"),
                             embed=(lambda texts: ollama.embed(embed_model, texts)) if has_embed else None)
        doc_tools.INDEX = self.docs
        self.executor = Executor(cfg, self.activity, self.pending)
        self.running: set[asyncio.Task] = set()

    # --- reaching Naman when he's away (M5) ---
    def _on_pending(self, p) -> None:
        if self.telegram and not presence.present(self.cfg["notify"]["away_after_min"] * 60):
            self.pending.extend(p.id, PHONE_TTL)
        self.notifier.notify(f"{p.id} — I need your OK: {p.summary}", "decision", key=p.id,
                             buttons=[("✅ Confirm", f"{p.id} yes"), ("✖ Cancel", f"{p.id} no")])

    def _still_open(self, pid: str) -> bool:
        """Notifier asks before escalating; escalated decisions get the phone's longer expiry."""
        return self.pending.extend(pid, PHONE_TTL)

    def _injection(self, source: str, rid: str) -> None:
        """U12: log it and alert Naman, naming the file but never quoting it."""
        self.activity.log(request_id=rid, tool="guard", args={"source": source}, tier=None, tainted=True,
                          decision="blocked injection", result=f"suspicious passage hidden from the model ({source})")
        self.notifier.notify(f"Security: I blocked text in {source} that tried to give me instructions. "
                             "Nothing was done because of it.", "critical")

    # --- your day (M6) ---
    def _events(self, start: datetime.date) -> str:
        from .tools import REGISTRY

        end = start + datetime.timedelta(days=1)
        try:
            return REGISTRY["list_events"].run({"start": f"{start}T00:00", "end": f"{end}T00:00"}, self.cfg).text
        except Exception:
            return ""

    async def day_summary(self) -> str:
        """End-of-day summary of work on the laptop, written by the main brain from gathered facts."""
        events = await asyncio.to_thread(self._events, datetime.date.today())
        data = await asyncio.to_thread(day.gather, self.cfg, self.apps, self.memory, self.activity, events)
        facts = day.as_text(data)
        model = self.cfg["models"]["main"]
        if not data["apps"] and not data["files"] and not data["commits"] and not data["said"]:
            return facts
        try:
            if model not in (await asyncio.to_thread(ollama.installed_models) or set()):
                raise httpx.HTTPError("main model missing")
            msg = await ollama.chat_message(model, [
                {"role": "system", "content": day.SUMMARY_PROMPT},
                {"role": "user", "content": f"<untrusted source=\"laptop activity\">\n{facts}\n</untrusted>"}])
            return guard.check_output(_clean(msg["content"]), set())
        except httpx.HTTPError:
            return facts  # model unavailable: the plain list is still useful

    def brief(self) -> str:
        """Morning brief: today's calendar, reminders due today, approvals waiting (no model needed)."""
        from .tools import REGISTRY

        today = datetime.date.today()
        events = self._events(today)
        try:
            reminders = REGISTRY["list_reminders"].run({}, self.cfg).text
        except Exception:
            reminders = ""
        due = [line for line in reminders.splitlines() if today.strftime("%a %b %d %Y") in line]
        waiting = self.pending.list()
        parts = [f"Good morning! {today.strftime('%A, %-d %B')}."]
        parts.append("Calendar:\n" + events if events and events != "No events." else "No meetings today.")
        if due:
            parts.append("Due today:\n" + "\n".join(f"- {d.split(' (due')[0]}" for d in due))
        if waiting:
            parts.append("Waiting for your OK: " + "; ".join(f"{p.id}: {p.summary}" for p in waiting))
        return "\n\n".join(parts)

    def bad_token(self) -> None:
        now = time.time()
        self.bad_tokens = [t for t in self.bad_tokens if now - t < 600] + [now]
        if len(self.bad_tokens) == 10:
            self.notifier.notify("Security: 10 requests with a wrong Jarvis token in 10 minutes. "
                                 "Something on the Mac may be probing Jarvis.", "critical")

    async def remote(self, text: str) -> str:
        """A message from Naman's paired Telegram chat: answers and controls only, never new actions."""
        t = re.sub(r"[.!?]+$", "", text.strip().lower())
        if t.startswith("/start"):
            return "Jarvis here. I'll message you when I need a decision or something urgent happens."
        if m := _REMOTE_ANSWER.match(t):
            yes = m.group(2) in {"yes", "y", "ok", "confirm", "haan", "ha"}
            fn = self.executor.confirm if yes else self.executor.cancel
            return await asyncio.to_thread(fn, m.group(1))
        if t in {"stop", "stop everything"}:
            reply = await self.stop()
            mac_notification("Kill switch used from Telegram.")
            return reply
        if t in {"pause", "resume"}:
            return await asyncio.to_thread(self._control, t)
        if t == "status":
            items = self.pending.list()
            waiting = "; ".join(f"{p.id}: {p.summary}" for p in items) or "nothing waiting"
            return f"{'Paused' if self.executor.paused else 'Running'}. Waiting for you: {waiting}."
        note, _ = capture.redact(text)
        await asyncio.to_thread(self.memory.add, "episode", f"(sent from Telegram) {note}", reply="saved as a note")
        return ("Saved as a note for when you're back. From Telegram I only take answers (A3 yes / A3 no), "
                "status, stop, pause and resume.")

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
        if t in {"what do you remember about me", "what do you know about me", "tumhe mere baare mein kya yaad hai"}:
            return self._recall_facts()
        return None

    def _remember(self, text: str) -> str | None:
        parsed = capture.parse_remember(text)
        if parsed is None:
            return None
        fact, forever = parsed
        if capture.redact(fact)[1]:
            return "I won't store passwords, OTPs, card or ID numbers. Keep those in the Passwords app or Keychain."
        self.memory.add("fact", fact, pinned=forever)
        return f"Pinned: {fact}. I'll keep it until you remove it." if forever else \
            f"Got it: {fact}. I'll remember this for 14 days; say 'remember forever' to keep it."

    def _recall_facts(self) -> str:
        facts = self.memory.facts()
        if not facts:
            return "I don't have any saved facts yet. Say 'remember forever that …' to pin one."
        lines = [f"{f['text']} ({'pinned' if f['pinned'] else 'for 14 days'})" for f in facts[:15]]
        return "Here's what I remember: " + "; ".join(lines) + "."

    def _record(self, text: str, out: dict) -> None:
        """Historian: store the exchange (secrets redacted) and any activity Naman mentioned."""
        if out.get("source") not in {"fastpath", "quick", "llm"}:
            return
        user, _ = capture.redact(text)
        reply, _ = capture.redact(out["reply"])
        self.memory.add("episode", user, reply=reply, tainted=out.get("tainted", False))
        if act := capture.parse_activity(user):
            self.memory.add("activity", f"{capture.VERBS[act[0]]} {act[1]}")

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
        if _DAY_SUMMARY.search(t):
            return {"reply": await self.day_summary(), "source": "day", "pending": []}
        if _BRIEF.search(t):
            return {"reply": await asyncio.to_thread(self.brief), "source": "day", "pending": []}
        if (reply := await asyncio.to_thread(self._remember, text)) is not None:
            return {"reply": reply, "source": "memory", "pending": []}

        task = asyncio.create_task(self._handle(text))
        self.running.add(task)
        try:
            out = await asyncio.wait_for(task, REQUEST_TIMEOUT)
            await asyncio.to_thread(self._record, text, out)
            out.pop("tainted", None)
            return out
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

        models = await asyncio.to_thread(ollama.installed_models) or set()
        router, main = self.cfg["models"]["router"], self.cfg["models"]["main"]
        model = main if main in models else router if router in models else None
        if model is None:
            return {"reply": "My language model is offline. Simple commands still work.", "source": "offline",
                    "pending": []}

        async def chat(messages, tools):
            return await ollama.chat_message(model, messages, tools)

        memory, mem_tainted = await asyncio.to_thread(context.build, self.memory, text)
        if context.EMPTY in memory and context.QUESTION.search(text):  # nothing to recall: don't let a model guess
            return {"reply": "I don't have anything saved for that time.", "source": "memory", "pending": []}
        try:
            # quick brain: plain chat is answered by the small model; only tasks wake the main one
            if router in models and model != router and not await ollama.needs_tools(router, text):
                return {"reply": _clean(await ollama.chat(router, text, memory)), "source": "quick", "pending": [],
                        "tainted": mem_tainted}
            classify = (lambda passage: ollama.is_injection(router, passage)) if router in models else None
            out = await planner.plan(text, chat=chat, executor=self.executor, cfg=self.cfg, request_id=rid,
                                     memory=memory, tainted=mem_tainted, classify=classify,
                                     on_injection=lambda source: self._injection(source, rid))
        except httpx.HTTPError:
            return {"reply": "My language model stopped responding.", "source": "offline", "pending": []}
        # Small models often claim a pending action is already done, so when something waits for
        # approval the reply is ours, not the model's.
        reply = planner.describe_pending(out["pending"]).strip() if out["pending"] else _clean(out["reply"])
        return {"reply": reply, "source": "llm", "pending": [p.public() for p in out["pending"]],
                "tainted": out["tainted"]}

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
    app = create_app(token, on_bad_token=j.bad_token)
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

    @app.get("/api/memory")
    async def memory(q: str = ""):
        return await asyncio.to_thread(j.memory.browse, q[:200])

    @app.post("/api/memory/pin")
    async def pin(b: Pin):
        return {"ok": await asyncio.to_thread(j.memory.set_pinned, b.id, b.pinned)}

    @app.post("/api/memory/delete")
    async def delete(b: MemoryId):
        return {"ok": await asyncio.to_thread(j.memory.delete, b.id)}

    @app.post("/api/memory/forget")
    async def forget(b: Forget):
        n = await asyncio.to_thread(j.memory.forget_everything, b.include_pinned, ("activity",))
        return {"reply": f"Forgot {n} memories and the activity log."}

    @app.get("/api/documents")
    async def documents():
        return await asyncio.to_thread(j.docs.stats)

    @app.post("/api/upload")
    async def upload(request: Request, name: str):
        """Drag-and-drop: save the file to ~/Jarvis/Inbox so tools can read it (kept 14 days)."""
        safe = re.sub(r"[^\w .()-]", "_", Path(name).name).strip(" .")[:120] or "file"
        if Path(safe).suffix.lower() not in DOC_EXT:
            raise HTTPException(415, "unsupported file type")
        body = await request.body()
        if len(body) > 30_000_000:
            raise HTTPException(413, "file too large (30 MB max)")
        INBOX.mkdir(parents=True, exist_ok=True)
        target = INBOX / safe
        if target.exists():
            target = INBOX / f"{target.stem}-{int(time.time())}{target.suffix}"
        target.write_bytes(body)
        return {"path": "~/" + str(target.relative_to(Path.home()))}

    app.state.jarvis = j
    return app


async def index_loop(j: Jarvis) -> None:
    """Keep the document index in step with the watched folders (startup, then hourly)."""
    # shortcut: hourly rescan by modified time instead of FSEvents; add watchdog if hourly feels stale
    while True:
        try:
            stats = await asyncio.to_thread(j.docs.scan, j.cfg["documents"]["index_folders"], j.cfg, stop=STOP)
            print(f"document index: {stats}", flush=True)
        except Exception as e:
            print(f"document index failed: {e}", flush=True)
            j.notifier.notify(f"Document indexing failed: {e}", "normal")
        await asyncio.sleep(3600)


def _clean_jarvis_files(now: float) -> None:
    """Jarvis's own screenshots and dropped files follow the 14-day rule."""
    for folder in (doc_tools.SCREENSHOTS, INBOX):
        for f in folder.glob("*") if folder.exists() else []:
            if f.is_file() and f.stat().st_mtime < now - 14 * 86400:
                f.unlink()


async def purge_loop(j: Jarvis) -> None:
    """Nightly 14-day purge. Checks hourly, so a laptop asleep at 03:30 catches up on wake."""
    while True:
        if await asyncio.to_thread(j.memory.purge_due):
            try:
                await asyncio.to_thread(j.memory.purge, None, (("activity", "ts"),))
                await asyncio.to_thread(_clean_jarvis_files, time.time())
                if j.apps:
                    await asyncio.to_thread(j.apps.prune)
            except Exception as e:  # never crash the daemon over a purge; it retries next hour
                print(f"memory purge failed: {e}", flush=True)
                j.notifier.notify(f"Memory purge failed: {e}. I'll retry within the hour.", "normal")
        await asyncio.sleep(3600)


async def notify_loop(j: Jarvis) -> None:
    """Escalations and held messages every 30 s; battery and disk checks every 5 min (U11)."""
    warned: set[str] = set()
    for n in itertools.count():
        await asyncio.to_thread(j.notifier.tick, j._still_open)
        if j.apps and n % 2 == 0:
            await asyncio.to_thread(j.apps.sample)  # once a minute: which app is in front (names only)
        await _maybe_brief(j)
        if n % 10 == 0:
            for key, problem in (await asyncio.to_thread(_power_and_disk)).items():
                if problem and key not in warned:
                    warned.add(key)
                    j.notifier.notify(problem, "high")
                elif not problem:
                    warned.discard(key)
        await asyncio.sleep(30)


async def _maybe_brief(j: Jarvis) -> None:
    """Send the morning brief once a day, at or after the configured time (catches up after sleep)."""
    at = j.cfg["day"]["daily_brief"]
    if not at:
        return
    now = datetime.datetime.now()
    if now.time() < datetime.time.fromisoformat(at) or j.memory.meta("last_brief") == now.date().isoformat():
        return
    j.memory.meta("last_brief", now.date().isoformat())
    j.notifier.notify(await asyncio.to_thread(j.brief), "brief")


def _power_and_disk() -> dict[str, str]:
    import shutil
    import subprocess

    out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)%;\s*discharging", out)
    free_gb = shutil.disk_usage("/System/Volumes/Data").free / 1e9
    return {
        "battery": f"Battery at {m.group(1)}% and not charging. I've paused heavy work like indexing."
        if m and int(m.group(1)) < 10 else "",
        "disk": f"Only {free_gb:.1f} GB of disk space left." if free_gb < 5 else "",
    }


async def telegram_loop(j: Jarvis) -> None:
    """Long-poll Telegram. Only the paired chat is listened to; messages older than 5 min are ignored."""
    if j.telegram is None:
        return
    offset, strangers = 0, []
    while True:
        try:
            updates = await asyncio.to_thread(j.telegram.updates, offset)
        except Exception as e:
            print(f"telegram poll failed: {e}", flush=True)
            await asyncio.sleep(30)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            button = u.get("callback_query")
            msg = button["message"] if button else u.get("message")
            sender = (button or u.get("message") or {}).get("from", {}).get("id")
            if not msg or sender != j.chat_id or msg["chat"]["id"] != j.chat_id:
                now = time.time()
                strangers = [t for t in strangers if now - t < 3600] + [now]
                if len(strangers) == 3:
                    j.notifier.notify("Security: 3 messages from unknown Telegram accounts in an hour. "
                                      "They were ignored.", "critical")
                continue
            if not button and time.time() - msg["date"] > 300:
                continue  # old or replayed
            text = button["data"] if button else (msg.get("text") or "")
            reply = await j.remote(text) if text else "I can only read text messages."
            try:
                if button:
                    await asyncio.to_thread(j.telegram.answer_button, button["id"], reply)
                await asyncio.to_thread(j.telegram.send, j.chat_id, reply)
            except Exception as e:
                print(f"telegram reply failed: {e}", flush=True)


async def _serve() -> None:
    app = build_app()
    config.SOCK_PATH.unlink(missing_ok=True)
    # uvicorn makes the socket 0666; the 0700 app dir and the token still keep other users out
    tcp = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config.PORT, log_level="warning"))
    uds = uvicorn.Server(uvicorn.Config(app, uds=str(config.SOCK_PATH), log_level="warning"))
    j = app.state.jarvis
    await asyncio.gather(tcp.serve(), uds.serve(), purge_loop(j), index_loop(j), notify_loop(j), telegram_loop(j))


def serve() -> None:
    asyncio.run(_serve())
