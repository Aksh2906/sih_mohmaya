"""Private identity records must not be substituted for unrelated form fields."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from privacy_guard.agent_runtime import BrowserAgentRuntime
from privacy_guard.gateway import ModelGateway
from privacy_guard.tasks import TaskManager


@pytest.mark.parametrize("label", [
    "Address *", "Permanent address", "Enter your residential address", "Address line 1",
    "postalAddress", "Email address", "Full name", "Location", "Details", "",
    "Name as on Aadhaar", "Aadhaar OTP", "Aadhaar-linked mobile number", "Aadhaar VID",
    "Address as on Aadhaar", "aadhaarAddress", "Aadhaar date of birth",
])
def test_aadhaar_cannot_fill_other_or_unknown_fields(label):
    assert not TaskManager.compatible(
        {"label": label, "input_type": "text"}, {"field_type": "aadhaar"}
    )

@pytest.mark.parametrize("kind", ["aadhaar", "pan", "bank_account", "phone", "person_name", "text"])
def test_address_requires_address_record(kind):
    assert not TaskManager.compatible(
        {"label": "Address *", "input_type": "text"}, {"field_type": kind}
    )

@pytest.mark.parametrize("label", ["Address *", "Permanent address", "postalAddress", "Address as on Aadhaar"])
def test_valid_postal_address_still_fills(label):
    assert TaskManager.compatible({"label": label}, {"field_type": "address"})


@pytest.mark.parametrize("label", ["Aadhaar", "Aadhaar number *", "Enter your 12-digit Aadhaar number", "Aadhar No."])
@pytest.mark.parametrize("input_type", ["text", "number", "tel"])
def test_aadhaar_number_fields_accept_aadhaar_only(label, input_type):
    field = {"label": label, "input_type": input_type}
    assert TaskManager.compatible(field, {"field_type": "aadhaar"})
    assert not TaskManager.compatible(field, {"field_type": "address"})
    assert not TaskManager.compatible(field, {"field_type": "phone"})


def test_email_address_is_not_postal_address():
    field = {"label": "Email address *", "input_type": "text"}
    assert TaskManager.compatible(field, {"field_type": "email"})
    assert not TaskManager.compatible(field, {"field_type": "address"})


async def test_wrong_model_reference_is_blocked_before_value_resolution_or_browser_write(monkeypatch):
    import browser_use.browser.profile as profile

    # This is a local executor test, not a macOS display/browser test.
    monkeypatch.setattr(profile, "get_display_size", lambda: None)
    browser = SimpleNamespace(execute=AsyncMock())
    manager = SimpleNamespace(
        gateway=ModelGateway(), browser=browser, approval=AsyncMock(),
        compatible=TaskManager.compatible,
        # Deliberately omit the actual private value: rejection must not need it.
        resolve=lambda *_args: {"field_type": "aadhaar"},
    )
    runtime = BrowserAgentRuntime(manager, {"_generation": 0})
    runtime.private = lambda: []
    runtime.field = AsyncMock(return_value={
        "index": 7, "label": "Address *", "tag": "textarea", "input_type": "text",
    })
    result = await runtime.execute_element("input_ref", 7, "ref_synthetic_aadhaar")
    assert result.error == "The private reference type is not compatible with this field."
    browser.execute.assert_not_awaited()
    manager.approval.assert_not_awaited()


def test_password_records_only_fill_password_fields():
    from privacy_guard.tasks import TaskManager
    assert TaskManager.compatible({"input_type":"password","label":"Password"},{"field_type":"password"})
    assert not TaskManager.compatible({"input_type":"password","label":"Password"},{"field_type":"aadhaar"})
    assert not TaskManager.compatible({"input_type":"email","label":"Email"},{"field_type":"password"})


def test_hindi_aadhaar_does_not_match_address_or_name():
    from privacy_guard.tasks import TaskManager
    record={"field_type":"aadhaar"}
    assert TaskManager.compatible({"label":"आधार नंबर","input_type":"text"},record)
    for label in ["पता", "आधार पर नाम", "आधार पर जन्म तिथि", "आधार ओटीपी"]:
        assert not TaskManager.compatible({"label":label,"input_type":"text"},record)
