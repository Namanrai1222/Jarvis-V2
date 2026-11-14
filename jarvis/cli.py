"""`jarvis` command: serve, menubar, doctor, ask, listen, install, uninstall, telegram."""
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


def telegram(action: str) -> int:
    """Pair Jarvis with your Telegram (free, official Bot API). You type the token; it goes to the Keychain."""
    import getpass
    import secrets as rnd

    from . import secrets
    from .notify.telegram import Telegram

    restart = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.namanrai.jarvis.daemon"]
    if action == "test":
        token, chat = secrets.get("telegram_token"), secrets.get("telegram_chat_id")
        if not (token and chat):
            raise SystemExit("Not paired yet. Run: uv run jarvis telegram setup")
        Telegram(token).send(int(chat), "Test from Jarvis ✅ Notifications reach your phone.")
        print("Sent. Check Telegram.")
        return 0
    if action == "unpair":
        secrets.delete("telegram_token")
        secrets.delete("telegram_chat_id")
        subprocess.run(restart, capture_output=True)
        print("Unpaired. Jarvis will only use Mac notifications.")
        return 0

    print("1. In Telegram, open @BotFather, send /newbot, and follow the steps.")
    token = getpass.getpass("2. Paste the bot token here (hidden): ").strip()
    bot = Telegram(token)
    name = bot.me()["username"]
    secrets.delete("telegram_chat_id")  # stop the daemon polling an old pairing while we pair
    subprocess.run(restart, capture_output=True)
    code = f"{rnd.randbelow(10**6):06d}"
    print(f"3. Open https://t.me/{name} and send exactly:  /start {code}\n   Waiting up to 5 minutes…")
    offset, deadline = 0, time.time() + 300
    while time.time() < deadline:
        for u in bot.updates(offset, wait=25):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            # only the person who sends this one-time code, in a private chat, becomes the owner
            if (m.get("text") or "").strip() == f"/start {code}" and m["chat"]["type"] == "private":
                secrets.put("telegram_token", token)
                secrets.put("telegram_chat_id", str(m["chat"]["id"]))
                bot.send(m["chat"]["id"], "Paired with Jarvis ✅ I'll message you here when I need a decision "
                                          "or something urgent happens. Reply 'status' any time.")
                subprocess.run(restart, capture_output=True)
                print(f"Paired with {m['from'].get('first_name', 'you')}. Jarvis restarted.")
                return 0
    raise SystemExit("No pairing message arrived. Run the command again.")


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
    sub.add_parser("telegram").add_argument("action", choices=["setup", "test", "unpair"])
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
    elif args.cmd == "telegram":
        sys.exit(telegram(args.action))
    elif args.cmd == "uninstall":
        sys.exit(uninstall())


if __name__ == "__main__":
    main()
