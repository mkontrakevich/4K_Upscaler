#!/usr/bin/env python3
"""Acceptance tests for active TECH MASTER V2 correction stages."""
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))

from editorial_auto_optics import apply as apply_optics, _profile_for
from editorial_auto_photometry import apply as apply_photo
from editorial_auto_vertical_perspective import _vertical_vp, _pixel_homography

# Unknown/non-RAW optics are never guessed.
raster=np.full((480,640,3),22000,np.uint16)
out,e=apply_optics(raster,Path("/tmp/not-used.jpg"),"RASTER")
assert e["status"]=="HOLD"
assert e["reason"]=="OPTICS_METADATA_NOT_TRUSTED_FOR_RASTER_SOURCE"
assert np.array_equal(out,raster)

# The verified Sony 16 mm profile is discoverable by exact metadata only.
p=_profile_for({"make":"SONY","camera":"ILCE-7M4","lens":"FE 16-35mm F2.8 GM","focal_mm":16.0})
assert p and p["id"]=="SONY_FE1635F28GM_16MM_PTLENS_3RD_PARTY"
assert _profile_for({"make":"SONY","camera":"ILCE-7M4","lens":"UNKNOWN","focal_mm":16.0}) is None

# Measurably dark neutral signal gets a bounded global exposure correction.
dark=np.full((520,760,3),np.uint16(round(.025*65535)),np.uint16)
fixed,proof=apply_photo(dark,"CAMERA_RAW")
assert proof["status"]=="APPLIED",proof
assert proof["exposure_status"]=="APPLIED",proof
assert 0.08<=proof["exposure_ev"]<=.65
assert proof["HDR_applied"] is False and proof["local_tone_mapping"] is False
assert proof["generative_processing"] is False
assert float(np.mean(fixed))>float(np.mean(dark))

# A small measurable neutral color cast is corrected without changing geometry.
cast=np.empty((520,760,3),np.uint16)
cast[:,:,0]=np.uint16(round(.300*65535))
cast[:,:,1]=np.uint16(round(.285*65535))
cast[:,:,2]=np.uint16(round(.280*65535))
balanced,color=apply_photo(cast,"RASTER")
assert color["status"]=="APPLIED",color
assert color["color_status"]=="APPLIED",color
assert color["HDR_applied"] is False and color["generative_processing"] is False
assert balanced.shape==cast.shape and balanced.dtype==np.uint16
before=np.mean(cast.astype(np.float64),axis=(0,1))
after=np.mean(balanced.astype(np.float64),axis=(0,1))
assert (after.max()-after.min()) < (before.max()-before.min())

# Synthetic distributed verticals converging at a stable off-frame VP produce
# a valid camera-level vertical perspective model, not facade frontalization.
vp=(.5,-2.0)
lines=[]
for xb in (.10,.20,.30,.40,.60,.70,.80,.90):
    # line through bottom point (xb,.92) and vp; sample two in-frame y values
    def x_at(y):
        t=(y-vp[1])/(.92-vp[1])
        return vp[0]+t*(xb-vp[0])
    p=[x_at(.12),.12,x_at(.92),.92]
    dx=p[2]-p[0];dy=p[3]-p[1]
    angle=np.degrees(np.arctan2(dy,dx))
    if angle<0: angle+=180
    lines.append({"xyxy_norm":[float(x) for x in p],"orientation_deg":float(angle)})
analysis={"status":"OBSERVATION_ONLY","diagnostic_lines":lines}
estimated,evidence=_vertical_vp(analysis)
assert estimated is not None,evidence
assert abs(estimated[0]-vp[0])<.03 and abs(estimated[1]-vp[1])<.20,(estimated,evidence)
H,reason=_pixel_homography(1200,800,estimated)
assert reason is None and H.shape==(3,3)

print("AUTO_TECH_MASTER_V2_COMPONENTS_PASS")
