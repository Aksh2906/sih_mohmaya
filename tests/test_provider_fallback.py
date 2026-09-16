"""Provider switches preserve credentials, request review and bounded attempts."""
import json
from types import SimpleNamespace

import httpx
import pytest

from privacy_guard.agent_runtime import BrowserAgentRuntime, ReferenceInput
from privacy_guard.gateway import ModelGateway, digest
from privacy_guard.tasks import TaskManager
from privacy_guard.vault import Vault


def make_runtime(tmp_path):
    vault = Vault(tmp_path)
    vault.initialize("synthetic-long-passphrase")
    gateway = ModelGateway()
    gateway.api_key = "synthetic-openrouter-key"
    gateway.fallback_api_key = "synthetic-gemini-key"
    manager = TaskManager(vault, SimpleNamespace(), gateway)
    task = {"_generation": 0, "_goal": "Inspect", "_origin": "https://example.test",
            "_ref_ids": {}, "_refs": {}, "events": []}
    return BrowserAgentRuntime(manager, task)


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    def peer(request):
        # This fixture tests provider failover. Simulate successful approval
        # checks independently; classifier transport has its own test suite.
        body = json.loads(request.content)
        if body["messages"][0]["content"].startswith("APPROVAL_CHECK:"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "false"}}]})
        return handler(request)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(peer), **kw))


@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("failure", [400, 401, 402, 403, 404, 408, 429, 500, "connection"])
async def test_switch_uses_separate_key_and_fresh_text_review(tmp_path, monkeypatch, review, failure):
    runtime = make_runtime(tmp_path)
    runtime.task["_review_text"] = review
    calls, approvals = [], []

    async def approve(task, generation, kind, title, payload):
        approvals.append(payload)
        assert len(calls) == 1
        assert payload["destination"] == "https://generativelanguage.googleapis.com/v1beta/openai"
        assert payload["request"]["model"] == "gemini-2.5-flash"

    runtime.manager.approval = approve

    def handle(request):
        calls.append(request)
        if request.url.host == "openrouter.ai":
            assert request.headers["authorization"] == "Bearer synthetic-openrouter-key"
            assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
            if failure == "connection":
                raise httpx.ConnectError("Synthetic connection failure", request=request)
            return httpx.Response(failure)
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-gemini-key"
        assert "store" not in json.loads(request.content)
        assert "synthetic-openrouter-key" not in request.content.decode()
        assert "synthetic-gemini-key" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"index":1,"value_ref":"ref"}'}}]})

    transport(monkeypatch, handle)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == 2
    assert len(approvals) == int(review)
    assert payload["model"] == "google/gemini-2.5-flash"
    assert runtime.manager.gateway.model == "google/gemini-2.5-flash"
    assert runtime.llm.model == "gemini-2.5-flash"
    next_payload = runtime.llm.prepare([{"role": "user", "content": "Continue"}], ReferenceInput, [])
    await runtime.llm.send(next_payload, digest(next_payload), [])
    assert len(calls) == 3
    assert calls[-1].url.host == "generativelanguage.googleapis.com"
    fresh = BrowserAgentRuntime(runtime.manager, dict(runtime.task))
    assert fresh.llm.base_url == "https://openrouter.ai/api/v1"
    assert fresh.llm.model == "google/gemini-2.5-flash"


@pytest.mark.parametrize("fallback_key,status,expected", [("", 400, 1), ("key", 500, 2), ("key", 307, 1)])
async def test_no_fallback_without_key_and_no_retry_loop(tmp_path, monkeypatch, fallback_key, status, expected):
    runtime = make_runtime(tmp_path)
    runtime.manager.gateway.fallback_api_key = fallback_key
    from privacy_guard.agent_llm import GuardedChatModel
    runtime.llm = GuardedChatModel(runtime)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status)

    transport(monkeypatch, handle)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    with pytest.raises(ValueError, match="HTTP"):
        await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == expected


