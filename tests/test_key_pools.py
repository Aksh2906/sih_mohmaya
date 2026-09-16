"""Credential failover is bounded, destination-aware, and never exposes key values."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from privacy_guard.agent_llm import GuardedChatModel
from privacy_guard.agent_runtime import ReferenceInput
from privacy_guard.api import create_app
from privacy_guard.config import FALLBACK_BASE_URL
from privacy_guard.gateway import digest
from privacy_guard.provider_errors import classify_error
from tests.test_provider_fallback import make_runtime, transport


def pooled_runtime(tmp_path):
    runtime = make_runtime(tmp_path)
    gateway = runtime.manager.gateway
    gateway.api_keys = ["primary-canary-one", "primary-canary-two"]
    gateway.fallback_api_keys = ["gemini-canary-one", "gemini-canary-two"]
    runtime.llm = GuardedChatModel(runtime)
    return runtime


@pytest.mark.parametrize("status", [401, 402, 403, 408, 429, 500])
async def test_exhausts_primary_then_gemini_pool_and_keeps_working_key(tmp_path, monkeypatch, status):
    runtime = pooled_runtime(tmp_path)
    calls = []

    def handler(request):
        key = request.headers["authorization"].removeprefix("Bearer ")
        calls.append((request.url.host, key))
        payload = json.loads(request.content)
        for secret in runtime.private():
            assert secret not in request.content.decode()
        if request.url.host == "generativelanguage.googleapis.com":
            assert "store" not in payload and payload["model"] == "gemini-2.5-flash"
        if key == "gemini-canary-two":
            return httpx.Response(200, json={"choices": [{"message": {"content": '{}'}}]})
        return httpx.Response(status)

    transport(monkeypatch, handler)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    await runtime.llm.send(payload, digest(payload), [])
    assert calls == [("openrouter.ai", "primary-canary-one"), ("openrouter.ai", "primary-canary-two"),
                     ("generativelanguage.googleapis.com", "gemini-canary-one"),
                     ("generativelanguage.googleapis.com", "gemini-canary-two")]
    payload = runtime.llm.prepare([{"role": "user", "content": "Continue"}], ReferenceInput, [])
    await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == 5 and calls[-1][1] == "gemini-canary-two"
    assert not any(secret in str(runtime.task["events"]) for secret in runtime.private())


@pytest.mark.parametrize("invalid_key,expected", [(False, 2), (True, 4)])
async def test_malformed_400_skips_key_rotation_but_invalid_key_400_rotates(tmp_path, monkeypatch, invalid_key, expected):
    runtime = pooled_runtime(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "API key not valid" if invalid_key else "Invalid argument PRIVATE_ECHO"}})

    transport(monkeypatch, handler)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    with pytest.raises(ValueError) as error:
        await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == expected
    assert "PRIVATE_ECHO" not in str(error.value) + str(runtime.task)


async def test_all_keys_fail_once_without_looping(tmp_path, monkeypatch):
    runtime = pooled_runtime(tmp_path)
    calls = []
    transport(monkeypatch, lambda request: (calls.append(request), httpx.Response(402))[1])
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    with pytest.raises(ValueError, match="billing"):
        await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == 4


async def test_rotation_rechecks_stop_before_next_key(tmp_path, monkeypatch):
    runtime = pooled_runtime(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        runtime.manager.generation += 1
        return httpx.Response(429)

    transport(monkeypatch, handler)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    with pytest.raises(asyncio.CancelledError):
        await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == 1


async def test_pool_change_invalidates_approval_and_all_keys_are_sanitized(tmp_path, monkeypatch):
    runtime = pooled_runtime(tmp_path)
    payload = runtime.llm.prepare([{"role": "user", "content": " ".join(runtime.private())}], ReferenceInput, runtime.private())
    assert not any(secret in json.dumps(payload) for secret in runtime.private())
    runtime.manager.gateway.api_keys[1] = "changed-secret"
    transport(monkeypatch, lambda request: pytest.fail("Settings changed but request was sent"))
    with pytest.raises(ValueError, match="settings changed"):
        await runtime.llm.send(payload, digest(payload), [])


async def test_long_retry_after_moves_to_fallback_without_hammering_pool(tmp_path, monkeypatch):
    runtime = pooled_runtime(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})

    transport(monkeypatch, handler)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    with pytest.raises(ValueError, match="quota"):
        await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == 2


async def test_pools_saved_encrypted_restored_and_clear_independently(tmp_path):
    app = create_app(tmp_path, browser=SimpleNamespace(invalidate=lambda: None), pairing_code="pool-pair-code", testing=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        pair = await client.post("/api/v1/pair", json={"code": "pool-pair-code"})
        client.headers["Authorization"] = "Bearer " + pair.json()["token"]
        password = "synthetic-long-passphrase"
        await client.post("/api/v1/vault/initialize", json={"passphrase": password})
        keys = ["encrypted-primary-canary-1", "encrypted-primary-canary-2"]
        fallback = ["encrypted-gemini-canary-1", "encrypted-gemini-canary-2"]
        saved = await client.post("/api/v1/settings", json={"api_keys": keys + [keys[0]], "fallback_api_keys": fallback,
                                                           "model": "custom/model"})
        assert saved.status_code == 200
        assert saved.json()["key_count"] == saved.json()["fallback_key_count"] == 2
        assert not any(key in saved.text for key in keys + fallback)
        await client.post("/api/v1/vault/lock")
        assert not app.state.gateway.api_keys and not app.state.gateway.fallback_api_keys
        await client.post("/api/v1/vault/unlock", json={"passphrase": password})
        assert app.state.gateway.api_keys == keys and app.state.gateway.fallback_api_keys == fallback
        await client.post("/api/v1/settings", json={})
        assert app.state.gateway.model == "custom/model"
        assert app.state.gateway.api_keys == keys
        bad = await client.post("/api/v1/settings", json={"api_keys": keys, "api_key": "conflict"})
        assert bad.status_code == 422
        bad = await client.post("/api/v1/settings", json={"api_keys": ["x"] * 11})
        assert bad.status_code == 422
        await client.post("/api/v1/settings", json={"fallback_api_keys": []})
        assert not app.state.gateway.fallback_api_keys and app.state.gateway.api_keys == keys
        await client.post("/api/v1/settings", json={"api_keys": None})
        assert not app.state.gateway.api_keys
    for file in tmp_path.rglob("*"):
        if file.is_file():
            assert not any(key.encode() in file.read_bytes() for key in keys + fallback)


def test_error_classification_never_returns_provider_echo():
    failure = classify_error(httpx.Response(400, json=[{"error": {"message": 'Unknown name "store": Cannot find field. PRIVATE_ECHO'}}]))
    assert failure.category == "unsupported_parameter"
    assert "PRIVATE_ECHO" not in failure.describe("Gemini", 400)


async def test_preflight_checks_every_key_with_synthetic_content(tmp_path, monkeypatch):
    from privacy_guard.provider_checks import check_keys

    runtime = pooled_runtime(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert "PRIVATE_TASK" not in request.content.decode()
        if request.url.host == "generativelanguage.googleapis.com":
            assert "store" not in payload
        if request.headers["authorization"].endswith("one"):
            return httpx.Response(400, json={"error": {"message": "API key not valid PRIVATE_ECHO"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    transport(monkeypatch, handler)
    results = await check_keys(runtime.manager.gateway, lambda: True)
    assert len(calls) == len(results) == 4
    assert sum(result["ok"] for result in results) == 2
    assert not any(secret in str(results) for secret in runtime.private())
    assert "PRIVATE_ECHO" not in str(results)
    runtime.manager.gateway.base_url = FALLBACK_BASE_URL
    calls.clear()
    results = await check_keys(runtime.manager.gateway, lambda: False)
    assert not calls and all(result["category"] == "locked" for result in results)
