"""When and how Jarvis reaches Naman: Mac notification first, Telegram if away or unanswered.

Ladder (DESIGN §9.2): every event shows a Mac notification. If Naman is away, it goes to Telegram now; a
decision left unanswered for 10 minutes goes to Telegram too. Quiet hours hold everything but critical
alerts until a morning digest. At most 6 Telegram messages an hour; identical alerts within 30 min merge.
"""
import datetime
import time
from typing import Callable

from ..memory.capture import redact

ESCALATE_AFTER = 600
DEDUPE_WINDOW = 1800


class Notifier:
    def __init__(self, cfg: dict, *, mac: Callable[[str], None], present: Callable[[], bool],
                 remote: Callable[[str, list | None], None] | None = None, clock=time.time) -> None:
        n = cfg["notify"]
        self.quiet = n["quiet_hours"]
        self.max_per_hour = n["max_per_hour"]
        self.mac, self.present, self.remote, self.clock = mac, present, remote, clock
        self.sent: list[tuple[float, str]] = []     # (time, text) of Telegram messages
        self.held: list[str] = []                   # waiting for quiet hours or the rate limit to end
        self.later: list[tuple[float, str, str, list | None]] = []  # (due, key, text, buttons)

    def notify(self, text: str, urgency: str = "normal", buttons: list | None = None, key: str = "") -> None:
        """urgency: normal | high | decision | critical | brief (always to the phone, outside quiet hours).
        Secrets are redacted before anything leaves."""
        text, _ = redact(text)
        self.mac(text)
        if self.remote is None:
            return
        if urgency in ("critical", "brief") or not self.present():
            self._send(text, buttons, force=urgency == "critical")
        elif urgency == "decision":
            self.later.append((self.clock() + ESCALATE_AFTER, key, text, buttons))

    def tick(self, still_open: Callable[[str], bool]) -> None:
        """Run every ~30 s: escalate unanswered decisions, flush held messages when allowed."""
        now = self.clock()
        due = [x for x in self.later if x[0] <= now]
        self.later = [x for x in self.later if x[0] > now]
        for _, key, text, buttons in due:
            if still_open(key):
                self._send(text, buttons)
        if self.held and self.remote and not self._quiet(now) and self._room(now):
            items, self.held = self.held, []
            body = items[0] if len(items) == 1 else "While you were away:\n" + "\n".join(f"• {i}" for i in items)
            self._deliver(body, None, now)

    def _send(self, text: str, buttons: list | None, force: bool = False) -> None:
        now = self.clock()
        if any(t == text and now - ts < DEDUPE_WINDOW for ts, t in self.sent):
            return
        if not force and (self._quiet(now) or not self._room(now)):
            if text not in self.held:
                self.held.append(text)
            return
        self._deliver(text, buttons, now)

    def _deliver(self, text: str, buttons: list | None, now: float) -> None:
        try:
            self.remote(text, buttons)
        except Exception as e:  # no internet: keep it for the next tick
            self.held.append(text)
            self.mac(f"Couldn't reach Telegram: {e}")
            return
        self.sent = [(t, s) for t, s in self.sent if now - t < 3600 * 2] + [(now, text)]

    def _room(self, now: float) -> bool:
        return sum(1 for t, _ in self.sent if now - t < 3600) < self.max_per_hour

    def _quiet(self, now: float) -> bool:
        start, end = (datetime.time.fromisoformat(x) for x in self.quiet)
        t = datetime.datetime.fromtimestamp(now).time()
        return (start <= t or t < end) if start > end else (start <= t < end)
