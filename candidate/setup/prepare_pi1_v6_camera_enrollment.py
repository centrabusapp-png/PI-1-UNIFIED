#!/usr/bin/env python3
"""Prepare a private Pi1 V6 camera config and reviewed Supabase SQL; never executes either."""
from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


DEVICE_ID = "GPS-001"
CAMERA_ID = "CAM-001"
PROCESSOR = "centrabus-v6-76c4acf-ram-v1"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOT = PACKAGE_ROOT / ".local-run"


def validate_backend_url(value: str, approve_trusted_lan_http: bool) -> tuple[str, bool]:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (not host or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError
        port = parsed.port
    except (ValueError, TypeError):
        raise ValueError("Invalid backend origin") from None

    if parsed.scheme == "https":
        if port not in (None, 443):
            raise ValueError("HTTPS backend must use its standard port")
        return f"https://{host}" + (f":{port}" if port else ""), False

    if parsed.scheme != "http" or port != 5000 or not approve_trusted_lan_http:
        raise ValueError("HTTP requires explicit trusted-LAN approval and port 5000")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("Trusted-LAN HTTP requires a literal private IP address") from None
    trusted_networks = tuple(ipaddress.IPv4Network(cidr) for cidr in
                             ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    if address.version != 4 or not any(address in network for network in trusted_networks):
        raise ValueError("Trusted-LAN HTTP requires a private IPv4 address")
    return f"http://{address}:5000", True


def enrollment_sql(credential_hash: str) -> str:
    if len(credential_hash) != 64 or any(char not in "0123456789abcdef" for char in credential_hash):
        raise ValueError("Invalid generated credential hash")
    return f"""-- PRIVATE Pi1 V6 CAMERA ENROLLMENT. Do not commit or share this file.
-- Target: CAM-001 / GPS-001; processor: {PROCESSOR}.
-- One transaction, inserts one camera mapping and one 24-hour frame enrollment.
-- Refuses existing CAM-001/GPS-001 registrations or any camera already on GPS-001's bus.
-- Uses the current enabled GPS-001 bus assignment; never copies Pi2's mapping or credential.
begin;
do $pi1_v6_camera_enrollment$
declare
  selected_bus_id text;
  affected_rows integer;
begin
  select g.bus_id into strict selected_bus_id
  from public.gps_devices g
  join public.buses b on b.bus_id = g.bus_id
  where g.device_id = '{DEVICE_ID}'
    and g.enabled is true
    and b.status = 'active'
    and b.is_archived is not true
  for update of g, b;

  if exists (select 1 from public.camera_devices c
             where c.camera_id = '{CAMERA_ID}' or c.bus_id = selected_bus_id) then
    raise exception 'STOP: CAM-001 or a camera mapping for the GPS-001 bus already exists';
  end if;
  if exists (select 1 from public.camera_frame_sources s
             where s.camera_id = '{CAMERA_ID}' or s.device_id = '{DEVICE_ID}') then
    raise exception 'STOP: camera frame enrollment already exists for Pi1';
  end if;

  insert into public.camera_devices
    (camera_id, bus_id, enabled, status, metadata, created_at, updated_at)
  values ('{CAMERA_ID}', selected_bus_id, true, null, '{{}}'::jsonb,
          clock_timestamp(), clock_timestamp());
  get diagnostics affected_rows = row_count;
  if affected_rows <> 1 then
    raise exception 'STOP: expected exactly one Pi1 camera mapping';
  end if;

  insert into public.camera_frame_sources
    (camera_id, device_id, credential_hash, processor_version, enabled, expires_at)
  values ('{CAMERA_ID}', '{DEVICE_ID}', '{credential_hash}', '{PROCESSOR}',
          true, clock_timestamp() + interval '24 hours');
  get diagnostics affected_rows = row_count;
  if affected_rows <> 1 then
    raise exception 'STOP: expected exactly one Pi1 frame enrollment';
  end if;

  raise notice 'Pi1 V6 camera enrollment prepared; mapping/source rows: 1/1; TTL: 24 hours';
end
$pi1_v6_camera_enrollment$;
commit;
"""


def build_pair(backend_url: str, approve_trusted_lan_http: bool,
               secret_factory=secrets.token_hex) -> tuple[dict, str]:
    normalized_url, trusted_http = validate_backend_url(backend_url, approve_trusted_lan_http)
    secret = secret_factory(32)
    if not isinstance(secret, str) or len(secret) != 64 or any(c not in "0123456789abcdef" for c in secret):
        raise ValueError("Secure random camera credential unavailable")
    credential_hash = hashlib.sha256(secret.encode("ascii")).hexdigest()
    config = {
        "device_id": DEVICE_ID,
        "camera_id": CAMERA_ID,
        "secret": secret,
        "backend_url": normalized_url,
        "allow_trusted_lan_http": trusted_http,
    }
    return config, enrollment_sql(credential_hash)


def _secure_windows_directory(path: Path) -> None:
    """Remove inherited readers before any secret-bearing files are created."""
    identity = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"],
                              capture_output=True, text=True, timeout=5, check=True)
    rows = list(csv.reader(identity.stdout.splitlines()))
    sid = rows[0][-1] if rows and rows[0] else ""
    if not sid.startswith("S-"):
        raise RuntimeError("Unable to establish current Windows account ACL")
    args = ["icacls.exe", str(path), "/inheritance:r", "/grant:r",
            f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"]
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError("Unable to restrict private output folder ACL")


def _private_output_path(value: str) -> Path:
    target = Path(value).expanduser()
    if not target.is_absolute():
        raise ValueError("Private output directory must be an absolute path")
    private_root = PRIVATE_ROOT.resolve()
    resolved = target.resolve()
    if resolved == private_root or not resolved.is_relative_to(private_root):
        raise ValueError("Private output must be a new child of this package's ignored .local-run directory")
    if PRIVATE_ROOT.is_symlink() or resolved.exists():
        raise ValueError("Private output path already exists or uses a symlink")
    return resolved


def write_pair(output_directory: str, backend_url: str, approve_trusted_lan_http: bool) -> Path:
    target = _private_output_path(output_directory)
    config, sql = build_pair(backend_url, approve_trusted_lan_http)
    created: list[Path] = []
    try:
        target.mkdir(parents=True, mode=0o700)
        if os.name == "nt":
            _secure_windows_directory(target)
        else:
            os.chmod(target, 0o700)
        config_path = target / "camera-private.json"
        sql_path = target / "enroll-camera-PRIVATE.sql"
        for file_path, content in ((config_path, json.dumps(config, indent=2) + "\n"), (sql_path, sql)):
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(file_path, flags, 0o600)
            created.append(file_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                os.chmod(file_path, 0o600)
        return target
    except Exception:
        for file_path in created:
            try:
                file_path.unlink()
            except OSError:
                pass
        try:
            target.rmdir()
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", required=True, help="Pi1-reachable API origin")
    parser.add_argument("--new-private-directory", required=True,
                        help="New absolute child directory under this package's ignored .local-run")
    parser.add_argument("--approve-trusted-lan-http", choices=("YES",),
                        help="Required only for unencrypted HTTP on the isolated trusted LAN")
    args = parser.parse_args(argv)
    try:
        result = write_pair(args.new_private_directory, args.backend_url,
                            args.approve_trusted_lan_http == "YES")
    except Exception:
        print("PREPARATION_STOP: no Supabase request or Pi operation was performed; check public options/output path.",
              file=sys.stderr)
        return 1
    print("PRIVATE_PAIR_READY=" + str(result))
    print("TARGET=GPS-001/CAM-001; PROCESSOR=" + PROCESSOR + "; ENROLLMENT_TTL=24h from SQL execution")
    print("SUPABASE_SQL_NOT_EXECUTED=true; PI_CONFIG_NOT_INSTALLED=true; SECRET_VALUES_NOT_PRINTED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
