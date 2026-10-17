#!/usr/bin/env bash
set -euo pipefail

stage=/home/centrabus/camera_calibration/web_dashboard_test_mode_stage
target=/home/centrabus/centrabus_camera
expected=beccb4c6a60e10fdac8f8077323bd944806a4ad62a3bd16a7f9ccb1f4be5bf25
actual=$(sha256sum "$target/app.py" | cut -d' ' -f1)
if [[ "$actual" != "$expected" ]]; then
    echo "app.py changed since staging; installation stopped." >&2
    exit 1
fi
if [[ -e "$target/gauge_bridge.py" || -e "$target/latest_gauges.json" ]]; then
    echo "Gauge integration files already exist; installation stopped." >&2
    exit 1
fi

backup="$target/app.py.backup_test_mode_$(date +%Y%m%d_%H%M%S)"
cp -a "$target/app.py" "$backup"
cp -a "$stage/gauge_bridge.py" "$target/gauge_bridge.py"
cp -a "$stage/app.py" "$target/app.py"
echo "Backup: $backup"
echo "Installed: $target/app.py"
echo "Created: $target/gauge_bridge.py"
