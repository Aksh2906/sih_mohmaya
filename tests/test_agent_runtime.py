"""Real Browser Use integration with synthetic pages and a deterministic HTTP peer."""

import asyncio
import base64
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from privacy_guard.agent_llm import clean_strings
from privacy_guard.agent_runtime import BrowserAgentRuntime, ReferenceInput, build_agent, quiet_browser_use
from privacy_guard.browser import BrowserDriver
from privacy_guard.config import DATA_DIR
from privacy_guard.gateway import ModelGateway, digest
from privacy_guard.models import TaskRequest
from privacy_guard.screenshots import SanitizedImageStore
from privacy_guard.tasks import TaskManager
from privacy_guard.vault import Vault


def runtime_stub(tmp_path):
    vault = Vault(tmp_path)
    vault.initialize("synthetic-long-passphrase")
    gateway = ModelGateway()
    gateway.model, gateway.api_key = "test-model", "sk-test-no-network-key"
    gateway.image_store = SanitizedImageStore()
    manager = TaskManager(vault, SimpleNamespace(), gateway)
    task = {
        "_generation": 0,
        "_goal": "Fill the form",
        "_origin": "https://example.test",
        "_ref_ids": {},
        "_refs": {},
    }
    return BrowserAgentRuntime(manager, task)


def test_agent_transport_drops_native_images_and_preserves_json(tmp_path):
    runtime = runtime_stub(tmp_path)
    secret = "PRIVATE_CANARY_921714"
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,RAW_UNREVIEWED"}}
    payload = runtime.llm.prepare(
        [
            {"role": "user", "content": [{"type": "text", "text": "Name: " + secret}, image]},
            {"role": "assistant", "content": "Echo " + secret},
        ],
        ReferenceInput,
        [secret],
    )
    encoded = json.dumps(payload)
    assert secret not in encoded and "RAW_UNREVIEWED" not in encoded
    assert payload["response_format"] == {"type": "json_object"}
    cleaned = clean_strings({"nested": {"message": "Name: " + secret}}, [secret])
    assert isinstance(cleaned["nested"], dict)
    assert "message" in cleaned["nested"]
    with_url = runtime.llm.prepare(
        [
            {
                "role": "user",
                "content": "Visit https://example.test/form?session=UNKNOWN_TOKEN#PRIVATE_FRAGMENT",
            }
        ],
        ReferenceInput,
        [],
    )
    assert "UNKNOWN_TOKEN" not in json.dumps(with_url) and "PRIVATE_FRAGMENT" not in json.dumps(with_url)


async def test_model_endpoint_changes_and_late_masks_cannot_reuse_approval(tmp_path):
    runtime = runtime_stub(tmp_path)
    payload = runtime.llm.prepare([{"role": "user", "content": "Return JSON"}], ReferenceInput, [])
    runtime.manager.gateway.base_url = "https://different.example/v1"
    with pytest.raises(ValueError, match="settings changed"):
        await runtime.llm.send(payload, digest(payload), [])
    future = asyncio.get_running_loop().create_future()
    future.set_result(True)
    runtime.task["pending"] = {"id": "accepted-id", "kind": "image", "expires_at": 10**12}
    runtime.manager.approvals["accepted-id"] = future
    runtime.image_state = {"artifact": {"id": "immutable-original"}}
    with pytest.raises(ValueError, match="no longer valid"):
        runtime.update_masks("accepted-id", [{"x": 0, "y": 0, "width": 10, "height": 10}])
    assert runtime.image_state["artifact"]["id"] == "immutable-original"


@pytest.mark.parametrize(
    "encoded",
    [r'Mira \"Sen\"', r"Mira \u0022Sen\u0022", "Mira%20%22Sen%22", "Mira &quot;Sen&quot;"],
)
async def test_send_rechecks_newly_reviewed_private_values_before_http(tmp_path, monkeypatch, encoded):
    runtime = runtime_stub(tmp_path)
    payload = runtime.llm.prepare(
        [{"role": "user", "content": "Previous page text: " + encoded}], ReferenceInput, []
    )
    approved_hash = digest(payload)
    # A document review can add a value while a screenshot/text approval is open.
    # The immutable payload must then be rejected rather than sent or rewritten
    # under the original approval receipt.
    runtime.manager.vault.put_record("Full name", "person_name", 'Mira "Sen"')

    def no_transport(**_kwargs):
        pytest.fail("A fresh private value crossed the HTTP boundary")

    monkeypatch.setattr("privacy_guard.agent_llm.httpx.AsyncClient", no_transport)
    with pytest.raises(ValueError, match="known private value"):
        await runtime.llm.send(payload, approved_hash, [])


