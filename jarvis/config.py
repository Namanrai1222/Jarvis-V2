"""Paths and settings. Config may only add to defaults; it holds no secrets."""
import copy
import os
import secrets
import tomllib
from pathlib import Path

APP_DIR = Path(os.environ.get("JARVIS_HOME", Path.home() / "Library/Application Support/Jarvis"))
CONFIG_PATH = APP_DIR / "config.toml"
TOKEN_PATH = APP_DIR / "token"
SOCK_PATH = APP_DIR / "jarvis.sock"
LOG_DIR = APP_DIR / "logs"
PORT = 8765
OLLAMA_URL = "http://127.0.0.1:11434"

DEFAULTS = {
    "models": {
        "router": "qwen3:1.7b",
        "main": "qwen3:8b",
        "embed": "qwen3-embedding:0.6b",
    },
    # spoken name -> bundle ID; only these can be opened
    "apps": {
        "chrome": "com.google.Chrome",
        "google chrome": "com.google.Chrome",
        "vs code": "com.microsoft.VSCode",
        "vscode": "com.microsoft.VSCode",
        "notes": "com.apple.Notes",
        "spotify": "com.spotify.client",
        "whatsapp": "net.whatsapp.WhatsApp",
        "safari": "com.apple.Safari",
        "finder": "com.apple.finder",
        "calendar": "com.apple.iCal",
        "reminders": "com.apple.reminders",
    },
    "voice": {
        "stt_model": "mlx-community/whisper-large-v3-turbo",
        "language": "en",  # Hinglish comes out romanised, which the fast path understands
        "speak_replies": True,
    },
}


def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(APP_DIR, 0o700)
    LOG_DIR.mkdir(exist_ok=True)


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            for section, values in tomllib.load(f).items():
                cfg.setdefault(section, {}).update(values)
    return cfg


def new_token() -> str:
    """Random token per daemon launch, readable only by this user."""
    token = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(TOKEN_PATH, 0o600)
    return token
