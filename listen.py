"""Hearing a video: its audio, decoded with PyAV and turned into text by a local SenseVoice model (sherpa-onnx).

Optional: without `av`, `sherpa_onnx` or the model files, `Listener.ready()` says why and nothing is heard. The model
(SenseVoiceSmall, int8, about 240 MB; FunASR model licence) lives in the plugin's data folder; nothing is sent
anywhere. SenseVoice also tells what else is in the sound (music, laughter, applause), so she can hear that a video
is a song or that people are laughing.
"""

from __future__ import annotations

import io
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger(__name__)

RATE = 16000
CHUNK_SECONDS = 20  # SenseVoice is best on pieces of half a minute or less
EVENTS = {"BGM": "背景音乐", "Laughter": "笑声", "Applause": "掌声", "Cry": "哭声", "Sneeze": "喷嚏", "Cough": "咳嗽",
          "Breath": "呼吸声"}
_TAG = re.compile(r"<\|[^|>]*\|>")
MODEL_FILES = ("model.int8.onnx", "tokens.txt")


def decode(audio: bytes, max_seconds: float) -> "np.ndarray":
    """Audio bytes (any format FFmpeg reads, e.g. Bilibili's m4a) -> 16 kHz mono float32, at most `max_seconds`.
    A file cut short (only the first bytes were downloaded) gives what was there."""
    import av  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    out, total, limit = [], 0, int(max_seconds * RATE)
    resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
    try:
        with av.open(io.BytesIO(audio)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                return np.zeros(0, dtype=np.float32)
            try:
                for frame in container.decode(stream):
                    for res in resampler.resample(frame):
                        pcm = res.to_ndarray().reshape(-1)
                        out.append(pcm)
                        total += pcm.size
                    if total >= limit:
                        break
            except (av.error.InvalidDataError, av.error.EOFError, EOFError) as exc:
                log.debug("audio ends early: %s", exc)  # the download was cut on purpose
    except av.error.InvalidDataError:
        return np.zeros(0, dtype=np.float32)
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out)[:limit].astype(np.float32)


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


_WORD = re.compile(r"[\w一-鿿]")


def _speech(text: str, event: str) -> str:
    """The text of a piece, or "" when it is not speech: punctuation alone, or a few syllables over music (the
    model makes those up where there is no voice)."""
    words = len(_WORD.findall(text))
    if words < 2 or (event == "BGM" and words < 6):
        return ""
    return text


def describe(pieces: list[tuple[float, str, str]], limit: int = 2500) -> str:
    """(start second, text, event) per piece -> what she hears: the speech with times, and the other sounds."""
    lines, sounds = [], []
    for start, text, event in pieces:
        event = (event or "").strip("<|>")
        text = _speech(_TAG.sub("", text or "").strip(), event)
        name = EVENTS.get(event, "")
        if name and name not in sounds:
            sounds.append(name)
        if text:
            lines.append(f"[{clock(start)}] {text}")
    said = "\n".join(lines)
    if len(said) > limit:
        said = said[:limit] + "…"
    head = f"（听到的声音里有：{'、'.join(sounds)}）\n" if sounds else ""
    if sounds and not said:
        head = f"（听到的声音里有：{'、'.join(sounds)}；没听到有人说话）"
    return (head + said).strip()


class Listener:
    """Turns audio into text, locally. One per plugin; the model is loaded on first use and kept."""

    def __init__(self, model_dir: str | Path, *, threads: int = 4, max_seconds: float = 480) -> None:
        self.model_dir = Path(model_dir)
        self.threads, self.max_seconds = threads, max_seconds
        self._recognizer = None
        self._lock = threading.Lock()

    def ready(self) -> tuple[bool, str]:
        """(can hear, why not)."""
        try:
            import av  # noqa: F401, PLC0415
            import sherpa_onnx  # noqa: F401, PLC0415
        except ImportError as exc:
            return False, f"缺少 {exc.name}"
        missing = [f for f in MODEL_FILES if not (self.model_dir / f).is_file()]
        if missing:
            return False, f"语音识别模型不在 {self.model_dir}（缺 {'、'.join(missing)}）"
        return True, ""

    def _model(self):
        with self._lock:
            if self._recognizer is None:
                import sherpa_onnx  # noqa: PLC0415

                self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(self.model_dir / "model.int8.onnx"), tokens=str(self.model_dir / "tokens.txt"),
                    num_threads=self.threads, use_itn=True)
            return self._recognizer

    def hear(self, audio: bytes) -> str:
        """Audio bytes -> what she hears (blocking: run it in a thread). "" when nothing was said or heard."""
        samples = decode(audio, self.max_seconds)
        if samples.size < RATE:  # less than a second
            return ""
        rec = self._model()
        step = CHUNK_SECONDS * RATE
        streams, starts = [], []
        for i in range(0, samples.size, step):
            piece = samples[i:i + step]
            if piece.size < RATE // 2:
                continue
            s = rec.create_stream()
            s.accept_waveform(RATE, piece)
            streams.append(s)
            starts.append(i / RATE)
        rec.decode_streams(streams)
        pieces = [(start, s.result.text, getattr(s.result, "event", "")) for start, s in zip(starts, streams)]
        return describe(pieces)
