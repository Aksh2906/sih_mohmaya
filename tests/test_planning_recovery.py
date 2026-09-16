"""Stalled planning escalates to reviewed vision, then a bounded human handoff."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from privacy_guard.agent_llm import UnusableModelResponse
from privacy_guard.agent_runtime import ReferenceInput
from tests.test_screenshot_recovery import setup_runtime, wait_pending
from tests.test_visual_privacy import _geometry


class Actions(BaseModel):
    action: list[dict]


def attach_agent(runtime, step):
    runtime.agent = SimpleNamespace(
        state=SimpleNamespace(paused=False, stopped=False, last_result=[], last_model_output=None),
        step=step,
    )


async def test_text_loop_escalates_to_review_even_with_no_sensitive_masks(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry())
    runtime.task["_vision"] = False
    runtime.llm.send = AsyncMock(return_value={"index": 1, "value_ref": "ref_test"})
    for _ in range(2):
        await runtime.llm.ainvoke([], ReferenceInput)
    assert all(call.args[3] is None for call in runtime.llm.send.call_args_list)
    work = asyncio.create_task(runtime.llm.ainvoke([], ReferenceInput))
    try:
        await wait_pending(runtime, work)
        assert runtime.llm.send.await_count == 2
        assert runtime.task["image_review"]["required"]
        preview = runtime.image_preview()
        assert preview["report"]["automatic_masks"] == 0
        assert "Agent needs help" in runtime.task["pending"]["title"]
        runtime.manager.approve("recovery", preview["approval_id"], True)
        await work
        payload, _, _, artifact = runtime.llm.send.call_args.args
        assert artifact["data_url"] in json.dumps(payload)
        assert "<browser_state>" in json.dumps(payload)
        assert "private_reference_catalog" in json.dumps(payload)
        assert "do not repeat ineffective" in json.dumps(payload)
        assert runtime.task["_vision"] is False
        runtime.manager.browser.execute.assert_not_awaited()
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize("first_response", [{"action": []}, {"wrong_key": True}, UnusableModelResponse("Invalid JSON")])
async def test_invalid_or_empty_response_recovers_before_any_action(tmp_path, first_response):
    runtime = setup_runtime(tmp_path, _geometry())
    runtime.task["_vision"] = False
    runtime.llm.send = AsyncMock(side_effect=[first_response, {"action": [{"wait": {}}]}])
    performed = []
    async def step(*_):
        response = await runtime.llm.ainvoke([], Actions)
        performed.extend(response.completion.action)
        runtime.task["status"] = "completed"
    attach_agent(runtime, step)
    work = asyncio.create_task(runtime.manager.run(runtime.task, 0))
    try:
        await wait_pending(runtime, work)
        assert performed == []
        assert runtime.task["planning_recovery"]["reason"] == "unusable_model_response"
        runtime.manager.approve("recovery", runtime.task["pending"]["id"], True)
        await work
        assert performed == [{"wait": {}}]
        assert runtime.task["status"] == "completed"
        assert runtime.llm.send.await_count == 2
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)


async def test_continuing_bad_responses_pause_after_two_reviews(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry())
    runtime.task["_vision"] = False
    runtime.manager.approval = AsyncMock()
    runtime.llm.send = AsyncMock(return_value={"action": []})
    async def step(*_):
        await runtime.llm.ainvoke([], Actions)
    attach_agent(runtime, step)
    await runtime.manager.run(runtime.task, 0)
    assert runtime.llm.send.await_count == 3
    assert runtime.manager.approval.await_count == 2
    assert runtime.task["status"] == "waiting_input"
    assert runtime.task["human_action"]["kind"] == "review"
    assert runtime.visual_recovery_attempts == 2
    runtime.manager.browser.execute.assert_not_awaited()


async def test_repeated_action_errors_escalate_even_when_page_changes(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry())
    runtime.task["_vision"] = False
    runtime.manager.approval = AsyncMock()
    runtime.llm.send = AsyncMock(return_value={"index": 1, "value_ref": "ref_test"})
    steps = 0
    async def step(*_):
        nonlocal steps
        steps += 1
        runtime.raw["text"] = f"Changed page {steps}"
        await runtime.llm.ainvoke([], ReferenceInput)
        runtime.agent.state.last_result = [SimpleNamespace(error="No supported target")]
        if steps == 4:
            runtime.task["status"] = "completed"
    attach_agent(runtime, step)
    await runtime.manager.run(runtime.task, 0)
    assert runtime.task["planning_recovery"]["reason"] == "repeated_action_errors"
    assert runtime.llm.send.call_args.args[3] is not None
    runtime.manager.approval.assert_awaited_once()


async def test_changed_fields_do_not_trigger_false_stagnation(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry())
    runtime.task["_vision"] = False
    runtime.llm.send = AsyncMock(return_value={"index": 1, "value_ref": "ref_test"})
    runtime.manager.approval = AsyncMock()
    for n in range(5):
        runtime.raw["fields"] = [{"index": 1, "label": "Field", "value": str(n)}]
        await runtime.llm.ainvoke([], ReferenceInput)
    assert runtime.visual_recovery_attempts == 0
    runtime.manager.approval.assert_not_awaited()
    runtime.manager.browser.capture_privacy.assert_not_awaited()


async def test_security_failure_is_not_retried_as_planning_failure(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry())
    runtime.task["_vision"] = False
    runtime.llm.send = AsyncMock(side_effect=PermissionError("Privacy check blocked this request"))
    async def step(*_):
        await runtime.llm.ainvoke([], Actions)
    attach_agent(runtime, step)
    await runtime.manager.run(runtime.task, 0)
    assert runtime.task["status"] == "blocked"
    assert runtime.llm.send.await_count == 1
    assert runtime.visual_recovery_attempts == 0


@pytest.mark.parametrize("cycle", [False, True])
async def test_repeated_actions_detected_despite_changing_page_text(tmp_path, cycle):
    runtime = setup_runtime(tmp_path, _geometry())
    runtime.task["_vision"] = False
    runtime.manager.approval = AsyncMock()
    runtime.llm.send = AsyncMock(return_value={"index": 1, "value_ref": "ref_test"})
    steps = 0
    limit = 7 if cycle else 4
    async def step(*_):
        nonlocal steps
        steps += 1
        runtime.raw["text"] = f"Clock tick {steps}"
        await runtime.llm.ainvoke([], ReferenceInput)
        runtime.agent.state.last_model_output = SimpleNamespace(
            action=[{"wait": {"seconds": 1 + (steps % 2 if cycle else 0)}}],
            model_dump=lambda: {},
        )
        if steps == limit:
            runtime.task["status"] = "completed"
    attach_agent(runtime, step)
    await runtime.manager.run(runtime.task, 0)
    assert runtime.task["planning_recovery"]["reason"] == "repeated_actions"
    assert runtime.llm.send.call_args.args[3] is not None
    runtime.manager.approval.assert_awaited_once()
