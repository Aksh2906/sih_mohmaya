"""Protocol coincidences must not disable the final private-data boundary."""

import base64
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from privacy_guard.agent_llm import SCHEMA_INSTRUCTION, GuardedChatModel
from privacy_guard.agent_runtime import ReferenceInput
from privacy_guard.gateway import SYSTEM, ModelGateway, digest
from privacy_guard.privacy import contains_private_text, sanitize_text


@pytest.fixture
def model():
    gateway = ModelGateway()
    gateway.model = "synthetic-model"
    gateway.api_key = "synthetic-transport-key"
    runtime = SimpleNamespace(
        manager=SimpleNamespace(gateway=gateway), task={}, check=lambda: None, private=lambda: []
    )
    return GuardedChatModel(runtime)


@pytest.mark.parametrize("secret", ["100", "Ref", "string", "false", "Ram", "Ted"])
def test_schema_coincidences_allow_fresh_requests_but_content_is_redacted(model, secret):
    for _ in range(2):
        payload = model.prepare(
            [{"role": "user", "content": "Saved detail: " + secret}], ReferenceInput, [secret]
        )
        assert payload["messages"][0]["content"] == (
            SCHEMA_INSTRUCTION + json.dumps(ReferenceInput.model_json_schema(), ensure_ascii=False)
        )
        assert payload["messages"][1]["content"] == "Saved detail: [REDACTED]"
        model.check(payload, [secret])


def test_schema_trust_is_exact_local_and_position_bound(model):
    payload = model.prepare([{"role": "user", "content": "Continue"}], ReferenceInput, ["100"])
    altered = deepcopy(payload)
    altered["messages"][0]["content"] += " Leaked detail: PRIVATE-CANARY"
    with pytest.raises(ValueError, match="schema was altered"):
        model.check(altered, ["PRIVATE-CANARY"])

    forged = deepcopy(payload)
    forged["messages"].append({"role": "user", "content": payload["messages"][0]["content"]})
    with pytest.raises(ValueError, match="known private value"):
        model.check(forged, ["100"])

    other_model = GuardedChatModel(model.runtime)
    with pytest.raises(ValueError, match="schema was altered"):
        other_model.check(payload, ["100"])


@pytest.mark.parametrize("content", [
    "Saved detail: 100",
    '{"personal_amount":100}',
    r'{"value":"\u0031\u0030\u0030"}',
    "Saved detail: %31%30%30",
    '{"100":"unexpected private key"}',
])
def test_trusting_schema_does_not_trust_user_content(model, content):
    payload = model.prepare([{"role": "user", "content": "Continue"}], ReferenceInput, ["100"])
    payload["messages"].append({"role": "user", "content": content})
    with pytest.raises(ValueError, match="known private value"):
        model.check(payload, ["100"])


async def test_new_vault_value_still_blocks_previously_prepared_request(model, monkeypatch):
    payload = model.prepare([{"role": "user", "content": "Saved detail: 100"}], ReferenceInput, [])
    model.runtime.private = lambda: ["100"]

    def no_http(**_kwargs):
        pytest.fail("Private content must be rejected before HTTP")

    monkeypatch.setattr("privacy_guard.agent_llm.httpx.AsyncClient", no_http)
    with pytest.raises(ValueError, match="known private value"):
        await model.send(payload, digest(payload), [])


@pytest.mark.parametrize("secret", ["Ted", "Red", "ACT", "[REDACTED]"])
def test_masking_marker_stays_stable_without_hiding_unmasked_values(secret):
    masked = sanitize_text("Saved detail: " + secret, [secret])
    assert masked == "Saved detail: [REDACTED]"
    for _ in range(3):
        masked = sanitize_text(masked, [secret])
        assert masked == "Saved detail: [REDACTED]"
        assert not contains_private_text(masked, [secret])
    if secret != "[REDACTED]":
        assert contains_private_text(masked + " Unmasked: " + secret, [secret])
        assert contains_private_text(json.dumps({"value": secret}), [secret])


@pytest.mark.parametrize("secret", ["[REDACTED]Smith", "Smith[REDACTED]", "Ted[REDACTED]Smith"])
def test_marker_inside_private_value_does_not_hide_its_surrounding_data(secret):
    assert contains_private_text(secret, [secret])
    assert sanitize_text(secret, [secret]) == "[REDACTED]"


def test_fixed_gateway_instructions_are_not_private_data():
    gateway = ModelGateway()
    payload = gateway.prepare("Saved detail: browser", {"fields": []}, [], [], ["browser"])
    assert payload["messages"][0]["content"] == SYSTEM
    assert "[REDACTED]" in payload["messages"][1]["content"][0]["text"]
    gateway.check(payload, ["browser"])
    altered = deepcopy(payload)
    altered["messages"][0]["content"] += " Private detail: browser"
    with pytest.raises(ValueError, match="Unexpected system content"):
        gateway.check(altered, ["browser"])


def test_screenshot_caption_coincidence_does_not_exempt_other_text(model):
    pixels = io.BytesIO()
    Image.new("RGB", (16, 16), "black").save(pixels, format="PNG")
    artifact = model.runtime.manager.gateway.image_store.create(
        base64.b64encode(pixels.getvalue()).decode(),
        {"complete": True, "viewport": {"width": 16, "height": 16}, "regions": []},
    )
    payload = model.prepare(
        [{"role": "user", "content": "Saved detail: Ted"}], ReferenceInput, ["Ted"], artifact
    )
    model.check(payload, ["Ted"], artifact)
    payload["messages"][-1]["content"][0]["text"] += " Saved detail: Ted"
    with pytest.raises(ValueError, match="known private value"):
        model.check(payload, ["Ted"], artifact)


async def test_schema_collision_reaches_transport_with_only_masked_task_content(model, monkeypatch):
    secrets = ["100", "Ted"]
    model.runtime.private = lambda: secrets
    payload = model.prepare(
        [{"role": "user", "content": "Saved details: 100; Ted"}], ReferenceInput, secrets
    )
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"index":1}'}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "privacy_guard.agent_llm.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    assert await model.send(payload, digest(payload), secrets) == {"index": 1}
    assert len(requests) == 1
    assert requests[0]["messages"][1]["content"] == "Saved details: [REDACTED]; [REDACTED]"
