import asyncio

from privacy_guard.action_summary import approval_summary
from tests.test_agent_runtime import runtime_stub


def test_click_summary_is_plain_language_and_preserves_button_label():
    payload={"control":"Download Aadhaar","destination":"https://example.test","notice":"Inspect this control before allowing the agent to click it."}
    summary=approval_summary("submit",payload)
    assert summary["action"]=='Click “Download Aadhaar”.'
    assert summary["destination"]==payload["destination"]
    assert "क्लिक" in approval_summary("submit",payload,"hi")["action"]
    assert approval_summary("submit",{**payload,"option":"English"})["action"]=='Select “English” in “Download Aadhaar”.'
    assert "Address" in approval_summary("disclosure",{"field":"Address"})["action"]


async def test_approval_adds_readable_summary_without_changing_exact_payload(tmp_path):
    runtime=runtime_stub(tmp_path)
    task=runtime.task
    task.update(id="task",status="observing",events=[],language="en")
    payload={"control":"Continue","destination":"https://example.test"}
    work=asyncio.create_task(runtime.manager.approval(task,0,"submit","Review click",payload))
    for _ in range(20):
        if task.get("pending"):
            break
        await asyncio.sleep(0)
    pending=task["pending"]
    assert pending["summary"]["action"]=='Click “Continue”.'
    assert pending["payload"]==payload
    runtime.manager.approvals[pending["id"]].set_result(True)
    await work
