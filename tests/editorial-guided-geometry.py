#!/usr/bin/env python3
"""Synthetic evidence: controlled facade rectification produces an altered TIFF or HOLD."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import tifffile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"scripts"))
from editorial_facade_geometry import correct, GeometryHold, validate
from editorial_raster_technical_master import prepare

PARENT="123e4567-e89b-42d3-a456-426614174061"
JOB="123e4567-e89b-42d3-a456-426614174062"
QUAD=[[.18,.23],[.82,.225],[.825,.77],[.175,.775]]
PLAN={
 "schema":1,"type":"GEOMETRY_FACADE_QUAD_V1",
 "categories":["geometry"],"source_quad_norm":QUAD,
 "parent_job_id":PARENT,"parent_master_sha256":"a"*64,"remarks_sha256":"b"*64,
 "facade_assumption_confirmed":True,"HDR_allowed":False,
 "geometry_transform":"CONTROLLED_PROJECTIVE_WARP"
}

def hold(p, tag):
    try:
        validate(p)
    except GeometryHold as exc:
        assert tag in str(exc),(tag,str(exc))
    else:
        raise AssertionError("must HOLD: "+tag)

hold({**PLAN,"source_quad_norm":QUAD[:3]},"four_fa")
hold({**PLAN,"source_quad_norm":[QUAD[0],QUAD[2],QUAD[1],QUAD[3]]},"corner_order")
hold({**PLAN,"source_quad_norm":[[.4,.4],[.45,.4],[.45,.45],[.4,.45]]},"degenerate_facade")
hold({**PLAN,"facade_assumption_confirmed":False},"human_must_confirm")
hold({**PLAN,"HDR_allowed":True},"unapproved_geometry")

h,w=460,660
x=np.linspace(0,1,w,dtype=np.float32)[None,:]
y=np.linspace(0,1,h,dtype=np.float32)[:,None]
pattern=np.empty((h,w,3),np.uint16)
pattern[:,:,0]=np.uint16((.26+.50*x+.07*y)*65535)
pattern[:,:,1]=np.uint16((.20+.17*x+.43*y)*65535)
pattern[:,:,2]=np.uint16((.19+.23*x+.18*y)*65535)
# Visible horizontal and vertical edges, resembling an architectural facade.
for i in range(70,w-60,44):pattern[:,i-1:i+2]=18000
for j in range(90,h-50,48):pattern[j-1:j+2,:]=54000
original=pattern.copy()
fixed,proof=correct(pattern,PLAN)
assert fixed.shape==pattern.shape and fixed.dtype==np.uint16
assert np.array_equal(pattern,original),"source pixels mutated"
assert not np.array_equal(fixed,original),"no actual correction"
assert proof["perspective_corrected"] is True
assert proof["geometry_transform"]=="CONTROLLED_PROJECTIVE_WARP"
assert proof["lens_profile_applied"] is False
assert proof["HDR_applied"] is False
assert proof["generative_processing"] is False
assert proof["human_visual_QA"]=="PENDING"
assert proof["off_plane_geometry_review_required"] is True
assert proof["changed_mean_absolute_fraction"]>.003
assert proof["invalid_border_fraction"]<=.001
assert proof["max_normalized_displacement"]<=.18
q=np.asarray(proof["corrected_quad_norm"],float)
assert max(abs(q[0,0]-q[3,0]),abs(q[1,0]-q[2,0]),
           abs(q[0,1]-q[1,1]),abs(q[3,1]-q[2,1]))<.0002

with tempfile.TemporaryDirectory() as tmp:
    t=Path(tmp)
    image=t/"source.png"
    ok=cv2.imwrite(str(image),np.uint8(np.rint(original/257))[:,:,::-1])
    assert ok
    digest=hashlib.sha256(image.read_bytes()).hexdigest()
    plan_file=t/"plan.json";plan_file.write_text(json.dumps(PLAN),encoding="utf-8")
    neutral=prepare(image,t/"neutral",JOB,digest)
    corrected=prepare(image,t/"corrected",JOB,digest,plan_file)
    assert hashlib.sha256(image.read_bytes()).hexdigest()==digest
    assert corrected["source_sha256"]==digest
    assert corrected["master_sha256"]!=neutral["master_sha256"]
    assert corrected["geometry"]=="CONTROLLED_PROJECTIVE_WARP"
    assert corrected["correction_status"]=="APPLIED"
    assert corrected["qa_checks"]["GEOMETRY_UNCHANGED"]=="NOT_APPLICABLE"
    assert corrected["qa_checks"]["GEOMETRY_TRANSFORM_EVIDENCED"]=="PASS"
    assert corrected["correction"]["human_visual_QA"]=="PENDING"
    assert corrected["HDR_applied"] is False
    assert corrected["human_visual_QA"]=="PENDING"
    out=tifffile.imread(t/"corrected"/"master16.tif")
    assert out.shape==original.shape and out.dtype==np.uint16
    assert (t/"corrected"/"fullres-preview.jpg").stat().st_size>10000

print("GUIDED_PERSPECTIVE_LOCK_SYNTHETIC_PASS")


# Automatic mode: controlled synthetic perspective with independently supplied
# exact architectural line segments. No human-corner flag or corner input.
from unittest.mock import patch
from editorial_facade_geometry import infer_front_quad
lines=[]
for level in np.linspace(.15,.85,9):
    m=-.12*(level-.5)
    lines.append({"xyxy_norm":[.10,float(level-.40*m),.90,float(level+.40*m)]})
for level in np.linspace(.15,.85,9):
    lines.append({"xyxy_norm":[float(level),.10,float(level),.90]})
analysis={"status":"OBSERVATION_ONLY","scene":{"intent":"FRONT_CANDIDATE"},"diagnostic_lines":lines}
auto_plan={k:v for k,v in PLAN.items() if k not in ("source_quad_norm","facade_assumption_confirmed")}
auto_plan.update(anchor_mode="AUTO_FRONT",frontality_requested=True)
q,hl,vl,evidence=infer_front_quad(analysis,w,h)
assert len(q)==4 and evidence["horizontal"]["distinct_levels"]>=5
# Warp a source pattern by the synthetic camera model.
N=np.array([[1/(w-1),0,-.5],[0,1/(h-1),-.5],[0,0,1]],float)
synthetic=cv2.warpPerspective(pattern,np.linalg.inv(N)@np.array([[1,0,0],[0,1,0],[.12,0,1]],float)@N,(w,h),borderMode=cv2.BORDER_REFLECT)
saved=synthetic.copy()
with patch("editorial_auto_perspective_analysis.analyze",return_value=analysis):
    fixed,proof=correct(synthetic,auto_plan)
assert np.array_equal(synthetic,saved)
assert proof["anchor_mode"]=="AUTO_FRONT"
assert proof["correction_class"]=="AUTO_INFERRED_PROJECTIVE"
assert proof["automatic_anchor_evidence"]["residual_QA"]["horizontal_after_p90_deg"]<1.2
assert proof["human_visual_QA"]=="PENDING"
assert not np.array_equal(fixed,synthetic)
assert proof["HDR_applied"] is False and proof["generative_processing"] is False
for bad in ({**analysis,"scene":{"intent":"ANGLE"}},{**analysis,"diagnostic_lines":lines[:3]}):
    try:
        infer_front_quad(bad,w,h)
    except GeometryHold:
        pass
    else:
        raise AssertionError("Ambiguous scene must HOLD")
print("AUTOMATIC_FRONT_PERSPECTIVE_SYNTHETIC_PASS")
