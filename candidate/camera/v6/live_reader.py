#!/usr/bin/env python3
"""Supervised V6 monitor. Explicit --live-camera or --replay-image is required."""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True
import argparse
import gc
import json
import math
import signal
import time
from pathlib import Path
import cv2
from common import ROOT, GAUGES, clean, verify_runtime
from pipeline import process, pack, rows_for
import fast_reader as v5
import centrabus_dashboard_reader as reader
from presentation import HEADER_HEIGHT, FOOTER_HEIGHT, ROWS, header_lines, text_margin, font
from gauge_data import payload


def reading_text(row):
    value=row['value'];angle=row['angle_image_deg']
    reading='N/A' if value is None else (f'{value:.2f} kg/cm²' if row['gauge'] in ('front','rear') else f'{value:.1f}%')
    fields=[reading]
    if row['gauge'] in ('coolant','battery','oil'):
        fields.append(row['indicative_status']+' (indicative, unverified)')
    fields.extend([f'{angle:.2f}°' if angle is not None else 'N/A',
                   'AUTO / unverified' if row['outcome']=='SUCCESS' else row['outcome']])
    return row['gauge'].upper()+': '+' | '.join(fields)


def terminal(result, timestamp, capture_available=True):
    return '\n'.join((*header_lines(timestamp,capture_available=capture_available),
                       *(reading_text(row) for row in result['rows'])))


def annotate(frame, result, timestamp=None):
    """Retain the full dashboard; text occupies added margins only."""
    height,width=frame.shape[:2]
    image=cv2.copyMakeBorder(frame,HEADER_HEIGHT,FOOTER_HEIGHT,0,0,cv2.BORDER_CONSTANT,value=0)
    for row in result['rows']:
        roi=row['roi']
        if not roi: continue
        x,y,x2,y2=map(int,roi);color=(0,255,0) if row['outcome']=='SUCCESS' else (0,180,255)
        cv2.rectangle(image,(x,y+HEADER_HEIGHT),(x2,y2+HEADER_HEIGHT),color,2)
        if row['outcome']!='FAILED':
            a,z=[tuple(int(round(v)) for v in row[k]) for k in ('shaft_base_image','tip_image')]
            cv2.line(image,(a[0],a[1]+HEADER_HEIGHT),(z[0],z[1]+HEADER_HEIGHT),(0,255,255),2,cv2.LINE_AA)
            cv2.circle(image,(z[0],z[1]+HEADER_HEIGHT),4,(255,255,0),-1)
        cv2.putText(image,row['gauge'].upper(),(x+4,y2+HEADER_HEIGHT-8),cv2.FONT_HERSHEY_SIMPLEX,.45,color,1,cv2.LINE_AA)
    title,captured=(header_lines(timestamp) if timestamp is not None else
                    ('CENTRABUS - OFFLINE REVIEW','Saved development image; capture time not asserted'))
    image[:HEADER_HEIGHT]=text_margin(width,HEADER_HEIGHT,[(12,7,title,25,width-24,(255,230,0)),(12,41,captured,21,width-24,(255,255,255))])
    by_gauge={r['gauge']:r for r in result['rows']};entries=[]
    for index,pair in enumerate(ROWS):
        for col,gauge in enumerate(pair):
            entries.append((12+col*(width//2),14+index*48,reading_text(by_gauge[gauge]),23,width//2-24,(255,255,255)))
    image[HEADER_HEIGHT+height:]=text_margin(width,FOOTER_HEIGHT,entries)
    return image


def unavailable(reason):
    pred=pack(reader.unavailable(reason))
    # rows_for only needs image dimensions for endpoint validation.
    import numpy as np
    return dict(prediction=pred,rows=rows_for(pred,np.zeros((1,1,3),np.uint8)),stage_errors=[],processing_seconds=0.)


def monitor(camera, folder, *, max_cycles=None, interval=1., diagnostics=False,
            output=None, errors=None, observer=None, processor=process, sleep=time.sleep):
    output=sys.stdout if output is None else output;errors=sys.stderr if errors is None else errors
    cycle=0
    try:
        while max_cycles is None or cycle<max_cycles:
            cycle+=1;started=time.perf_counter();frame=None;available=False
            timestamp=v5.now_manila()
            try:
                frame,timestamp=camera.capture()  # Exactly one acquisition attempt.
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError('Capture timestamp must be timezone aware')
                timestamp=timestamp.astimezone(v5.MANILA);available=True
            except Exception as exc:
                frame=None;timestamp=v5.now_manila()
                result=unavailable(f'Capture failed: {type(exc).__name__}: {exc}')
            else:
                try: result=processor(frame)
                except Exception as exc: result=unavailable(f'Processing failed: {type(exc).__name__}: {exc}')
            print(terminal(result,timestamp,available),file=output,flush=True)
            data=payload(result,timestamp,cycle,capture_available=available)
            if diagnostics:
                print(json.dumps(dict(data,camera_metadata=getattr(camera,'last_metadata',{}) if available else {}),default=str,allow_nan=False),file=errors,flush=True)
            saved=None
            if frame is not None and cycle<=v5.ANNOTATION_CYCLES:
                try: saved=v5.save_annotation(folder,cycle,timestamp,annotate(frame,result,timestamp))
                except Exception as exc: print(f'Annotation failed: {exc}',file=errors,flush=True)
            if observer is not None: observer(cycle,data,saved)
            if cycle%v5.COLLECTION_INTERVAL==0:gc.collect()
            del frame,result,data
            delay=max(interval,1. if not available else 0.)-(time.perf_counter()-started)
            if delay>0:sleep(delay)
    except KeyboardInterrupt:
        print('Monitoring stopped; releasing camera.',file=output,flush=True)
    finally:camera.close()
    return cycle


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live-camera',action='store_true',help='Explicitly open the physical camera')
    mode.add_argument('--replay-image',type=Path,help='Saved image; no camera imports')
    parser.add_argument('--cycles',type=int,help='Stop after this many attempted cycles; default continuous')
    parser.add_argument('--interval',type=float,default=1.,help='Minimum cycle period, not guaranteed throughput')
    parser.add_argument('--settle-seconds',type=float,default=2.)
    parser.add_argument('--diagnostics',action='store_true',help='Print current-frame JSON evidence; no connection or upload')
    parser.add_argument('--output-root',type=Path,default=ROOT/'annotations')
    args=parser.parse_args(argv)
    if args.cycles is not None and args.cycles<1:parser.error('--cycles must be positive')
    if any(not math.isfinite(v) or v<0 for v in (args.interval,args.settle_seconds)):parser.error('Times must be finite and nonnegative')
    if args.replay_image is not None and not args.replay_image.is_file():parser.error('Replay file does not exist')
    camera=None
    try:
        verify_runtime(check_original=False);cv2.setNumThreads(2);font(23);reader.reference_features()
        signal.signal(signal.SIGTERM,v5._stop)
        folder=v5.session_folder(args.output_root)
        camera=(v5.ReplayCamera(args.replay_image) if args.replay_image is not None else
                v5.PersistentCamera(args.settle_seconds,collect_metadata=args.diagnostics))
        print(f'V6 unverified development candidate | one capture/cycle | first 10 attempted cycles save JPGs: {folder}',flush=True)
        owned=camera;camera=None
        monitor(owned,folder,max_cycles=args.cycles,interval=args.interval,diagnostics=args.diagnostics)
        return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(f'Runtime failed: {type(exc).__name__}: {exc}',file=sys.stderr,flush=True);return 1
    finally:
        if camera is not None:camera.close()


if __name__=='__main__':raise SystemExit(main())
