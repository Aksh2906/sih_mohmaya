"""Value-free provider diagnostics. Raw provider errors never enter logs or task history."""

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ProviderError:
    category: str
    guidance: str
    rotate_key: bool = False

    def describe(self, provider: str, status: int) -> str:
        return f"{provider} request failed (HTTP {status}, {self.category}): {self.guidance}"


def classify_error(response: httpx.Response) -> ProviderError:
    # Inspect bounded text only to select our own static messages. Provider
    # messages may echo secrets, prompts or HTML, so never return any of it.
    text = ""
    if len(response.content) <= 64_000:
        try:
            body = response.json()
            if isinstance(body, list) and body:
                body = body[0]
            error = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(error, dict):
                text = str(error).lower()
        except (ValueError, TypeError):
            pass
    status = response.status_code
    if status == 400 and "store" in text and any(t in text for t in ("unknown", "unsupported", "cannot find")):
        return ProviderError("unsupported_parameter", "The provider does not accept the store parameter.")
    if any(t in text for t in ("api_key_invalid", "api key not valid", "api key expired", "api key was reported as leaked")):
        return ProviderError("invalid_key", "Replace this invalid, expired or blocked API key in Settings.", True)
    if status == 401:
        return ProviderError("invalid_key", "Check the API key and its provider in Settings.", True)
    if status == 402 or any(t in text for t in ("billing_disabled", "billing is disabled", "billing must be enabled", "free tier is not available")):
        return ProviderError("billing", "Check provider credits, billing and regional availability.", True)
    if any(t in text for t in ("safety", "moderation", "guardrail")):
        return ProviderError("content_blocked", "The provider blocked this request; review the task.")
    if status == 403:
        return ProviderError("permission_denied", "Check key restrictions, enabled APIs and model access.", True)
    if status == 429:
        return ProviderError("quota", "The key or project is rate limited or out of quota.", True)
    if status == 404 or ("model" in text and any(t in text for t in ("not found", "not supported", "does not exist"))):
        return ProviderError("model_unavailable", "Check the model ID and API endpoint; Gemini uses gemini-* IDs without google/.")
    if "failed_precondition" in text:
        return ProviderError("precondition", "Check Google AI Studio billing, region and project setup.")
    if status == 400:
        return ProviderError("invalid_request", "Check the model ID, message format and supported parameters. Key rotation cannot repair a malformed request.")
    if status == 408 or 500 <= status < 600:
        return ProviderError("temporary_failure", "The provider timed out or is temporarily unavailable.", True)
    return ProviderError("request_rejected", "Check provider configuration; this response is not retried.")


def completion_options(base_url: str) -> dict:
    options = {"response_format": {"type": "json_object"}, "max_completion_tokens": 2400}
    # Google's compatibility API rejects OpenAI's store field, even when false.
    if base_url.rstrip("/") != "https://generativelanguage.googleapis.com/v1beta/openai":
        options["store"] = False
    return options
