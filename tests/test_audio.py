"""Local speech never uploads audio; lock cancels work and transcripts remain drafts."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_workflow import client_for

from privacy_guard.audio import MAX_AUDIO_BYTES, LocalTranscriber, _recognize


@pytest.fixture
def transcriber(tmp_path):
    for name in ("model.bin", "tokenizer.json"):
        (tmp_path / name).touch()
    return LocalTranscriber(tmp_path)


async def test_local_result_preserves_hindi_without_cloud_key(transcriber, monkeypatch):
    recognize = AsyncMock(return_value={"text": "मेरा आधार डाउनलोड करें", "language": "hi"})
    monkeypatch.setattr(transcriber, "_request", recognize)
    result = await transcriber.transcribe(b"synthetic-audio", "audio/webm;codecs=opus")
    assert result == {"text": "मेरा आधार डाउनलोड करें", "language": "hi", "provider": "Local", "model": "faster-whisper-small"}
    recognize.assert_awaited_once_with(b"synthetic-audio")
    assert not hasattr(transcriber, "api_key")


@pytest.mark.parametrize("data,mime", [(b"", "audio/webm"), (b"audio", "text/plain"), (b"x" * (MAX_AUDIO_BYTES+1), "audio/wav")])
async def test_invalid_recording_is_rejected(transcriber, monkeypatch, data, mime):
    worker = AsyncMock()
    monkeypatch.setattr(transcriber, "_request", worker)
    with pytest.raises(ValueError):
        await transcriber.transcribe(data, mime)
    worker.assert_not_awaited()


async def test_missing_model_explains_local_install(tmp_path):
    with pytest.raises(ValueError, match="speech_install"):
        await LocalTranscriber(tmp_path).transcribe(b"audio", "audio/wav")


async def test_lock_cancels_work_and_overlapping_requests(transcriber, monkeypatch):
    started = asyncio.Event()
    async def delayed(*args):
        started.set()
        await asyncio.Future()
    monkeypatch.setattr(transcriber, "_request", delayed)
    first = asyncio.create_task(transcriber.transcribe(b"audio", "audio/webm"))
    await started.wait()
    with pytest.raises(ValueError, match="already"):
        await transcriber.transcribe(b"audio", "audio/webm")
    transcriber.clear()
    with pytest.raises(ValueError, match="locked"):
        await first
    assert transcriber._pending is None and transcriber.configured


def test_worker_uses_offline_local_model_and_auto_detects_language(monkeypatch):
    calls, output = [], []
    class Model:
        def __init__(self, path, **kwargs):
            assert path == "/local/model" and kwargs["local_files_only"] is True
            assert kwargs["device"] == "cpu"
        def transcribe(self, data, **kwargs):
            calls.append(data.read())
            assert kwargs["task"] == "transcribe" and "language" not in kwargs
            return [SimpleNamespace(text=" हिन्दी कार्य ")], SimpleNamespace(language="hi", duration=2)
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(disable_telemetry_events=lambda: None))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    connection = SimpleNamespace(send=output.append, close=lambda: None)
    _recognize(b"synthetic audio", "/local/model", connection)
    assert output == [{"text": "हिन्दी कार्य", "language": "hi"}]
    assert calls == [b"synthetic audio"]


async def test_audio_api_is_paired_bounded_local_and_does_not_start_task(tmp_path, monkeypatch, transcriber):
    app, _, client = await client_for(tmp_path / "api")
    app.state.transcriber.model_path = transcriber.model_path
    worker = AsyncMock(return_value={"text": "मेरा आधार डाउनलोड करें", "language": "hi"})
    monkeypatch.setattr(app.state.transcriber, "_request", worker)
    async with client:
        rejected = await client.post("/api/v1/audio/transcribe", content=b"audio", headers={"Content-Type":"audio/webm", "Authorization":"Bearer invalid"})
        assert rejected.status_code == 401
        rejected = await client.post("/api/v1/audio/transcribe", content=b"x"*(MAX_AUDIO_BYTES+1), headers={"Content-Type":"audio/webm"})
        assert rejected.status_code == 413
        result = await client.post("/api/v1/audio/transcribe", content=b"audio", headers={"Content-Type":"audio/webm"})
        assert result.status_code == 200 and result.json()["language"] == "hi"
        assert result.json()["provider"] == "Local"
        assert not app.state.manager.tasks
        settings = (await client.get("/api/v1/settings")).json()
        assert settings["speech_ready"] and settings["speech_provider"] == "local"
        assert "whisper_api_key" not in settings
        await client.post("/api/v1/vault/lock")
        assert (await client.post("/api/v1/audio/transcribe", content=b"audio", headers={"Content-Type":"audio/webm"})).status_code == 423
