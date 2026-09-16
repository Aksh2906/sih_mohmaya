"""Single audited HTTP boundary for Browser Use's native message protocol.

Native images are never forwarded. Only an immutable artifact produced by the
local screenshot filter can enter this transport, under the task review policy.
"""

import asyncio
import json
import re
import time
from copy import deepcopy

import httpx

from .config import FALLBACK_BASE_URL
from .diagnostics import logger, traced
from .gateway import canonical, digest, validate_endpoint
from .privacy import _safe_url, contains_private_text, sanitize_text
from .provider_errors import classify_error, completion_options
from .review_classifier import requires_review

SCHEMA_INSTRUCTION = "Return exactly one JSON object satisfying this schema. No markdown. "
SCREENSHOT_INSTRUCTION = (
    "Locally redacted screenshot. Use the accompanying sanitized DOM and local element indices to act. Black rectangles conceal private information; never infer concealed values."
)


class UnusableModelResponse(ValueError):
    """A model response failed before any requested browser action executed."""


def sanitize_agent_text(value, private):
    # Native messages can include URLs from page state and history. Query
    # strings and fragments commonly contain unknown session credentials.
    value = re.sub(r"https?://[^\s<>\"']+", lambda match: _safe_url(match.group(0), private), value)
    return sanitize_text(value, private)


def clean_strings(value, private):
    """Sanitize string leaves without changing JSON structure or action keys."""
    if isinstance(value, str):
        return sanitize_agent_text(value, private)
    if isinstance(value, list):
        return [clean_strings(item, private) for item in value]
    if isinstance(value, dict):
        return {key: clean_strings(item, private) for key, item in value.items()}
    return value


