#!/usr/bin/env python3
"""Synthetic deterministic correction checks: no fabricated geometric fixes."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import tifffile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from editorial_deterministic_correction import correct,CorrectionHold
from editorial_raster_technical_master import prepare

JOB="123e4567-e89b-42d3-a456-426614174069"
PARENT="123e4567-e89b-42d3-a456-426614174068"
PLAN={"schema":1,"type":"DETERMINISTIC_TECH_REWORK",
      "parent_job_id":PARENT,"parent_master_sha256":"a"*64,"remarks_sha256":"b"*64,
      "categories":["exposure"],"HDR_allowed":False,"geometry_transform":"IDENTITY"}

def held(rgb,p,reason):
    try:
        correct(rgb,p)
    except CorrectionHold as e:
        assert reason in str(e),(reason,e)
    else:
        raise AssertionError("MUST_HOLD:"+reason)

w,h=520,400
rng=np.random.default_rng(17)
dark=np.clip(rng.normal(.13,.009,(h,w,3)),.07,.21)
rgb16=np.uint16(np.rint(dark*65535))
out,evidence=correct(rgb16,PLAN)
assert out.shape==rgb16.shape
assert np.mean(out)>np.mean(rgb16)*1.12
assert evidence["correction_status"]=="APPLIED"
assert evidence["operations"][0]["op"]=="GLOBAL_EXPOSURE"
assert evidence["geometry_transform"]=="IDENTITY"
assert evidence["perspective_corrected"] is False
assert evidence["HDR_applied"] is False
assert evidence["human_visual_QA"]=="PENDING"
assert evidence["changed_mean_absolute_fraction"]>.003
neutral=np.uint16(np.full((h,w,3),.45)*65535)
held(neutral,PLAN,"exposure_is_not_objectively_out_of_safe_range")
bad={**PLAN,"categories":["geometry"]}
held(rgb16,bad,"unverified_geometry")
bad={**PLAN,"HDR_allowed":True}
held(rgb16,bad,"unauthorized_Hdr")
bad={**PLAN,"geometry_transform":"HOMOGRAPHY"}
held(rgb16,bad,"unauthorized_Hdr")
cast=np.zeros((h,w,3),np.uint16)
cast[:,:,0]=np.uint16(.46*65535)
cast[:,:,1]=np.uint16(.425*65535)
cast[:,:,2]=np.uint16(.40*65535)
balance={**PLAN,"categories":["color"]}
fixed,colorProof=correct(cast,balance)
assert fixed.shape==cast.shape and colorProof["operations"][0]["op"]=="NEUTRAL_GRAY_PATCH_GAIN"
# The correction should reduce, not increase, the global RGB imbalance.
assert np.ptp(fixed.astype(np.float64).mean(axis=(0,1)))<np.ptp(cast.astype(np.float64).mean(axis=(0,1)))

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    src=root/"source.png"
    # uint8 raster, no RAW detail invented.
    pixels=np.uint8(np.rint(dark*255))
    ok=cv2.imwrite(str(src),pixels[:,:,::-1]);assert ok
    source_hash=hashlib.sha256(src.read_bytes()).hexdigest()
    p=root/"plan.json";p.write_text(json.dumps(PLAN),encoding="utf-8")
    raw_qa=prepare(src,root/"neutral",JOB,source_hash)
    corrected_qa=prepare(src,root/"corrected",JOB,source_hash,p)
    assert src.is_file() and hashlib.sha256(src.read_bytes()).hexdigest()==source_hash
    assert corrected_qa["source_sha256"]==raw_qa["source_sha256"]
    assert corrected_qa["master_sha256"]!=raw_qa["master_sha256"]
    assert corrected_qa["correction_status"]=="APPLIED"
    assert corrected_qa["correction"]["parent_job_id"]==PARENT
    assert corrected_qa["correction"]["parent_master_sha256"]=="a"*64
    assert corrected_qa["HDR_applied"] is False
    assert corrected_qa["geometry"]=="IDENTITY_NO_CORRECTION"
    for key in ("master16.tif","fullres-preview.jpg","qa.json"):
        assert (root/"corrected"/key).is_file(),key
    actual=tifffile.imread(root/"corrected"/"master16.tif")
    assert actual.shape==(h,w,3) and actual.dtype==np.uint16

print("DETERMINISTIC_AUTO_REWORK_SYNTHETIC_PASS")
