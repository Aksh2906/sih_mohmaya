"""Bounded, cancellable local speech recognition; recordings never leave this machine."""

import asyncio
import io
import multiprocessing
import os
import time
from pathlib import Path

from .config import DATA_DIR

MAX_AUDIO_BYTES = 10 * 1024 * 1024
FORMATS = {
    "audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav",
    "audio/wave": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "m4a", "video/mp4": "mp4", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/flac": "flac",
}


def audio_format(content_type: str) -> tuple[str, str]:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in FORMATS:
        raise ValueError("Use a WebM, WAV, MP3, MP4, M4A, OGG, or FLAC recording")
    return mime, FORMATS[mime]


def _recognize(data, model_path, connection):
    # Isolated process permits cancellation on lock; no recording files or network.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        from faster_whisper import WhisperModel

        model = WhisperModel(model_path, device="cpu", compute_type="int8", cpu_threads=4,
                             local_files_only=True)
        segments, info = model.transcribe(io.BytesIO(data), beam_size=3, vad_filter=True,
                                          condition_on_previous_text=False, task="transcribe")
        if info.duration > 90:
            raise ValueError("Recording is too long")
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text or len(text) > 20000:
            connection.send({"error": "No usable speech was recognized. Record again or type your task."})
        else:
            connection.send({"text": text, "language": info.language})
    except Exception:
        connection.send({"error": "Local transcription failed. Try a shorter, clearer recording or type your task."})
    finally:
        connection.close()


class LocalTranscriber:
    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or Path(os.environ.get("GUARD_SPEECH_MODEL", str(DATA_DIR / "models/whisper-small")))
        self._pending: asyncio.Task | None = None
        self._generation = 0

    @property
    def configured(self):
        return (self.model_path / "model.bin").is_file() and (self.model_path / "tokenizer.json").is_file()

    def clear(self):
        self._generation += 1
        if self._pending and not self._pending.done():
            self._pending.cancel()

    async def transcribe(self, data: bytes, content_type: str):
        audio_format(content_type)
        if not data or len(data) > MAX_AUDIO_BYTES:
            raise ValueError("Recordings must be between 1 byte and 10 MiB")
        if not self.configured:
            raise ValueError("Install the local speech model with: .venv/bin/python scripts/speech_install.py")
        if self._pending and not self._pending.done():
            raise ValueError("A recording is already being transcribed; wait or discard it first")
        generation = self._generation
        work = asyncio.create_task(self._request(data))
        self._pending = work
        try:
            result = await work
            if generation != self._generation:
                raise ValueError("Transcription was discarded because the vault was locked")
            return {**result, "provider": "Local", "model": "faster-whisper-small"}
        except asyncio.CancelledError:
            if generation != self._generation:
                raise ValueError("Transcription was discarded because the vault was locked") from None
            raise
        finally:
            if self._pending is work:
                self._pending = None

    async def _request(self, data):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=_recognize, args=(data, str(self.model_path), child), daemon=True)
        try:
            process.start()
            child.close()
            deadline = time.monotonic() + 120
            while not parent.poll():
                if not process.is_alive():
                    raise ValueError("Local speech process stopped. Retry or type your task.")
                if time.monotonic() >= deadline:
                    raise ValueError("Local transcription timed out. Use a shorter recording.")
                await asyncio.sleep(0.05)
            result = parent.recv()
            if result.get("error"):
                raise ValueError(result["error"])
            return result
        finally:
            parent.close()
            child.close()
            if process.pid:
                await asyncio.to_thread(process.join, 0.5)
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                process.close()
