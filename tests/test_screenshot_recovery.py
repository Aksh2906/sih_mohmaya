"""Screenshot recovery never repeats actions or bypasses exact-image review."""

import asyncio
import base64
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from privacy_guard.agent_runtime import ReferenceInput
from privacy_guard.browser import BrowserError
from privacy_guard.gateway import digest
from privacy_guard.image_review import image_review_decision
from privacy_guard.privacy_geometry import privacy_failure_codes
from privacy_guard.screenshots import SanitizedImageStore
from tests.test_agent_runtime import runtime_stub
from tests.test_visual_privacy import _geometry, _pixels, _png


def setup_runtime(tmp_path, geometry):
    runtime = runtime_stub(tmp_path)
    runtime.task.update(id="recovery", target_id="tab", _vision=True, events=[], step=16, mode="remote")
    runtime.manager.tasks["recovery"] = runtime.task
    runtime.task["_runtime"] = runtime
    runtime.raw = {"url": "https://example.test", "text": "Continue", "fields": [], "screenshot": _png()}
    runtime.check_target = AsyncMock()
    runtime.manager.browser = SimpleNamespace(
        observe=AsyncMock(side_effect=lambda *a, **kw: runtime.raw),
        capture_privacy=AsyncMock(return_value=(runtime.raw, geometry)),
        execute=AsyncMock(),
    )
    return runtime


async def wait_pending(runtime, work):
    async def wait():
        while not runtime.task.get("pending"):
            if work.done():
                await work
                pytest.fail("Planning finished before review")
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 6)


async def test_transient_scan_failure_recaptures_without_repeating_action(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry())
    runtime.manager.browser.capture_privacy.side_effect = [
        BrowserError("page_changed_during_observation"),
        (runtime.raw, _geometry(complete=False, warnings=["Wait for page fonts to finish loading."])),
        (runtime.raw, _geometry()),
    ]
    runtime.manager.approval = AsyncMock()
    state = await runtime.prepare_visual_request([], ReferenceInput)
    assert state["artifact"]["mask_report"]["geometry_validated"]
    assert runtime.manager.browser.capture_privacy.await_count == 3
    runtime.manager.browser.execute.assert_not_awaited()
    runtime.manager.approval.assert_not_awaited()
    assert "screenshot_recovery" not in runtime.task


async def test_incomplete_scan_waits_for_latest_masks_then_sends_dom_and_image(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry(complete=False, warnings=["Generated CSS media needs manual handling."]))
    runtime.llm.send = AsyncMock(return_value={"index": 1, "value_ref": "ref_test"})
    work = asyncio.create_task(runtime.llm.ainvoke([], ReferenceInput))
    try:
        await wait_pending(runtime, work)
        runtime.llm.send.assert_not_awaited()
        preview = runtime.image_preview()
        assert preview["report"]["recovery_codes"] == ["generated_media"]
        assert preview["report"]["requires_manual_review"]
        assert preview["report"]["automatic_masks"] == 0
        old_id = preview["approval_id"]
        new = runtime.update_masks(old_id, [{"x": 10, "y": 10, "width": 20, "height": 20}])
        with pytest.raises(ValueError, match="no longer valid"):
            runtime.manager.approve("recovery", old_id, True)
        runtime.llm.send.assert_not_awaited()
        runtime.manager.approve("recovery", new["approval_id"], True)
        await work
        payload, receipt, private, artifact = runtime.llm.send.call_args.args
        assert receipt == digest(payload)
        assert "<browser_state>" in json.dumps(payload)
        assert "private_reference_catalog" in json.dumps(payload)
        assert artifact["data_url"] in json.dumps(payload)
        assert runtime.raw["screenshot"] not in json.dumps(payload)
        with _pixels(artifact) as image:
            assert image.getpixel((15, 15)) == (0, 0, 0)
            assert image.getpixel((150, 80)) != (0, 0, 0)
        runtime.manager.browser.execute.assert_not_awaited()
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)


async def test_denied_recovery_never_sends(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry(complete=False))
    runtime.llm.send = AsyncMock()
    work = asyncio.create_task(runtime.llm.ainvoke([], ReferenceInput))
    await wait_pending(runtime, work)
    runtime.manager.approve("recovery", runtime.task["pending"]["id"], False)
    with pytest.raises(PermissionError, match="declined"):
        await work
    runtime.llm.send.assert_not_awaited()
    assert runtime.image_state is None


