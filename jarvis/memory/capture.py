"""What gets written to memory: redaction of secrets, "remember…" phrases, activity phrases, time ranges."""
import datetime
import re

# Never stored: passwords, OTPs, card numbers, Aadhaar, PAN, IFSC, bank account numbers.
SENSITIVE = [
    re.compile(r"\b(?:password|passcode|pwd|pin)\b\s*(?:is|:|=)?\s*\S+", re.I),
    re.compile(r"\b(?:otp|one[- ]time (?:password|code)|verification code)\b\D{0,12}\d{4,8}\b", re.I),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                       # card / long account numbers
    re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),                    # Aadhaar
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),                       # PAN
    re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),                     # IFSC
    re.compile(r"\b(?:account|a/c|acct)\s*(?:no\.?|number)?\s*(?:is|:)?\s*\d{9,18}\b", re.I),
]


def redact(text: str) -> tuple[str, bool]:
    found = False
    for pat in SENSITIVE:
        text, n = pat.subn("[redacted]", text)
        found = found or n > 0
    return text, found


_REMEMBER = re.compile(
    r"^(?:please\s+|jarvis,?\s+)?(?:remember|yaad rakh(?:na|o)?)\s+"
    r"(?P<forever>forever|permanently|hamesha(?: ke liye)?)?\s*(?:that|ki)?\s*(?P<fact>.+?)"
    r"(?:\s+(?P<forever2>forever|permanently|hamesha(?: ke liye)?))?[.!]?$", re.I)


def parse_remember(text: str) -> tuple[str, bool] | None:
    """'remember forever that X' -> (X, True); 'remember that X' -> (X, False)."""
    m = _REMEMBER.match(text.strip())
    if not m or len(m.group("fact")) < 3:
        return None
    return m.group("fact").strip(), bool(m.group("forever") or m.group("forever2"))


# v1's activity phrases, plus Hinglish ("maine X ke liye apply kiya")
_ACTIVITIES = [
    ("application", re.compile(r"\bi(?:'ve| have)?\s+(?:just\s+)?applied (?:for|to)\s+(.+)", re.I)),
    ("submission", re.compile(r"\bi(?:'ve| have)?\s+(?:just\s+)?submitted\s+(.+)", re.I)),
    ("email", re.compile(r"\bi(?:'ve| have)?\s+(?:just\s+)?(?:emailed|mailed|sent an email to)\s+(.+)", re.I)),
    ("meeting", re.compile(r"\bi(?:'ve| have)?\s+(?:just\s+)?(?:attended|had a (?:call|meeting) with)\s+(.+)", re.I)),
    ("done", re.compile(r"\bi(?:'ve| have)?\s+(?:just\s+)?(?:completed|finished)\s+(.+)", re.I)),
    ("application", re.compile(r"\bmaine\s+(.+?)\s+(?:ke liye\s+|me\s+|mein\s+)?apply kiya", re.I)),
    ("submission", re.compile(r"\bmaine\s+(.+?)\s+submit (?:kiya|kar diya)", re.I)),
    ("done", re.compile(r"\bmaine\s+(.+?)\s+(?:complete|finish|khatam) (?:kiya|kar diya)", re.I)),
]


VERBS = {"application": "applied for", "submission": "submitted", "email": "emailed", "meeting": "attended",
         "done": "finished"}


def parse_activity(text: str) -> tuple[str, str] | None:
    for kind, pat in _ACTIVITIES:
        m = pat.search(text)
        if m:
            return kind, m.group(1).strip().rstrip(".!")
    return None


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def time_range(text: str, now: datetime.datetime | None = None) -> tuple[float, float] | None:
    """Past time phrases -> (start, end) epoch seconds, local time. None if the text has none."""
    now = now or datetime.datetime.now()
    t = text.lower()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day = datetime.timedelta(days=1)

    def span(start, end):
        return start.timestamp(), end.timestamp()

    if re.search(r"\b(today|aaj)\b", t):
        return span(day0, day0 + day)
    if re.search(r"\b(yesterday|kal)\b", t):  # memory questions are about the past, so "kal" = yesterday
        return span(day0 - day, day0)
    if re.search(r"\b(this week|is hafte|is week)\b", t):
        return span(day0 - day * now.weekday(), day0 + day)
    if re.search(r"\b(last week|pichle hafte|pichhle hafte)\b", t):
        monday = day0 - day * now.weekday()
        return span(monday - 7 * day, monday)
    if m := re.search(r"\b(?:last|past|pichle)\s+(\d{1,2})\s+days?\b", t):
        return span(day0 - day * (int(m.group(1)) - 1), day0 + day)
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b(?:on |last )?{name}\b", t):
            back = (now.weekday() - i) % 7 or 7  # most recent past one
            start = day0 - day * back
            return span(start, start + day)
    return None
