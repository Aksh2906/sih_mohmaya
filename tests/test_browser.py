"""Browser safety boundaries, plus an opt-in test against a real Chromium process."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from privacy_guard.browser import BrowserDriver, BrowserError, _origin
from privacy_guard.config import DATA_DIR


class _OwnedProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


async def test_shutdown_terminates_owned_process_when_cdp_cleanup_fails(tmp_path):
    driver = BrowserDriver(tmp_path, tmp_path)
    process = driver._process = _OwnedProcess()

    async def broken_reset():
        raise RuntimeError("Synthetic connection failure")

    stopped = []

    async def stop_event_bus(**_kwargs):
        stopped.append(True)

    driver._session = SimpleNamespace(reset=broken_reset, event_bus=SimpleNamespace(stop=stop_event_bus))
    await driver.shutdown()
    assert process.terminated
    assert stopped == [True]
    assert driver._process is None and driver._session is None
    assert not driver._lock.locked()


async def test_shutdown_cancellation_still_terminates_owned_process(tmp_path):
    driver = BrowserDriver(tmp_path, tmp_path)
    process = driver._process = _OwnedProcess()
    reset_started = asyncio.Event()

    async def hanging_reset():
        reset_started.set()
        await asyncio.Event().wait()

    driver._session = SimpleNamespace(reset=hanging_reset)
    shutdown = asyncio.create_task(driver.shutdown())
    await asyncio.wait_for(reset_started.wait(), timeout=1)
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    assert process.terminated
    assert driver._process is None and driver._session is None
    assert not driver._lock.locked()


async def test_shutdown_does_not_wait_forever_for_busy_driver(tmp_path, monkeypatch):
    monkeypatch.setattr("privacy_guard.browser.SHUTDOWN_LOCK_TIMEOUT", 0.01)
    driver = BrowserDriver(tmp_path, tmp_path)
    process = driver._process = _OwnedProcess()
    await driver._lock.acquire()
    try:
        await asyncio.wait_for(driver.shutdown(), timeout=0.5)
        assert process.terminated
        assert driver._lock.locked(), "Shutdown must not release a lock owned by an active operation"
    finally:
        driver._lock.release()


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/test",
        "javascript:alert(1)",
        "chrome://settings",
        "https://user:password@example.com",
        "data:text/html,form",
        "https://example.com:bad/form",
        "http://[invalid",
    ],
)
def test_non_web_or_credentialed_origins_are_rejected(url):
    with pytest.raises(BrowserError, match="unsupported_origin"):
        _origin(url)


def test_origin_normalization():
    assert _origin("https://EXAMPLE.com:443/form?q=private") == "https://example.com"
    assert _origin("http://127.0.0.1:8766/form") == "http://127.0.0.1:8766"
    assert _origin("http://[::1]:8766/form") == "http://[::1]:8766"


_FORM = b"""<!doctype html><html><head><title>Veil synthetic test</title></head>
<body><h1>Synthetic application</h1><form onsubmit="event.preventDefault()">
<label>Full name<input name="full_name" autocomplete="name"></label>
<label>State<select name="state"><option value="">Choose</option><option value="DL">Delhi</option></select></label>
<button type="button" onclick="document.getElementById('result').textContent='Reviewed'">Review form</button>
<button type="submit">Submit form</button><p id="result"></p></form></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_FORM)))
        self.end_headers()
        self.wfile.write(_FORM)

    def log_message(self, *args):
        pass


async def _stable_observation(driver, target_id):
    for _ in range(20):
        try:
            state = await driver.observe(target_id)
            if len(state["fields"]) == 4:
                return state
        except BrowserError as exc:
            if str(exc) not in {
                "observation_failed",
                "page_changed_during_observation",
                "unsupported_origin",
            }:
                raise
        await asyncio.sleep(0.1)
    raise AssertionError("Synthetic page did not become observable")