async def test_invalid_dimensions_remain_resumable_in_task_runner(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry(width=999))
    runtime.llm.send = AsyncMock()
    runtime.agent = SimpleNamespace(
        state=SimpleNamespace(paused=False, stopped=False, last_model_output=None, last_result=None),
        step=AsyncMock(),
    )
    async def step(*a):
        await runtime.prepare_visual_request([], ReferenceInput)
    runtime.agent.step.side_effect = step
    await runtime.manager.run(runtime.task, 0)
    assert runtime.task["status"] == "waiting_input"
    assert runtime.task["human_action"]["kind"] == "manual"
    assert runtime.task["request"] is None
    assert runtime.image_state is None
    runtime.llm.send.assert_not_awaited()
    # A later continuation can capture valid geometry; the task was not ended.
    runtime.manager.browser.capture_privacy.return_value = (runtime.raw, _geometry())
    runtime.manager.approval = AsyncMock()
    resumed = []
    async def resumed_step(*a):
        resumed.append(await runtime.prepare_visual_request([], ReferenceInput))
        runtime.task["status"] = "completed"
    runtime.agent.step.side_effect = resumed_step
    await runtime.manager.control("recovery", "resume")
    await runtime.manager.workers["recovery"]
    assert resumed[0]["artifact"]["mask_report"]["geometry_validated"]
    assert runtime.task["status"] == "completed"
    assert runtime.task["human_action"] is None


async def test_manual_candidate_cannot_be_sent_without_exact_review(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry(complete=False))
    artifact = runtime.manager.gateway.image_store.create_for_manual_review(runtime.raw["screenshot"], _geometry(complete=False))
    payload = runtime.llm.prepare([], ReferenceInput, [], artifact)
    with pytest.raises(PermissionError, match="requires approval"):
        await runtime.llm.send(payload, digest(payload), [], artifact)
    runtime.image_state = {"reviewed_hash": "stale"}
    with pytest.raises(PermissionError, match="requires approval"):
        await runtime.llm.send(payload, digest(payload), [], artifact)


@pytest.mark.parametrize("changes", [
    {"viewport": {"width": 10, "height": 100}},
    {"visual_scale": 2},
    {"regions": [{"x": float("nan"), "y": 0, "width": 10, "height": 10}]},
    {"warnings": ["Invalid privacy geometry."]},
])
def test_manual_review_cannot_override_invalid_geometry(changes):
    with pytest.raises(ValueError):
        SanitizedImageStore().create_for_manual_review(_png(), _geometry(complete=False, **changes))


def test_failure_logging_is_value_free_and_manual_policy_survives_added_masks():
    assert privacy_failure_codes({"warnings": ["SECRET CANARY", "DOM privacy collection timed out."]}) == ["collection_incomplete", "collection_timeout"]
    store = SanitizedImageStore()
    artifact = store.create_for_manual_review(_png(metadata=True), _geometry(complete=False))
    assert image_review_decision(artifact["mask_report"])["required"]
    artifact = store.add_masks(artifact["id"], [{"x": 1, "y": 1, "width": 5, "height": 5}])
    assert image_review_decision(artifact["mask_report"])["required"]
    assert artifact["mask_report"]["requires_manual_review"]
    with _pixels(artifact) as image:
        assert not image.info


async def test_cancel_during_retry_does_not_open_review_or_send(tmp_path):
    runtime = setup_runtime(tmp_path, _geometry(complete=False))
    runtime.manager.approval = AsyncMock()
    runtime.llm.send = AsyncMock()
    runtime.check_target.side_effect = [None, None, asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await runtime.llm.ainvoke([], ReferenceInput)
    runtime.manager.approval.assert_not_awaited()
    runtime.llm.send.assert_not_awaited()


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Set GUARD_BROWSER_TESTS=1 for Chromium")
@pytest.mark.parametrize("cause", ["animation", "generated_text"])
async def test_real_incomplete_dom_scan_becomes_pixel_aligned_manual_review(cause):
    from playwright.async_api import async_playwright

    from privacy_guard.browser import BrowserDriver
    from privacy_guard.config import DATA_DIR
    from privacy_guard.privacy_geometry import PRIVACY_REGIONS_JS

    executable = BrowserDriver(DATA_DIR, DATA_DIR)._browser_executable()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page(viewport={"width": 600, "height": 400}, device_scale_factor=2)
            css = ('@keyframes spin {to {transform:rotate(360deg)}} #spinner {animation:spin 5s infinite}'
                   if cause == "animation" else '#private::before {content:"PRIVATE_TEST_CANARY"}')
            await page.set_content('<style>' + css + '</style><p id="private">Private area</p><span id="spinner">Loading</span><button>Continue</button>')
            geometry = await page.evaluate(PRIVACY_REGIONS_JS, ["PRIVATE_TEST_CANARY"])
            assert geometry["complete"] is False
            assert "PRIVATE_TEST_CANARY" not in json.dumps(geometry)
            raw = base64.b64encode(await page.screenshot()).decode()
            store = SanitizedImageStore()
            candidate = store.create_for_manual_review(raw, geometry)
            assert candidate["width"] == 1200 and candidate["height"] == 800
            assert image_review_decision(candidate["mask_report"])["required"]
            masked = store.add_masks(candidate["id"], [{"x": 0, "y": 0, "width": 1200, "height": 120}])
            with _pixels(masked) as image:
                assert image.getpixel((30, 30)) == (0, 0, 0)
                assert image.getpixel((1100, 750)) == (255, 255, 255)
        finally:
            await browser.close()
