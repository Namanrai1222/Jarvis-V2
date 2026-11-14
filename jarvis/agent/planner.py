"""LLM planner: the model proposes tool calls, the executor (policy first) decides what actually runs."""
import asyncio
import datetime
import json
from pathlib import Path
from typing import Awaitable, Callable

from ..policy.pending import Pending
from ..tools import REGISTRY
from .executor import Executor, UnknownTool

MAX_STEPS = 8
MAX_BAD_CALLS = 2
PROMPT = (Path(__file__).parent / "prompts/system.md").read_text()

ChatFn = Callable[[list[dict], list[dict]], Awaitable[dict]]  # (messages, tools) -> assistant message


def system_prompt(cfg: dict) -> str:
    return PROMPT.format(
        now=datetime.datetime.now().astimezone().strftime("%A %-d %B %Y, %H:%M %Z"),
        home=str(Path.home()),
        roots=", ".join(cfg["scope"]["allowed_roots"]),
        apps=", ".join(sorted(cfg["apps"])),
        shortcuts=", ".join(sorted(cfg["shortcuts"])) or "none yet",
    )


def _wrap_untrusted(source: str, text: str) -> str:
    text = text.replace("</untrusted>", "&lt;/untrusted&gt;")  # can't close the block early
    return f'<untrusted source="{source}">\n{text}\n</untrusted>'


async def plan(text: str, *, chat: ChatFn, executor: Executor, cfg: dict, request_id: str) -> dict:
    messages = [{"role": "system", "content": system_prompt(cfg)}, {"role": "user", "content": text}]
    schemas = [t.schema() for t in REGISTRY.values()]
    tainted, bad, pending = False, 0, []

    for _ in range(MAX_STEPS):
        msg = await chat(messages, schemas)
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
        if not calls:
            return {"reply": msg.get("content", "").strip() or "Done.", "pending": pending}
        for call in calls:
            fn = call.get("function", {})
            name, args = fn.get("name", ""), fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = None
            try:
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
                result, p = await asyncio.to_thread(
                    executor.call, name, args, tainted=tainted, request_id=request_id)
            except (UnknownTool, ValueError) as e:
                bad += 1
                if bad >= MAX_BAD_CALLS:
                    return {"reply": "I'm not sure how to do that. Can you say it another way?", "pending": pending}
                result, p = f"Invalid tool call: {e}", None
            if p is not None:
                pending.append(p)
            tool = REGISTRY.get(name)
            if tool is not None and tool.untrusted_output:
                tainted = True
                result = _wrap_untrusted(name, result)
            messages.append({"role": "tool", "content": result, "tool_name": name})
    return {"reply": "I stopped after 8 steps. Here's where things stand; ask me to continue if needed.",
            "pending": pending}


def describe_pending(pending: list[Pending]) -> str:
    if not pending:
        return ""
    items = "; ".join(f"{p.id}: {p.summary}" for p in pending)
    return f" Nothing changed yet. Needs your OK, {items}. Say confirm or cancel."
