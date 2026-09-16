"""URL selection precedes navigation and shares the guarded provider transport."""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from privacy_guard.agent_llm import GuardedChatModel
from privacy_guard.site_selection import validate_website_url
from tests.test_provider_fallback import make_runtime, transport


@pytest.mark.parametrize("result", [
    {}, {"url": None}, {"url": 5}, {"url": "https://example.com/", "extra": True},
    {"url": "javascript:alert(1)"}, {"url": "http://example.com/"},
    {"url": "https://localhost/"}, {"url": "https://127.0.0.1/"},
    {"url": "https://[::1]/"}, {"url": "https://foo.local/"},
    {"url": "https://user:password@example.com/"},
    {"url": "https://example.com:8443/"}, {"url": "https://example.com/checkout"},
    {"url": "https://example.com/?secret=abc"}, {"url": "https://example.com/#token"},
    {"url": "https://www.google.com/"}, {"url": "https://www.google.co.in/"},
    {"url": "https://www.bing.com/"}, {"url": "https://duckduckgo.com/"},
    {"url": "https://PRIVATE_CANARY.com/"},
])
def test_invalid_model_urls_do_not_become_destinations(result):
    with pytest.raises(ValueError, match="Enter the website URL"):
        validate_website_url(result, ["PRIVATE_CANARY"])


def test_selected_homepage_is_normalized():
    assert validate_website_url({"url": "https://WWW.AMAZON.IN"}, []) == "https://www.amazon.in/"


def selection_runtime(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.task.update(target_id=None, _origin="", destination="", _discover_site=True,
                        _goal="Find headphones on Amazon PRIVATE_CANARY", _review_text=False)
    runtime.manager.vault.put_record("Private name", "text", "PRIVATE_CANARY")
    runtime.manager.browser = SimpleNamespace(
        status=lambda: {"running": False}, launch=AsyncMock(),
        new_page=AsyncMock(return_value={"target_id": "destination-tab", "url": "https://www.amazon.in/"}),
    )
    return runtime


@pytest.mark.parametrize("fallback", [False, True])
async def test_model_returns_url_before_browser_opens_and_fallback_is_retained(tmp_path, monkeypatch, fallback):
    runtime = selection_runtime(tmp_path)
    runtime.task["_review_text"] = True
    runtime.manager.approval = AsyncMock()
    requests = []

    def handle(request):
        runtime.manager.browser.launch.assert_not_awaited()
        runtime.manager.browser.new_page.assert_not_awaited()
        requests.append(request)
        assert "PRIVATE_CANARY" not in request.content.decode()
        assert "browser_state" not in request.content.decode()
        if fallback and request.url.host == "openrouter.ai":
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"url":"https://www.amazon.in/"}'}}]})

    transport(monkeypatch, handle)
    await runtime.open_starting_website()
    assert len(requests) == (2 if fallback else 1)
    assert runtime.manager.approval.await_count == (2 if fallback else 1)
    runtime.manager.browser.launch.assert_awaited_once()
    runtime.manager.browser.new_page.assert_awaited_once_with("https://www.amazon.in/")
    assert runtime.task["target_id"] == "destination-tab"
    assert runtime.task["_origin"] == "https://www.amazon.in"
    assert runtime.task["destination"] == "https://www.amazon.in"
    assert runtime.task["metrics"]["model_calls"] == 1
    if fallback:
        assert runtime.llm.model == "gemini-2.5-flash"


@pytest.mark.parametrize("reply,stop", [({"url": None}, False), ({"url": "https://www.google.com/"}, False),
                                        ({"url": "https://www.amazon.in/"}, True)])
async def test_invalid_output_or_stop_never_opens_browser(tmp_path, monkeypatch, reply, stop):
    runtime = selection_runtime(tmp_path)

    def handle(request):
        if stop:
            runtime.manager.generation += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    transport(monkeypatch, handle)
    with pytest.raises(asyncio.CancelledError if stop else ValueError):
        await runtime.open_starting_website()
    runtime.manager.browser.launch.assert_not_awaited()
    runtime.manager.browser.new_page.assert_not_awaited()


async def test_selection_rotates_primary_keys_before_navigation(tmp_path, monkeypatch):
    runtime = selection_runtime(tmp_path)
    runtime.manager.gateway.api_keys = ["first-key-canary", "second-key-canary"]
    runtime.llm = GuardedChatModel(runtime)
    calls = []

    def handle(request):
        calls.append(request.headers["authorization"])
        if len(calls) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"url":"https://www.amazon.in/"}'}}]})

    transport(monkeypatch, handle)
    await runtime.open_starting_website()
    assert calls == ["Bearer first-key-canary", "Bearer second-key-canary"]
    runtime.manager.browser.new_page.assert_awaited_once_with("https://www.amazon.in/")


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Requires local Chromium and internet")
async def test_selected_url_opens_in_real_browser_without_intermediate_page(tmp_path, monkeypatch):
    from privacy_guard.agent_runtime import quiet_browser_use
    from privacy_guard.browser import BrowserDriver
    from privacy_guard.config import DATA_DIR, ROOT

    quiet_browser_use()
    import browser_use.browser.profile as profile

    monkeypatch.setattr(profile, "get_display_size", lambda: None)
    runtime = selection_runtime(tmp_path / "vault")
    runtime.task["_goal"] = "Open the Example Domain demonstration website"
    extension = ROOT / "apps/extension"
    executable = BrowserDriver(DATA_DIR, extension)._browser_executable()
    driver = BrowserDriver(tmp_path / "browser", extension, headless=True)
    monkeypatch.setattr(driver, "_browser_executable", lambda: executable)
    runtime.manager.browser = driver
    runtime.llm.send = AsyncMock(return_value={"url": "https://example.com/"})
    try:
        await runtime.open_starting_website()
        await runtime.check_target()
        observed = await driver.observe(runtime.task["target_id"], include_screenshot=False)
        assert observed["url"] == "https://example.com/"
        assert observed["title"] == "Example Domain"
        pages = await driver.tabs()
        assert [p["url"] for p in pages if p["url"] != "about:blank"] == ["https://example.com/"]
    finally:
        await driver.shutdown()
