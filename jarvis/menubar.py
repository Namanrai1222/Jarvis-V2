"""Menu bar app: status icon, Ask…, push-to-talk on ⌥Space, stop speaking."""
import threading

import rumps
from PyObjCTools import AppHelper
from quickmachotkey import mask, quickHotKey
from quickmachotkey.constants import kVK_Space, optionKey

from . import client, config
from .voice import stt, tts

READY, LISTENING, THINKING, OFFLINE = "◯", "●", "◐", "◌"


class JarvisApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Jarvis", title=OFFLINE, quit_button="Quit Jarvis menu")
        self.cfg = config.load()
        self.rec = stt.Recorder()
        self.menu = [
            rumps.MenuItem("Ask…", callback=self.ask),
            rumps.MenuItem("Push to talk  (⌥Space)", callback=lambda _: self.toggle_voice()),
            rumps.MenuItem("Stop speaking", callback=lambda _: tts.stop()),
        ]
        self.timer = rumps.Timer(self.refresh, 15)
        self.timer.start()

    def refresh(self, _=None) -> None:
        if self.rec.recording or self.title == THINKING:
            return
        h = client.health()
        self.title = READY if h and h.get("llm") else OFFLINE

    # --- voice ---
    def toggle_voice(self) -> None:
        if self.rec.recording:
            audio = self.rec.stop()
            self.title = THINKING
            threading.Thread(target=self._handle_audio, args=(audio,), daemon=True).start()
        else:
            tts.stop()
            try:
                self.rec.start()
            except Exception as e:  # no mic / permission denied
                rumps.notification("Jarvis", "Microphone unavailable", str(e))
                return
            self.title = LISTENING

    def _handle_audio(self, audio) -> None:
        v = self.cfg["voice"]
        try:
            text = stt.transcribe(audio, v["stt_model"], v["language"])
            reply = client.ask(text) if text else "I didn't catch that."
        except Exception as e:
            text, reply = "", f"Something went wrong: {e}"
        AppHelper.callAfter(self._show, text, reply, v["speak_replies"])

    def _show(self, heard: str, reply: str, speak: bool) -> None:
        self.title = READY
        rumps.notification("Jarvis", f"You: {heard}" if heard else "", reply)
        if speak:
            tts.speak(reply)
        self.refresh()

    # --- typed ---
    def ask(self, _) -> None:
        resp = rumps.Window("What do you need?", "Ask Jarvis", ok="Ask", cancel="Cancel", dimensions=(320, 24)).run()
        if not resp.clicked or not resp.text.strip():
            return
        self.title = THINKING
        text = resp.text.strip()

        def work():
            try:
                reply = client.ask(text)
            except Exception as e:
                reply = f"Something went wrong: {e}"
            AppHelper.callAfter(self._show, text, reply, False)

        threading.Thread(target=work, daemon=True).start()


_app: JarvisApp | None = None


@quickHotKey(virtualKey=kVK_Space, modifierMask=mask(optionKey))
def _hotkey() -> None:
    # Carbon hot key: works without Input Monitoring permission. Press once to talk, again to send.
    if _app is not None:
        _app.toggle_voice()


def run() -> None:
    global _app
    _app = JarvisApp()
    _app.run()