async def test_denied_fallback_review_never_contacts_gemini(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    runtime.task["_review_text"] = True
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(400)

    async def deny(*args):
        raise PermissionError("Denied")

    runtime.manager.approval = deny
    transport(monkeypatch, handle)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    with pytest.raises(PermissionError):
        await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == 1


async def test_changed_fallback_settings_cannot_reuse_approval(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    runtime.manager.gateway.fallback_api_key = "changed-key"
    transport(monkeypatch, lambda request: pytest.fail("No request allowed"))
    with pytest.raises(ValueError, match="settings changed"):
        await runtime.llm.send(payload, digest(payload), [])


async def test_image_fallback_requires_new_destination_review_and_sends_updated_masks(tmp_path, monkeypatch):
    import base64
    import io

    from PIL import Image

    runtime = make_runtime(tmp_path)
    runtime.task["_image_review"] = "always"
    pixels = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(pixels, format="PNG")
    raw = base64.b64encode(pixels.getvalue()).decode()
    store = runtime.manager.gateway.image_store
    artifact = store.create(raw, {"complete": True, "viewport": {"width": 32, "height": 32},
                                  "regions": []})
    runtime.image_state = {"original": "data:image/png;base64," + raw, "artifact": artifact,
                           "messages": [{"role": "user", "content": "Inspect layout"}], "private": []}
    runtime._prepare_image_payload()
    primary = runtime.image_state["payload"]
    calls = []
    reviewed = []

    async def approve(task, generation, kind, title, payload):
        assert len(calls) == 1
        assert kind == "image"
        assert payload["destination"] == "https://generativelanguage.googleapis.com/v1beta/openai"
        assert payload["sha256"] != digest(primary)
        runtime.image_state["artifact"] = store.add_masks(artifact["id"], [{"x": 0, "y": 0, "width": 12, "height": 12}])
        runtime._prepare_image_payload()
        reviewed.append(runtime.image_state["payload"])

    async def check_target():
        runtime.check()

    runtime.check_target = check_target
    runtime.manager.approval = approve

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429)
        assert reviewed and json.loads(request.content) == reviewed[0]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{}'}}]})

    transport(monkeypatch, handle)
    await runtime.llm.send(primary, digest(primary), [], artifact)
    assert len(calls) == 2


async def test_fallback_key_is_encrypted_restored_and_never_returned(tmp_path):
    from privacy_guard.api import create_app

    app = create_app(tmp_path, browser=SimpleNamespace(invalidate=lambda: None), pairing_code="test-pair-code", testing=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/api/v1/pair", json={"code": "test-pair-code"})
        client.headers["Authorization"] = "Bearer " + response.json()["token"]
        password = "synthetic-long-passphrase"
        await client.post("/api/v1/vault/initialize", json={"passphrase": password})
        response = await client.post("/api/v1/settings", json={"fallback_api_key": "private-fallback-canary"})
        assert response.status_code == 200
        assert response.json()["fallback_configured"]
        assert "private-fallback-canary" not in response.text
        await client.post("/api/v1/vault/lock")
        assert not (await client.get("/api/v1/settings")).json()["fallback_configured"]
        await client.post("/api/v1/vault/unlock", json={"passphrase": password})
        assert (await client.get("/api/v1/settings")).json()["fallback_configured"]
        await client.post("/api/v1/settings", json={})
        assert (await client.get("/api/v1/settings")).json()["fallback_configured"]
        await client.post("/api/v1/settings", json={"fallback_api_key": None})
        assert not (await client.get("/api/v1/settings")).json()["fallback_configured"]
    for file in tmp_path.rglob("*"):
        if file.is_file():
            assert b"private-fallback-canary" not in file.read_bytes()


@pytest.mark.parametrize("legacy_base,legacy_model,expected_fallback", [
    ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-pro", "legacy-primary-key"),
    ("https://api.openai.com/v1", "gpt-4.1-mini", ""),
    ("https://openrouter.ai/api/v1", "google/gemini-2.5-flash", ""),
])
async def test_unlock_migrates_legacy_credentials_once(tmp_path, legacy_base, legacy_model, expected_fallback):
    from privacy_guard.api import create_app

    app = create_app(tmp_path, browser=SimpleNamespace(invalidate=lambda: None), pairing_code="test-pair-code", testing=True)
    password = "synthetic-long-passphrase"
    app.state.vault.initialize(password)
    app.state.vault.save_blob("provider_settings", {
        "mode": "remote", "model": legacy_model, "base_url": legacy_base,
        "api_key": "legacy-primary-key", "fallback_model": "gpt-4.1-mini",
        "fallback_api_key": "legacy-openai-key", "whisper_api_key": "separate-whisper-key",
    })
    app.state.vault.lock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/api/v1/pair", json={"code": "test-pair-code"})
        client.headers["Authorization"] = "Bearer " + response.json()["token"]
        for _ in range(2):
            response = await client.post("/api/v1/vault/unlock", json={"passphrase": password})
            assert response.status_code == 200
            gateway = app.state.gateway
            assert gateway.base_url == "https://openrouter.ai/api/v1"
            assert gateway.model == "google/gemini-2.5-flash"
            assert gateway.api_key == ("legacy-primary-key" if "openrouter.ai" in legacy_base else "")
            assert gateway.fallback_api_key == expected_fallback
            assert gateway.fallback_model == (legacy_model if expected_fallback else "gemini-2.5-flash")
            assert not hasattr(app.state.transcriber, "api_key")
            saved = app.state.vault.load_blob("provider_settings", {})
            assert saved["provider_settings_version"] == 2
            assert "legacy-openai-key" not in json.dumps(saved)
            assert "whisper_api_key" not in saved
            public = await client.get("/api/v1/settings")
            assert "legacy-primary-key" not in public.text
            assert "separate-whisper-key" not in public.text
            await client.post("/api/v1/vault/lock")
