# Jarvis-V2

A private assistant for macOS that runs entirely on your laptop: local LLM (Ollama), offline voice (Whisper + `say`), menu bar app, and a token-protected local API.

## Setup

```bash
brew install uv ollama portaudio
brew services start ollama
ollama pull qwen3:1.7b
uv sync
uv run jarvis doctor
uv run jarvis install   # starts the daemon and menu bar at login
```

## Use

- Voice: press ⌥Space, speak, press ⌥Space again.
- Typed: menu bar → Ask…, or `uv run jarvis ask "open chrome and notes"`.
- Terminal voice test: `uv run jarvis listen`.
- Tests: `uv run pytest`.
