"""User-facing action descriptions, built from sanitized local control labels."""


def approval_summary(kind: str, payload: dict, language: str = "en") -> dict:
    hi = language == "hi"
    control = str(payload.get("control") or payload.get("field") or ("चुना हुआ नियंत्रण" if hi else "the selected control"))[:250]
    destination = str(payload.get("destination") or "")[:300]
    if kind == "submit" and payload.get("option") is not None:
        option = str(payload["option"])[:250]
        action = f'“{control}” में “{option}” चुनें।' if hi else f'Select “{option}” in “{control}”.'
    elif kind == "submit" and payload.get("control"):
        action = f'“{control}” पर क्लिक करें।' if hi else f'Click “{control}”.'
    elif kind == "submit":
        action = "इस वेबसाइट पर जाएँ।" if hi else "Open this website."
    elif kind == "disclosure":
        action = f'सहेजी गई जानकारी “{control}” में भरें।' if hi else f'Fill “{control}” using saved information.'
    elif kind == "image":
        action = "मॉडल को निजी जानकारी छिपाया गया चित्र और पृष्ठ का पाठ भेजें।" if hi else "Send the redacted screenshot and page text to the model."
    else:
        action = "मॉडल को सुरक्षित किया गया पृष्ठ संदर्भ भेजें।" if hi else "Send the sanitized page context to the model."
    return {"action": action, "destination": destination, "detail": str(payload.get("notice") or "")}
