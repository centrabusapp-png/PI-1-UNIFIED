# GPS-001: backend connection preparation

Prepared 2026-10-18. LOCAL CANDIDATE ONLY. Subsequent authorized verification
passed 60 mocked cases per package, combined 56-file syntax compilation and
full source/candidate hashes. Physical tests, real Wi-Fi and production
integration are still pending. No file was transferred, no device service
changed, and nothing was pushed/deployed.

## Included changes

- `candidate/gps/gps_sender.py` now accepts the public HTTPS origin setting
  `CENTRABUS_BACKEND_URL` as well as the existing `CENTRABUS_API_URL` option.
- New required sibling `candidate/gps/backend_connection.py` validates public
  connection settings without reading environment files or any credentials.
- Package-specific identity protection requires `GPS-001` at sender startup.
  This is a local configuration guard, not a replacement for backend authentication.
- `candidate/setup/device_settings.json` records PUBLIC reference metadata only.
- `candidate/setup/prepare_connection.py` creates a NEW review JSON file, not an
  installation or runtime environment. It does not contact the backend or Pi.
- `tests/test_connection_setup.py` contains deferred offline regression cases.

The GPS monitor, route KML, filtering, capture timestamps, 10-second sample-age
limit, retry/history/live scheduling, heartbeat, secret handling and SQLite queue
operations are unchanged by this update. The original `sources/` stays unchanged.
Camera code, real Wi-Fi operations, GNSS and boot/service logic are not changed.

## Device-specific reference, not live verification

| Setting | Expected reference |
|---|---|
| Device identity | GPS-001 |
| Account | centrabus1 |
| Hostname | centrabus1-pi |
| GPS JSON | /home/centrabus1/latest_gps.json |
| Queue | /home/centrabus1/centrabus-device/gps_offline_queue.sqlite3 |

Actual protected environment overrides and the EXISTING queue take precedence.
Never replace them with these defaults without independently verifying the device.
No setup helper opens the queue, reads GPS data or verifies installed service paths.

## Address rules

The value is the backend ORIGIN, for example `https://api.example.invalid` as a
nonfunctional illustration. It is NOT the dashboard/Vercel frontend address and
must NOT include `/api/gps/update`. The sender derives the GPS, heartbeat and
history endpoints once, retaining the current payload/authentication contract.

- New `CENTRABUS_BACKEND_URL`: requires public HTTPS. No embedded password,
  username, query, fragment, API path, local/private address or wildcard.
- Existing `CENTRABUS_API_URL`: a valid existing local HTTP/HTTPS origin remains
  supported when no new cloud value is supplied. No automatic endpoint switch.
- If both names exist, their normalized origins MUST agree. A mismatch stops
  startup instead of silently replacing the old destination. Review/backup the
  existing configuration before separately authorizing any environment edit.
- `CENTRABUS_DEVICE_ID` must be GPS-001. Keep its existing device secret private
  and unchanged. The backend must still independently authenticate it and resolve
  the correct active bus; this local helper cannot prove that mapping.

A review artifact or matching URL format is not a successful TLS/auth/GPS test.
The backend host/domain are not selected by this package. No production URL or
credential was invented, and no .env file was read or generated.

## Prepare a review file later

This step is OPTIONAL, OFFLINE and does NOT install or activate the sender.
After tests are authorized, from this package root, fill in the approved HTTPS
backend origin and independently reviewed actual Pi paths:

```text
python candidate/setup/prepare_connection.py --backend-url <APPROVED_HTTPS_BACKEND_ORIGIN> --gps-json-path <EXISTING_ABSOLUTE_PI_JSON_PATH> --queue-path <EXISTING_ABSOLUTE_PI_QUEUE_PATH> --output connection-review.json
```

Angle-bracket values are placeholders, not literal commands to paste. The helper
never accepts a password/token. It writes a new review file only, refuses existing
files, environment/live-snapshot filenames and runtime data paths, and requests
mode 0600 on Linux. The report explicitly says not installed/not enrolled, marks
physical operations disabled and leaves bus mapping/queue verification pending.
It does not update systemd, protected env files, profiles or the runtime sender.

## Before any separately approved installation

1. Review the completed local tests/compile/hash results in `LOCAL_VERIFICATION.md`.
   They cover the new sender configuration branch using mocks, not real Pi
   dependencies, Linux permissions or physical source/transport acceptance.
2. Confirm the actual account/host, installed versions, interpreter, source path,
   existing queue path, protected overrides and distinct backend bus association.
3. Verify reviewed candidate hashes against `CANDIDATE_SHA256.csv` and back up
   exact installed files/settings. Retain the source reference and working boot.
4. Stage the sender WITH `backend_connection.py` and the existing required helpers
   `telemetry_policy.py` and `raw_gps_motion.py`. Do not replace a monitor or camera
   just to update backend URL support. A single sender file is no longer sufficient.
5. Keep the sender disabled/stopped until authorized physical source-freshness,
   configuration and stationary E2E checks pass. No queue migration/deletion.
6. Only then authorize public URL changes and controlled sender operation. HTTP
   200 is insufficient: inspect `ok`, `applied`, correct device/bus and original
   GPS capture time. Never retimestamp old fixes or loosen freshness limits.

Pi1's historical 15-16 second source-age issue is not fixed by this package.
Do not assume that issue also exists on Pi2. Do not change receiver/boot settings
or copy another Pi's credentials as a cloud-deployment shortcut.

## Wi-Fi status and automatic management

Real agent enrollment, authenticated report delivery, manager inventory,
credential encryption, local rollback and physical fallback are NOT implemented
by this connection update. Wi-Fi metadata stays unverified and actions disabled.
Copying this package does not make the Pi appear Connected in Settings or run a
setup wizard automatically on boot. No installer/management systemd unit is added.

The Vercel dashboard must use the separately hosted backend; a Pi needs internet
to reach it. Saved-network auto-connect is a future verified local-manager feature;
a completely offline Pi cannot receive a new hotspot password from the cloud.

## Local checks completed; physical validation and rollback still pending

The operator authorized these local checks; all passed. Repeat on any changed
candidate and perform separately approved staged checks with the actual Pi
interpreter before installation:

```text
python -m unittest discover -s tests -p "test*.py"
python verify_package.py
python -m py_compile candidate/gps/backend_connection.py candidate/gps/gps_sender.py candidate/setup/prepare_connection.py
```

These checks are offline only, not physical acceptance. Physical staging or
installation still needs explicit approval. If the sender configuration/import
regresses after a later approved installation, restore the exact sender/helper
and public-setting backups. Do not delete the queue, replace the secret or change
the original monitor/boot. Stop/reconcile only the authorized test operation.

