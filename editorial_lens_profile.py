#!/usr/bin/env python3
"""Optical distortion calibration for DSC09818 Sony ILCE-7M4 + FE 16-35 F2.8 GM @ 16mm.
Third-party Hugin/PTLens calibration from https://discuss.pixls.us/t/create-lens-calibration-data-for-lensfun/9848?page=3
NOT official Sony calibration. Apply BEFORE measured PerspectiveLock.
"""
import argparse, hashlib, json
from pathlib import Path
import cv2, numpy as np, tifffile
PROFILE="SONY_FE1635F28GM_16MM_PTLENS_3RD_PARTY"
CALIBRATION="https://discuss.pixls.us/t/create-lens-calibration-data-for-lensfun/9848?page=3"
ABC=(.021,-.052,-.003)
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for x in iter(lambda:f.read(1024*1024),b""):h.update(x)
 return h.hexdigest()
def exif(p):
 with tifffile.TiffFile(p) as f:
  x=f.pages[0].tags
  t=x["ExifTag"].value
  return dict(make=x["Make"].value,camera=x["Model"].value,lens=t["LensModel"],focal_mm=t["FocalLength"][0]/t["FocalLength"][1])
def correct(source,raw,out,qa):
 metadata=exif(raw)
 if metadata!={"make":"SONY","camera":"ILCE-7M4","lens":"FE 16-35mm F2.8 GM","focal_mm":16.0}:
  raise RuntimeError("OPTICAL_HOLD_EXIF_MISMATCH:"+repr(metadata))
 if sha(raw)!="1b4344f4b8ca629830254850fbcbaf2931c83c00a158ab87d84e5ee7722046d5":
  raise RuntimeError("OPTICAL_HOLD_RAW_HASH")
 im=cv2.imread(source,cv2.IMREAD_COLOR)
 if im is None:raise RuntimeError("OPTICAL_HOLD_UNREADABLE_SOURCE")
 h,w=im.shape[:2];cx=(w-1)*.5;cy=(h-1)*.5;unit=min(w,h)*.5
 a,b,c=ABC;dst=np.empty_like(im)
 n=0;good=0;maxshift=0.;mind=float("inf")
 for y0 in range(0,h,192):
  y1=min(h,y0+192);yy,xx=np.mgrid[y0:y1,0:w].astype(np.float32)
  u=(xx-cx)/unit;v=(yy-cy)/unit;r=np.sqrt(u*u+v*v)
  f=1-a-b-c+c*r+b*r*r+a*r*r*r
  derivative=1-a-b-c+2*c*r+3*b*r*r+4*a*r*r*r
  mind=min(mind,float(derivative.min()))
  if mind<=0:raise RuntimeError("OPTICAL_HOLD_NON_MONOTONIC_MAP")
  mx=(u*f)*unit+cx;my=(v*f)*unit+cy
  good+=int(((mx>=0)&(mx<w-1)&(my>=0)&(my<h-1)).sum());n+=mx.size
  maxshift=max(maxshift,float(np.max(np.hypot(mx-xx,my-yy))))
  dst[y0:y1]=cv2.remap(im,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
 if not cv2.imwrite(out,dst,[cv2.IMWRITE_JPEG_QUALITY,95]):raise RuntimeError("OPTICAL_HOLD_WRITE")
 obj=dict(project="tropicana-park",frame="DSC09818",status="PROFILE_APPLIED",calibration_type="third_party_not_Sony_official",profile=PROFILE,source=CALIBRATION,coefficients_abc=list(ABC),radius="half_short_image_edge",exif=metadata,raw_sha256=sha(raw),source_sha256=sha(source),output_sha256=sha(out),image_size=[w,h],max_pixel_shift=round(maxshift,3),valid_source_fraction=round(good/n,7),minimum_radial_derivative=round(mind,5),residual_manual_line_qc="REQUIRED_BEFORE_FINAL",tangential_decentering="not_calibrated",chromatic_aberration="not_applied")
 Path(qa).write_text(json.dumps(obj,indent=2,ensure_ascii=False),encoding="utf8")
 print(json.dumps(obj,ensure_ascii=False))
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("operation",choices=["correct"]);p.add_argument("--source",required=True);p.add_argument("--raw",required=True);p.add_argument("--output",required=True);p.add_argument("--report",required=True);a=p.parse_args()
 correct(a.source,a.raw,a.output,a.report)
