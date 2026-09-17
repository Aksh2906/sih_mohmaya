"""Login monitoring never acts on challenges and retains manual task controls."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from privacy_guard.browser import BrowserError
from tests.test_agent_runtime import runtime_stub


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    runtime = runtime_stub(tmp_path)
    manager, task = runtime.manager, runtime.task
    task.update(id="login-watch", status="waiting_input", events=[], target_id="same-tab",
                _record_ids=["selected-identity"], human_action={"kind": "login", "auto_resume": True})
    manager.tasks[task["id"]] = task
    manager.login_poll_seconds = 0.001
    manager.browser = SimpleNamespace(
        tabs=AsyncMock(return_value=[{"target_id": "same-tab", "url": "https://example.test/home"}]),
        observe=AsyncMock(), execute=AsyncMock(),
    )
    monkeypatch.setattr(manager, "launch", Mock())
    return manager, task


def destination(**overrides):
    return {"url": "https://example.test/home", "text": "Account home", "fields": [], **overrides}


async def test_monitor_waits_through_captcha_otp_errors_and_loading(handoff):
    manager, task = handoff
    pages = [
        destination(url="https://example.test/login", text="Login to Aadhaar",
                    fields=[{"tag": "input", "label": "Enter Aadhaar Number", "value": "123412341234"}]),
        destination(fields=[{"tag": "input", "label": "Captcha", "value": "secret-captcha"}]),
        destination(fields=[{"tag": "input", "autocomplete": "one-time-code", "value": "secret-otp"}]),
        BrowserError("observation_failed"), destination(text=""),
        destination(ready_state="loading"), destination(unsupported_frames=1),
        destination(truncated_fields=True), destination(), destination(),
    ]
    manager.browser.observe.side_effect = pages
    await asyncio.wait_for(manager.watch_login(task, 0), 2)
    assert manager.browser.observe.await_count == len(pages)
    assert all(call.kwargs == {"include_screenshot": False} for call in manager.browser.observe.await_args_list)
    manager.browser.execute.assert_not_awaited()
    manager.launch.assert_called_once_with(task)
    assert task["status"] == "observing" and task["human_action"] is None
    assert task["target_id"] == "same-tab" and task["_record_ids"] == ["selected-identity"]
    assert "Observe fresh state" in task["_human_resume"]["instruction"]
    public = json.dumps(manager.public(task))
    assert all(value not in public for value in ["123412341234", "secret-captcha", "secret-otp"])


async def test_challenge_reappearing_resets_stability_check(handoff):
    manager, task = handoff
    manager.browser.observe.side_effect = [destination(), destination(fields=[{"tag": "input", "label": "OTP"}]),
                                          destination(), destination()]
    await asyncio.wait_for(manager.watch_login(task, 0), 2)
    assert manager.browser.observe.await_count == 4


@pytest.mark.parametrize("action", ["pause", "stop", "resume", "shutdown", "lock"])
async def test_task_controls_cancel_login_watch(handoff, action):
    manager, task = handoff
    observed = asyncio.Event()

    async def login(*_args, **_kwargs):
        observed.set()
        return destination(url="https://example.test/login")

    manager.browser.observe.side_effect = login
    watcher = asyncio.create_task(manager.watch_login(task, 0))
    manager.login_watchers[task["id"]] = watcher
    await asyncio.wait_for(observed.wait(), 2)
    if action == "shutdown":
        await manager.stop_all()
    elif action == "lock":
        manager.vault.lock()
    else:
        await manager.control(task["id"], action)
    await asyncio.wait_for(watcher, 2)
    assert task["id"] not in manager.login_watchers
    assert manager.launch.call_count == (1 if action == "resume" else 0)


async def test_new_destination_requires_manual_continue(handoff):
    manager, task = handoff
    checked = asyncio.Event()

    async def tabs():
        checked.set()
        return [{"target_id": "same-tab", "url": "https://another.example/home"}]

    manager.browser.tabs.side_effect = tabs
    watcher = asyncio.create_task(manager.watch_login(task, 0))
    manager.login_watchers[task["id"]] = watcher
    await asyncio.wait_for(checked.wait(), 2)
    await manager.cancel_login_watch(task["id"])
    manager.browser.observe.assert_not_awaited()
    manager.launch.assert_not_called()


async def test_another_generation_cannot_be_resumed(handoff):
    manager, task = handoff
    manager.generation = 1
    await manager.watch_login(task, 0)
    manager.browser.observe.assert_not_awaited()
    manager.launch.assert_not_called()
