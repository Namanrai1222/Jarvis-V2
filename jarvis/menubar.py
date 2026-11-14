"""Menu bar app: status icon, Ask…, "Hey Jarvis", push-to-talk (⌥Space), approvals, undo, Stop everything (⌃⌥⌘.)."""
import threading

import rumps
from PyObjCTools import AppHelper
from quickmachotkey import mask, quickHotKey
from quickmachotkey.constants import cmdKey, controlKey, kVK_ANSI_Period, kVK_Space, optionKey

from . import client, config
from .voice import stt, tts
from .voice.wake import WakeListener

READY, LISTENING, THINKING, OFFLINE, PAUSED = "◯", "●", "◐", "◌", "‖"


class JarvisApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Jarvis", title=OFFLINE, quit_button="Quit Jarvis menu")
        self.cfg = config.load()
        self.rec = stt.Recorder()
        self.busy = False
        self.pause_item = rumps.MenuItem("Pause", callback=self.toggle_pause)
        self.menu = [
            rumps.MenuItem("Ask…", callback=self.ask),
            rumps.MenuItem("Push to talk  (⌥Space)", callback=lambda _: self.toggle_voice()),
            rumps.MenuItem("Approvals…", callback=lambda _: self._background(self._review_pending)),
            rumps.MenuItem("Undo last action", callback=lambda _: self._background(
                lambda: self._call_after_show("", client.post("/api/undo")))),
            None,
            self.pause_item,
            rumps.MenuItem("Stop everything  (⌃⌥⌘.)", callback=lambda _: stop_everything()),
            rumps.MenuItem("Stop speaking", callback=lambda _: tts.stop()),
        ]
        self.wake_item = rumps.MenuItem("Listen for “Hey Jarvis”", callback=self.toggle_wake)
        self.menu.insert_after("Push to talk  (⌥Space)", self.wake_item)
        v = self.cfg["voice"]
        self.wake = WakeListener(on_command=self._wake_command, on_wake=self._on_wake, threshold=v["wake_threshold"],
                                 muted=lambda: self.busy or self.rec.recording or tts.speaking())
        if v["wake_word"]:
            self._set_wake(True)
        self._background(lambda: stt.preload(v["stt_model"]))  # first command shouldn't wait for model load
        self.timer = rumps.Timer(self.refresh, 15)
        self.timer.start()

    # --- "Hey Jarvis" ---
    def _set_wake(self, on: bool) -> None:
        try:
            self.wake.start() if on else self.wake.stop()
        except Exception as e:  # no mic or permission denied
            rumps.notification("Jarvis", "Can't listen for “Hey Jarvis”", str(e))
        self.wake_item.state = int(self.wake.running)

    def toggle_wake(self, _) -> None:
        self._set_wake(not self.wake.running)

    def _on_wake(self) -> None:  # worker thread
        tts.stop()
        tts.chime()
        AppHelper.callAfter(setattr, self, "title", LISTENING)

    def _wake_command(self, audio) -> None:  # worker thread: transcribe, act, speak
        self.busy = True
        AppHelper.callAfter(setattr, self, "title", THINKING)
        try:
            self._handle_audio(audio)
        except Exception as e:
            self._call_after_show("", f"Something went wrong: {e}")

    def refresh(self, _=None) -> None:
        if self.rec.recording or self.busy:
            return
        h = client.health()
        if not h:
            self.title = OFFLINE
            return
        paused = h.get("status") == "paused"
        self.pause_item.title = "Resume" if paused else "Pause"
        icon = PAUSED if paused else (READY if h.get("llm") else OFFLINE)
        self.title = f"{icon} {h['pending']}" if h.get("pending") else icon

    def _background(self, fn) -> None:
        def wrapped():
            try:
                fn()
            except Exception as e:
                self._call_after_show("", f"Something went wrong: {e}")
        threading.Thread(target=wrapped, daemon=True).start()

    def _call_after_show(self, heard: str, reply: str, speak: bool = False, pending=None) -> None:
        AppHelper.callAfter(self._show, heard, reply, speak, pending or [])

    def _show(self, heard: str, reply: str, speak: bool, pending: list[dict]) -> None:
        self.busy = False
        rumps.notification("Jarvis", f"You: {heard}" if heard else "", reply)
        if speak:
            tts.speak(reply, self.cfg["voice"]["tts_voice"])
        self.refresh()
        for p in pending:
            self._approve(p)

    def _approve(self, p: dict) -> None:
        note = "\n\nSuggested after reading a file or app content." if p.get("tainted") else ""
        ok = rumps.alert(f"Jarvis needs your OK ({p['id']})", p["summary"] + note, ok="Confirm", cancel="Cancel")
        path = "/api/confirm" if ok == 1 else "/api/cancel"
        self._background(lambda: self._call_after_show("", client.post(path, {"id": p["id"]})))

    def _review_pending(self) -> None:
        with client.client() as c:
            items = c.get("/api/pending").json()
        if not items:
            self._call_after_show("", "Nothing waiting for approval.")
        else:
            AppHelper.callAfter(lambda: [self._approve(p) for p in items])

    def toggle_pause(self, _) -> None:
        word = "resume" if self.pause_item.title == "Resume" else "pause"
        self._background(lambda: self._call_after_show("", client.ask(word)))

    # --- voice ---
    def toggle_voice(self) -> None:
        if self.rec.recording:
            audio = self.rec.stop()
            self.title, self.busy = THINKING, True
            self._background(lambda: self._handle_audio(audio))
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
        text = stt.transcribe(audio, v["stt_model"], v["language"])
        out = client.send(text) if text else {"reply": "I didn't catch that.", "pending": []}
        self._call_after_show(text, out["reply"], v["speak_replies"], out.get("pending"))

    # --- typed ---
    def ask(self, _) -> None:
        resp = rumps.Window("What do you need?", "Ask Jarvis", ok="Ask", cancel="Cancel", dimensions=(320, 24)).run()
        text = resp.text.strip()
        if not resp.clicked or not text:
            return
        self.title, self.busy = THINKING, True
        self._background(lambda: (lambda out: self._call_after_show(text, out["reply"], False, out.get("pending")))(
            client.send(text)))


_app: JarvisApp | None = None


def stop_everything() -> None:
    tts.stop()
    if _app is not None and _app.rec.recording:
        _app.rec.stop()
    try:
        reply = client.post("/api/stop")
    except Exception as e:
        reply = f"Couldn't reach Jarvis: {e}"
    rumps.notification("Jarvis", "Stop everything", reply)
    if _app is not None:
        _app.busy = False
        _app.refresh()


# Carbon hot keys work without Input Monitoring permission.
@quickHotKey(virtualKey=kVK_Space, modifierMask=mask(optionKey))
def _talk_hotkey() -> None:
    if _app is not None:
        _app.toggle_voice()


@quickHotKey(virtualKey=kVK_ANSI_Period, modifierMask=mask(controlKey, optionKey, cmdKey))
def _stop_hotkey() -> None:
    stop_everything()


def run() -> None:
    global _app
    _app = JarvisApp()
    _app.run()