class GuardedChatModel:
    _verified_api_keys = True
    provider = "privacy-guard"

    def __init__(self, runtime):
        self.runtime = runtime
        self.model = runtime.manager.gateway.model
        self.base_url = validate_endpoint(runtime.manager.gateway.base_url)
        self._keys = tuple(runtime.manager.gateway.api_keys)
        self._key_index = 0
        self._api_key = runtime.manager.gateway.api_key
        self._fallback = False
        self._settings = self._settings_snapshot()
        # Only schemas generated locally by prepare() are trusted protocol text.
        # Keep exact copies across action, site-selection and visual requests.
        self._schema_messages: set[str] = set()

    def _settings_snapshot(self):
        gateway = self.runtime.manager.gateway
        return (gateway.model, validate_endpoint(gateway.base_url), tuple(gateway.api_keys),
                gateway.fallback_model, tuple(gateway.fallback_api_keys))

    @property
    def name(self):
        return self.model

    async def requires_review(self, kind, candidate, private, artifact=None):
        return await requires_review(self, kind, candidate, private, artifact)

    @property
    def model_name(self):
        return self.model

    def _messages(self, messages, private):
        cleaned = []
        browser_state_added = False
        for message in messages:
            message = message.model_dump() if hasattr(message, "model_dump") else message
            role = message.get("role")
            if role not in ("system", "user", "assistant"):
                raise ValueError("Unsupported agent message role")
            content = message.get("content", "")
            if isinstance(content, list):
                # Browser Use may retain native screenshot history. Never send it.
                content = "\n".join(part["text"] for part in content if part.get("type") == "text")
            if not isinstance(content, str):
                raise ValueError("Unsupported agent message content")
            # Native DOM can contain uninspectable frames, closed components,
            # or values absent from our local observer. Replace the whole state
            # message, including native metadata, rather than filtering fragments.
            if role == "user" and "<browser_state>" in content:
                if browser_state_added:
                    continue
                browser_state_added = True
                content = "<browser_state>\n" + self.runtime.model_observation() + "\n</browser_state>"
            try:
                structured = json.loads(content)
            except (ValueError, TypeError):
                pass
            else:
                content = json.dumps(clean_strings(structured, private), ensure_ascii=False)
            cleaned.append({"role": role, "content": sanitize_agent_text(content, private)})
        return cleaned

    def prepare(self, messages, output_format, private, artifact=None):
        if artifact:
            canonical_artifact = self.runtime.manager.gateway.image_store.get(artifact["id"])
            if artifact != canonical_artifact:
                raise ValueError("Screenshot artifact was altered")
            artifact = canonical_artifact
        cleaned = self._messages(messages, private)
        schema = output_format.model_json_schema() if output_format else None
        if schema:
            # json_object works with Browser Use's dynamically generated action
            # union, whose optional fields do not satisfy OpenAI strict schemas.
            schema_message = SCHEMA_INSTRUCTION + json.dumps(schema, ensure_ascii=False)
            self._schema_messages.add(schema_message)
            cleaned.insert(
                0,
                {
                    "role": "system",
                    "content": schema_message,
                },
            )
        if artifact:
            self.runtime.manager.gateway.image_store.verify(artifact["base64"])
            cleaned.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": SCREENSHOT_INSTRUCTION,
                        },
                        {"type": "image_url", "image_url": {"url": artifact["data_url"]}},
                    ],
                }
            )
        payload = {
            "model": self.model,
            "messages": cleaned,
            **completion_options(self.base_url),
        }
        self.check(payload, private, artifact)
        return payload

    def check(self, payload, private, artifact=None):
        options = completion_options(self.base_url)
        if set(payload) != {"model", "messages", *options}:
            raise ValueError("Unexpected model envelope")
        if any(payload[key] != value or type(payload[key]) is not type(value) for key, value in options.items()):
            raise ValueError("Unexpected model options")
        if payload["max_completion_tokens"] != 2400 or payload["model"] != self.model:
            raise ValueError("Model settings changed")
        texts, images = [payload["model"]], []
        for position, message in enumerate(payload["messages"]):
            if set(message) != {"role", "content"} or message["role"] not in ("system", "user", "assistant"):
                raise ValueError("Unexpected message metadata")
            content = message["content"]
            if isinstance(content, str):
                if position == 0 and message["role"] == "system" and content.startswith(SCHEMA_INSTRUCTION):
                    if content not in self._schema_messages:
                        raise ValueError("The model request schema was altered")
                    # Limits, property names and tool descriptions come from
                    # local code, not the vault/page. Scanning them as personal
                    # data rejects innocent matches such as maxLength: 100.
                    continue
                texts.append(content)
                continue
            if not isinstance(content, list):
                raise ValueError("Invalid message content")
            for part in content:
                if set(part) == {"type", "text"} and part["type"] == "text":
                    if not (
                        artifact and position == len(payload["messages"]) - 1
                        and message["role"] == "user" and part["text"] == SCREENSHOT_INSTRUCTION
                    ):
                        texts.append(part["text"])
                elif set(part) == {"type", "image_url"} and part["type"] == "image_url":
                    if set(part["image_url"]) != {"url"}:
                        raise ValueError("Unexpected image metadata")
                    images.append(part["image_url"]["url"])
                else:
                    raise ValueError("Unsupported model content")
        if images:
            if not artifact or images != [artifact["data_url"]]:
                raise ValueError("Unreviewed image in model request")
            canonical_artifact = self.runtime.manager.gateway.image_store.get(artifact["id"])
            if artifact != canonical_artifact or images != [canonical_artifact["data_url"]]:
                raise ValueError("Screenshot artifact was altered")
            self.runtime.manager.gateway.image_store.verify(images[0])
        if contains_private_text("\n".join(texts), private):
            raise ValueError("A known private value remains in the model request")
        if len(canonical(payload)) > 8_000_000:
            raise ValueError("Model context exceeded the size limit")

    @traced("agent_llm.send")
    async def send(self, payload, approved_hash, private, artifact=None):
        self.runtime.check()
        if digest(payload) != approved_hash:
            raise ValueError("Model payload changed after review")
        if artifact and (artifact["mask_report"].get("requires_manual_review")
                         or (self.runtime.image_state or {}).get("force_review")):
            state = self.runtime.image_state
            if not state or state.get("reviewed_hash") != approved_hash:
                raise PermissionError("Screenshot recovery requires approval of this exact screenshot and text.")
        gateway = self.runtime.manager.gateway
        if self._settings_snapshot() != self._settings or not self._api_key:
            raise ValueError("Model settings changed; start a fresh task")
        self.check(payload, private + self.runtime.private(), artifact)
        endpoint = self.base_url + "/chat/completions"
        started = time.monotonic()
        response = None
        reason = "Model connection failed"
        allow_fallback = True
        deadline = time.monotonic() + 90
        for index in range(self._key_index, len(self._keys)):
            self.runtime.check()
            if self._settings_snapshot() != self._settings:
                raise ValueError("Model settings changed; start a fresh task")
            if digest(payload) != approved_hash:
                raise ValueError("Model payload changed after review")
            self.check(payload, private + self.runtime.private(), artifact)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._key_index = index
            self._api_key = self._keys[index]
            provider = "Gemini" if self.base_url == FALLBACK_BASE_URL else "Primary provider"
            delay = min(0.5 * 2 ** index, 4)
            try:
                async with asyncio.timeout(min(30, remaining)), httpx.AsyncClient(
                    timeout=min(30, remaining), follow_redirects=False, trust_env=False,
                ) as client:
                    response = await client.post(
                        endpoint, content=canonical(payload),
                        headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                    )
            except (httpx.TransportError, TimeoutError):
                logger.warning("model.transport_failed provider=%s key_slot=%s", provider, index + 1)
                reason = provider + " connection failed; check your network or try another provider"
                rotate = True
                response = None
            else:
                self.runtime.check()
                logger.info("model.response provider=%s key_slot=%s status=%s", provider, index + 1, response.status_code)
                if response.status_code == 200:
                    break
                failure = classify_error(response)
                reason = failure.describe(provider, response.status_code)
                rotate = failure.rotate_key
                allow_fallback = (response.status_code in (400, 401, 402, 403, 404, 408, 429)
                                  or 500 <= response.status_code < 600) and failure.category != "content_blocked"
                logger.warning("model.failure category=%s", failure.category)
                try:
                    delay = max(delay, float(response.headers.get("retry-after", "0")))
                except ValueError:
                    delay = 5
                # Do not wait out a long provider cooldown in the demo, or
                # rotate immediately against a possibly shared project quota.
                if delay > 5:
                    rotate = False
            if not rotate or index + 1 == len(self._keys):
                break
            self.runtime.manager.event(
                self.runtime.task, reason + f"; trying key {index + 2} of {len(self._keys)}.", "warning",
            )
            await asyncio.sleep(delay)
        if response is None or response.status_code != 200:
            if allow_fallback:
                return await self._send_fallback(payload, private, artifact, reason)
            raise ValueError(reason)
        if len(response.content) > 1_000_000:
            raise ValueError("Model response exceeded the size limit")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise UnusableModelResponse("The model returned invalid JSON; no action was executed") from None
        # Never retain provider headers or raw content with possible private echoes.
        gateway.sent.append(deepcopy(payload))
        gateway.sent = gateway.sent[-20:]
        metrics = self.runtime.task.setdefault("metrics", {})
        metrics["model_calls"] = metrics.get("model_calls", 0) + 1
        metrics["model_latency_ms"] = round(
            metrics.get("model_latency_ms", 0) + (time.monotonic() - started) * 1000
        )
        if artifact:
            metrics["image_calls"] = metrics.get("image_calls", 0) + 1
        return result

    @traced("agent_llm._send_fallback")
    async def _send_fallback(self, payload, private, artifact, reason):
        gateway = self.runtime.manager.gateway
        self.runtime.check()
        if self._settings_snapshot() != self._settings:
            raise ValueError("Model settings changed; start a fresh task")
        if self._fallback or not gateway.fallback_api_key or not gateway.fallback_model:
            raise ValueError(reason)
        # One provider switch per task; new tasks always begin with the primary.
        self._fallback = True
        self.model = gateway.fallback_model
        self.base_url = FALLBACK_BASE_URL
        self._keys = tuple(gateway.fallback_api_keys)
        self._key_index = 0
        self._api_key = self._keys[0]
        self.runtime.manager.event(self.runtime.task, reason + "; switching to Gemini fallback.")
        if artifact:
            if not self.runtime.image_state:
                raise ValueError("Fallback image needs a fresh screenshot review")
            self.runtime._prepare_image_payload()
            await self.runtime.review_image_if_needed("Review screenshot for Gemini fallback")
            state = self.runtime.image_state
            await self.runtime.check_target()
            payload, artifact = state["payload"], state["artifact"]
        else:
            payload = {"model": self.model, "messages": deepcopy(payload["messages"]),
                       **completion_options(self.base_url)}
            self.check(payload, private + self.runtime.private())
            self.runtime.task["request"] = payload
            await self.runtime.review_text_if_needed(payload, private, "Review sanitized text for Gemini fallback")
        self.runtime.task["status"] = "reasoning"
        return await self.send(payload, digest(payload), private, artifact)

    async def ainvoke(self, messages, output_format=None, **_kwargs):
        from browser_use.llm.views import ChatInvokeCompletion

        await self.runtime.observe_for_model()
        await self.runtime.check_planning_progress()
        private = self.runtime.private()
        catalog = self.runtime.manager.catalog(self.runtime.task)
        messages = list(messages) + [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "private_reference_catalog": catalog,
                        "instruction": "Use input_ref(index, value_ref) for private fields. Never guess or type literal private values. If references are missing, call request_information. Stop before final submission.",
                    }
                ),
            }
        ]
        try:
            recovery = self.runtime.force_visual_recovery
            if recovery:
                messages.append({"role": "user", "content": (
                    "Planning has stalled. Reassess the original goal using the reviewed screenshot AND fresh "
                    "sanitized DOM indices. Choose a concrete supported next action; do not repeat ineffective "
                    "steps. If human input is needed, request it. Claim success only with verified goal evidence."
                )})
            if self.runtime.task.get("_vision") or recovery:
                state = await self.runtime.prepare_visual_request(messages, output_format, force_review=recovery)
                payload, artifact, private = state["payload"], state["artifact"], state["private"]
            else:
                artifact = None
                payload = self.prepare(messages, output_format, private)
                self.runtime.task["request"] = payload
                await self.runtime.review_text_if_needed(payload, private, "Review sanitized text context")
            self.runtime.task["status"] = "reasoning"
            result = await self.send(payload, digest(payload), private, artifact)
        finally:
            self.runtime.image_state = None
            self.runtime.force_visual_recovery = False
        # Output is also sanitized before entering native agent history/logs.
        safe = clean_strings(result, private)
        try:
            completion = output_format.model_validate(safe) if output_format else json.dumps(safe)
        except ValueError:
            raise UnusableModelResponse("The model returned an invalid action; no action was executed") from None
        if hasattr(completion, "action") and not completion.action:
            raise UnusableModelResponse("The model returned no action")
        return ChatInvokeCompletion(completion=completion, usage=None)
