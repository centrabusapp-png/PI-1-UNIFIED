"""Validate or exercise a condition file without camera initialization or writes."""
import sys
sys.dont_write_bytecode = True
import argparse
import json
import math
from condition_calibration import DEFAULT_PATH, GAUGES, load_calibration, classify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=DEFAULT_PATH)
    parser.add_argument('--gauge', choices=GAUGES)
    parser.add_argument('--reference-angle', type=float)
    parser.add_argument('--transitions', action='store_true', help='Sample every configured boundary on both sides and exactly at it')
    args = parser.parse_args()
    try:
        cfg = load_calibration(args.config)
        if args.reference_angle is not None:
            if args.gauge is None or not math.isfinite(args.reference_angle):
                parser.error('--reference-angle requires --gauge and a finite angle')
            print(json.dumps(classify(args.gauge,args.reference_angle,'DETECTED',calibration=cfg),indent=2))
        elif args.transitions:
            examples = []
            for gauge,entry in cfg['gauges'].items():
                boundaries = sorted({r[k] for r in entry['regions'] for k in ('min_deg','max_deg')})
                for boundary in boundaries:
                    for angle in (math.nextafter(boundary,-math.inf),boundary,math.nextafter(boundary,math.inf)):
                        result = classify(gauge,angle,'DETECTED',calibration=cfg)
                        examples.append(dict(gauge=gauge,boundary=boundary,angle=angle,label=result['label'],reason=result['reason']))
            print(json.dumps(dict(examples=examples,note='No boundaries means no approved regions in this file.'),indent=2))
        else:
            print(json.dumps(dict(valid=True,configuration_sha256=cfg['_sha256'],
                                  enabled={g:e['enabled'] for g,e in cfg['gauges'].items()},
                                  semantics=cfg['semantics']),indent=2))
    except (OSError,ValueError,TypeError,KeyError) as exc:
        parser.exit(1,f'Invalid condition configuration: {exc}\n')


if __name__ == '__main__': main()
