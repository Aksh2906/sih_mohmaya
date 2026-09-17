"""Real Chromium UI tests using an isolated HTTP origin and mock API.

Run from repository root: .venv/bin/python -m pytest apps/dashboard/tests/test_ui_smoke.py -q
No remote model or saved user vault is accessed.
"""

import base64
import io
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


def capture(page, name):
    folder = os.getenv("GUARD_UI_CAPTURE_DIR")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / name), full_page=True)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


@pytest.fixture(scope="module")
def chromium():
    from privacy_guard.browser import BrowserDriver
    from privacy_guard.config import DATA_DIR

    executable = BrowserDriver(DATA_DIR, ROOT / "apps/extension")._browser_executable()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=str(executable),
            headless=True,
        )
        yield browser
        browser.close()


@pytest.fixture
def dashboard(chromium):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "apps/dashboard/dist"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    context = chromium.new_context(viewport={"width": 1360, "height": 1050})
    context.add_init_script("sessionStorage.setItem('dpg.session', 'test-only-pair-token');")
    page = context.new_page()
    state = {
        "vault": {"initialized": True, "unlocked": True},
        "browser": {"connected": False},
        "provider": {"mode": "remote", "model": "fixture-model", "configured": True},
        "task": None,
    }
    calls = []
    custom = {}
    records = [
        {
            "id": "name1",
            "label": "Full name",
            "field_type": "person_name",
            "value": "Aarav Example",
            "scope": "profile",
        }
    ]

    def api(route):
        request = route.request
        path = request.url.split("/api/v1", 1)[1]
        body = None
        if request.method == "POST":
            body = request.post_data_json
            calls.append((path, body))
        if path in custom:
            payload = custom[path](body)
        elif path == "/status":
            payload = state
        elif path == "/records":
            payload = {"records": records}
        elif path == "/documents":
            payload = {"documents": []}
        elif path == "/browser/tabs":
            payload = {"tabs": []}
        elif path == "/tasks" and request.method == "GET":
            payload = {"tasks": [state["task"]] if state["task"] else []}
        elif path == "/tasks" and request.method == "POST":
            state["task"] = {
                "id": "new-task",
                "goal": body["goal"],
                "status": "created",
                "step": 0,
                "events": [],
            }
            payload = state["task"]
        elif path.startswith("/tasks/"):
            payload = state["task"]
        else:
            payload = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/v1/**", api)
    yield page, f"http://127.0.0.1:{server.server_port}", state, calls, custom
    context.close()
    server.shutdown()
    server.server_close()


def test_task_can_start_without_existing_tab(dashboard):
    page, url, state, calls, _ = dashboard
    page.goto(url + "/#page=activity&new=1")
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("textbox", name="Task", exact=True).fill(
        "Go to https://example.test and fill the application"
    )
    expect(dialog.get_by_role("checkbox", name="Full name profile")).to_be_checked()
    expect(dialog.get_by_role("checkbox", name="Redacted images at every planning step")).to_be_checked()
    expect(dialog.get_by_role("checkbox", name="Stop before final submission")).to_be_checked()
    capture(page, "new-task-test-fixture.png")
    dialog.get_by_role("button", name="Start task", exact=True).click()
    page.get_by_text("Task new-task", exact=False).wait_for()
    body = next(body for path, body in calls if path == "/tasks")
    assert "target_id" not in body and "start_url" not in body
    assert body["vision"] is True and body["stop_before_submit"] is True
    assert body["image_review"] == "sensitive"
    assert body["review_text"] is False and body["mode"] == "remote"


@pytest.mark.parametrize("auto_resume", [False, True])
def test_login_handoff_continues_same_task_without_replacing_vault_selection(dashboard, auto_resume):
    page, url, state, calls, _ = dashboard
    state["task"] = {
        "id": "login-task", "goal": "Open the Download Aadhaar page", "status": "waiting_input",
        "step": 3, "events": [], "result": "Sign in directly in the controlled browser.",
        "human_action": {"kind": "login", "message": "Sign in", "target_id": "same-tab", "auto_resume": auto_resume},
        "plan": [{"title": "Open the download service", "success_criteria": "Download heading is visible",
                  "status": "in_progress", "source_ids": [1]}],
        "sources": [{"id": 1, "title": "Official help", "url": "https://example.test/help", "note": "Service route"}],
    }
    page.goto(url + "/#page=activity&task=login-task")
    expect(page.get_by_role("heading", name="Task plan", exact=True)).to_be_visible()
    expect(page.get_by_text("Your browser action is needed", exact=True)).to_be_visible()
    if auto_resume:
        expect(page.get_by_text("The agent will continue automatically", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Add details", exact=True)).not_to_be_visible()
    page.get_by_text("Sources consulted", exact=True).click()
    expect(page.get_by_role("link", name="Official help")).to_have_attribute("href", "https://example.test/help")
    capture(page, "login-handoff-test-fixture.png")
    page.get_by_role("button", name="I've finished — continue", exact=True).click()
    assert next(body for path, body in calls if path == "/tasks/login-task/control") == {"action": "resume"}
    expect(page.get_by_role("dialog")).not_to_be_visible()


def test_typed_draft_requires_explicit_start(dashboard):
    page, url, _, calls, _ = dashboard
    page.goto(url + "/#page=activity&new=1")
    dialog = page.get_by_role("dialog")
    draft = "मेरी प्रोफ़ाइल से यह फ़ॉर्म भरें — stop before submitting."
    dialog.get_by_role("textbox", name="Task", exact=True).fill(draft)
    expect(dialog.get_by_role("textbox", name="Task", exact=True)).to_have_value(draft)
    expect(dialog.get_by_role("button", name="Record task", exact=True)).to_have_count(0)
    expect(page.locator("audio")).to_have_count(0)
    assert not any(path == "/tasks" for path, _ in calls)
    dialog.get_by_role("button", name="Start task", exact=True).click()
    page.get_by_text("Task new-task", exact=False).wait_for()
    assert next(body for path, body in calls if path == "/tasks")["goal"] == draft


def png(mask=None):
    image = Image.new("RGB", (640, 360), "#eff5f1")
    draw = ImageDraw.Draw(image)
    draw.text((25, 25), "Fictional application", fill="black")
    draw.rectangle((25, 65, 400, 110), outline="black", width=2)
    if mask:
        x, y, width, height = (mask[k] for k in ("x", "y", "width", "height"))
        draw.rectangle((x, y, x + width, y + height), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.mark.parametrize("recovery", [False, True])
def test_visual_masks_use_image_coordinates_and_refresh_approval(dashboard, recovery):
    page, url, state, calls, custom = dashboard
    task = {
        "id": "visual-task",
        "goal": "Fill fictional application",
        "status": "awaiting_approval",
        "step": 1,
        "events": [],
        "pending": {
            "id": "image-1",
            "kind": "image",
            "title": "Review screenshot",
            "payload": {
                "sha256": "fixture-only-request-hash",
                "destination": "https://model.example.test/v1",
                "request": {
                    "messages": [
                        {"role": "user", "content": "Identify the missing required fields."}
                    ]
                },
            },
        },
    }
    state["task"] = task
    preview = {
        "approval_id": "image-1",
        "original": png(),
        "redacted": png(),
        "width": 640,
        "height": 360,
        "report": {"method": "synthetic test fixture", "requires_manual_review": recovery,
                   "recovery_reasons": ["Generated CSS media needs manual handling."]},
    }
    custom["/tasks/visual-task/image-preview"] = lambda _: preview

    def apply_masks(body):
        assert body["approval_id"] == "image-1"
        assert len(body["masks"]) == 1
        task["pending"]["id"] = "image-2"
        preview.update({"approval_id": "image-2", "redacted": png(body["masks"][0])})
        return {"approval_id": "image-2"}

    custom["/tasks/visual-task/masks"] = apply_masks
    page.goto(url + "/#page=activity&task=visual-task")
    approve = page.get_by_role("button", name="Approve image & continue")
    expect(approve).to_be_enabled()
    if recovery:
        expect(page.get_by_text("Automatic privacy checks need your review", exact=True)).to_be_visible()
        expect(page.get_by_text("Generated CSS media needs manual handling.", exact=True)).to_be_visible()
        assert not any(path.endswith("/approve") for path, _ in calls)
    page.get_by_text("Outgoing text, destination & request hash", exact=True).click()
    request_details = page.locator("details").filter(
        has=page.get_by_text("Outgoing text, destination & request hash", exact=True)
    )
    expect(request_details).to_contain_text("Identify the missing required fields.")
    expect(request_details).to_contain_text("https://model.example.test/v1")
    expect(request_details).to_contain_text("fixture-only-request-hash")
    page.get_by_text("Outgoing text, destination & request hash", exact=True).click()
    stage = page.locator(".mask-stage")
    stage.scroll_into_view_if_needed()
    box = stage.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.1, box["y"] + box["height"] * 0.2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.4)
    page.mouse.up()
    expect(approve).to_be_disabled()
    page.get_by_role("button", name="Apply masks & reload preview").click()
    expect(approve).to_be_enabled()
    mask = next(body["masks"][0] for path, body in calls if path.endswith("/masks"))
    assert abs(mask["x"] - 64) <= 1 and abs(mask["y"] - 72) <= 1
    assert abs(mask["width"] - 192) <= 1 and abs(mask["height"] - 72) <= 1
    page.get_by_role("button", name="Show local original").click()
    capture(page, "visual-checkpoint-test-fixture.png")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, "visual-checkpoint-mobile-test-fixture.png")
    approve.click()
    assert next(body for path, body in calls if path.endswith("/approve"))["approval_id"] == "image-2"


def test_portal_requires_details_and_stops_at_review(chromium):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "demo")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    context = chromium.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/portal.html")
        page.set_viewport_size({"width": 1280, "height": 900})
        capture(page, "portal-landing.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Start application", exact=True).click()
        page.get_by_role("button", name="Review application", exact=True).click()
        expect(
            page.get_by_text("Complete the required details before continuing.", exact=False)
        ).to_be_visible()
        values = {
            "full_name": "Aarav Example",
            "email": "aarav@example.test",
            "phone": "9000012345",
            "pan": "ABCDE1234F",
            "address": "42 Sample Lane, Demo City",
            "statement_total": "3050.00",
        }
        for key, value in values.items():
            page.locator("#" + key).fill(value)
        page.get_by_role("button", name="Review application", exact=True).click()
        expect(page.get_by_role("heading", name="Everything in one place.")).to_be_visible()
        expect(page.get_by_text("NOT SUBMITTED", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        capture(page, "portal-review-mobile.png")
        page.get_by_role("button", name="Submit demo application", exact=True).click()
        expect(page.get_by_role("heading", name="Demo application complete.")).to_be_visible()
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_hindi_interface_preserves_draft_and_passes_language(dashboard):
    page,url,state,calls,_=dashboard
    page.goto(url+"/#page=activity&new=1")
    dialog=page.get_by_role("dialog")
    draft="मेरा आधार डाउनलोड करें"
    dialog.get_by_role("textbox",name="Task",exact=True).fill(draft)
    page.get_by_label("Language / भाषा").select_option("hi")
    expect(dialog.get_by_role("textbox",name="कार्य",exact=True)).to_have_value(draft)
    expect(dialog.get_by_role("button",name="कार्य शुरू करें",exact=True)).to_be_visible()
    expect(page.get_by_role("navigation")).to_contain_text("निजी वॉल्ट")
    dialog.get_by_role("button",name="कार्य शुरू करें",exact=True).click()
    page.wait_for_timeout(200)
    body=next(body for path,body in calls if path=="/tasks")
    assert body["goal"]==draft and body["language"]=="hi" and body["vision"] is True


def test_click_approval_is_readable_with_collapsed_technical_details(dashboard):
    page,url,state,calls,_=dashboard
    state["task"]={"id":"click-task","goal":"Open the download page","status":"awaiting_approval","step":1,"events":[],
                   "pending":{"id":"click-approval","kind":"submit","title":"Click “Download Aadhaar”?",
                              "payload":{"control":"Download Aadhaar","destination":"https://example.test"},
                              "summary":{"action":"Click “Download Aadhaar”.","destination":"https://example.test","detail":"Check the button and website before continuing."}}}
    page.goto(url+"/#page=activity")
    expect(page.locator('.action-summary')).to_contain_text('Click “Download Aadhaar”.')
    expect(page.locator('.action-summary')).to_contain_text('https://example.test')
    details=page.locator('.approval-card details')
    expect(details).not_to_have_attribute('open','')
    expect(details.locator('pre')).not_to_be_visible()
    details.locator('summary').click()
    expect(details.locator('pre')).to_contain_text('"control": "Download Aadhaar"')
