"""Read local test camera state without opening the camera or claiming validation."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path


def summarize(state, now=None, max_age_seconds=10.0):
    if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError('Maximum age must be positive')
    now = now or datetime.now(timezone.utc)
    age = None
    value = state.get('timestamp') if isinstance(state, dict) else None
    try:
        captured = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if captured.tzinfo is not None:
            age = (now - captured).total_seconds()
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    fresh = bool(isinstance(state, dict) and state.get('status') == 'accepted'
                 and state.get('fresh') is True and age is not None and 0 <= age <= max_age_seconds)
    return {'mode': 'TEST_ONLY', 'integrationReady': False, 'numericalValuesVerified': False,
            'capturedAt': value, 'ageSeconds': age, 'freshImageResult': fresh,
            'status': 'CURRENT_TEST_RESULT' if fresh else 'STALE_OR_UNAVAILABLE'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--max-age-seconds', type=float, default=10.0)
    args = parser.parse_args()
    try:
        state = json.loads(args.path.read_text(encoding='utf-8'))
        report = summarize(state, max_age_seconds=args.max_age_seconds)
    except (OSError, ValueError):
        report = summarize({})
    print(json.dumps(report, allow_nan=False))


if __name__ == '__main__':
    main()
