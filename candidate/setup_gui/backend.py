"""Explicit, credential-free readiness check; never sends telemetry or saves URLs."""
from __future__ import annotations
import ipaddress
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from backend_connection import normalize_backend_origin
from network import SetupError


def public_origin(value):
    try:
        origin = normalize_backend_origin(value, cloud=False)
        host = urlsplit(origin).hostname
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return normalize_backend_origin(origin, cloud=True)
        private_lan = address.version == 4 and any(address in ipaddress.ip_network(net)
                                                  for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        if not private_lan:
            return normalize_backend_origin(origin, cloud=True)
        return origin
    except (ValueError, TypeError, AttributeError):
        raise SetupError("Enter the Express API origin, such as the laptop's private IP and port 5000, "
                         "or a public HTTPS API origin. No credentials, dashboard path, query or fragment.") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_backend(value):
    origin = public_origin(value)
    result = {"origin": origin, "state": "unreachable", "checkedAt": time.time(),
              "message": "Express API unreachable. Check that the laptop backend is running, its API address is current, "
                         "and the Pi can reach it on this network."}
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(origin + "/readyz", timeout=5) as response:
            payload = response.read(4097)
            if response.status != 200 or len(payload) > 4096:
                result.update(state="unverified", message="API responded without a bounded readiness report.")
                return result
            try:
                report = json.loads(payload)
            except (ValueError, UnicodeError):
                report = None
            if (isinstance(report, dict) and report.get("ready") is True
                    and report.get("simulation") is False and report.get("gpsPersistenceEnabled") is True
                    and report.get("scope") == "backend-process-only"):
                result.update(state="ready", message="Express reports ready with GPS persistence enabled. "
                              "Device authentication, bus assignment and fresh dashboard GPS are still unverified.")
            else:
                result.update(state="unverified", message="API responded, but did not confirm the expected Express readiness flags. "
                              "This does not verify dashboard or Supabase ingestion.")
    except HTTPError as error:
        result.update(state="unverified", message=f"API returned HTTP {error.code} for /readyz. "
                      "Confirm the Express API address and readiness route. Redirects are not followed.")
    except (URLError, TimeoutError, OSError, ValueError):
        pass  # Do not echo raw server bodies, URLs from errors or manager details.
    return result
