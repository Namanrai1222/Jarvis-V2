"""Hands-free "Hey Jarvis": a tiny wake-word model listens on the CPU; audio stays in memory.

Frames before the wake word are scored and dropped at once. After it, audio is kept only until you stop
talking (voice-activity detection), then handed over for transcription and dropped.
"""
import queue
import threading
from typing import Callable

import numpy as np

RATE = 16000
FRAME = 1280                 # 80 ms, what openWakeWord expects
END_SILENCE = 10             # 0.8 s of silence after speech ends the command
NO_SPEECH_TIMEOUT = 30       # 2.4 s with no speech after the wake word: give up
MAX_FRAMES = 190             # 15 s max per command


def _default_models():
    import warnings

    warnings.filterwarnings("ignore", message=".*CUDAExecutionProvider.*")  # onnxruntime noise on Macs
    import openwakeword
    from openwakeword.model import Model
    from openwakeword.vad import VAD

    wake = Model(wakeword_model_paths=[openwakeword.models["hey_jarvis"]["model_path"]])
    vad = VAD()
    return (lambda f: max(wake.predict(f).values()), wake.reset,
            lambda f: float(vad.predict(f, frame_size=640)))


class WakeListener:
    def __init__(self, on_command: Callable[[np.ndarray], None], on_wake: Callable[[], None] = lambda: None,
                 threshold: float = 0.5, muted: Callable[[], bool] = lambda: False, models=None) -> None:
        self.on_command, self.on_wake, self.threshold, self.muted = on_command, on_wake, threshold, muted
        self._models = models  # (wake_score(frame), reset(), speech_prob(frame)); loaded lazily
        self._listening = False
        self._buf: list[np.ndarray] = []
        self._heard = False
        self._silence = 0
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._stream = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._stream is not None

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        """One 80 ms int16 frame in; the finished command (float32) out when it ends."""
        score, reset, speech = self._models
        if not self._listening:
            if self.muted():
                return None
            if score(frame) >= self.threshold:
                self._listening, self._buf, self._heard, self._silence = True, [], False, 0
                self.on_wake()
            return None
        self._buf.append(frame)
        if speech(frame) > 0.5:
            self._heard, self._silence = True, 0
        else:
            self._silence += 1
        n = len(self._buf)
        done = (self._heard and self._silence >= END_SILENCE) or (not self._heard and n >= NO_SPEECH_TIMEOUT) \
            or n >= MAX_FRAMES
        if not done:
            return None
        self._listening = False
        reset()  # forget the wake word so it can't fire again on the same audio
        audio = np.concatenate(self._buf).astype(np.float32) / 32768 if self._heard else None
        self._buf = []
        return audio

    def start(self) -> None:
        import sounddevice as sd

        if self.running:
            return
        if self._models is None:
            self._models = _default_models()
        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                      callback=lambda data, frames, t, status: self._put(data[:, 0].copy()))
        self._stream.start()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _put(self, frame: np.ndarray) -> None:
        try:
            self._q.put_nowait(frame)
        except queue.Full:  # busy transcribing: dropping audio is fine, it was not for us anyway
            pass

    def _loop(self) -> None:
        while self._stream is not None:
            try:
                frame = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            audio = self.feed(frame)
            if audio is not None:
                self.on_command(audio)
                while not self._q.empty():  # drop audio that arrived while we were busy
                    self._q.get_nowait()

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self._listening, self._buf = False, []
