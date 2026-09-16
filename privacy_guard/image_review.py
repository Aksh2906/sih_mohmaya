"""Decide when a verified redacted image needs human review."""

# These masks conceal page areas rather than detected personal information.
LAYOUT_MASKS = {"embedded_frame", "uninspected_media", "background_media", "uninspectable_component", "empty_private_field"}


def image_review_decision(report: dict, mode: str = "sensitive") -> dict:
    reasons = sorted({mask.get("reason", "private_region") for mask in report.get("masks", [])
                      if mask.get("reason", "private_region") not in LAYOUT_MASKS})
    recovery = report.get("requires_manual_review") is True
    required = recovery or mode == "always" or bool(reasons)
    return {"required": required, "mode": mode, "sensitive_reasons": reasons,
            "reason": "Automatic privacy checks need manual screenshot review." if recovery else
                      "Sensitive information was redacted." if reasons else
                      "Review every image is enabled." if required else
                      "No sensitive information was detected in the redacted regions."}
