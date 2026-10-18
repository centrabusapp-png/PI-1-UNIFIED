# Pi1 dashboard alignment candidate

Baseline: latest Pi-originated commit `56bb1ce777335bc5313ec6c66a6b1ed198837c63`.
All 50 manifest-listed files and five additional GUI/docs/test paths matched the
Pi package before this review. Work is in a separate local review checkout; the
running Pi desktop setup and installed monitor/sender are unchanged.

## Prepared correction

The GUI now separates API readiness, backend GPS persistence and authenticated
dashboard check-in. It recognizes the actual setup-only `/readyz` response:

```json
{
  "ready": true,
  "mode": "local-setup",
  "simulation": false,
  "gpsPersistenceEnabled": false,
  "setupPresenceEnabled": true
}
```

This contract means API ready with GPS writes disabled. The current setup-only
response omits `scope`; the recognizer allows that omission for this exact mode
and flags only. If scope is provided, it must be `backend-process-only`.

The GPS-enabled `real-lan` response must retain `ready: true`,
`simulation: false`, `gpsPersistenceEnabled: true` and
`scope: backend-process-only`. The previous no-mode GPS response remains
compatible. Unknown or inconsistent reports remain unverified.

Neither readiness response authenticates GPS-001, confirms bus assignment or
proves Supabase/dashboard GPS delivery. The readiness check sends only the existing explicit,
credential-free GET `/readyz`, using the same bounds, no proxies or redirects.
It does not select a backend mode or enable persistence. The approved local GPS
runner retains its stricter GPS-enabled readiness gate unchanged.

## Setup-presence client prepared from the supplied contract

The Windows backend source remains unpushed and was not available for a source
merge. The operator supplied the full wire contract, implemented in the new
`candidate/setup_gui/presence.py` without replacing the Pi Wi-Fi adapter.

1. GET `/api/device-presence/challenge/GPS-001`, validate device/nonce/30-second
   lifetime, and reject nonce reuse.
2. Derive `K = HMAC-SHA256(SHA256(secret).digest(), UTF8(domain))`, with domain
   `centrabus-local-setup-checkin-v1`. The existing device secret is never sent.
3. Sign the exact newline-joined domain, challenge, device ID, true/false/null
   Wi-Fi text and internet value, without a trailing newline.
4. POST only `deviceId`, `challenge`, `wifiConnected` and `internet` to
   `/api/device-presence/check-in`, with Content-Type and X-Centrabus-Signature.
   No Origin header, cookies, Bearer token, raw secret or GPS data is sent.
5. Validate `ok`, device ID, original UTC millisecond acknowledgement time,
   `scope: setup-presence-only` and the domain/ack/device/challenge/time HMAC proof.
   Compare the proof in constant time before reporting Setup online.

Each request has a four-second socket timeout, bounded JSON, no proxy or redirect.
Each attempt requests a new challenge on the approximately 15-second cadence.
No immediate retries, queued replay or saved presence state are used. An ack's
45-second window cannot be extended by network delay, failed attempts or replay.
The client rejects ack times older than 45 seconds or over five seconds ahead of
the Pi clock. Unknown/stale network observations are sent as null/unknown.

Normal launch discovers only the documented existing runtime.env/GPS-001.env
paths by metadata; `--presence-env` can select existing paths explicitly. No
configuration contents are read until the operator approves Start check-ins.
The files must be regular, owner-private, inside this account's home and contain
GPS-001's existing identity/secret. No credentials are created or changed. The
browser and status receive no secret, key, signature, challenge or proof.

API readiness, backend GPS persistence and authenticated setup presence remain
separate statuses. Readiness does not start check-ins, and check-ins do not start
the sender or enable persistence. Stop/foreground-process exit/session expiry
stops the owned worker; backend presence then expires after its last accepted
check-in. Actual API/device authentication and dashboard visibility still need
an explicitly approved live check-in test. No Windows source merge or backend
deployment is claimed from the protocol tests.

## Physical observations during this review

The laptop API answered HTTP 200 in local-setup mode with GPS persistence disabled.
Pi1's installed GPS monitor was active; the installed sender was inactive/disabled
and its unchanged configuration still targeted the previous laptop address.
Entering an API address in the GUI does not edit that sender configuration.
These observations are not ongoing health guarantees or integration acceptance.

No live authenticated check-in, sender start, production write, installation, service
change, receiver setting, queue/secret/bus change or camera operation was performed.
The unchanged sources reference and native Wi-Fi adapter remain untouched.