@pytest.mark.skipif(
    os.environ.get("GUARD_BROWSER_TESTS") != "1",
    reason="Set GUARD_BROWSER_TESTS=1 after installing the official browser",
)
async def test_real_browser_reference_fill_target_epoch_and_approval(tmp_path, monkeypatch, caplog):
    """Exercise Browser Use/CDP, no fake driver or remote model."""
    finder = BrowserDriver(DATA_DIR, tmp_path, headless=True)
    executable = finder._browser_executable()
    monkeypatch.setenv("GUARD_BROWSER_EXECUTABLE", str(executable))
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "Veil test",
                "version": "0.0.1",
                "permissions": ["activeTab"],
                "action": {"default_title": "Veil"},
            }
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    driver = BrowserDriver(tmp_path / "data", extension, headless=True)
    try:
        assert (await driver.launch())["running"]
        url = f"http://127.0.0.1:{server.server_port}/form"
        first = await driver.new_page(url)
        second = await driver.new_page(url)
        first_state = await _stable_observation(driver, first["target_id"])
        second_state = await _stable_observation(driver, second["target_id"])
        assert first_state["target_id"] != second_state["target_id"]
        assert base64.b64decode(first_state["screenshot"]).startswith(b"\x89PNG")
        assert first_state["width"] > 0 and first_state["height"] > 0
        secret = "SYNTHETIC_PRIVATE_NAME_93271"
        result = await driver.execute(
            first["target_id"],
            first_state,
            {"action": "input_ref", "element_index": 1, "value_ref": "profile.full_name"},
            secret,
        )
        assert result["ok"] and secret not in str(result)
        fresh = await _stable_observation(driver, first["target_id"])
        assert fresh["fields"][0]["value"] == secret
        other = await _stable_observation(driver, second["target_id"])
        assert other["fields"][0]["value"] == ""
        with pytest.raises(BrowserError, match="stale_observation"):
            await driver.execute(
                first["target_id"],
                first_state,
                {"action": "input_ref", "element_index": 1, "value_ref": "profile.full_name"},
                "WRONG",
            )
        await driver.execute(
            first["target_id"],
            fresh,
            {"action": "select_ref", "element_index": 2, "value_ref": "profile.state"},
            "Delhi",
        )
        fresh = await _stable_observation(driver, first["target_id"])
        assert fresh["fields"][1]["value"] == "DL"
        with pytest.raises(BrowserError, match="approval_required"):
            await driver.execute(first["target_id"], fresh, {"action": "click", "element_index": 3})
        assert (
            await driver.execute(
                first["target_id"], fresh, {"action": "click", "element_index": 3, "_approved": True}
            )
        )["ok"]
        fresh = await _stable_observation(driver, first["target_id"])
        assert "Reviewed" in fresh["text"]
        # The document mutates after a snapshot, while the target and URL stay identical.
        page, _ = await driver._context(first["target_id"])
        await page.evaluate("() => { document.querySelector('input').disabled = true; }")
        with pytest.raises(BrowserError, match="stale_observation"):
            await driver.execute(
                first["target_id"],
                fresh,
                {"action": "input_ref", "element_index": 1, "value_ref": "profile.full_name"},
                "WRONG",
            )
        fresh = await _stable_observation(driver, first["target_id"])
        driver.can_execute = lambda: False
        with pytest.raises(BrowserError, match="task_stopped"):
            await driver.execute(first["target_id"], fresh, {"action": "wait"})
        driver.invalidate()
        with pytest.raises(BrowserError, match="stale_observation"):
            await driver.execute(first["target_id"], fresh, {"action": "done"})
        assert secret not in caplog.text
    finally:
        await driver.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not driver.running


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Requires Chromium")
async def test_real_browser_frames_shadow_controls_and_ready_redirect(tmp_path, monkeypatch):
    class Pages(_Handler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/page")
                self.end_headers()
                return
            if self.path == "/frame":
                body = '<label>Frame name<input id="frame_name"></label>'
            else:
                body = '''<label>Main name<input id="main_name"></label>
                <iframe src="/frame"></iframe>
                <iframe sandbox srcdoc="<input value='UNINSPECTED_FRAME_CANARY'>"></iframe>
                <profile-box></profile-box>
                <a href="/next" target="_blank">Next page</a>
                <script>document.querySelector('profile-box').attachShadow({mode:'open'}).innerHTML = '<label>Shadow name<input id="shadow_name"></label>';</script>'''
            content = ('<!doctype html><html><body>' + body + '</body></html>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(content)

    finder = BrowserDriver(DATA_DIR, tmp_path, headless=True)
    monkeypatch.setenv("GUARD_BROWSER_EXECUTABLE", str(finder._browser_executable()))
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text(json.dumps({"manifest_version": 3, "name": "Test", "version": "1"}))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    driver = BrowserDriver(tmp_path / "browser", extension, headless=True)
    try:
        await driver.launch()
        origin = f"http://127.0.0.1:{server.server_port}"
        tab = await driver.new_page(origin + "/redirect")
        assert tab["url"] == origin + "/page"
        target = tab["target_id"]
        for _ in range(30):
            raw = await driver.observe(target, include_screenshot=False)
            if {f.get("id") for f in raw["fields"]} >= {"main_name", "frame_name", "shadow_name"}:
                break
            await asyncio.sleep(0.1)
        assert raw["unsupported_frames"] == 1
        assert "UNINSPECTED_FRAME_CANARY" not in json.dumps(raw)
        for identifier in ("main_name", "frame_name", "shadow_name"):
            raw = await driver.observe(target, include_screenshot=False)
            field = next(f for f in raw["fields"] if f.get("id") == identifier)
            await driver.execute(target, raw, {"action": "input_ref", "element_index": field["index"], "value_ref": "test"}, "SYNTHETIC_" + identifier)
            fresh = await driver.observe(target, include_screenshot=False)
            assert next(f for f in fresh["fields"] if f.get("id") == identifier)["value"] == "SYNTHETIC_" + identifier
        # Frame navigation invalidates previously approved actions.
        page, _ = await driver._context(target)
        await page.evaluate("() => document.querySelector('iframe').src = '/frame?changed=1'")
        with pytest.raises(BrowserError, match="stale_observation"):
            await driver.execute(target, fresh, {"action": "wait"})
        await asyncio.sleep(0.2)
        raw = await driver.observe(target, include_screenshot=False)
        link = next(f for f in raw["fields"] if f["tag"] == "a")
        before = len(await driver.tabs())
        await driver.execute(target, raw, {"action": "click", "element_index": link["index"], "_approved": True})
        ready = await driver._wait_for_ready(target)
        assert ready["url"] == origin + "/next"
        assert len(await driver.tabs()) == before
        page, _ = await driver._context(target)
        await page.evaluate("() => { const host = document.createElement('div'); document.body.append(host); host.attachShadow({mode:'closed'}).innerHTML = '<p>CLOSED_PRIVATE_CANARY</p>'; }")
        with pytest.raises(BrowserError, match="closed_shadow"):
            await driver.capture_privacy(target, [])
    finally:
        await driver.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Opt-in real Chromium test")
async def test_dropdown_identity_normalization_rejection_and_event_verification(tmp_path):
    from playwright.async_api import async_playwright

    from privacy_guard.browser import _EXECUTE_JS, _OBSERVE_JS

    executable = BrowserDriver(DATA_DIR, tmp_path, headless=True)._browser_executable()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page()
            await page.route("https://example.test/**", lambda route: route.fulfill(
                content_type="text/html", body="""<label>Application<select>
                <option value="none">Choose</option>
                <option value="49A">New PAN - Form No. 93 (Indian Citizen)</option>
                <option value="49A">New PAN - Form No. 94 (Indian Entity)</option>
                <option value="P">INDIVIDUAL</option>
                <option value="W">  White   Space </option>
                <optgroup disabled><option value="X">Disabled group</option></optgroup>
                <option value="D" disabled>Disabled option</option>
                </select></label><script>
                window.changes = 0;
                document.querySelector('select').addEventListener('change', () => window.changes++);
                </script>"""))
            await page.goto("https://example.test/form")

            async def execute(kind, value=None, **extra):
                state = await page.evaluate(_OBSERVE_JS)
                return await page.evaluate(
                    "args => (" + _EXECUTE_JS + ")(...args)",
                    [{"url": state["url"], "origin": "https://example.test", "epoch": state["epoch"]},
                     {"type": kind, "index": 1, **extra}, value],
                )

            result = await execute("select_ref", "49A")
            assert result["error"] == "option_ambiguous"
            assert await page.evaluate("window.changes") == 0
            result = await execute("select_ref", "New PAN - Form No. 94 (Indian Entity)")
            assert result["ok"]
            assert await page.locator("select").evaluate("e => e.selectedIndex") == 2
            assert (await execute("select_ref", " individual "))["ok"]
            assert await page.locator("select").input_value() == "P"
            assert (await execute("select_ref", "white\u00a0space"))["ok"]
            assert (await execute("select_ref", "nonexistent"))["error"] == "option_not_found"
            assert (await execute("select_ref", "X"))["error"] == "option_not_found"
            assert (await execute("select_option", option_index=5, approved=True))["error"] == "option_disabled"
            assert (await execute("select_option", option_index=6, approved=True))["error"] == "option_disabled"
            assert (await execute("select_option", option_index=1))["error"] == "approval_required"
            assert (await execute("select_option", option_index=1, approved=True))["ok"]
            assert await page.locator("select").evaluate("e => e.selectedIndex") == 1
            assert (await execute("select_option", option_index=2, approved=True))["ok"]
            assert await page.locator("select").evaluate("e => e.selectedIndex") == 2
            # A site's synchronous handler rejecting a choice must not report success.
            await page.locator("select").evaluate("e => e.onchange = () => { e.selectedIndex = 0; }")
            assert (await execute("select_option", option_index=2, approved=True))["error"] == "selection_not_accepted"
        finally:
            await browser.close()


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Opt-in real Chromium test")
async def test_uidai_identity_adapter_requires_exact_origin_login_and_unlabelled_control(tmp_path):
    from playwright.async_api import async_playwright

    from privacy_guard.browser import _OBSERVE_JS
    from privacy_guard.tasks import TaskManager

    executable = BrowserDriver(DATA_DIR, tmp_path, headless=True)._browser_executable()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page()
            # Fulfil all requests locally, including the official-looking origin.
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<body></body>"))
            cases = [
                ("https://tathya.uidai.gov.in", "Aadhaar - Login", '<input name="uid">', "Aadhaar number"),
                ("https://example.test", "Aadhaar - Login", '<input name="uid">', "uid"),
                ("https://tathya.uidai.gov.in.example.test", "Aadhaar - Login", '<input name="uid">', "uid"),
                ("http://tathya.uidai.gov.in", "Aadhaar - Login", '<input name="uid">', "uid"),
                ("https://tathya.uidai.gov.in", "Aadhaar profile", '<input name="uid">', "uid"),
                ("https://tathya.uidai.gov.in", "Login", '<input name="uid">', "uid"),
                ("https://tathya.uidai.gov.in", "Aadhaar - Login", '<input name="uid" aria-label="Address">', "Address"),
                ("https://tathya.uidai.gov.in", "Aadhaar - Login", '<input name="uid" placeholder="OTP">', "OTP"),
                ("https://tathya.uidai.gov.in", "Aadhaar - Login", '<input name="uid" type="password">', "uid"),
                ("https://tathya.uidai.gov.in", "Aadhaar - Login", '<input name="captcha">', "captcha"),
            ]
            for origin, title, markup, expected in cases:
                await page.goto(origin + "/fixture")
                await page.set_content(f"<title>{title}</title>{markup}")
                # Production navigation rejects public HTTP. This negative
                # fixture still needs an observer nonce in an insecure context.
                if origin.startswith("http:"):
                    await page.evaluate("() => { crypto.randomUUID = () => 'synthetic-http-nonce'; }")
                state = await page.evaluate(_OBSERVE_JS)
                field = state["fields"][0]
                assert field["label"] == expected, (origin, title, markup)
                assert TaskManager.compatible(field, {"field_type": "aadhaar"}) == (expected == "Aadhaar number")
        finally:
            await browser.close()


async def test_stop_during_cursor_motion_prevents_dispatch(tmp_path, monkeypatch):
    from privacy_guard.browser_cursor import CURSOR_MOVE_JS

    driver = BrowserDriver(tmp_path, tmp_path)
    observation = {"epoch": "snapshot", "target_id": "owned", "url": "https://example.test/",
                   "fields": [{"index": 1}]}
    driver._observations["owned"] = observation
    calls = []

    async def context(*args):
        return None, 1

    async def call(page, context, function, *args):
        calls.append(function)
        if function == CURSOR_MOVE_JS:
            driver.invalidate()
        return {"ok": True}

    monkeypatch.setattr(driver, "_context", context)
    monkeypatch.setattr(driver, "_call", call)
    with pytest.raises(BrowserError, match="task_stopped"):
        await driver.execute("owned", observation, {"action": "click", "element_index": 1, "_approved": True})
    assert calls == [CURSOR_MOVE_JS]


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Requires Chromium")
async def test_visible_cursor_does_not_block_clicks_or_invalidate_observation(tmp_path):
    from playwright.async_api import async_playwright

    from privacy_guard.browser import _EXECUTE_JS, _OBSERVE_JS
    from privacy_guard.browser_cursor import CURSOR_MOVE_JS
    from privacy_guard.privacy_geometry import PRIVACY_REGIONS_JS

    executable = BrowserDriver(DATA_DIR, tmp_path, headless=True)._browser_executable()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1000, "height": 650})
            await page.route("https://example.test/**", lambda route: route.fulfill(
                content_type="text/html", body="""<!doctype html><title>Agent cursor preview</title>
                <style>body{font:18px system-ui;background:#f1f5f9;padding:60px;color:#172033}
                main{background:white;padding:36px;border-radius:20px;max-width:660px}
                button{margin-top:30px;background:#172033;color:white;padding:18px 32px;border:0;border-radius:10px;font:inherit}
                </style><main><h1>Visible browser actions</h1><p>The blue cursor moves to the next control before the agent acts.</p>
                <button onclick="this.dataset.clicked='yes'">Compare options</button></main>"""))
            await page.goto("https://example.test/")
            state = await page.evaluate(_OBSERVE_JS)
            assert len(state["fields"]) == 1
            assert "Agent" not in state["text"]
            await page.evaluate(CURSOR_MOVE_JS, {"type": "click", "index": 1})
            await page.wait_for_timeout(550)
            pointer = await page.evaluate("""() => {
                const c = globalThis.__privacyGuardCursor;
                const r = c.pointer.getBoundingClientRect();
                return {x:r.x,y:r.y,target:document.elementFromPoint(c.x,c.y)?.tagName};
            }""")
            rect = state["fields"][0]["rect"]
            assert abs(pointer["x"] - (rect["x"] + rect["width"] / 2)) < 1
            assert abs(pointer["y"] - (rect["y"] + rect["height"] / 2)) < 1
            assert pointer["target"] == "BUTTON"
            geometry = await page.evaluate(PRIVACY_REGIONS_JS, [])
            assert geometry["complete"], geometry["warnings"]
            folder = os.environ.get("GUARD_UI_CAPTURE_DIR")
            if folder:
                from pathlib import Path
                Path(folder).mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(Path(folder) / "browser-agent-cursor.png"))
            result = await page.evaluate("args => (" + _EXECUTE_JS + ")(...args)", [
                {"url": state["url"], "origin": "https://example.test", "epoch": state["epoch"]},
                {"type": "click", "index": 1, "approved": True}, None])
            assert result["ok"], result
            assert await page.locator("button").get_attribute("data-clicked") == "yes"
            # A real page mutation during animation still invalidates the action.
            state = await page.evaluate(_OBSERVE_JS)
            await page.evaluate(CURSOR_MOVE_JS, {"type": "click", "index": 1})
            await page.locator("button").evaluate("e => e.textContent = 'Changed control'")
            await page.wait_for_timeout(550)
            result = await page.evaluate("args => (" + _EXECUTE_JS + ")(...args)", [
                {"url": state["url"], "origin": "https://example.test", "epoch": state["epoch"]},
                {"type": "click", "index": 1, "approved": True}, None])
            assert result["error"] == "stale_observation"
            # Re-observation must retain one cursor and a stable control map.
            assert len((await page.evaluate(_OBSERVE_JS))["fields"]) == 1
        finally:
            await browser.close()
