"""Real approval protocol with synthetic HTTP peers; never contact a provider."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from privacy_guard.agent_llm import GuardedChatModel
from privacy_guard.agent_runtime import ReferenceInput
from privacy_guard.review_classifier import REVIEW_INSTRUCTION
from tests.test_screenshot_recovery import setup_runtime, wait_pending
from tests.test_visual_privacy import _geometry


def setup(tmp_path, monkeypatch, handler, geometry=None):
    # Resolve Browser Use's runtime HTTP annotations before patching the client.
    from browser_use.agent.views import ActionResult  # noqa: F401

    runtime = setup_runtime(tmp_path, geometry or _geometry())
    runtime.llm = GuardedChatModel(runtime)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return runtime


def reply(answer, status=200):
    return httpx.Response(status, json={"choices": [{"message": {"content": answer}}]})


@pytest.mark.parametrize("answer,expected", [("true", True), ("false", False), (" false\n", False),
    ("False", True), ("false because safe", True), ('{"sensitive":false}', True), ("", True), (None, True), (False, True)])
async def test_same_model_credentials_and_strict_binary_protocol(tmp_path, monkeypatch, answer, expected):
    requests = []
    def handle(request):
        requests.append(request)
        return reply(answer)
    runtime = setup(tmp_path, monkeypatch, handle)
    candidate = runtime.llm.prepare([{"role": "user", "content": "Public service navigation"}], ReferenceInput, [])
    assert await runtime.llm.requires_review("payload", candidate, []) is expected
    assert len(requests) == 1
    request = requests[0]
    body = json.loads(request.content)
    assert str(request.url) == runtime.llm.base_url + "/chat/completions"
    assert body["model"] == runtime.llm.model
    assert request.headers["authorization"] == "Bearer " + runtime.llm._api_key
    assert body["messages"][0]["content"].startswith(REVIEW_INSTRUCTION)
    assert "response_format" not in body
    assert body["max_completion_tokens"] == 128
    assert runtime.task["metrics"]["approval_checks"] == 1


@pytest.mark.parametrize("failure", [401, 429, 500, "timeout", "malformed", "oversized"])
async def test_check_failures_require_hitl_without_provider_fallback(tmp_path, monkeypatch, failure):
    requests = []
    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private provider echo", request=request)
        if failure == "malformed":
            return httpx.Response(200, text="not JSON")
        if failure == "oversized":
            return reply("false" + " " * 20_000)
        return reply("false", failure)
    runtime = setup(tmp_path, monkeypatch, handle)
    candidate = runtime.llm.prepare([], ReferenceInput, [])
    assert await runtime.llm.requires_review("payload", candidate, [])
    assert len(requests) == 1
    assert "private provider echo" not in json.dumps(runtime.manager.public(runtime.task))


async def test_classifier_receives_one_redacted_image_and_all_candidate_text(tmp_path, monkeypatch):
    requests = []
    runtime = setup(tmp_path, monkeypatch, lambda request: requests.append(request) or reply("false"))
    artifact = runtime.manager.gateway.image_store.create(runtime.raw["screenshot"], _geometry(
        {"x": 0, "y": 0, "width": 20, "height": 20, "reason": "known_value"}))
    candidate = runtime.llm.prepare([
        {"role": "user", "content": "Prior context must also be checked"},
        {"role": "user", "content": "<browser_state>fresh</browser_state>"},
    ], ReferenceInput, [], artifact)
    assert not await runtime.llm.requires_review("payload", candidate, [], artifact)
    body = requests[0].content.decode()
    assert body.count(artifact["data_url"]) == 1
    assert runtime.raw["screenshot"] not in body
    assert "Prior context must also be checked" in body
    assert "browser_state" in body


async def test_private_text_or_incomplete_images_never_go_to_classifier(tmp_path, monkeypatch):
    runtime = setup(tmp_path, monkeypatch, lambda _: pytest.fail("No network allowed"))
    candidate = runtime.llm.prepare([], ReferenceInput, [])
    candidate["messages"].append({"role": "user", "content": "PRIVATE_CANARY_1298"})
    assert await runtime.llm.requires_review("payload", candidate, ["PRIVATE_CANARY_1298"])
    artifact = runtime.manager.gateway.image_store.create_for_manual_review(runtime.raw["screenshot"], _geometry(complete=False))
    candidate = runtime.llm.prepare([], ReferenceInput, [], artifact)
    assert await runtime.llm.requires_review("payload", candidate, [], artifact)


@pytest.mark.parametrize("vision", [True, False])
async def test_false_decision_continues_without_image_hitl(tmp_path, monkeypatch, vision):
    checks, main = [], []
    def handle(request):
        body = json.loads(request.content)
        if body["messages"][0]["content"].startswith(REVIEW_INSTRUCTION):
            checks.append(body)
            return reply("false")
        main.append(body)
        return reply('{"index":1,"value_ref":"ref_test"}')
    runtime = setup(tmp_path, monkeypatch, handle, _geometry({"x": 0, "y": 0, "width": 20, "height": 20, "reason": "known_value"}))
    runtime.task["_vision"] = vision
    runtime.manager.approval = AsyncMock()
    await runtime.llm.ainvoke([], ReferenceInput)
    assert len(checks) == len(main) == 1
    runtime.manager.approval.assert_not_awaited()
    if vision:
        assert runtime.task["image_review"]["required"] is False


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("vision", [True, False])
async def test_true_decision_waits_for_review_before_main_request(tmp_path, monkeypatch, approved, vision):
    main = []
    def handle(request):
        body = json.loads(request.content)
        if body["messages"][0]["content"].startswith(REVIEW_INSTRUCTION):
            return reply("true")
        main.append(body)
        return reply('{"index":1,"value_ref":"ref_test"}')
    runtime = setup(tmp_path, monkeypatch, handle)
    runtime.task["_vision"] = vision
    work = asyncio.create_task(runtime.llm.ainvoke([], ReferenceInput))
    try:
        await wait_pending(runtime, work)
        assert not main
        identifier = runtime.task["pending"]["id"]
        if vision:
            updated = runtime.update_masks(identifier, [{"x": 0, "y": 0, "width": 30, "height": 30}])
            with pytest.raises(ValueError):
                runtime.manager.approve("recovery", identifier, True)
            identifier = updated["approval_id"]
        else:
            assert runtime.task["pending"]["kind"] == "model"
        runtime.manager.approve("recovery", identifier, approved)
        if approved:
            await work
            assert len(main) == 1
            if vision:
                assert updated["redacted"] in json.dumps(main[0])
        else:
            with pytest.raises(PermissionError):
                await work
            assert not main
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize("answer", ["true", "false", "invalid"])
async def test_click_gate_uses_context_and_preserves_approval(tmp_path, monkeypatch, answer):
    requests = []
    runtime = setup(tmp_path, monkeypatch, lambda request: requests.append(request) or reply(answer))
    runtime.raw.update(text="Expand service details", fields=[{"index": 7, "tag": "button", "label": "More", "is_submit": False}])
    runtime.manager.browser.execute.return_value = {"ok": True}
    runtime.manager.approval = AsyncMock()
    result = await runtime.execute_element("click", 7)
    assert not result.error
    assert len(requests) == 1
    body = requests[0].content.decode()
    assert "Expand service details" in body and "More" in body and "Fill the form" in body
    assert runtime.manager.approval.await_count == (0 if answer == "false" else 1)
    runtime.manager.browser.execute.assert_awaited_once()


async def test_final_submission_is_not_released_by_false_classifier(tmp_path, monkeypatch):
    runtime = setup(tmp_path, monkeypatch, lambda _: pytest.fail("Final-submit safeguard runs first"))
    runtime.raw["fields"] = [{"index": 1, "tag": "button", "label": "Pay", "is_submit": True}]
    result = await runtime.execute_element("click", 1)
    assert result.error
    runtime.manager.browser.execute.assert_not_awaited()


async def test_cancellation_during_check_does_not_return_auto_permission(tmp_path, monkeypatch):
    def handle(request):
        runtime.manager.generation += 1
        return reply("false")
    runtime = setup(tmp_path, monkeypatch, handle)
    candidate = runtime.llm.prepare([], ReferenceInput, [])
    with pytest.raises(asyncio.CancelledError):
        await runtime.llm.requires_review("payload", candidate, [])


@pytest.mark.parametrize("change", ["settings", "candidate"])
async def test_changed_request_or_settings_cannot_reuse_false_decision(tmp_path, monkeypatch, change):
    def handle(request):
        if change == "settings":
            runtime.manager.gateway.model = "different-model"
        else:
            candidate["messages"].append({"role": "user", "content": "Different proposed context"})
        return reply("false")
    runtime = setup(tmp_path, monkeypatch, handle)
    candidate = runtime.llm.prepare([], ReferenceInput, [])
    assert await runtime.llm.requires_review("payload", candidate, [])
