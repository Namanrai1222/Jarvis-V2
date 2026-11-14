"""Spoken replies with macOS `say` (offline), in the best English voice installed."""
import functools
import re
import subprocess

_proc: subprocess.Popen | None = None
MAX_SPOKEN = 400  # characters; the full reply is still shown on screen


@functools.cache
def pick_voice(preferred: str = "") -> str | None:
    """Preferred voice if installed, else Premium/Enhanced Indian or US English, else Rishi, else system default."""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    voices = [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"^(.+?)\s+(\w\w_\w\w)\s+#", out, re.M)]
    names = [n for n, _ in voices]
    if preferred and preferred in names:
        return preferred
    for quality in ("Premium", "Enhanced"):
        for lang in ("en_IN", "en_US", "en_GB"):
            for name, code in voices:
                if quality in name and code == lang:
                    return name
    return next((n for n in names if n.startswith("Rishi")), None)


def for_speech(text: str) -> str:
    """Strip markdown and links, keep the first few sentences."""
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_`#>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_SPOKEN:
        return text
    cut = text.rfind(". ", 0, MAX_SPOKEN)
    return text[:cut + 1] if cut > 0 else text[:MAX_SPOKEN]


def speak(text: str, voice: str = "") -> None:
    global _proc
    stop()
    cmd = ["say", "-r", "190"]
    if v := pick_voice(voice):
        cmd += ["-v", v]
    # text goes via stdin so it can never be read as a `say` option
    _proc = subprocess.Popen(cmd + ["-f", "-"], stdin=subprocess.PIPE, text=True)
    _proc.stdin.write(for_speech(text))
    _proc.stdin.close()


def speaking() -> bool:
    return _proc is not None and _proc.poll() is None


def stop() -> None:
    if speaking():
        _proc.terminate()


def wait() -> None:
    if _proc is not None:
        _proc.wait()


def chime() -> None:
    """Short sound so Naman knows Jarvis is listening."""
    subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])
