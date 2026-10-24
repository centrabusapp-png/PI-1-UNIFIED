"""Shared V6 imports and local report I/O. No camera is opened on import."""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
import importlib.abc
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import numpy as np

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
RUNTIME=ROOT/'runtime'
GAUGES=('fuel','coolant','front','rear','battery','oil')


class NoCamera(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in ('picamera2','libcamera'):
            raise RuntimeError('Camera imports are prohibited in this offline development pipeline')



sys.path.insert(0,str(RUNTIME))


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''): h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def clean(value):
    if isinstance(value,dict):
        return {k:clean(v) for k,v in value.items() if k not in ('needle_mask','pixels','mask')}
    if isinstance(value,np.ndarray):
        return clean(value.tolist()) if value.size<100 else dict(omitted_shape=list(value.shape))
    if isinstance(value,np.generic): return clean(value.item())
    if isinstance(value,(tuple,list)): return [clean(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value): return None
    return value


def atomic(path,data):
    path=Path(path)
    if not path.resolve().is_relative_to(ROOT): raise ValueError('Output outside development folder')
    path.parent.mkdir(parents=True,exist_ok=True)
    if not isinstance(data,(str,bytes)): data=json.dumps(clean(data),indent=2,allow_nan=False)
    if isinstance(data,str): data=data.encode()
    fd,temporary=tempfile.mkstemp(prefix='.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def csv_write(path,rows,fields=None):
    fields=fields or (list(rows[0]) if rows else ['filename','gauge'])
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
    for row in rows:
        writer.writerow({k:json.dumps(clean(v),separators=(',',':')) if isinstance(v,(list,dict,tuple)) else v for k,v in row.items()})
    atomic(path,stream.getvalue())


def verify_runtime(*, check_original=True):
    provenance=read_json(ROOT/'runtime_provenance.json')
    for name,sha in provenance['sha256'].items():
        paths=[RUNTIME/name]
        if check_original: paths.append(Path(provenance['source'])/name)
        for path in paths:
            if digest(path)!=sha: raise ValueError('V5 source/configuration hash changed: '+str(path))
    return provenance


def transform(points,matrix):
    m=np.asarray(matrix,float)
    if m.shape==(2,3): m=np.vstack((m,[0,0,1]))
    points=np.asarray(points,float).reshape(-1,2)
    p=np.column_stack((points,np.ones(len(points))))@m.T
    if not np.isfinite(p).all() or np.any(np.abs(p[:,2])<1e-10): raise ValueError('Invalid projection')
    return p[:,:2]/p[:,2:]


def line_delta(a,b):
    d=abs(a-b)%180
    return min(d,180-d)
