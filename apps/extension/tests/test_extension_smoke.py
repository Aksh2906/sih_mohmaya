"""Chrome extension UI smoke test. Synthetic microphone; all companion API calls mocked.
Run: .venv/bin/python -m pytest apps/extension/tests/test_extension_smoke.py -q
"""

import base64
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("start_url", ["https://example.test/application", None])
def test_extension_voice_delivery_preserves_original_tab(tmp_path, start_url):
    from privacy_guard.browser import BrowserDriver
    from privacy_guard.config import DATA_DIR

    executable = BrowserDriver(DATA_DIR, ROOT / "apps/extension")._browser_executable()
    calls = []
    image = Image.new("RGB", (200, 100), "white")
    original = io.BytesIO()
    image.save(original, format="PNG")
    ImageDraw.Draw(image).rectangle((10,10,40,40),fill="black")
    masked = io.BytesIO()
    image.save(masked, format="PNG")
    preview = {"approval_id":"image-1", "width":200, "height":100,
               "report":{"masks":1, "requires_manual_review":True,
                         "recovery_reasons":["Generated CSS media needs manual handling."]},
               "original":"data:image/png;base64,"+base64.b64encode(original.getvalue()).decode(),
               "redacted":"data:image/png;base64,"+base64.b64encode(masked.getvalue()).decode()}

    state = {
        "vault": {"initialized": True, "unlocked": True},
        "browser": {"connected": True},
        "provider": {"mode": "remote", "model": "fixture-model", "configured": True},
        "task": None,
    }

    def api(route):
        request = route.request
        path = request.url.split("/api/v1", 1)[1]
        if request.method == "POST":
            calls.append((path, request.post_data_buffer))
        if path == "/pair":
            payload = {"token": "test-only-extension-token"}
        elif path == "/status":
            payload = state
        elif path == "/record-catalog":
            payload = {"records": []}
        elif path == "/browser/tabs":
            payload = {"tabs": []}
        elif path == "/audio/transcribe":
            payload = {
                "text": "Fill this form using my selected profile.",
                "provider": "Local",
                "model": "faster-whisper-small",
            }
        elif path == "/tasks/ui-task/image-preview":
            payload = preview
        elif path == "/tasks/ui-task/masks":
            preview["approval_id"] = "image-2"
            state["task"]["pending"]["id"] = "image-2"
            payload = preview
        elif path == "/tasks/ui-task/approve":
            state["task"]["pending"] = None
            payload = {"ok": True}
        elif path == "/tasks":
            payload = {
                "id": "ui-task",
                "goal": "Fill this form",
                "step": 0,
                "status": "created",
                "events": [],
            }
            state["task"] = payload
        elif path == "/tasks/ui-task":
            payload = state["task"]
        else:
            payload = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(tmp_path / "chromium-profile"),
            executable_path=str(executable),
            headless=True,
            viewport={"width": 390, "height": 950},
            args=[
                f"--disable-extensions-except={ROOT / 'apps/extension'}",
                f"--load-extension={ROOT / 'apps/extension'}",
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
        )
        try:
            context.route("http://127.0.0.1:8765/api/v1/**", api)
            worker = (
                context.service_workers[0]
                if context.service_workers
                else context.wait_for_event("serviceworker")
            )
            extension_id = worker.url.split("/")[2]
            source = context.new_page()
            source.set_content("<title>Original fictional form</title><h1>Original fictional form</h1>")
            source_tab = worker.evaluate(
                "async () => (await chrome.tabs.query({})).find(t => t.title === 'Original fictional form').id"
            )
            panel = context.new_page()
            panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
            panel.get_by_label("Terminal pairing code").fill("test-only-code")
            panel.get_by_role("button", name="Connect extension", exact=False).click()
            expect(panel.get_by_role("button", name="Start task", exact=False)).to_be_enabled()
            expect(panel.locator("#mode")).to_have_value("remote")
            expect(panel.locator("#vision")).to_be_checked()
            expect(panel.locator("#stop-before-submit")).to_be_checked()
            capture = context.new_page()
            capture.goto(f"chrome-extension://{extension_id}/capture.html?source_tab={source_tab}")
            capture.get_by_role("button", name="Start recording", exact=True).click()
            capture.get_by_role("button", name="Stop recording", exact=True).wait_for()
            capture.wait_for_timeout(300)
            capture.get_by_role("button", name="Stop recording", exact=True).click()
            expect(capture.get_by_role("button", name="Transcribe locally", exact=True)).to_be_enabled()
            expect(capture.locator("#recording-playback")).not_to_have_js_property("readyState", 0)
            assert not any(path == "/audio/transcribe" for path, _ in calls)
            capture.get_by_role("button", name="Transcribe locally", exact=True).click()
            expect(capture.get_by_role("textbox", name="Editable transcript")).to_have_value(
                "Fill this form using my selected profile."
            )
            assert not any(path == "/tasks" for path, _ in calls)
            capture.get_by_role("button", name="Use transcript in task", exact=True).click()
            expect(panel.locator("#goal")).to_have_value("Fill this form using my selected profile.")
            # Delivery is acknowledged before restoring the source tab, without auto-starting.
            capture.get_by_text("Transcript placed in the side panel task draft.", exact=False).wait_for()
            active = worker.evaluate(
                "async () => (await chrome.tabs.query({active:true,currentWindow:true}))[0].id"
            )
            assert active == source_tab
            assert not any(path == "/tasks" for path, _ in calls)
            assert sum(path == "/audio/transcribe" for path, _ in calls) == 1
            folder = os.getenv("GUARD_UI_CAPTURE_DIR")
            if folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
                capture.screenshot(
                    path=str(Path(folder) / "extension-transcript-test-fixture.png"), full_page=True
                )
                panel.screenshot(path=str(Path(folder) / "extension-task-test-fixture.png"), full_page=True)
            panel.bring_to_front()
            expect(panel.locator("#use-current-tab")).not_to_be_checked()
            if start_url:
                panel.locator("#start-url").fill(start_url)
            panel.get_by_role("button", name="Start task", exact=False).click()
            panel.locator("#task-status").get_by_text("created", exact=True).wait_for()
            request = json.loads(next(body for path, body in calls if path == "/tasks"))
            if start_url:
                assert request["start_url"] == start_url
            else:
                assert "start_url" not in request
            assert "target_id" not in request
            assert request["vision"] is True and request["stop_before_submit"] is True
            assert request["image_review"] == "sensitive"
            state["task"].update(
                status="waiting_input", result="Sign in directly in the controlled browser.",
                human_action={"kind": "login", "message": "Sign in", "target_id": "original-tab"},
            )
            expect(panel.locator("#human-action-note")).to_be_visible()
            expect(panel.locator("#resume-information")).not_to_be_visible()
            panel.get_by_role("button", name="I've finished — continue", exact=True).click()
            assert json.loads(next(body for path, body in calls if path == "/tasks/ui-task/control")) == {"action": "resume"}
            state["task"].update(status="awaiting_approval", human_action=None,
                pending={"id":"click-review", "kind":"submit", "title":"Click “Download Aadhaar”?", "payload":{"control":"Download Aadhaar","destination":"https://example.test"},
                         "summary":{"action":"Click “Download Aadhaar”.","destination":"https://example.test","detail":"Check the button before continuing."}})
            expect(panel.locator("#action-description")).to_have_text('Click “Download Aadhaar”.')
            expect(panel.locator("#action-destination")).to_contain_text("https://example.test")
            expect(panel.locator("#approval-payload")).not_to_be_visible()
            state["task"].update(status="awaiting_approval", human_action=None,
                pending={"id":"image-1", "kind":"image", "title":"Review the redacted screenshot before sending", "payload":{"report":{"masks":1}}})
            expect(panel.locator("#visual-image")).to_have_attribute("src",preview["redacted"])
            expect(panel.locator("#approve")).to_be_enabled()
            panel.locator("#show-original").check()
            expect(panel.locator("#visual-recovery")).to_contain_text("Automatic privacy checks need your review")
            expect(panel.locator("#visual-recovery")).to_contain_text("Generated CSS media needs manual handling.")
            expect(panel.locator("#visual-image")).to_have_attribute("src",preview["original"])
            expect(panel.locator("#approve")).to_be_disabled()
            panel.locator("#show-original").uncheck()
            expect(panel.locator("#approve")).to_be_enabled()
            stage=panel.locator("#visual-stage").bounding_box()
            panel.mouse.move(stage["x"]+stage["width"]*0.25,stage["y"]+stage["height"]*0.25)
            panel.mouse.down()
            panel.mouse.move(stage["x"]+stage["width"]*0.75,stage["y"]+stage["height"]*0.75)
            panel.mouse.up()
            expect(panel.locator("#approve")).to_be_disabled()
            panel.locator("#apply-mask").click()
            expect(panel.locator("#approve")).to_be_enabled()
            mask_request=json.loads(next(body for path,body in calls if path=="/tasks/ui-task/masks"))
            assert mask_request["approval_id"]=="image-1"
            assert mask_request["masks"][0]["width"]==pytest.approx(100,abs=2)
            panel.get_by_label("Language / भाषा").select_option("hi")
            expect(panel.locator("#approve")).to_have_text("चित्र भेजने की अनुमति दें")
            expect(panel.locator("#visual-recovery")).to_contain_text("स्वचालित गोपनीयता जाँच के लिए आपकी समीक्षा चाहिए")
            panel.locator("#approve").click()
            approval=json.loads(next(body for path,body in calls if path=="/tasks/ui-task/approve"))
            assert approval=={"approval_id":"image-2","approved":True}

        finally:
            context.close()
