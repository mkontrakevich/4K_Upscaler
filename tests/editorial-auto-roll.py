#!/usr/bin/env python3
"""B2 acceptance: real automatic levelling/crop, no manual points or fake HDR."""
from pathlib import Path
import json
import hashlib
import sys
import tempfile
import cv2
import numpy as np
import tifffile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from editorial_auto_perspective_analysis import analyze
from editorial_auto_roll_correction import apply
from editorial_raster_technical_master import prepare

w,h=1024,720
facade=np.full((h,w,3),35,np.uint8)
for x in (95,185,275,365,455,545,635,725,815,905):
    cv2.line(facade,(x,68),(x,650),(215,215,215),3,cv2.LINE_AA)
for y in (100,195,290,385,480,575):
    cv2.line(facade,(72,y),(952,y),(180,180,180),3,cv2.LINE_AA)

upright=analyze(facade)
initial=facade.astype(np.uint16)*257
no_change,proof=apply(initial,upright)
assert proof["status"]=="NO_CHANGE_NEEDED",proof
assert np.array_equal(no_change,initial)
assert proof["geometry_transform"]=="IDENTITY"

turn=cv2.getRotationMatrix2D(((w-1)/2,(h-1)/2),2.4,1)
tilted=cv2.warpAffine(facade,turn,(w,h),flags=cv2.INTER_LINEAR,borderValue=(35,35,35))
signal=tilted.astype(np.uint16)*257
source=signal.copy()
observed=analyze(signal)
assert observed["verticals"]["status"]=="CANDIDATE_FOR_VALIDATION",observed["verticals"]
corrected,proof=apply(signal,observed)
assert proof["status"]=="APPLIED",proof
assert proof["geometry_transform"]=="AUTO_CAMERA_ROLL_CORRECTION"
assert proof["engine"]=="AUTO_CAMERA_ROLL_SAFE_CROP_V1"
assert corrected.shape==signal.shape and corrected.dtype==np.uint16
assert np.array_equal(signal,source),"original source pixels were mutated"
assert proof["changed_mean_absolute_fraction"]>.003
assert proof["vertical_lines_before"]>=10
assert abs(proof["vertical_residual_before_deg"])>1.5
assert abs(proof["vertical_residual_after_deg"])<.4
assert abs(proof["vertical_residual_after_deg"])<abs(proof["vertical_residual_before_deg"])*.4
assert 0<proof["crop_area_fraction"]<=.15
assert proof["lost_salient_edge_fraction"]<=.055
assert proof["invalid_border_fraction"]==0
assert proof["lens_profile_applied"] is False
assert proof["facade_perspective_corrected"] is False
assert proof["semantic_crop_completed"] is False
assert proof["HDR_applied"] is False
assert proof["generative_processing"] is False
assert proof["human_visual_QA"]=="PENDING"
assert len(proof["operations"])==1

empty=np.full((h,w,3),50,np.uint16)
same,hold=apply(empty,analyze(empty))
assert hold["status"]=="HOLD" and np.array_equal(same,empty)
assert hold["operations"]==[]

# Near-frame protected detail must not be hidden by arbitrarily strong crop.
# The test also covers disqualification when confidence is insufficient.
spoiled=observed.copy()
spoiled["scene"]={**observed["scene"],"confidence":.62}
candidate,evidence=apply(signal,spoiled)
assert evidence["status"]=="HOLD" and np.array_equal(candidate,signal)
assert evidence["reason"]=="INDEPENDENT_VERTICAL_SUPPORT_INSUFFICIENT"

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    src=root/"facade-rotated.png"
    assert cv2.imwrite(str(src),tilted[:,:,::-1])
    digest=hashlib.sha256(src.read_bytes()).hexdigest()
    before_bytes=src.read_bytes()
    result=prepare(src,root/"result","123e4567-e89b-42d3-a456-426614174071",digest)
    assert src.read_bytes()==before_bytes
    assert result["auto_geometry_correction"]["status"]=="APPLIED"
    assert result["geometry"]=="AUTO_CAMERA_ROLL_CORRECTION"
    assert result["qa_checks"]["GEOMETRY_UNCHANGED"]=="NOT_APPLICABLE"
    assert result["qa_checks"]["GEOMETRY_TRANSFORM_EVIDENCED"]=="PASS"
    assert result["qa_checks"]["AUTO_GEOMETRY_CORRECTED"]=="PASS"
    assert result["technical_version"]=="AUTO_TECH_MASTER_V2"
    assert result["human_visual_QA"]=="PENDING"
    assert result["HDR_applied"] is False
    assert result["generative_processing"] is False
    out=tifffile.imread(root/"result"/"master16.tif")
    assert out.shape==(h,w,3) and out.dtype==np.uint16
    assert np.mean(np.abs(out.astype(np.int32)-signal.astype(np.int32)))>100
    assert result["master_sha256"]==hashlib.sha256((root/"result"/"master16.tif").read_bytes()).hexdigest()
    assert (root/"result"/"fullres-preview.jpg").stat().st_size>10000
    saved=json.loads((root/"result"/"qa.json").read_text())
    assert saved["auto_geometry_correction"]["vertical_residual_after_deg"]==proof["vertical_residual_after_deg"]
print("AUTO_CAMERA_ROLL_SAFE_CROP_SYNTHETIC_PASS")
