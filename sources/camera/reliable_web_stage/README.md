# Reliable live dashboard stage

`app.py` is staged from `web_dashboard_test_mode_stage/app.py`. It retains the
2-second `/api/gauges/latest` polling and reads `latest_gauges.json` beside
the installed app. On `fresh=false` or `status=retry_failed`, it displays
**STALE / RETRY FAILED** while retaining the last accepted gauge values. It
also shows per-gauge `CHECK IMAGE` flags. Transient API errors leave existing
browser values visible.

The old single-image `gauge_bridge.py` call was removed from manual photo
capture in this stage. That bridge would publish a 6/6 result without the new
consensus gate, violating the accepted-only JSON contract. Manual photo and
video functions otherwise remain as in the prior test-mode stage.

This stage has **not** replaced `/home/centrabus/centrabus_camera/app.py`.
The current Flask app has no gauge API or gauge cards. The workspace sandbox
does not permit writing to `/home/centrabus/centrabus_camera`, and the user
requested no production promotion. The stage can be reviewed and installed
later under an appropriate filesystem permission profile, with an app backup.

The producer defaults to
`/home/centrabus/centrabus_camera/latest_gauges.json` for deployment. Tests
used a workspace `--state-path` and left the web app unchanged.