def test_mixed_artifact_cannot_send_original_pixels(tmp_path):
    runtime = runtime_stub(tmp_path)
    pixels = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(pixels, format="PNG")
    raw = base64.b64encode(pixels.getvalue()).decode()
    artifact = runtime.manager.gateway.image_store.create(
        raw,
        {
            "complete": True,
            "viewport": {"width": 32, "height": 32},
            "regions": [{"x": 0, "y": 0, "width": 10, "height": 10, "reason": "known_value"}],
        },
    )
    artifact["data_url"] = "data:image/png;base64," + raw
    with pytest.raises(ValueError, match="artifact was altered"):
        runtime.llm.prepare([{"role": "user", "content": "Return JSON"}], ReferenceInput, [], artifact)


def test_native_agent_has_only_guarded_tools_and_no_persistence(tmp_path, monkeypatch):
    quiet_browser_use()
    import browser_use.browser.profile as profile

    monkeypatch.setattr(profile, "get_display_size", lambda: None)
    from browser_use import BrowserSession

    runtime = runtime_stub(tmp_path)
    session = BrowserSession(cdp_url="http://127.0.0.1:9222", use_cloud=False, captcha_solver=False)
    agent = build_agent(runtime, session)
    assert agent.file_system is None
    assert not agent.agent_directory.exists()
    assert not agent.settings.use_vision and not agent.settings.use_judge
    assert not agent.settings.message_compaction.enabled
    assert not agent.enable_signal_handler
    assert agent.settings.page_extraction_llm is runtime.llm
    assert set(agent.tools.registry.registry.actions) == {
        "navigate",
        "click",
        "input_ref",
        "scroll",
        "wait",
        "visual_checkpoint",
        "request_information",
        "done",
    }
    agent.tools.set_coordinate_clicking(True)
    assert "coordinate_x" not in agent.tools.registry.registry.actions["click"].param_model.model_fields


class _Portal(BaseHTTPRequestHandler):
    def do_GET(self):
        page = (
            '<!doctype html><html><body><h1>Application home</h1><a href="/form">Open application</a></body></html>'
            if self.path != "/form"
            else """<!doctype html><html><body><h1>Application form</h1>
                <label>Full name<input id="full_name" required></label>
                <label>Email<input id="email" type="email" required></label>
                <p>Please supply your contact document if email is missing.</p>
                <button type="submit">Submit application</button></body></html>"""
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page.encode())

    def log_message(self, *_args):
        pass


