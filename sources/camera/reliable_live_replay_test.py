#!/usr/bin/env python3
"""Exercise ten producer cycles with saved development captures (no camera)."""

from __future__ import annotations

from pathlib import Path
import json
import shutil

import centrabus_reliable_live_reader as reliable


ROOT = Path(__file__).resolve().parent
SOURCE = Path("/home/centrabus/centrabus_camera/captures")
SEQUENCES = [
    ("104847_627779", "104804_403366", "104815_200291"),
    ("104836_756202", "104918_966489", "104929_972590"),
    ("104940_819909", "104929_972590", "104951_832177"),
    ("104825_912288", "104804_403366", "104815_200291"),
    ("104804_403366", "104815_200291"),
    ("104918_966489", "104929_972590"),
    ("104929_972590", "104940_819909", "104951_832177"),
    ("104825_912288", "104836_756202", "104847_627779"),
    ("104918_966489", "104951_832177"),
    ("104825_912288", "104836_756202", "104940_819909"),
]


def main() -> None:
    run_dir = ROOT / "reliable_live_logs" / f"replay_{reliable.timestamp_tag()}"
    run_dir.mkdir(parents=True)
    state_path = run_dir / "latest_gauges_test.json"
    cycles = []
    for index, sequence in enumerate(SEQUENCES, 1):
        used = iter(sequence)

        def replay_capture(target: Path, log):
            tag = next(used)
            source = SOURCE / f"dashboard_20261014_{tag}.jpg"
            shutil.copy2(source, target)
            print(f"REPLAY SOURCE: {source}", file=log)

        previous = reliable.load_state(state_path)
        summary = reliable.run_cycle(
            state_path, run_dir, reliable.ANGLE_TOLERANCE_SIMPLE,
            reliable.ANGLE_TOLERANCE_PRESSURE, capture_fn=replay_capture)
        cycles.append(summary)
        current = reliable.load_state(state_path)
        if summary["status"] == "retry_failed" and previous:
            assert current["gauges"] == previous["gauges"], index
            assert current["timestamp"] == previous["timestamp"], index
            assert current["fresh"] is False, index
        if index == 4:
            assert summary["consensus_retries"] == 1, summary
            assert summary["accepted_attempt"] == 3, summary
            assert current["gauges"]["front"]["angle"] > 80, current
        if index == 1:
            assert summary["orb_retries"] == 1, summary
        if index == 2:
            assert summary["gauge_exception_retries"] == 1, summary
    summary_path = reliable.summarize(cycles, run_dir, state_path)
    expected = [3, 3, 3, 3, 2, 2, 3, None, 2, None]
    actual = [cycle["accepted_attempt"] for cycle in cycles]
    assert actual == expected, (actual, expected)
    (run_dir / "replay_manifest.json").write_text(json.dumps({
        "kind": "saved-capture replay; not new camera captures",
        "source_directory": str(SOURCE),
        "sequences": SEQUENCES,
        "accepted_attempts": actual,
        "summary": str(summary_path),
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Replay summary: {summary_path}")


if __name__ == "__main__":
    main()
