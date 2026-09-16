"""Small, explicit preflight calls with synthetic content only."""

import asyncio
import json

import httpx

from .config import FALLBACK_BASE_URL
from .gateway import validate_endpoint
from .provider_errors import classify_error, completion_options


async def check_keys(gateway, unlocked):
    candidates = [("OpenRouter / primary", validate_endpoint(gateway.base_url), gateway.model, tuple(gateway.api_keys)),
                  ("Gemini", FALLBACK_BASE_URL, gateway.fallback_model, tuple(gateway.fallback_api_keys))]
    semaphore = asyncio.Semaphore(3)

    async def check(provider, base, model, key, slot):
        result = {"provider": provider, "key_slot": slot, "ok": False}
        async with semaphore:
            if not unlocked():
                return {**result, "category": "locked", "message": "Vault locked; check cancelled."}
            payload = {"model": model, "messages": [{"role": "user", "content": 'Return exactly this JSON object: {"ok":true}'}],
                       **completion_options(base)}
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
                    response = await client.post(base + "/chat/completions", json=payload,
                                                 headers={"Authorization": "Bearer " + key})
            except httpx.TransportError:
                return {**result, "category": "connection", "message": "Could not reach this provider."}
            if not unlocked():
                return {**result, "category": "locked", "message": "Vault locked; result discarded."}
            if response.status_code != 200:
                failure = classify_error(response)
                return {**result, "status": response.status_code, "category": failure.category,
                        "message": failure.guidance}
            try:
                if len(response.content) > 1_000_000:
                    raise ValueError
                content = response.json()["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not isinstance(json.loads(content), dict):
                    raise ValueError
            except (ValueError, KeyError, IndexError, TypeError):
                return {**result, "status": 200, "category": "invalid_output",
                        "message": "Provider responded but did not return the required JSON object."}
            return {**result, "ok": True, "status": 200, "category": "ready", "message": "Text and JSON check passed."}

    return await asyncio.gather(*(check(provider, base, model, key, index + 1)
                                  for provider, base, model, keys in candidates
                                  for index, key in enumerate(keys)))
