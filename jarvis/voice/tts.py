"""Spoken replies with macOS `say` (offline)."""
import subprocess

_proc: subprocess.Popen | None = None


def speak(text: str) -> None:
    global _proc
    stop()
    # text goes via stdin so it can never be read as a `say` option
    _proc = subprocess.Popen(["say", "-f", "-"], stdin=subprocess.PIPE, text=True)
    _proc.stdin.write(text)
    _proc.stdin.close()


def stop() -> None:
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()


def wait() -> None:
    if _proc is not None:
        _proc.wait()
