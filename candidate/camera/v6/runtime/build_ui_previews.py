"""Offline before/after render from ONE historical raw frame; no camera."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import json,subprocess
HERE=Path(__file__).resolve().parent
BEFORE=HERE.parent/'centrabus_fast_runtime_v5_verification_dev_20261020'
OUT=HERE/'review/calibration_ui';OUT.mkdir(exist_ok=True)
code=r'''
import sys,json,cv2
from pathlib import Path
from datetime import datetime
sys.path.insert(0,sys.argv[1])
from fast_reader import annotate,readings_for,terminal_line
rows=json.loads(Path(sys.argv[2]).read_text())
r=next(r for r in rows if r['source_dataset']=='previous_v2_v3_live' and '115022' in r['image_path'])
image=cv2.imread(r['image_path'])
metadata=json.loads(Path(r['image_path']).with_suffix('.json').read_text())
time=datetime.fromisoformat(metadata['timestamp'])
from zoneinfo import ZoneInfo
time=time.astimezone(ZoneInfo('Asia/Manila'))
readings=readings_for(r)
cv2.imwrite(sys.argv[3],annotate(image,r,readings,1,time))
if len(sys.argv)>4:Path(sys.argv[4]).write_text(terminal_line(1,time,readings)+'\n')
'''
for name,source in [('before',BEFORE),('after',HERE)]:
 args=[sys.executable,'-B','-c',code,str(source),str(BEFORE/'review/full_regression/results.json'),str(OUT/f'{name}.jpg')]
 if name=='after':args.append(str(OUT/'terminal_preview.txt'))
 subprocess.run(args,check=True)
import cv2,numpy as np
before=cv2.imread(str(OUT/'before.jpg'));after=cv2.imread(str(OUT/'after.jpg'))
cv2.imwrite(str(OUT/'before_after.jpg'),np.hstack((cv2.resize(before,(960,662)),cv2.resize(after,(960,662)))))
cv2.imwrite(str(OUT/'after_footer.jpg'),after[-170:])
(OUT/'preview_provenance.json').write_text(json.dumps(dict(source_image='/home/centrabus/camera_calibration/reliable_live_logs/cycle_20261014_115022_959263/attempt_1.jpg',timestamp='2026-10-14T03:50:22.959672+00:00',timestamp_basis='historical attempt metadata, sensor time unavailable in archive',cycle='offline render only',detection='frozen source result; same geometry before/after'),indent=2))
print((OUT/'terminal_preview.txt').read_text())
