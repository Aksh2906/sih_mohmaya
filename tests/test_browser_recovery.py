"""Dynamic pages remain navigable without relaxing private-entry checks."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from privacy_guard.browser import _EXECUTE_JS, _OBSERVE_JS, BrowserDriver, BrowserError
from privacy_guard.config import DATA_DIR
from privacy_guard.tasks import TaskManager


@pytest.mark.parametrize("code,expected", [
    ("page_changed_during_observation", "The page kept changing"),
    ("PRIVATE_EXCEPTION_CANARY", "The browser could not complete this step"),
])
async def test_task_explains_browser_failure_without_exposing_unknown_messages(code, expected, caplog):
    vault = SimpleNamespace(unlocked=True)
    browser = SimpleNamespace(observe=AsyncMock(side_effect=BrowserError(code)))
    manager = TaskManager(vault, browser, None)
    task = {"id": "test", "mode": "demo", "step": 0, "target_id": "tab", "events": []}
    await manager.run(task, 0)
    assert task["status"] == "failed"
    assert expected in task["error"]
    assert "PRIVATE_EXCEPTION_CANARY" not in str(task)
    assert all("PRIVATE_EXCEPTION_CANARY" not in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("screenshots,validations", [(False, []), (True, [False, True])])
async def test_observation_recovers_without_replaying_actions(tmp_path, screenshots, validations):
    driver = BrowserDriver(tmp_path, tmp_path)
    page = SimpleNamespace(screenshot=AsyncMock(return_value="pixels"))
    driver._context = AsyncMock(return_value=(page, 1))
    calls = []
    outcomes = iter(validations)

    async def call(_page, _context, function, *args):
        calls.append(function)
        if function == _OBSERVE_JS:
            return {"url": "https://example.test/", "epoch": str(len(calls)), "fields": []}
        assert args[1] == {"type": "wait"}
        return {"ok": next(outcomes)}

    driver._call = call
    raw = await driver.observe("tab", include_screenshot=screenshots)
    assert raw == driver._observations["tab"]
    assert calls.count(_OBSERVE_JS) == (2 if screenshots else 1)
    assert page.screenshot.await_count == (2 if screenshots else 0)


async def test_unstable_screenshots_exhaust_bounded_retries_and_clear_cache(tmp_path):
    driver = BrowserDriver(tmp_path, tmp_path)
    page = SimpleNamespace(screenshot=AsyncMock(return_value="mismatched pixels"))
    driver._context = AsyncMock(return_value=(page, 1))
    driver._observations["tab"] = {"epoch": "old"}

    async def call(_page, _context, function, *args):
        if function == _OBSERVE_JS:
            return {"url": "https://example.test/", "epoch": "new", "fields": []}
        return {"ok": False}

    driver._call = call
    with pytest.raises(BrowserError, match="page_changed_during_observation"):
        await driver.observe("tab")
    assert page.screenshot.await_count == 3
    assert "tab" not in driver._observations


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Opt-in real Chromium test")
async def test_live_updates_allow_only_the_same_approved_navigation_link(tmp_path):
    from playwright.async_api import async_playwright

    executable = BrowserDriver(DATA_DIR, tmp_path, headless=True)._browser_executable()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page()
            await page.route("https://example.test/**", lambda route: route.fulfill(
                content_type="text/html", body='''<p id="score">0</p>
                <a href="/latest">Latest match</a><input aria-label="PAN"><button>Submit</button>
                <script>window.clicks=0; document.querySelector('a').onclick=e=>{
                e.preventDefault(); window.clicks++};</script>'''))
            await page.goto("https://example.test/")

            async def observe():
                return await page.evaluate(_OBSERVE_JS)

            async def execute(raw, action):
                return await page.evaluate(
                    "([fn, raw, action]) => (0,eval)('(' + fn + ')')(raw,action,'PRIVATE_CANARY')",
                    [_EXECUTE_JS, {"url": raw["url"], "origin": "https://example.test",
                                   "epoch": raw["epoch"]}, action],
                )

            raw = await observe()
            await page.evaluate("() => {window.timer=setInterval(()=>score.textContent=String(Math.random()),10)}")
            await page.wait_for_timeout(80)
            # Neither private entry nor a submission gets the navigation exception.
            for action in ({"type": "input_ref", "index": 2},
                           {"type": "click", "index": 3, "approved": True}):
                assert (await execute(raw, action))["error"] == "stale_observation"
            assert (await execute(raw, {"type": "click", "index": 1}))["error"] == "approval_required"
            assert (await execute(raw, {"type": "click", "index": 1, "approved": True}))["ok"]
            assert await page.evaluate("window.clicks") == 1
            assert await page.locator("input").input_value() == ""
            await page.evaluate("clearInterval(window.timer)")

            # Changed destinations, labels, nodes and superseded snapshots are rejected.
            for mutation in (
                "document.querySelector('a').href='/different'",
                "document.querySelector('a').textContent='Different match'",
                "document.querySelector('a').outerHTML=document.querySelector('a').outerHTML",
                "document.head.innerHTML='<base href=\"https://other.test/\">'",
            ):
                raw = await observe()
                await page.evaluate("() => {" + mutation + "}")
                assert (await execute(raw, {"type": "click", "index": 1, "approved": True}))["error"] == "stale_observation"
            raw = await observe()
            await observe()
            assert (await execute(raw, {"type": "click", "index": 1, "approved": True}))["error"] == "stale_observation"
        finally:
            await browser.close()
