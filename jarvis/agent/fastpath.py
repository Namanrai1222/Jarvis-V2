"""Instant commands that skip the LLM. They return tool calls, which still go through the policy engine."""
import datetime
import re
from dataclasses import dataclass, field

_OPEN_PREFIX = re.compile(r"^(?:please\s+|jarvis\s+)?(?:open|launch|start)\s+(.+)$")
_OPEN_SUFFIX = re.compile(r"^(.+?)\s+(?:kholo|khol do|open karo|chalu karo)$")  # Hinglish
_SEARCH = re.compile(r"^(?:search(?:\s+for)?|google)\s+(.+)$|^(.+?)\s+(?:search karo|google karo)$")
_SITE = re.compile(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$")


@dataclass
class Fast:
    calls: list[tuple[str, dict]] = field(default_factory=list)
    reply: str = ""


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


def match(text: str, cfg: dict) -> Fast | None:
    t = _clean(text)

    m = _OPEN_PREFIX.match(t)
    if m and _SITE.match(m.group(1)):
        return Fast([("open_url", {"url": m.group(1)})])

    m = _SEARCH.match(t)
    if m:
        return Fast([("web_search", {"query": m.group(1) or m.group(2)})])

    parsed = parse_open(text, cfg["apps"])
    if parsed is not None and parsed[0] and not parsed[1]:  # anything unrecognised goes to the LLM
        return Fast([("open_app", {"app": b}) for b in dict.fromkeys(parsed[0])])

    now = datetime.datetime.now()
    if re.search(r"\btime\b", t) and re.search(r"what|kya|tell|kitne", t):
        return Fast(reply=f"It's {now.strftime('%-I:%M %p')}.")
    if re.search(r"\b(date|day)\b", t) and re.search(r"what|kya|today|aaj", t):
        return Fast(reply=f"Today is {now.strftime('%A, %-d %B')}.")
    if re.search(r"\bbattery\b|\bdisk space\b", t):
        return Fast([("system_status", {})])
    return None
