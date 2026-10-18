"""Guided local GPS session. Does not install services or change GNSS/boot."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "candidate/setup/local_run.py"), run_name="__main__")
