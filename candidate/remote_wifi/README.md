# Remote Wi-Fi agent candidate

This folder contains the Pi-side client for the separate laptop Wi-Fi control feature. It is a source candidate; the package is not installed or started by `setup-pi.py` and does not replace `candidate/setup_gui/`.

The feature also requires the matching Express API and dashboard code in their own repositories, plus an approved public HTTPS deployment. Pushing these files does not activate remote Wi-Fi control on a Pi.

Before staging on either bus, verify that Pi's account, hostname and device ID match its existing private identity, and keep local recovery access available. The agent does not install a service or change startup settings.
