"""Instant commands that skip the LLM. Opening apps is limited to the allowlist."""
import datetime
import re
import subprocess

_OPEN_PREFIX = re.compile(r"^(?:please\s+|jarvis\s+)?(?:open|launch|start)\s+(.+)$")
_OPEN_SUFFIX = re.compile(r"^(.+?)\s+(?:kholo|khol do|open karo|chalu karo)$")  # Hinglish


def _clean(text: str) -> str:
    return re.sub(r"[.!?,]+$", "", text.lower().strip())


def parse_open(text: str, apps: dict[str, str]) -> tuple[list[str], list[str]] | None:
    """Return (bundle IDs to open, names not allowed), or None if this isn't an open command."""
    t = _clean(text)
    m = _OPEN_PREFIX.match(t) or _OPEN_SUFFIX.match(t)
    if not m:
        return None
    names = [n.strip() for n in re.split(r",|\band\b|\baur\b", m.group(1)) if n.strip()]
    names = [re.sub(r"^(?:the|my)\s+", "", n).removesuffix(" app") for n in names]
    bundles = [apps[n] for n in names if n in apps]
    unknown = [n for n in names if n not in apps]
    return bundles, unknown


def _battery() -> str:
    out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)%;\s*([\w ]+);", out)
    return f"Battery is at {m.group(1)} percent, {m.group(2)}." if m else "I couldn't read the battery."


def try_fastpath(text: str, apps: dict[str, str]) -> str | None:
    parsed = parse_open(text, apps)
    if parsed is not None:
        bundles, unknown = parsed
        for b in dict.fromkeys(bundles):
            subprocess.run(["open", "-b", b], check=False)
        parts = []
        if bundles:
            parts.append("Opening.")
        if unknown:
            parts.append(f"{', '.join(unknown)} isn't in my allowed apps.")
        return " ".join(parts)

    t = _clean(text)
    now = datetime.datetime.now()
    if re.search(r"\btime\b", t) and re.search(r"what|kya|tell", t):
        return f"It's {now.strftime('%-I:%M %p')}."
    if re.search(r"\b(date|day)\b", t) and re.search(r"what|kya|today|aaj", t):
        return f"Today is {now.strftime('%A, %-d %B')}."
    if "battery" in t:
        return _battery()
    return None
