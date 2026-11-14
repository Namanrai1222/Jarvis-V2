"""`jarvis` command: serve, menubar, doctor, ask, listen, install, uninstall."""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config

AGENTS = {"ollama": None, "daemon": "serve", "menubar": "menubar"}
LAUNCH_DIR = Path.home() / "Library/LaunchAgents"
# One model in GPU memory at a time: 8B + 1.7B together overflow a 16 GB Mac and slow to 0.2 tok/s.
OLLAMA_ENV = {"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0",
              "OLLAMA_KEEP_ALIVE": "10m", "OLLAMA_MAX_LOADED_MODELS": "1"}


def _label(name: str) -> str:
    return f"com.namanrai.jarvis.{name}"


def install() -> int:
    config.ensure_app_dir()
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    ollama = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
    # Jarvis runs Ollama itself so the settings above survive reboots; the plain brew service would clash on the port
    subprocess.run(["brew", "services", "stop", "ollama"], capture_output=True)
    for name, cmd in AGENTS.items():
        path = LAUNCH_DIR / f"{_label(name)}.plist"
        plist = {
            "Label": _label(name),
            "ProgramArguments": [ollama, "serve"] if cmd is None else [sys.executable, "-m", "jarvis.cli", cmd],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Interactive" if name == "menubar" else "Background",
            "StandardOutPath": str(config.LOG_DIR / f"{name}.log"),
            "StandardErrorPath": str(config.LOG_DIR / f"{name}.log"),
        }
        if cmd is None:
            plist["EnvironmentVariables"] = OLLAMA_ENV
        path.write_bytes(plistlib.dumps(plist))
        subprocess.run(["launchctl", "bootout", f"{domain}/{_label(name)}"], capture_output=True)
        for attempt in range(10):  # bootout finishes asynchronously; bootstrap fails until it has
            r = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
            if r.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise SystemExit(f"couldn't load {path}: {r.stderr.strip()}")
        print(f"loaded {path}")
    return 0


def uninstall() -> int:
    for name in AGENTS:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{_label(name)}"], capture_output=True)
        (LAUNCH_DIR / f"{_label(name)}.plist").unlink(missing_ok=True)
        print(f"removed {_label(name)}")
    return 0


def listen() -> int:
    """Terminal push-to-talk, for testing voice without the menu bar."""
    from . import client
    from .voice import stt, tts

    cfg = config.load()["voice"]
    rec = stt.Recorder()
    input("Press Enter to start talking…")
    rec.start()
    input("Listening. Press Enter when done.")
    text = stt.transcribe(rec.stop(), cfg["stt_model"], cfg["language"])
    print(f"You: {text}")
    reply = client.ask(text) if text else "I didn't catch that."
    print(f"Jarvis: {reply}")
    tts.speak(reply)
    tts.wait()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="jarvis")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("serve", "menubar", "doctor", "listen", "install", "uninstall"):
        sub.add_parser(name)
    sub.add_parser("ask").add_argument("text", nargs="+")
    args = p.parse_args()

    if args.cmd == "serve":
        from .daemon import serve
        serve()
    elif args.cmd == "menubar":
        from .menubar import run
        run()
    elif args.cmd == "doctor":
        from .doctor import run
        sys.exit(run())
    elif args.cmd == "ask":
        from .client import ask
        print(ask(" ".join(args.text)))
    elif args.cmd == "listen":
        sys.exit(listen())
    elif args.cmd == "install":
        sys.exit(install())
    elif args.cmd == "uninstall":
        sys.exit(uninstall())


if __name__ == "__main__":
    main()
