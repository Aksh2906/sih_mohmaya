"""Validate a model-selected destination before browser navigation."""

import ipaddress
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .privacy import sanitize_text


class WebsiteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str | None = Field(max_length=2000)


def validate_website_url(result, private):
    try:
        selection = WebsiteSelection.model_validate(result)
        if selection.url is None:
            raise ValueError
        url = selection.url.strip()
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (sanitize_text(url, private) != url or parsed.scheme != "https"
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or any(c.isspace() for c in url)
                or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", host)
                or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        host_without_www = host.removeprefix("www.")
        if (re.fullmatch(r"google\.(?:com|[a-z]{2}|com\.[a-z]{2}|co\.[a-z]{2})", host_without_www)
                or host_without_www in {"bing.com", "duckduckgo.com", "search.yahoo.com", "search.brave.com"}):
            raise ValueError
        return f"https://{host}/"
    except (ValueError, TypeError):
        raise ValueError("The model could not select a valid website. Enter the website URL and start again.") from None
