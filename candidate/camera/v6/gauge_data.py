"""JSON-safe integration boundary. No network, database or transport client."""
from common import GAUGES, clean


def payload(result, timestamp, cycle, *, capture_available=True):
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('Timestamp must be timezone aware')
    return clean(dict(
        schema_version=1, runtime_version='centrabus-v6-dev-20261023',
        cycle=cycle, captured_at=timestamp.isoformat() if capture_available else None,
        capture_available=capture_available, measurement_verified=False,
        processing_seconds=result.get('processing_seconds'),
        registration=result['prediction']['registration'],
        stage_errors=result.get('stage_errors', []),
        gauges={row['gauge']:row for row in result['rows']}))
