#!/usr/bin/env python3
"""Acceptance tests for active TECH MASTER V2 correction stages."""
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))

from editorial_auto_optics import (
    apply as apply_optics, _profile_for, _find_lensfun,
    _apply_vignetting, _apply_tca
)
from editorial_source_metadata import extract_source_metadata
from editorial_auto_photometry import apply as apply_photo
from editorial_auto_vertical_perspective import _vertical_vp, _pixel_homography

# Missing metadata is never guessed, regardless of file format.
raster=np.full((480,640,3),22000,np.uint16)
out,e=apply_optics(raster,Path("/tmp/not-used.jpg"),"RASTER",{
    "source_kind":"RASTER","make":None,"camera":None,"lens":None,
    "lens_make":None,"focal_mm":None,"aperture":None,"focus_distance_m":None,
    "optics_identity_complete":False,"vignetting_metadata_complete":False,
})
assert e["status"]=="HOLD"
assert e["distortion"]["status"]=="HOLD"
assert np.array_equal(out,raster)


# JPEG/TIFF-style rendered source can carry trustworthy EXIF. Metadata extraction
# is format-independent and therefore not tied to ARW.
from PIL import Image
import tempfile
with tempfile.TemporaryDirectory() as td:
    jpeg=Path(td)/"nikon.jpg"
    im=Image.new("RGB",(640,480),(80,90,100))
    ex=Image.Exif()
    ex[271]="NIKON CORPORATION"
    ex[272]="NIKON D3S"
    ex[42036]="Nikkor 28mm f/2.8D AF"
    ex[37386]=(28,1)
    ex[33437]=(40,10)
    im.save(jpeg,exif=ex)
    meta=extract_source_metadata(jpeg,"RASTER")
    assert meta["make"]=="NIKON CORPORATION",meta
    assert meta["camera"]=="NIKON D3S",meta
    assert "28mm" in meta["lens"],meta
    assert abs(meta["focal_mm"]-28.0)<.01,meta
    cam,lens,reason=_find_lensfun(meta)
    assert cam is not None and lens is not None,(meta,reason)
    rendered=np.full((480,640,3),22000,np.uint16)
    rendered_out,proof=apply_optics(rendered,jpeg,"RASTER",meta)
    assert proof["profile_verified"] is True,proof
    assert proof["engine"]=="AUTO_PROFILED_OPTICS_V2"
    assert proof["chromatic_aberration"]["status"]=="HOLD"
    assert proof["chromatic_aberration"]["reason"]=="NONLINEAR_RENDERED_SOURCE_UNSAFE_FOR_PROFILE_TCA"
    assert proof["vignetting"]["status"]=="HOLD"
    assert proof["vignetting"]["reason"]=="NONLINEAR_RENDERED_SOURCE_UNSAFE_FOR_PROFILE_VIGNETTING"
    assert proof["distortion"]["status"] in ("APPLIED","NO_CHANGE_NEEDED"),proof

# Lensfun stages for linear RAW are independent. A profile with suitable
# calibration can correct vignetting/TCA even though the source extension is irrelevant.
raw_meta={
    "source_kind":"CAMERA_RAW",
    "make":"Canon","camera":"Canon EOS 5D Mark IV",
    "lens_make":"Sigma","lens":"Sigma 8mm f/3.5 EX DG circular fisheye",
    "focal_mm":8.0,"aperture":11.0,"focus_distance_m":10.0,
    "optics_identity_complete":True,"vignetting_metadata_complete":True,
}
cam2,lens2,reason2=_find_lensfun(raw_meta)
assert cam2 is not None and lens2 is not None,(raw_meta,reason2)
linear=np.zeros((360,540,3),np.uint16)
yy,xx=np.mgrid[0:360,0:540]
base=(.15+.55*(xx/539.0)).astype(np.float32)
for ch in range(3):
    linear[...,ch]=np.uint16(np.rint(np.clip(base*(1.0+.015*(ch-1)),0,1)*65535))
vig_out,vig=_apply_vignetting(linear,cam2,lens2,raw_meta)
tca_out,tca=_apply_tca(vig_out,cam2,lens2,raw_meta)
assert vig["status"] in ("APPLIED","NO_CHANGE_NEEDED","HOLD"),vig
assert tca["status"] in ("APPLIED","NO_CHANGE_NEEDED","HOLD"),tca
assert vig["HDR_applied"] is False and vig["generative_processing"] is False
assert tca["HDR_applied"] is False and tca["generative_processing"] is False
assert vig_out.shape==linear.shape and tca_out.shape==linear.shape

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
