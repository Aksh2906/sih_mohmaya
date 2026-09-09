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


_FORM = b"""<!doctype html><html><head><title>Privacy Guard synthetic test</title></head>
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
                "name": "Privacy Guard test",
                "version": "0.0.1",
                "permissions": ["activeTab"],
                "action": {"default_title": "Privacy Guard"},
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
