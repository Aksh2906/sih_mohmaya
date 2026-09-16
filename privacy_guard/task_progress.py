"""Resumable browser handoffs and explicit, source-backed task progress."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

HumanActionKind = Literal["login", "otp", "captcha", "manual", "review"]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    success_criteria: str = Field(min_length=1, max_length=400)
    status: Literal["pending", "in_progress", "done"] = "pending"
    source_ids: list[int] = Field(default_factory=list, max_length=12)


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStep] = Field(min_length=1, max_length=12)


class CompletionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    achieved: bool
    reason: str = Field(min_length=1, max_length=1000)
    human_action: HumanActionKind | None = None


def authentication_barrier(raw: dict) -> str | None:
    """Recognize active challenges, not an incidental login link in a footer."""
    for field in raw.get("fields", []):
        if not field.get("in_viewport", True) or field.get("tag") not in {"input", "textarea"}:
            continue
        if field.get("input_type") in {"hidden", "submit", "button", "checkbox", "radio"}:
            continue
        label = " ".join(str(field.get(key) or "") for key in ("label", "name", "id", "autocomplete"))
        if re.search(r"\b(otp|one[ -]?time[ -](password|passcode|code)|verification code)\b|ओटीपी", label, re.I):
            return "otp"
        if re.search(r"\b(captcha|security code|security text)\b|कैप्चा", label, re.I):
            return "captcha"
        if field.get("input_type") == "password":
            return "login"
    if re.search(r"/(?:login|log-in|signin|sign-in)(?:/|$)", urlsplit(raw.get("url", "")).path, re.I):
        return "login"
    return None


def is_login_form(raw: dict) -> bool:
    """Require editable controls and login context, not just a footer login link."""
    fields = raw.get("fields", [])
    inputs = [f for f in fields if f.get("tag") == "input" and f.get("in_viewport", True)
              and not f.get("disabled") and not f.get("readonly")
              and f.get("input_type") not in {"hidden", "submit", "button", "checkbox", "radio", "file"}]
    if not inputs:
        return False
    login = r"\b(?:log[ -]?in|sign[ -]?in)\b|लॉगिन|साइन इन"
    return bool(
        re.search(r"/(?:login|log-in|signin|sign-in)(?:/|$)", urlsplit(raw.get("url", "")).path, re.I)
        or re.search(login, raw.get("title", ""), re.I)
        or re.search(r"^\s*(?:login|log in|sign in|लॉगिन|साइन इन)\b", raw.get("text", ""), re.I)
        or any(f.get("tag") == "button" and re.search(login, f.get("label", ""), re.I) for f in fields)
        or any(f.get("autocomplete") == "current-password" for f in inputs)
    )


def completion_state(raw: dict) -> tuple:
    """Ignore moving geometry while checking that verification evidence is fresh."""
    return (
        raw.get("url"), raw.get("title"), raw.get("text"),
        tuple((f.get("index"), f.get("label"), f.get("value"), f.get("filled"), f.get("required"))
              for f in raw.get("fields", [])),
    )
