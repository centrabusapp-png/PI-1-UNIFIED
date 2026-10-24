"""Render every unique archived raw fixture offline, without saving images."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json,re
from unittest.mock import patch
import cv2,numpy as np
cv2.setNumThreads(2)
import fast_reader as runtime
import presentation
HERE=Path(__file__).resolve().parent;OUT=HERE/'review/calibration_ui';BEFORE=HERE.parent/'centrabus_fast_runtime_v5_verification_dev_20261020'
rows=json.loads((BEFORE/'review/full_regression/results.json').read_text())+json.loads((BEFORE/'review/expanded_after.json').read_text())+json.loads((OUT/'additional_after.json').read_text())
from datetime import datetime,timezone
timestamp=datetime(2026,10,19,19,49,16,tzinfo=timezone.utc)
counts=dict(frames=0,rendered_needle_lines=0,missing_detections=0,suppressed_numbers=0)
for row in rows:
 readings=runtime.readings_for(row);text=runtime.terminal_line(999999,timestamp,readings)
 lines=text.splitlines();assert lines[:3]==['CENTRABUS','2026-10-20 03:49:16 PHT','']
 assert [s.split(':')[0] for s in lines[3:]]==[g.upper() for g in presentation.ORDER]
 assert not any(s in text for s in ('PROVISIONAL','DETECTED','UNDETECTED','UNCERTAIN','STATUS_UNVERIFIED','UNVERIFIED','Cycle'))
 assert 'EMPTY' not in text and 'FULL' not in text
 for g,r in readings.items():
  line=presentation.reading_text(g,r);parts=line.split(': ',1)[1].split(' | ')
  if r['status']!='DETECTED':
   assert all(x=='N/A' for x in parts);counts['missing_detections']+=1
  else:
   assert re.fullmatch(r'\d+\.\d{2}°',parts[-1]),line
   if r['value'] is None:
    assert parts[0]=='N/A' and not r['conversion_supported'];counts['suppressed_numbers']+=1
   elif g in ('front','rear'):assert re.fullmatch(r'\d+\.\d{2} kg/cm²',parts[0]),line
   else:assert re.fullmatch(r'\d+\.\d%',parts[0]),line
  if g in ('battery','coolant','oil'):assert parts[1]=='N/A'
 frame=cv2.imread(row['image_path']);assert frame is not None
 # Record the actual rendered strings and line endpoints; compare against the
 # common terminal and original candidate, not an OCR of rescaled JPEG text.
 with patch.object(presentation,'text_margin',wraps=presentation.text_margin) as margins,patch.object(runtime.cv2,'line',wraps=cv2.line) as strokes:
  image=runtime.annotate(frame,row,readings,999999,timestamp)
 entries=[entry for call in margins.call_args_list for entry in call.args[2]]
 assert [e[2] for e in entries[:2]]==lines[:2]
 assert [e[2] for e in entries[2:]]==[presentation.reading_text(g,readings[g]) for pair in presentation.ROWS for g in pair]
 assert image.shape==(frame.shape[0]+244,frame.shape[1],3)
 expected=[]
 if not any(w.startswith(('registration:','geometry:')) for w in row.get('warnings',[])):
  for g in runtime.GAUGES:
   if readings[g]['status']=='DETECTED' and g in row.get('rois',{}):
    raw=row['results'][g];x,y,_,_=row['rois'][g]
    expected.append(((x+int(raw['base'][0]),y+74+int(raw['base'][1])),(x+int(raw['tip'][0]),y+74+int(raw['tip'][1]))))
 assert [(c.args[1],c.args[2]) for c in strokes.call_args_list]==expected
 counts['rendered_needle_lines']+=len(expected);counts['frames']+=1
 del image,frame,margins,strokes
print(counts)
(OUT/'presentation_archive_check.json').write_text(json.dumps(dict(**counts,passed=True,physical_accuracy_claim=False),indent=2))
