"""Verify the packaged public source and candidates; no device/network access."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def verify(manifest):
    checked = 0
    failures = []
    expected_paths = set()
    with manifest.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            relative = row['RelativePath']
            expected_paths.add(relative)
            path = (ROOT / relative).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                failures.append(relative)
                continue
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
            checked += 1
            if digest.hexdigest().lower() != row['SHA256'].lower():
                failures.append(relative)
    if manifest.name == 'SOURCE_SHA256.csv':
        actual_paths = {path.relative_to(ROOT).as_posix()
                        for path in (ROOT / 'sources').rglob('*') if path.is_file()}
        failures.extend('UNEXPECTED_FILE: ' + path for path in sorted(actual_paths - expected_paths))
    return {'manifest': manifest.name, 'checked': checked, 'failures': failures}


def main():
    results = [verify(ROOT / name) for name in ('SOURCE_SHA256.csv', 'CANDIDATE_SHA256.csv')]
    print(json.dumps(results))
    return 1 if any(result['failures'] for result in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
