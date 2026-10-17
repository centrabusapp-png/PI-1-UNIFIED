"""Public backend URL parsing only: no environment files, I/O or credentials."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


def normalize_backend_origin(value: str, *, cloud: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A backend origin is required.")
    value = value.strip()
    if re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("The backend origin contains invalid characters.")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
        if parsed.scheme not in ({"https"} if cloud else {"http", "https"}):
            raise ValueError
        if not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError
        address = None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in host.split(".")):
                raise ValueError
        if cloud and ((address is not None and not address.is_global)
                      or (address is None and ("." not in host or host.endswith((".local", ".localhost", ".localdomain"))))):
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port is not None:
            if not 1 <= port <= 65535:
                raise ValueError
            if port != (443 if parsed.scheme == "https" else 80):
                authority += f":{port}"
        return f"{parsed.scheme}://{authority}"
    except (ValueError, UnicodeError):
        # Never echo a supplied URL; it could contain accidentally pasted secrets.
        raise ValueError("Use a valid backend origin without credentials, an API path, query or fragment; cloud mode requires public HTTPS.") from None


def resolve_backend_origin(environment) -> str:
    cloud_value = environment.get("CENTRABUS_BACKEND_URL", "")
    legacy_value = environment.get("CENTRABUS_API_URL", "")
    if cloud_value and cloud_value.strip():
        cloud_origin = normalize_backend_origin(cloud_value, cloud=True)
        if legacy_value and legacy_value.strip():
            if normalize_backend_origin(legacy_value, cloud=False) != cloud_origin:
                raise ValueError("Backend URL settings conflict. Review the existing API URL before changing it; no endpoint was selected.")
        return cloud_origin
    if legacy_value and legacy_value.strip():
        # Preserve the existing local HTTP transport option; no automatic migration.
        return normalize_backend_origin(legacy_value, cloud=False)
    raise ValueError("CENTRABUS_BACKEND_URL or the existing CENTRABUS_API_URL is required.")
