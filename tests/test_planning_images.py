"""Each action turn sends only a fresh, explicitly approved masked image."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from privacy_guard.agent_runtime import ReferenceInput
from privacy_guard.gateway import digest
from tests.test_agent_runtime import runtime_stub
from tests.test_visual_privacy import _geometry, _pixels, _png


async def test_every_planning_turn_waits_for_exact_masked_image_approval(tmp_path):
    runtime = runtime_stub(tmp_path)
    runtime.task.update(id="visual-task", target_id="tab", _vision=True, events=[], status="observing")
    runtime.raw = {"url":"https://example.test/form", "title":"Form", "text":"Public text", "fields":[], "screenshot":_png()}
    runtime.manager.browser = SimpleNamespace(observe=AsyncMock(side_effect=lambda *_, **_kw: runtime.raw), capture_privacy=AsyncMock(side_effect=lambda *_, **_kw:(runtime.raw,_geometry({"x":10,"y":10,"width":20,"height":20}))))
    runtime.check_target = AsyncMock()
    runtime.generation = 0
    calls, approvals = [], []
    released, waiting = asyncio.Event(), asyncio.Event()
    async def review(task, generation, kind, title, payload):
        assert kind == "image" and payload["sha256"] == digest(payload["request"])
        approvals.append(payload)
        assert len(calls) == len(approvals)-1
        waiting.set()
        await released.wait()
        # Simulate an added user mask while the original approval is pending.
        artifact = runtime.image_state["artifact"]
        runtime.image_state["artifact"] = runtime.manager.gateway.image_store.add_masks(artifact["id"],[{"x":80,"y":20,"width":20,"height":20}])
        runtime._prepare_image_payload()
    async def send(payload, receipt, private, artifact):
        assert receipt == digest(payload)
        assert artifact["data_url"] in json.dumps(payload)
        assert runtime.raw["screenshot"] not in json.dumps(payload)
        with _pixels(artifact) as image:
            assert image.getpixel((15,15)) == (0,0,0)
            assert image.getpixel((85,25)) == (0,0,0)
            assert image.getpixel((150,80)) != (0,0,0)
        calls.append(payload)
        return {"index":1,"value_ref":"ref_test"}
    runtime.manager.approval=review
    runtime.llm.send=send
    for _ in range(2):
        released.clear()
        waiting.clear()
        work=asyncio.create_task(runtime.llm.ainvoke([{"role":"user","content":"Plan the next action"}],ReferenceInput))
        waiter = asyncio.create_task(waiting.wait())
        await asyncio.wait({work, waiter}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if work.done():
            await work
        await asyncio.wait_for(waiter, 5)
        assert not work.done()
        released.set()
        await work
        assert runtime.image_state is None
    assert len(calls)==len(approvals)==2
    assert calls[0] != approvals[0]["request"]  # Added masks replaced the request.


async def test_missing_geometry_pauses_planning_without_text_fallback(tmp_path):
    from privacy_guard.agent_runtime import ScreenshotRecoveryPending
    runtime=runtime_stub(tmp_path)
    runtime.task.update(id="task",target_id="tab",_vision=True,events=[])
    runtime.raw={"url":"https://example.test","fields":[],"screenshot":_png()}
    runtime.check_target=AsyncMock()
    runtime.manager.browser=SimpleNamespace(observe=AsyncMock(return_value=runtime.raw),capture_privacy=AsyncMock(return_value=(runtime.raw,{"complete":False})))
    runtime.llm.send=AsyncMock()
    with pytest.raises(ScreenshotRecoveryPending):
        await runtime.llm.ainvoke([],ReferenceInput)
    assert runtime.task["status"] == "waiting_input"
    assert runtime.task["human_action"]["kind"] == "manual"
    runtime.llm.send.assert_not_awaited()


async def test_extension_can_review_mask_and_approve_images_but_cannot_read_vault(tmp_path, monkeypatch):
    from test_workflow import client_for
    app, _, client = await client_for(tmp_path)
    preview={"approval_id":"current","redacted":"data:image/png;base64,synthetic","original":"data:image/png;base64,synthetic"}
    monkeypatch.setattr(app.state.manager,"image_preview",lambda task:preview)
    masks=[]
    monkeypatch.setattr(app.state.manager,"update_masks",lambda *args: masks.append(args) or {**preview,"approval_id":"new"})
    approved=[]
    monkeypatch.setattr(app.state.manager,"approve",lambda *args: approved.append(args) or {"ok":True})
    async with client:
        origin="chrome-extension://"+"a"*32
        pair=await client.post("/api/v1/pair",json={"code":"test-code-123"},headers={"Origin":origin})
        headers={"Origin":origin,"Authorization":"Bearer "+pair.json()["token"]}
        assert (await client.get("/api/v1/records",headers=headers)).status_code==403
        response=await client.get("/api/v1/tasks/task/image-preview",headers=headers)
        assert response.status_code==200 and response.json()==preview
        response=await client.post("/api/v1/tasks/task/masks",json={"approval_id":"current","masks":[{"x":0,"y":0,"width":10,"height":10}]},headers=headers)
        assert response.status_code==200 and response.json()["approval_id"]=="new"
        assert masks[0][1]=="current"
        response=await client.post("/api/v1/tasks/task/approve",json={"approval_id":"new","approved":True},headers=headers)
        assert response.status_code==200 and approved==[("task","new",True)]
        await client.post("/api/v1/vault/lock")
        assert (await client.get("/api/v1/tasks/task/image-preview",headers=headers)).status_code==423


@pytest.mark.parametrize("regions", [[], [{"x":10,"y":10,"width":20,"height":20,"reason":"empty_private_field"}], [{"x":10,"y":10,"width":20,"height":20,"reason":"uninspected_media"}]])
async def test_routine_image_keeps_fresh_dom_and_can_return_action_without_review(tmp_path, regions):
    runtime=runtime_stub(tmp_path)
    runtime.task.update(id="routine-task",target_id="tab",_vision=True,status="observing",events=[])
    runtime.raw={"url":"https://example.test/form","title":"Service","text":"Download Aadhaar", "screenshot":_png(),
                 "fields":[{"index":7,"label":"Download Aadhaar","tag":"button","input_type":"button","value":"","rect":{}}]}
    runtime.check_target=AsyncMock()
    runtime.manager.browser=SimpleNamespace(observe=AsyncMock(return_value=runtime.raw),capture_privacy=AsyncMock(return_value=(runtime.raw,_geometry(*regions))))
    runtime.manager.approval=AsyncMock()
    sent=[]
    async def send(payload,receipt,private,artifact):
        encoded=json.dumps(payload,ensure_ascii=False)
        assert "Download Aadhaar" in encoded and '\\"index\\": 7' in encoded
        assert "private_reference_catalog" in encoded and "<browser_state>" in encoded
        assert artifact["data_url"] in encoded
        assert receipt==digest(payload)
        sent.append(payload)
        return {"index":7,"value_ref":"ref_test"}
    runtime.llm.send=send
    result=await runtime.llm.ainvoke([{"role":"user","content":"Choose the next action"}],ReferenceInput)
    assert result.completion.index==7 and len(sent)==1
    runtime.manager.approval.assert_not_awaited()
    assert runtime.task["image_review"]["required"] is False


def test_personal_redactions_trigger_review_and_always_mode_is_available():
    from privacy_guard.image_review import image_review_decision
    for reason in ["known_value","labeled_private_text","password_field","populated_field","email_pattern","identifier_pattern","private_region","manual_mask"]:
        decision=image_review_decision({"masks":[{"reason":reason}]})
        assert decision["required"] and decision["sensitive_reasons"]==[reason]
    assert image_review_decision({"masks":[]},"always")["required"]
