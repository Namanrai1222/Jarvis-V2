"""Tool registry. Each tool declares its tier, argument checks and how to describe itself for confirmation."""
import threading
from dataclasses import dataclass, field
from typing import Callable

STOP = threading.Event()  # kill switch: long-running tools check this between items


@dataclass
class ToolResult:
    text: str
    undo: list[dict] | None = None  # ops that reverse this action


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    required: list[str]
    tier: int | Callable[[dict, dict], int]
    run: Callable[[dict, dict], ToolResult]
    check: Callable[[dict, dict], dict] | None = None
    summary: Callable[[dict], str] = field(default=lambda a: str(a))
    untrusted_output: bool = False  # output comes from files/apps -> taints the request

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


REGISTRY: dict[str, Tool] = {}


def register(**kw) -> Tool:
    t = Tool(**kw)
    REGISTRY[t.name] = t
    return t


from . import apps, files, pim  # noqa: E402,F401  (registers tools)
