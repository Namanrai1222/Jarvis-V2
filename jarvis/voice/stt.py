"""Offline speech-to-text. Audio stays in memory and is never written to disk."""
import numpy as np
import sounddevice as sd

RATE = 16000


class Recorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=RATE, channels=1, dtype="float32",
            callback=lambda data, frames, t, status: self._chunks.append(data.copy()),
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        return np.concatenate(self._chunks)[:, 0] if self._chunks else np.zeros(0, np.float32)


def transcribe(audio: np.ndarray, model: str, language: str | None = "en") -> str:
    if audio.size < RATE * 0.3:  # under 0.3 s is a mis-press
        return ""
    import mlx_whisper  # slow import; only load when voice is used

    return mlx_whisper.transcribe(audio, path_or_hf_repo=model, language=language)["text"].strip()
