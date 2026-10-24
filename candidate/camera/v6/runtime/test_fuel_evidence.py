"""Saved JPEG physical geometry fixtures, never raw-frame ground truth."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import math,unittest,json
import cv2,numpy as np
cv2.setNumThreads(2)
from fuel_angle_refinement import read_fuel_refined,_read_fuel_previous
from fuel_shaft import audit_shaft,complete_strict_shaft
from gauge_readers import _fit_simple_needle
ROOT=Path(__file__).resolve().parent.parent
SESSION=ROOT/'centrabus_fast_runtime_v5_continuous_dev_20261020/annotations/session_20261020_010623_790726_3565_de3491ea'

class FuelEvidenceTests(unittest.TestCase):
 def fixture(self,n):
  image=cv2.imread(str(next(SESSION.glob(f'cycle_{n:03d}_*'))))[74:1154]
  # Geometry measured from annotated JPEG replay, not asserted original metadata.
  x,y,w,h,hub,scale=((425,158,379,281,(184.4066,180.072),.93555086) if n==7 else (436,157,377,279,(182.8546,178.8283),.93007533))
  return image[y:y+h,x:x+w],hub,scale
 def test_actual_seven_eight_supported_tip_completion_excludes_warning_mark(self):
  for n in (7,8):
   image,hub,scale=self.fixture(n)
   old=_read_fuel_previous(image,scale=scale,hub=hub)
   new=read_fuel_refined(image,scale=scale,hub=hub)
   ev=new['fuel_geometry']
   self.assertEqual(ev['status'],'verified')
   self.assertEqual(ev.get('source'),'strict_with_audited_relaxed_completion')
   self.assertLess(abs(new['angle']-134),2)
   self.assertGreater(new['line_span'],110)
   self.assertGreater(math.dist(old['tip'],new['tip']),12*scale)
   self.assertEqual(np.count_nonzero(new['needle_mask'][84:105,56:75]),0)
   self.assertLess(ev['endpoint_spread'],3)
   self.assertLess(ev['tip_pixel_distance'],2)
 def test_warning_mark_alone_cannot_authorize_completion(self):
  mask=np.zeros((281,379),np.uint8)
  cv2.rectangle(mask,(55,82),(83,100),255,-1)
  raw=_fit_simple_needle(mask);raw['mask_mode']='strict'
  image=np.zeros((281,379,3),np.uint8);image[mask>0]=(60,60,220)
  ev,rep=complete_strict_shaft(image,raw,{},(184,180),.935)
  self.assertIsNone(ev);self.assertIsNone(rep)
 def test_unresolved_colour_variant_remains_ambiguous(self):
  # Sensitivity test, not a reconstruction of the missing original raw frame.
  image,hub,scale=self.fixture(8)
  hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
  hsv[:,:,1]=np.clip(hsv[:,:,1].astype(int)+6,0,255).astype(np.uint8)
  variant=cv2.cvtColor(hsv,cv2.COLOR_HSV2BGR)
  old=_read_fuel_previous(variant,scale=scale,hub=hub)
  ev,_=audit_shaft(old,hub,scale)
  new=read_fuel_refined(variant,scale=scale,hub=hub)
  self.assertEqual(ev['status'],'ambiguous')
  self.assertEqual(new['fuel_geometry']['status'],'ambiguous')
  self.assertEqual(new['base'],old['base']);self.assertEqual(new['tip'],old['tip'])
 def test_completion_preserves_projective_selection_and_other_five(self):
  from centrabus_dashboard_reader import read_frame
  path=ROOT/'centrabus_fast_runtime_v5_reliability_dev_20261019/validation/after/210/result.json'
  old=json.loads(path.read_text());new=read_frame(cv2.imread(old['image_path']))
  self.assertEqual(new['registration']['model'],'projective')
  for gauge in ('coolant','front','rear','battery','oil'):
   a,b=old['results'][gauge],new['results'][gauge]
   self.assertEqual(a['angle'],b['angle'])
   for key in ('base','tip','roi'):self.assertEqual(tuple(a[key]),tuple(b[key]))
if __name__=='__main__':unittest.main(verbosity=2)
