"""Pending actions waiting for Naman's OK. Each is frozen to its exact arguments and expires after 15 min."""
import itertools
import json
import time
from dataclasses import dataclass, field

TTL = 15 * 60


@dataclass
class Pending:
    id: str
    tool: str
    args_json: str  # frozen: confirming runs exactly these arguments
    summary: str
    tainted: bool
    request_id: str
    expires: float = field(default_factory=lambda: time.time() + TTL)

    @property
    def args(self) -> dict:
        return json.loads(self.args_json)

    def public(self) -> dict:
        return {"id": self.id, "summary": self.summary, "tainted": self.tainted}


class PendingStore:
    def __init__(self, clock=time.time, on_add=None) -> None:
        self._items: dict[str, Pending] = {}
        self._ids = itertools.count(1)
        self._clock = clock
        self.on_add = on_add  # e.g. tell the notifier a decision is waiting

    def add(self, tool: str, args: dict, summary: str, tainted: bool, request_id: str) -> Pending:
        p = Pending(f"A{next(self._ids)}", tool, json.dumps(args, sort_keys=True), summary, tainted, request_id,
                    expires=self._clock() + TTL)
        self._items[p.id] = p
        if self.on_add:
            self.on_add(p)
        return p

    def extend(self, pid: str, seconds: float) -> bool:
        """Give a decision sent to Naman's phone time to be answered (DESIGN §9.4: 60 min)."""
        self._expire()
        p = self._items.get(pid.upper())
        if p is None:
            return False
        p.expires = max(p.expires, self._clock() + seconds)
        return True

    def _expire(self) -> None:
        now = self._clock()
        for k in [k for k, p in self._items.items() if p.expires <= now]:
            del self._items[k]

    def list(self) -> list[Pending]:
        self._expire()
        return list(self._items.values())

    def pop(self, pid: str) -> Pending | None:
        self._expire()
        return self._items.pop(pid.upper(), None)

    def clear(self) -> None:
        self._items.clear()
