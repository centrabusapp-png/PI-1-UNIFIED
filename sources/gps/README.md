# Raspberry Pi 2 GPS

GPS project source from `/home/centrabus/centrabus-device`.

## Contents

- `centrabus_gps_monitor.py`: GPS monitor using the route KML.
- `gps_sender.py` and `telemetry_policy.py`: telemetry sender, offline queue logic, and telemetry policy.
- `centrabus-gnss-readonly.py`: GNSS diagnostic utility.
- `route_monitor.py` and `route_dashboard.py`: route monitoring utilities.
- `gps_test.py`: GPS test utility.
- `kml_to_csv.py`: route conversion utility.
- `centrabus_route.kml` and `reference_route.csv`: static route reference files.

Runtime GPS JSON, recorded trips, SQLite databases and sidecars, environment files, credentials, caches, and historical script copies are excluded. The source scripts retain their original Raspberry Pi paths and configuration behavior.
