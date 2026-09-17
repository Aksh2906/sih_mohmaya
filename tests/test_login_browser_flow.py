"""An isolated local login handoff through the real Browser Use runtime."""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from privacy_guard.agent_runtime import quiet_browser_use
from privacy_guard.browser import BrowserDriver
from privacy_guard.config import DATA_DIR, ROOT
from privacy_guard.gateway import ModelGateway
from privacy_guard.models import TaskRequest
from privacy_guard.tasks import TaskManager
from privacy_guard.vault import Vault


class LoginFixture(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in {"/login/otp", "/login/floating"}:
            identity = ('<div><input id="identity" name="uid" autocomplete="off"><span>Enter Aadhaar Number</span></div>'
                        if self.path == "/login/floating" else '<input id="identity" placeholder="Enter Aadhaar Number">')
            body = ('<h1>Login to Aadhaar via OTP</h1>' + identity +
                    '<input id="captcha" placeholder="Enter Captcha">'
                    '<button type="button" onclick="location.href=\'/verify\'">Login With OTP</button>')
        elif self.path == "/verify":
            body = ('<h1>Verify your identity</h1>'
                    '<input id="otp" autocomplete="one-time-code" placeholder="Code">'
                    '<button type="button" onclick="location.href=\'/download/en\'">Verify OTP</button>')
        else:
            body = (
            '<h1>Login</h1><label>Aadhaar number<input id="identity"></label>'
            '<label>Password<input id="password" type="password"></label><label>Captcha<input id="captcha"></label>'
            '<button type="button" onclick="location.href=\'/download/en\'">Complete fictional login</button>'
            if self.path == "/login" else '<h1>Download Aadhaar</h1><p>English download service</p>'
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(("<!doctype html><html><body>" + body + "</body></html>").encode())

    def log_message(self, *_args):
        pass


async def approve_until_idle(manager, task_id):
    for _ in range(600):
        task = manager.tasks[task_id]
        if task.get("pending"):
            manager.approve(task_id, task["pending"]["id"], True)
        if manager.workers[task_id].done():
            break
        await asyncio.sleep(0.1)
    await asyncio.wait_for(manager.workers[task_id], 5)


async def human_click(driver, target, label):
    # Simulate a person's DOM click, independent of the agent's observation epoch.
    async with driver._lock:
        page, context = await driver._context(target, fresh=True)
        await driver._call(page, context, """function(label) {
          [...document.querySelectorAll('button')].find(b => b.textContent === label).click();
          return {ok:true};
        }""", label)


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Requires isolated Chromium")
@pytest.mark.parametrize("login_path", ["/login", "/login/otp", "/login/floating"])
async def test_login_handoff_resumes_to_verified_destination_in_same_browser_tab(tmp_path, monkeypatch, login_path):
    otp_login = login_path != "/login"
    quiet_browser_use()
    import browser_use.browser.profile as profile

    monkeypatch.setattr(profile, "get_display_size", lambda: None)
    # Load native HTTP annotations before replacing the transport class.
    from browser_use import BrowserSession  # noqa: F401
    executable = BrowserDriver(DATA_DIR, ROOT / "apps/extension")._browser_executable()
    driver = BrowserDriver(tmp_path / "browser", ROOT / "apps/extension", headless=True)
    monkeypatch.setattr(driver, "_browser_executable", lambda: executable)
    server = ThreadingHTTPServer(("127.0.0.1", 0), LoginFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    vault = Vault(tmp_path / "vault")
    vault.initialize("synthetic-login-test-passphrase")
    vault.put_record("Aadhaar", "Aadhaar Number" if otp_login else "aadhaar", "123412341234")
    vault.put_record("Password", "password", "SYNTHETIC-LOGIN-SECRET-483")
    gateway = ModelGateway()
    gateway.model, gateway.api_key = "fixture-model", "synthetic-model-key"
    manager = TaskManager(vault, driver, gateway, demo_port=server.server_port)
    manager.login_poll_seconds = 0.1
    origin = f"http://127.0.0.1:{server.server_port}"
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert "123412341234" not in json.dumps(payload)
        assert "SYNTHETIC-LOGIN-SECRET-483" not in json.dumps(payload)
        task = next(iter(manager.tasks.values()))
        if '"title": "CompletionCheck"' in payload["messages"][0]["content"]:
            response = {"achieved": True, "reason": "The English download page is visible.", "human_action": None}
        else:
            at_destination = task["_runtime"].raw["url"].endswith("/download/en")
            if not task.get("plan") or (at_destination and task["plan"][0]["status"] != "done"):
                action = {"update_plan": {"steps": [{
                    "title": "Open the English Download Aadhaar page",
                    "success_criteria": "Download Aadhaar heading and English service text visible",
                    "status": "done" if at_destination else "in_progress", "source_ids": [],
                }]}}
            else:
                # Intentionally try to stop on login: the guard must hand off.
                action = {"done": {"text": "Reached the requested page", "success": True}}
            response = {"evaluation_previous_goal": "Read current page", "memory": "Keep the original goal",
                        "next_goal": "Reach the service", "action": [action]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})

    real_client = httpx.AsyncClient

    class FixtureClient(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr("privacy_guard.agent_llm.httpx.AsyncClient", FixtureClient)
    try:
        task = await manager.start(TaskRequest(goal="Open the Download Aadhaar page in English",
                                              start_url=origin + login_path, mode="remote"))
        task_id, target = task["id"], task["target_id"]
        await approve_until_idle(manager, task_id)
        task = manager.tasks[task_id]
        assert task["status"] == "waiting_input", manager.public(task)
        assert task["human_action"]["kind"] == "login"
        assert calls == []  # Fill and handoff are local; the model cannot skip them.
        raw = await driver.observe(target)
        assert next(f for f in raw["fields"] if f.get("id") == "identity")["value"] == "123412341234"
        if not otp_login:
            assert next(f for f in raw["fields"] if f.get("id") == "password")["filled"] is True
        assert not next(f for f in raw["fields"] if f.get("id") == "captcha")["value"]
        # Simulate the human's button press on the local fictional site.
        await human_click(driver, target, "Login With OTP" if otp_login else "Complete fictional login")
        await driver._wait_for_ready(target)
        if otp_login:
            # The intermediate OTP page must remain completely under human control.
            await asyncio.sleep(0.4)
            assert task["status"] == "waiting_input"
            assert calls == []
            raw = await driver.observe(target)
            assert not next(f for f in raw["fields"] if f.get("id") == "otp")["value"]
            await human_click(driver, target, "Verify OTP")
            await driver._wait_for_ready(target)
            # No dashboard Continue click: the local monitor resumes the same task.
            for _ in range(100):
                if task["status"] != "waiting_input":
                    break
                await asyncio.sleep(0.1)
            assert task["status"] != "waiting_input"
        else:
            await manager.control(task_id, "resume")
        await approve_until_idle(manager, task_id)
        assert task["status"] == "completed", manager.public(task)
        assert task["target_id"] == target
        assert task["completion_check"]["achieved"] is True
        assert (await driver.observe(target))["url"] == origin + "/download/en"
        assert len(calls) == 3
    finally:
        await manager.stop_all()
        await driver.shutdown()
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Requires isolated Chromium")
async def test_floating_aadhaar_label_requires_unambiguous_visible_evidence(tmp_path):
    from playwright.async_api import async_playwright

    from privacy_guard.browser import _OBSERVE_JS

    executable = BrowserDriver(DATA_DIR, ROOT / "apps/extension")._browser_executable()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page()
            await page.route("https://example.test/**", lambda route: route.fulfill(
                content_type="text/html", body="<!doctype html><html><body></body></html>"))
            await page.goto("https://example.test/login")
            await page.set_content('''
                <div><input id="floating" name="uid"><span>Enter Aadhaar Number</span></div>
                <div><input id="explicit" placeholder="Email"><span>Enter Aadhaar Number</span></div>
                <div><input id="unknown" name="uid"></div>
                <div><input id="hidden-label"><span hidden>Enter Aadhaar Number</span></div>
                <div><input id="first"><input id="second"><span>Enter Aadhaar Number</span></div>
                <div><input id="ambiguous"><span>Aadhaar number</span><span>Your Aadhaar number</span></div>
            ''')
            raw = await page.evaluate(_OBSERVE_JS)
            labels = {field["id"]: field["label"] for field in raw["fields"]}
            assert labels == {"floating": "Enter Aadhaar Number", "explicit": "Email", "unknown": "uid",
                              "hidden-label": "hidden-label", "first": "first", "second": "second", "ambiguous": "ambiguous"}
        finally:
            await browser.close()