@pytest.mark.skipif(
    os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Set GUARD_BROWSER_TESTS=1 for Chromium integration"
)
async def test_stock_agent_navigation_ref_fill_image_review_missing_resume(tmp_path, monkeypatch):
    # Load native HTTP type annotations before replacing the transport factory.
    quiet_browser_use()
    import browser_use  # noqa: F401

    finder = BrowserDriver(DATA_DIR, tmp_path, headless=True)
    monkeypatch.setenv("GUARD_BROWSER_EXECUTABLE", str(finder._browser_executable()))
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "Guard integration",
                "version": "0.0.1",
                "permissions": ["activeTab"],
                "action": {},
            }
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Portal)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    driver = BrowserDriver(tmp_path / "browser", extension, headless=True)
    vault = Vault(tmp_path / "vault")
    vault.initialize("synthetic-long-passphrase")
    canary = "SYNTHETIC_PRIVATE_PERSON_194825"
    name_record = vault.put_record("Full name", "person_name", canary)
    gateway = ModelGateway()
    gateway.model, gateway.api_key = "test-model", "sk-test-no-network-key"
    gateway.image_store = SanitizedImageStore()
    manager = TaskManager(vault, driver, gateway, demo_port=server.server_port)
    origin = f"http://127.0.0.1:{server.server_port}"
    sent, stages = [], {"navigated": False, "asked": False}
    original_client = httpx.AsyncClient

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert canary not in json.dumps(payload)
        runtime = next(iter(manager.tasks.values()))["_runtime"]
        if any(isinstance(message["content"], list) for message in payload["messages"]):
            assert runtime.task.get("pending") is None
            response = {
                "summary": "The email is missing. Add a reviewed contact record.",
                "missing_fields": ["Email"],
                "document_requests": ["Contact document"],
            }
        else:
            schema = runtime.agent.AgentOutput.model_json_schema()
            state = runtime.agent.browser_session._cached_browser_state_summary
            catalog = manager.catalog(runtime.task)
            name_ref = next(ref["id"] for ref in catalog if ref["type"] == "person_name")
            if not stages["navigated"]:
                action = {"navigate": {"url": origin + "/form", "new_tab": False}}
                stages["navigated"] = True
            else:
                nodes = state.dom_state.selector_map
                name_index = next(
                    index for index, node in nodes.items() if node.attributes.get("id") == "full_name"
                )
                email_index = next(
                    index for index, node in nodes.items() if node.attributes.get("id") == "email"
                )
                local_fields = runtime.raw["fields"]
                name_field = next(
                    field
                    for field in local_fields
                    if field.get("name") == "" and field["label"] == "Full name"
                )
                email_field = next(field for field in local_fields if field["label"] == "Email")
                if not name_field["value"]:
                    action = {"input_ref": {"index": name_index, "value_ref": name_ref}}
                elif not stages["asked"]:
                    stages["asked"] = True
                    action = {
                        "request_information": {
                            "message": "Please provide the missing email or contact document."
                        }
                    }
                elif not email_field["value"]:
                    email_ref = next(ref["id"] for ref in catalog if ref["type"] == "email")
                    action = {"input_ref": {"index": email_index, "value_ref": email_ref}}
                else:
                    action = {
                        "done": {
                            "text": "The fields are filled and ready for review. Submission was withheld.",
                            "success": True,
                        }
                    }
            response = {
                "evaluation_previous_goal": "Checked the page",
                "memory": "Synthetic test",
                "next_goal": "Continue the requested form",
                "action": [action],
            }
            # Additional planning fields are optional in the pinned schema.
            assert "action" in schema["properties"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})

    monkeypatch.setattr(
        "privacy_guard.agent_llm.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    try:
        await driver.launch()
        request = TaskRequest(
            goal="Open this website and fill the application",
            start_url=origin,
            mode="remote",
            vision=True,
            record_ids=[name_record["id"]],
        )
        task = await manager.start(request)
        task_id = task["id"]
        for _ in range(600):
            task = manager.tasks[task_id]
            if task.get("pending"):
                assert task["pending"]["kind"] == "image", manager.public(task)
                before = manager.image_preview(task_id)
                outgoing_before = len(sent)
                old_id = before["approval_id"]
                after = manager.update_masks(task_id, old_id, [{"x": 0, "y": 0, "width": 12, "height": 12}])
                assert after["approval_id"] != old_id and len(sent) == outgoing_before
                with pytest.raises(ValueError, match="no longer valid"):
                    manager.approve(task_id, old_id, True)
                assert after["original"] != after["redacted"]
                picture = Image.open(io.BytesIO(base64.b64decode(after["redacted"].split(",", 1)[1])))
                assert any(hi > 0 for _lo, hi in picture.convert("RGB").getextrema())
                manager.approve(task_id, after["approval_id"], True)
            if task["status"] == "waiting_input":
                break
            if task["status"] in ("failed", "blocked", "outcome_unknown", "completed"):
                pytest.fail(str(manager.public(task)))
            await asyncio.sleep(0.1)
        assert task["status"] == "waiting_input", manager.public(task)
        assert len(sent) == 4
        assert task["metrics"]["image_calls"] == 1
        raw = await driver.observe(task["target_id"])
        assert raw["fields"][0]["value"] == canary
        email = vault.put_record("Email", "email", "private-person@example.test")
        await manager.control(task_id, "resume", [name_record["id"], email["id"]])
        await asyncio.wait_for(manager.workers[task_id], timeout=45)
        assert task["status"] == "completed", manager.public(task)
        raw = await driver.observe(task["target_id"])
        assert raw["fields"][1]["value"] == "private-person@example.test"
        assert len(sent) == 6
        assert all("private-person@example.test" not in json.dumps(payload) for payload in sent)
        assert not task["_runtime"].agent.agent_directory.exists()
    finally:
        await manager.stop_all()
        await driver.shutdown()
        server.shutdown()
