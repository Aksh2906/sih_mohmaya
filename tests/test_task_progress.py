"""Handoffs resume the original task; intermediate pages cannot claim success."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from privacy_guard.task_progress import TaskPlan, authentication_barrier, is_login_form
from tests.test_agent_runtime import runtime_stub


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import browser_use.browser.profile as profile

    monkeypatch.setattr(profile, "get_display_size", lambda: None)
    runtime = runtime_stub(tmp_path)
    runtime.task.update(id="progress-task", target_id="original-tab", step=0, status="observing", events=[],
                        _goal="Open the Download Aadhaar page in English", _record_ids=["selected-record"],
                        _origin="https://portal.example")
    runtime.manager.tasks[runtime.task["id"]] = runtime.task
    runtime.raw = {"url": "https://portal.example/login", "title": "Login", "text": "Sign in", "fields": []}
    runtime.check_target = AsyncMock()
    runtime.manager.browser = SimpleNamespace(observe=AsyncMock(side_effect=lambda *_a, **_kw: runtime.raw))
    runtime.llm.send = AsyncMock(return_value={"achieved": True, "reason": "The requested page is visible.", "human_action": None})
    return runtime


def test_barriers_require_an_active_challenge():
    assert authentication_barrier({"url": "https://portal.example/", "text": "Login", "fields": [
        {"tag": "a", "label": "Login"},
        {"tag": "input", "input_type": "password", "in_viewport": False},
    ]}) is None
    assert authentication_barrier({"fields": [{"tag": "input", "label": "Enter OTP"}]}) == "otp"
    assert authentication_barrier({"fields": [{"tag": "input", "label": "Captcha"}]}) == "captcha"


async def test_login_done_becomes_handoff_then_same_task_can_complete(runtime, monkeypatch):
    runtime.task["plan"] = [{"title": "Reach the download page", "success_criteria": "Download Aadhaar heading", "status": "in_progress", "source_ids": []}]
    result = await runtime.finish_task("Reached the website")
    assert not result.is_done
    assert runtime.task["status"] == "waiting_input"
    assert runtime.task["human_action"]["kind"] == "login"
    runtime.llm.send.assert_not_awaited()

    launches = []
    monkeypatch.setattr(runtime.manager, "launch", lambda task: launches.append(task["id"]))
    await runtime.manager.control(runtime.task["id"], "resume")
    assert launches == ["progress-task"]
    assert runtime.task["target_id"] == "original-tab"
    assert runtime.task["_record_ids"] == ["selected-record"]
    assert runtime.task["plan"][0]["status"] == "in_progress"
    assert runtime.task["human_action"] is None
    assert runtime.task["_human_resume"]["kind"] == "login"
    # Merely pressing Continue must not bypass a still-present login screen.
    assert not (await runtime.finish_task("Done")).is_done
    assert runtime.task["human_action"]["kind"] == "login"

    await runtime.manager.control(runtime.task["id"], "resume")
    runtime.raw.update(url="https://portal.example/download/en", title="Download Aadhaar", text="Download Aadhaar in English")
    runtime.task["plan"][0]["status"] = "done"
    assert (await runtime.finish_task("Opened the English Download Aadhaar page")).is_done
    assert runtime.task["status"] == "completed"


async def test_verifier_rejects_intermediate_page_without_ending_task(runtime):
    runtime.raw.update(url="https://portal.example/", title="Home", text="Welcome")
    runtime.llm.send.return_value = {"achieved": False, "reason": "This is the homepage; open the download service.", "human_action": None}
    result = await runtime.finish_task("Done")
    assert result.error and not result.is_done
    assert runtime.task["status"] == "observing"


async def test_failure_is_resumable_and_otp_never_requested_in_vault(runtime):
    runtime.raw.update(url="https://portal.example/auth", fields=[{"tag": "input", "label": "OTP", "value": "SYNTHETIC-OTP-CANARY"}])
    result = await runtime.finish_task("I need help", success=False)
    assert not result.is_done and runtime.task["status"] == "waiting_input"
    assert runtime.task["human_action"]["kind"] == "otp"
    assert "Enter OTPs and CAPTCHA answers only on the website" in runtime.task["result"]
    assert "SYNTHETIC-OTP-CANARY" not in json.dumps(runtime.manager.public(runtime.task))


async def test_unfinished_plan_cannot_terminate(runtime):
    runtime.raw.update(url="https://portal.example/home")
    runtime.task["plan"] = [{"title": "Find download", "status": "pending"}]
    assert (await runtime.finish_task("Done")).error
    runtime.llm.send.assert_not_awaited()


async def test_changed_page_invalidates_success_verification(runtime):
    runtime.raw.update(url="https://portal.example/download/en")

    async def verify(*_a, **_kw):
        runtime.raw["url"] = "https://portal.example/login"
        return {"achieved": True, "reason": "The previously observed destination matched.", "human_action": None}

    runtime.llm.send.side_effect = verify
    result = await runtime.finish_task("Done")
    assert result.error and not result.is_done
    assert runtime.task["status"] != "completed"


async def test_sources_are_observed_redacted_and_used_by_plan(runtime):
    runtime.manager.vault.put_record("Private name", "person_name", "PRIVATE-SOURCE-CANARY")
    runtime.raw.update(url="https://help.example/guide?session=SECRET", title="Official help", text="Instructions PRIVATE-SOURCE-CANARY")
    runtime.record_source("Read help for PRIVATE-SOURCE-CANARY")
    runtime.raw.update(url="https://portal.example/faq", title="Portal FAQ")
    runtime.record_source("Cross-check the service route")
    assert [source["id"] for source in runtime.task["sources"]] == [1, 2]
    assert "PRIVATE-SOURCE-CANARY" not in json.dumps(runtime.task["sources"])
    assert "SECRET" not in json.dumps(runtime.task["sources"])
    valid = TaskPlan(steps=[{"title": "Open service", "success_criteria": "Requested heading visible", "source_ids": [1, 2]}])
    assert not runtime.update_plan(valid).error
    valid.steps[0].source_ids = [3]
    assert runtime.update_plan(valid).error
    assert runtime.task["plan"][0]["source_ids"] == [1, 2]


async def test_step_limit_preserves_task_for_another_run(runtime):
    runtime.manager.max_steps = 2
    runtime.task["step"] = 20
    runtime.agent = SimpleNamespace(state=SimpleNamespace(paused=False, stopped=False, last_result=[]), step=AsyncMock())
    await runtime.run(0)
    assert runtime.task["step"] == 22
    assert runtime.task["status"] == "waiting_input"
    assert runtime.task["human_action"]["kind"] == "review"
    await runtime.run(0)
    assert runtime.task["step"] == 24


async def test_navigation_goal_can_finish_on_public_page_with_unused_captcha(runtime):
    runtime.raw.update(url="https://portal.example/download/en", title="Download Aadhaar", text="Download Aadhaar",
                       fields=[{"index": 1, "tag": "input", "label": "Captcha", "required": True, "value": ""}])
    assert (await runtime.finish_task("Opened the requested page")).is_done


async def test_login_fills_known_reference_before_captcha_handoff(runtime):
    from browser_use.agent.views import ActionResult
    runtime.raw.update(fields=[
        {"index":1,"tag":"input","input_type":"text","label":"Aadhaar number","value":""},
        {"index":2,"tag":"input","input_type":"text","label":"Captcha","value":""},
    ])
    runtime.task["_record_ids"]=None
    runtime.manager.vault.put_record("Aadhaar", "aadhaar", "123412341234")
    fills=[]
    async def execute(action,index,ref):
        assert runtime.task["status"] != "waiting_input"
        fills.append((action,index,runtime.manager.resolve(runtime.task,ref)["field_type"]))
        runtime.raw["fields"][0]["filled"]=True
        return ActionResult(extracted_content="Filled locally")
    runtime.execute_element=execute
    await runtime.request_human_action("captcha","Complete the CAPTCHA")
    assert fills==[("input_ref",1,"aadhaar")]
    assert runtime.task["status"]=="waiting_input"
    assert runtime.task["human_action"]["kind"]=="captcha"
    assert "123412341234" not in json.dumps(runtime.manager.public(runtime.task))


async def test_ambiguous_login_records_are_not_guessed(runtime):
    runtime.raw.update(fields=[{"index":1,"tag":"input","input_type":"email","label":"Email","value":""}])
    runtime.task["_record_ids"]=None
    runtime.manager.vault.put_record("Work email","email","work@example.test")
    runtime.manager.vault.put_record("Home email","email","home@example.test")
    runtime.execute_element=AsyncMock()
    await runtime.request_human_action("login","Choose an account")
    runtime.execute_element.assert_not_awaited()
    assert runtime.task["status"]=="waiting_input"


def test_hindi_authentication_challenges_remain_manual():
    assert authentication_barrier({"fields":[{"tag":"input","label":"ओटीपी दर्ज करें"}]}) == "otp"
    assert authentication_barrier({"fields":[{"tag":"input","label":"कैप्चा भरें"}]}) == "captcha"


@pytest.mark.parametrize("kind", ["manual", "review", "login", "captcha"])
async def test_all_login_handoffs_fill_normalized_selected_identity_first(runtime, kind):
    from browser_use.agent.views import ActionResult

    record = runtime.manager.vault.put_record("Identity", "Aadhaar Number", "123412341234")
    runtime.task["_record_ids"] = [record["id"]]
    runtime.raw.update(text="Login to Aadhaar via OTP", fields=[
        {"index": 1, "id": "identity", "tag": "input", "input_type": "text", "label": "Enter Aadhaar Number", "value": ""},
        {"index": 2, "tag": "input", "label": "Enter Captcha", "value": ""},
        {"index": 3, "tag": "input", "label": "Code", "autocomplete": "one-time-code", "value": ""},
    ])
    async def fill(action, index, ref):
        assert action == "input_ref" and index == 1
        assert runtime.task["status"] != "waiting_input"
        runtime.raw["fields"][0].update(value=runtime.manager.resolve(runtime.task, ref)["value"], filled=True)
        return ActionResult(extracted_content="Filled")
    runtime.execute_element = AsyncMock(side_effect=fill)
    await runtime.request_human_action(kind, "Complete remaining login steps")
    runtime.execute_element.assert_awaited_once()
    assert runtime.task["login_fill"]["filled"] == ["Enter Aadhaar Number"]
    assert runtime.task["status"] == "waiting_input"
    assert "123412341234" not in json.dumps(runtime.manager.public(runtime.task))
    assert all(not field["value"] for field in runtime.raw["fields"][1:])


async def test_handoff_does_not_use_unselected_records_or_overwrite_existing_value(runtime):
    runtime.manager.vault.put_record("Identity", "aadhaar", "123412341234")
    runtime.raw["fields"] = [{"index": 1, "tag": "input", "label": "Enter Aadhaar Number", "value": ""}]
    runtime.execute_element = AsyncMock()
    await runtime.request_human_action("login", "Finish login")
    assert runtime.task["login_fill"]["missing"] == ["Enter Aadhaar Number"]
    runtime.execute_element.assert_not_awaited()
    runtime.task["_record_ids"] = None
    runtime.raw["fields"][0]["value"] = "Already entered by user"
    await runtime.request_human_action("login", "Finish login")
    runtime.execute_element.assert_not_awaited()


def test_otp_password_style_and_autocomplete_fields_are_always_human():
    assert authentication_barrier({"fields": [{"tag": "input", "input_type": "password", "label": "OTP"}]}) == "otp"
    assert authentication_barrier({"fields": [{"tag": "input", "label": "Code", "autocomplete": "one-time-code"}]}) == "otp"


def test_proactive_login_detection_requires_an_actual_form():
    assert is_login_form({"url": "https://example.test/authorize", "text": "Login to Aadhaar via OTP",
                          "fields": [{"tag": "input", "label": "Enter Aadhaar Number"}]})
    assert not is_login_form({"text": "Login", "fields": [{"tag": "a", "label": "Login"}]})
    assert not is_login_form({"url": "https://example.test/download", "text": "Download Aadhaar",
                             "fields": [{"tag": "input", "label": "Captcha"}]})
    assert not is_login_form({"url": "https://example.test/application", "text": "Application form\nFooter\nLogin",
                             "fields": [{"tag": "input", "label": "Name"}, {"tag": "a", "label": "Login"}]})


async def test_early_continue_at_otp_hands_back_without_model_or_input(runtime):
    runtime.task["_human_resume"] = {"kind": "login", "auto_resume": True}
    runtime.raw.update(url="https://portal.example/verify", title="Verify your identity", text="Verify your identity",
                       fields=[{"index": 1, "tag": "input", "autocomplete": "one-time-code", "label": "Code"}])
    runtime.execute_element = AsyncMock()
    assert await runtime.handoff_login_if_needed()
    runtime.execute_element.assert_not_awaited()
    runtime.llm.send.assert_not_awaited()
    assert runtime.task["human_action"]["kind"] == "otp"
    assert runtime.task["human_action"]["auto_resume"] is True
