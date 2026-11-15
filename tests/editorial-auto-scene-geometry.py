#!/usr/bin/env python3
"""Synthetic first-stage AUTO PERSPECTIVE LOCK analysis, no invented correction."""
from pathlib import Path
import json
import sys
import tempfile
import hashlib

import cv2
import numpy as np
import tifffile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from editorial_auto_perspective_analysis import analyze
from editorial_raster_technical_master import prepare

w,h=1024,720
blank=np.full((h,w,3),135,np.uint8)
no_lines=analyze(blank)
assert no_lines["status"]=="OBSERVATION_ONLY"
assert no_lines["verticals"]["status"]=="HOLD"
assert no_lines["verticals"]["candidate_roll_correction_deg"] is None
assert no_lines["scene"]["intent"]=="UNKNOWN"
assert no_lines["automatic_correction_applied"] is False

facade=np.full((h,w,3),35,np.uint8)
# Bright architectural grid, broad spatial support: columns and horizontal courses.
for x in (95,185,275,365,455,545,635,725,815,905):
    cv2.line(facade,(x,68),(x,650),(215,215,215),3,cv2.LINE_AA)
for y in (100,195,290,385,480,575):
    cv2.line(facade,(72,y),(952,y),(180,180,180),3,cv2.LINE_AA)
upright=analyze(facade)
assert upright["vertical_inlier_count"]>=6,upright
assert upright["verticals"]["status"] in ("NO_CHANGE_NEEDED","CANDIDATE_FOR_VALIDATION")
assert upright["verticals"]["median_tilt_deg"] is not None
assert abs(upright["verticals"]["median_tilt_deg"])<.6,upright["verticals"]
assert upright["verticals"]["candidate_roll_correction_deg"] is None

rotation=cv2.getRotationMatrix2D(((w-1)/2,(h-1)/2),2.4,1)
tilted=cv2.warpAffine(facade,rotation,(w,h),flags=cv2.INTER_LINEAR,borderValue=(35,35,35))
scan=analyze(tilted)
assert scan["vertical_inlier_count"]>=6,scan
assert scan["verticals"]["status"]=="CANDIDATE_FOR_VALIDATION",scan["verticals"]
assert 1.6<abs(scan["verticals"]["median_tilt_deg"])<3.2,scan["verticals"]
assert scan["verticals"]["candidate_roll_correction_deg"] is not None
assert scan["scene"]["intent"] in ("FRONT_CANDIDATE","UNKNOWN"),scan["scene"]
assert scan["automatic_correction_applied"] is False
assert scan["pixels_corrected"] is False
assert len(scan["diagnostic_lines"])<=60
assert scan["lens_status"]=="UNCALIBRATED"
assert scan["crop_status"]=="NOT_PERFORMED"
assert scan["HDR_applied"] is False

# A real Tech Master still has no automatic geometry modification, only
# explicit evidence; QA cannot mistake detected lines for completed correction.
with tempfile.TemporaryDirectory() as directory:
    folder=Path(directory)
    source=folder/"facade.png"
    cv2.imwrite(str(source),cv2.cvtColor(tilted,cv2.COLOR_RGB2BGR))
    before=source.read_bytes()
    sha=hashlib.sha256(before).hexdigest()
    out=folder/"result"
    qa=prepare(source,out,"123e4567-e89b-42d3-a456-426614174065",sha)
    assert source.read_bytes()==before
    assert qa["auto_geometry_analysis_status"]=="OBSERVATION_ONLY"
    assert qa["auto_geometry_analysis"]["verticals"]["candidate_roll_correction_deg"] is not None
    assert qa["qa_checks"]["AUTO_GEOMETRY_ANALYZED"]=="PASS"
    assert qa["qa_checks"]["AUTO_GEOMETRY_CORRECTED"]=="NOT_APPLICABLE"
    assert qa["geometry"]=="IDENTITY_NO_CORRECTION"
    assert qa["technical_status"]=="TECHNICAL_QA_PASS"
    assert qa["human_visual_QA"]=="PENDING"
    assert qa["HDR_applied"] is False
    assert qa["generative_processing"] is False
    tiff=tifffile.imread(out/"master16.tif")
    assert tiff.shape==(h,w,3) and tiff.dtype==np.uint16
    assert np.array_equal(tiff,np.uint16(tilted)*257),"observational analysis must not modify pixels"
    parsed=json.loads((out/"qa.json").read_text())
    assert parsed["auto_geometry_analysis"]==qa["auto_geometry_analysis"]

print("AUTO_SCENE_GEOMETRY_OBSERVATION_SYNTHETIC_PASS")
