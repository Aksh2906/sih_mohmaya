import json

from privacy_guard.models import TaskRequest
from privacy_guard.task_brief import build_task_brief


def test_short_multilingual_request_is_expanded_without_changing_scope():
    original="मेरा आधार डाउनलोड करें"
    brief=json.loads(build_task_brief(original,"hi",[]))
    assert brief["original_request"]==original
    assert brief["response_language"]=="Hindi"
    assert len(brief["workflow"])>=6
    assert "saved file" in brief["workflow"][-1]
    assert "OTP" in json.dumps(brief) and "https://" not in json.dumps(brief)
    assert TaskRequest(goal=original).vision is True


def test_brief_does_not_disclose_private_user_input():
    brief=build_task_brief("Fill my name PRIVATE_CANARY_834", "en", ["PRIVATE_CANARY_834"])
    assert "PRIVATE_CANARY_834" not in brief
    assert "English" in brief
