#!/usr/bin/env python3
"""Create a NEW public connection review file. Never install, connect or start."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent


def connection_helpers():
    path = HERE.parent / "gps" / "backend_connection.py"
    spec = importlib.util.spec_from_file_location("package_backend_connection", path)
    if spec is None or spec.loader is None:
        raise ValueError("The packaged backend connection helper is missing.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_plan(backend_url, gps_json_path, queue_path):
    defaults = json.loads((HERE / "device_settings.json").read_text(encoding="utf-8"))
    if defaults.get("device_id") != "GPS-001" or defaults.get("physical_operations_enabled") is not False:
        raise ValueError("Pi1 setup metadata is not the expected disabled candidate.")
    origin = connection_helpers().normalize_backend_origin(backend_url, cloud=True)
    for value in (gps_json_path, queue_path):
        if not isinstance(value, str) or not value.startswith("/") or ".." in PurePosixPath(value).parts or any(ord(char) < 32 for char in value):
            raise ValueError("Provide reviewed absolute Pi paths, not relative or traversal paths.")
    if gps_json_path == queue_path:
        raise ValueError("GPS input and queue paths must be distinct.")
    return {
        "schema_version": 1,
        "kind": "public-connection-review-only",
        "device_id": defaults["device_id"],
        "expected_account": defaults["expected_account"],
        "expected_hostname": defaults["expected_hostname"],
        "runtime_changes_applied": False,
        "physical_operations_enabled": False,
        "credentials_included": False,
        "proposed_public_settings": {
            "CENTRABUS_DEVICE_ID": defaults["device_id"],
            "CENTRABUS_BACKEND_URL": origin,
            "CENTRABUS_GPS_JSON_PATH": gps_json_path,
            "CENTRABUS_GPS_QUEUE_PATH": queue_path,
        },
        "endpoints": {"health": origin + "/health", "gps": origin + "/api/gps/update",
                      "heartbeat": origin + "/api/gps/heartbeat", "history": origin + "/api/gps/history/batch"},
        "existing_secret_must_be_preserved": True,
        "existing_queue_requires_independent_verification": True,
        "backend_identity_and_bus_mapping_verified": False,
        "wifi_management": {"enabled": False, "enrolled": False, "network_manager": "unverified"},
        "remaining_gates": ["local_regressions", "approved_host_and_tls", "actual_device_paths_and_identity",
                            "existing_queue_and_secret_preserved", "physical_freshness", "stationary_e2e"],
    }


def write_new_plan(path, plan):
    target = Path(path)
    runtime_paths = plan["proposed_public_settings"]
    if target.suffix.lower() != ".json" or target.name in {"latest_gps.json", "latest_gauges.json"}:
        raise ValueError("Use a new review JSON filename, never a live snapshot or environment file.")
    if target.resolve() in {Path(runtime_paths[key]).resolve() for key in ("CENTRABUS_GPS_JSON_PATH", "CENTRABUS_GPS_QUEUE_PATH")}:
        raise ValueError("The review file cannot be a runtime data path.")
    # Serialize BEFORE touching the new output; never truncate an existing file.
    payload = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--gps-json-path", required=True)
    parser.add_argument("--queue-path", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        plan = make_plan(args.backend_url, args.gps_json_path, args.queue_path)
        write_new_plan(args.output, plan)
    except (OSError, ValueError):
        print("REVIEW_NOT_CREATED: check the public URL, package metadata, reviewed paths and a new output filename; values redacted.", file=sys.stderr)
        return 2
    print("PUBLIC_REVIEW_CREATED: not installed, not connected, credentials not collected; Wi-Fi actions disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
