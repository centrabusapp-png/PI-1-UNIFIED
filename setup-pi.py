"""Open this Pi's local browser setup. No service installation or GPS changes."""
import os
import sys
if not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", "-B", os.path.abspath(__file__), *sys.argv[1:]])
import csv
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FOLDER = ROOT / "candidate/setup_gui"
REQUIRED = ("network.py", "server.py", "web/index.html", "web/style.css", "web/app.js")

def main():
    with (ROOT / "CANDIDATE_SHA256.csv").open(encoding="utf-8-sig", newline="") as source:
        expected = {row["RelativePath"]: row["SHA256"].lower() for row in csv.DictReader(source)}
    for name in REQUIRED:
        path = FOLDER / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected.get("candidate/setup_gui/" + name):
            raise SystemExit("STOP: setup GUI hash mismatch. No operations performed.")
    # Load only the verified sibling, without adding the checkout to sys.path.
    for name in ("network", "server"):
        spec = importlib.util.spec_from_file_location(name, FOLDER / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    sys.modules["server"].main("GPS-001")

if __name__ == "__main__":
    main()
