"""Configuration does not read or persist credentials in the source tree."""

import os
from pathlib import Path

# Set before importing any Browser Use module.
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
os.environ["BROWSER_USE_LOGGING_LEVEL"] = "error"
os.environ["BROWSER_USE_SETUP_LOGGING"] = "false"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(
    os.environ.get("GUARD_DATA_DIR", "~/Library/Application Support/Dev Privacy Guard")
).expanduser()
PORT = int(os.environ.get("GUARD_PORT", "8765"))
DEMO_PORT = int(os.environ.get("GUARD_DEMO_PORT", "8766"))
ORIGIN = f"http://127.0.0.1:{PORT}"

DEFAULT_MODEL = "google/gemini-2.5-flash"
DEFAULT_MODEL_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_FALLBACK_MODEL = "gemini-2.5-flash"
FALLBACK_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def migrate_provider_settings(saved: dict) -> dict:
    """Retarget legacy settings without forwarding old credentials to a new host."""
    if not saved or saved.get("provider_settings_version") == 2:
        return saved
    migrated = {**saved, "provider_settings_version": 2,
                "fallback_model": DEFAULT_FALLBACK_MODEL, "fallback_api_key": ""}
    old_base = saved.get("base_url", "").rstrip("/")
    if old_base == FALLBACK_BASE_URL:
        migrated["fallback_model"] = saved.get("model") or DEFAULT_FALLBACK_MODEL
        migrated["fallback_api_key"] = saved.get("api_key", "")
    if old_base in (FALLBACK_BASE_URL, "https://api.openai.com/v1", ""):
        migrated.update(model=DEFAULT_MODEL, base_url=DEFAULT_MODEL_BASE_URL, api_key="")
    return migrated
