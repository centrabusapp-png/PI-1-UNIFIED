# Device boundary: Pi1

- Device identity: `GPS-001`; preserve its existing secret and authenticated bus assignment.
- Expected account: `centrabus1`; expected hostname: `centrabus1-pi`. Reconfirm physically before writes.
- Candidate monitor default KML: `/home/centrabus1/centrabus_route.kml`.
- Candidate monitor output and sender default input: `/home/centrabus1/latest_gps.json`.
- Candidate sender default queue: `/home/centrabus1/centrabus-device/gps_offline_queue.sqlite3`.
- The exact existing queue path and protected environment overrides take precedence. A different existing path is a deployment blocker until reconciled; never create a fresh empty queue as a migration shortcut.
- Last reported state: original GPS monitor automatic boot verified, GPSD active, sender inactive/disabled, old tuner masked. This is historical operator evidence and must be rechecked.
- Pi1 had approximately 15-16s GNSS source age and near-capacity serial traffic. A successful copy or Windows test does not resolve that physical blocker.
- Do not install Pi2 credentials, copy Pi2 service units, modify R6, unmask the tuner or change GNSS/RAM/baud/boot configuration from this package.
- Stage camera runtime in a new, explicitly approved directory under the current account. Do not overwrite another camera installation or run a Flask server on the Express host's port 5000.
