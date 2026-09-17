"""Exercise multi-key settings in the production dashboard with synthetic APIs."""
import json
import os
from urllib.parse import urlsplit

import pytest


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Requires Chromium and dashboard build")
def test_key_pool_settings_save_preserve_and_preflight(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    from privacy_guard.browser import BrowserDriver
    from privacy_guard.config import DATA_DIR, ROOT

    saved = {"mode": "remote", "model": "google/gemini-2.5-flash", "base_url": "https://openrouter.ai/api/v1",
             "fallback_model": "gemini-2.5-flash", "key_count": 1, "fallback_key_count": 1,
             "configured": True, "fallback_configured": True}
    posts = []

    def route_request(route):
        request = route.request
        path = urlsplit(request.url).path
        if path.startswith("/api/v1/"):
            if path == "/api/v1/settings":
                if request.method == "POST":
                    body = request.post_data_json
                    posts.append(body)
                    for field, count in (("api_keys", "key_count"), ("fallback_api_keys", "fallback_key_count")):
                        if field in body:
                            saved[count] = len(body[field])
                payload = saved
            elif path == "/api/v1/status":
                payload = {"vault": {"initialized": True, "unlocked": True},
                           "browser": {"connected": False}, "provider": saved, "task": None}
            elif path == "/api/v1/settings/check":
                payload = {"checks": [{"provider": "Gemini", "key_slot": 1, "ok": True, "status": 200,
                                        "category": "ready", "message": "Text and JSON check passed."}]}
            else:
                payload = {"records": [], "documents": [], "tasks": [], "tabs": []}
            route.fulfill(content_type="application/json", body=json.dumps(payload))
        else:
            target = ROOT / "apps/dashboard/dist" / (path.lstrip("/") or "index.html")
            route.fulfill(path=str(target))

    executable = BrowserDriver(DATA_DIR, tmp_path)._browser_executable()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.route("http://127.0.0.1:8765/**", route_request)
            page.add_init_script("sessionStorage.setItem('dpg.session', 'synthetic-session-token')")
            page.goto("http://127.0.0.1:8765/#page=settings")
            pools = page.locator(".api-key-pool")
            expect(pools).to_have_count(2)
            for index in (0, 1):
                pool = pools.nth(index)
                pool.locator("input").fill(f"synthetic-pool-{index}-one")
                pool.get_by_role("button", name="Add another key").click()
                pool.locator("input").nth(1).fill(f"synthetic-pool-{index}-two")
                assert pool.locator("input").nth(1).get_attribute("type") == "password"
            expect(page.get_by_role("button", name="Check saved keys")).to_be_disabled()
            page.get_by_role("button", name="Save settings", exact=True).click()
            expect(page.get_by_text("Provider settings saved locally.")).to_be_visible()
            assert posts[-1]["api_keys"] == ["synthetic-pool-0-one", "synthetic-pool-0-two"]
            assert posts[-1]["fallback_api_keys"] == ["synthetic-pool-1-one", "synthetic-pool-1-two"]
            for index in (0, 1):
                expect(pools.nth(index).locator("input")).to_have_value("")
                expect(pools.nth(index)).to_contain_text("2 saved keys")
            page.get_by_role("button", name="Save settings", exact=True).click()
            expect(page.get_by_role("button", name="Check saved keys")).to_be_enabled()
            assert "api_keys" not in posts[-1] and "fallback_api_keys" not in posts[-1]
            page.get_by_role("button", name="Check saved keys").click()
            expect(page.get_by_text("Text and JSON check passed.", exact=False)).to_be_visible()
            assert "synthetic-pool" not in page.locator("body").inner_text()
            folder = os.environ.get("GUARD_UI_CAPTURE_DIR")
            if folder:
                from pathlib import Path
                Path(folder).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(folder) / "multi-key-settings.png"), full_page=True)
        finally:
            browser.close()
