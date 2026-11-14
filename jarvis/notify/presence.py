"""Is Naman at the Mac? Present = input in the last 10 minutes and the screen isn't locked."""
import re
import subprocess

AWAY_AFTER = 600  # seconds


def idle_seconds() -> float:
    out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True).stdout
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else 0.0


def screen_locked() -> bool:
    import Quartz

    d = Quartz.CGSessionCopyCurrentDictionary() or {}
    return bool(d.get("CGSSessionScreenIsLocked"))


def present(away_after: float = AWAY_AFTER) -> bool:
    return idle_seconds() < away_after and not screen_locked()
