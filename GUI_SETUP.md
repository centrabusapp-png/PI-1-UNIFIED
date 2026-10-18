# GPS-001 browser setup candidate

This package now includes `setup-pi.py` and `candidate/setup_gui/`.
Read candidate/setup_gui/README.md completely for safety and integration limits.
This does not install or enable a remote Wi-Fi agent, GPS service or camera.

## On Windows now

Double-click **Open Setup Preview.cmd**. It opens the browser interface in an
explicit preview, with no real networks or fabricated device status. Close the
foreground launcher to finish. The runtime needs the existing Python 3.10+
installation. No package installation, private env or network-manager call.

## On the actual Pi, only after separate authorization

The reviewed entrypoint is `python3 -I -B setup-pi.py`, launched from this
package on its OWN Pi's desktop session. It opens a local browser automatically.
This default mode permits explicit status/scan only. No services are installed.

Connection trials additionally require `--allow-network-changes --interface
YOUR_VERIFIED_WIFI_INTERFACE` in a separately approved local maintenance session.
These options are not approval to run now. Verify actual manager, private local
login, permissions, original fallback profile and native checkpoint behavior first.
Do not use sudo, SSH-only operation, an archived installer, or the other Pi's
identity. Do not change GPS boot, GNSS, sender, camera, KML or queue.

The app binds only 127.0.0.1. You use the browser ON this Pi; it is not a LAN app
for the laptop/phone. The browser opens with a private in-memory session. If it
does not open automatically, STOP and review local browser availability rather
than exposing the server to the LAN or printing/copying the session token.

For a trial, select a scanned WPA2/WPA3 personal network, type the password and
approve Connect. Then verify connectivity and choose Keep or Roll back. Failing
to confirm asks NetworkManager to restore the original known-good connection.
Unknown recovery is a STOP, not proof of rollback. Automatic checkpoint behavior,
secret-agent prompts and saved-profile permissions need real Linux acceptance.

Fully offline/headless provisioning and remote dashboard command delivery are
not implemented by this local-console UI. No public listener or service has been
created. Physical verification is pending; Windows tests are not Pi validation.

## Rollback

Do not replace any installed GPS/camera/service file. Close the local GUI; any
unconfirmed connection trial requests rollback. Confirm the original network.
Only a new profile created by this app is eligible for cleanup after confirmed
rollback; existing profiles are never deleted. If Keep was explicitly chosen,
return to the previous network using the existing OS manager in an approved local
session; do not wipe Wi-Fi profiles. No GPS or database rollback is involved.

Do not push or transfer these local changes without reviewing hashes and approval.
