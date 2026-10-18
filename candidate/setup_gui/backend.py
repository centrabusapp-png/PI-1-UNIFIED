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


def readiness_report(report):
    """Recognize backend modes without enabling writes or claiming presence."""
    unverified = {"state": "unverified", "mode": None, "gpsPersistenceEnabled": None,
                  "setupPresenceEnabled": None,
                  "message": "API responded, but its readiness report was not recognized. "
                             "Dashboard check-in and GPS delivery remain unverified."}
    if not isinstance(report, dict) or report.get("ready") is not True or report.get("simulation") is not False:
        return unverified

    mode = report.get("mode")
    # START SYSTEM currently omits scope. Recognize only its explicit setup-only
    # contract; a missing scope never qualifies a GPS-writing backend as ready.
    setup_ready = (mode == "local-setup" and report.get("gpsPersistenceEnabled") is False
                   and report.get("setupPresenceEnabled") is True
                   and ("scope" not in report or report["scope"] == "backend-process-only"))
    gps_ready = (mode in (None, "real-lan") and report.get("gpsPersistenceEnabled") is True
                 and report.get("scope") == "backend-process-only")
    if not setup_ready and not gps_ready:
        return unverified

    presence = report.get("setupPresenceEnabled")
    return {"state": "ready", "mode": mode or "legacy-gps",
            "gpsPersistenceEnabled": gps_ready,
            "setupPresenceEnabled": presence if isinstance(presence, bool) else None,
            "message": ("Express setup API is ready. GPS writes are disabled on the backend. " if setup_ready else
                        "Express API is ready with GPS writes enabled on the backend. ")
                       + "Dashboard check-in, device authentication, bus assignment and fresh GPS delivery are still unverified."}


def check_backend(value):
    origin = public_origin(value)
    result = {"origin": origin, "state": "unreachable", "checkedAt": time.time(),
              "mode": None, "gpsPersistenceEnabled": None, "setupPresenceEnabled": None,
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
            result.update(readiness_report(report))
    except HTTPError as error:
        result.update(state="unverified", message=f"API returned HTTP {error.code} for /readyz. "
                      "Confirm the Express API address and readiness route. Redirects are not followed.")
    except (URLError, TimeoutError, OSError, ValueError):
        pass  # Do not echo raw server bodies, URLs from errors or manager details.
    return result
