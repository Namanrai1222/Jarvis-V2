"""`jarvis doctor`: checks the machine and Jarvis's own setup. Exit code 1 if anything required fails."""
import stat
import subprocess
from pathlib import Path

from . import client, config
from .llm import ollama

OK, WARN, FAIL = "ok", "warn", "FAIL"


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def checks(cfg: dict) -> list[tuple[str, str, str]]:
    out = []

    fv = "FileVault is On" in _run("fdesetup", "status")
    out.append((OK if fv else FAIL, "FileVault disk encryption",
                "on" if fv else "off: System Settings > Privacy & Security > FileVault"))

    fw = "enabled" in _run("/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate").lower()
    out.append((OK if fw else WARN, "Firewall", "on" if fw else "off: System Settings > Network > Firewall"))

    mode = stat.S_IMODE(config.APP_DIR.stat().st_mode) if config.APP_DIR.exists() else None
    out.append((OK if mode == 0o700 else FAIL, "Data folder private (0700)",
                str(config.APP_DIR) if mode == 0o700 else "run `jarvis serve` once to create it"))

    models = ollama.installed_models()
    out.append((OK if models is not None else FAIL, "Ollama running on 127.0.0.1:11434",
                "reachable" if models is not None else "brew services start ollama"))
    if models is not None:
        listen = _run("lsof", "-nP", "-iTCP:11434", "-sTCP:LISTEN")
        exposed = "*:11434" in listen
        out.append((FAIL if exposed else OK, "Ollama not exposed to the network",
                    "listening on all interfaces: unset OLLAMA_HOST" if exposed else "loopback only"))
        for role, required in (("router", True), ("main", False), ("embed", False)):
            name = cfg["models"][role]
            have = name in models
            out.append((OK if have else (FAIL if required else WARN), f"Model {role}: {name}",
                        "installed" if have else f"ollama pull {name}"))

    repo = cfg["voice"]["stt_model"].replace("/", "--")
    cached = (Path.home() / ".cache/huggingface/hub" / f"models--{repo}").exists()
    out.append((OK if cached else WARN, "Whisper speech model",
                "downloaded" if cached else "downloads on first voice use (~0.5 GB)"))
    out.append((OK, "Microphone permission", "macOS asks the first time Jarvis listens"))

    from . import secrets

    paired = bool(secrets.get("telegram_token") and secrets.get("telegram_chat_id"))
    out.append((OK if paired else WARN, "Telegram alerts when away",
                "paired" if paired else "optional: uv run jarvis telegram setup"))

    h = client.health() if config.TOKEN_PATH.exists() else None
    out.append((OK if h else WARN, "jarvisd running", "ready" if h else "run `jarvis install` or `jarvis serve`"))
    return out


def run() -> int:
    results = checks(config.load())
    for level, name, detail in results:
        print(f"[{level:>4}] {name}: {detail}")
    failed = sum(level == FAIL for level, _, _ in results)
    print("\nAll required checks passed." if not failed else f"\n{failed} required check(s) failed.")
    return 1 if failed else 0
