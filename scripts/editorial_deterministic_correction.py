"""Deterministic, bounded technical corrections for an explicitly returned QA job.

No geometry inference, camera substitutions, HDR, generative synthesis or
unverified lens profiling. In ambiguous cases fail closed with CorrectionHold.
"""
from __future__ import annotations
import hashlib
import json
import math
import numpy as np

SUPPORTED=frozenset(("exposure","color"))

class CorrectionHold(ValueError):
    def __init__(self,reason):
        super().__init__("CORRECTION_HOLD:"+str(reason))
        self.reason=str(reason)

def validate(plan):
    if not isinstance(plan,dict) or plan.get("schema")!=1 or plan.get("type")!="DETERMINISTIC_TECH_REWORK":
        raise CorrectionHold("invalid_signed_correction_plan")
    cats=plan.get("categories")
    if not isinstance(cats,list) or not cats or len(cats)>2 or len(set(cats))!=len(cats):
        raise CorrectionHold("invalid_categories")
    if any(c not in SUPPORTED for c in cats):
        raise CorrectionHold("unverified_geometry_materials_or_detail_require_control_points_and_human_review")
    if not isinstance(plan.get("parent_job_id"),str) or len(plan["parent_job_id"])!=36:
        raise CorrectionHold("missing_parent_lineage")
    if not isinstance(plan.get("parent_master_sha256"),str) or len(plan["parent_master_sha256"])!=64:
        raise CorrectionHold("missing_parent_master_hash")
    if not isinstance(plan.get("remarks_sha256"),str) or len(plan["remarks_sha256"])!=64:
        raise CorrectionHold("missing_remarks_hash")
    if plan.get("HDR_allowed") is not False or plan.get("geometry_transform")!="IDENTITY":
        raise CorrectionHold("unauthorized_Hdr_or_geometry_transform")
    return cats

def correct(rgb16,plan):
    cats=validate(plan)
    if not isinstance(rgb16,np.ndarray) or rgb16.dtype!=np.uint16 or rgb16.ndim!=3 or rgb16.shape[2]!=3:
        raise CorrectionHold("expected_real_uint16_rgb")
    if rgb16.shape[0]<320 or rgb16.shape[1]<320:
        raise CorrectionHold("source_too_small")
    before=rgb16
    pixels=rgb16.astype(np.float32)/65535.0
    # Coarse sampling is deterministic, low-memory and not scene-generating.
    step=max(1,int(math.sqrt((pixels.shape[0]*pixels.shape[1])/250000)))
    sample=pixels[::step,::step,:]
    ops=[]
    if "exposure" in cats:
        lum=np.sum(sample*np.array([.2126,.7152,.0722],dtype=np.float32),axis=2)
        median=float(np.median(lum))
        if .18<=median<=.76:
            raise CorrectionHold("exposure_is_not_objectively_out_of_safe_range")
        if median<.055 or median>.92:
            raise CorrectionHold("exposure_extreme_scene_intent_unknown")
        desired=.24 if median<.18 else .68
        ev=float(np.clip(math.log2(desired/max(1e-5,median)),-.30,.30))
        if abs(ev)<.055:raise CorrectionHold("exposure_adjustment_too_small")
        pixels*=np.float32(2.0**ev)
        ops.append({"op":"GLOBAL_EXPOSURE","ev":round(ev,5),"input_median":round(median,5),
                    "target_median":desired,"bound_ev":.30})
    if "color" in cats:
        sample=pixels[::step,::step,:]
        light=np.mean(sample,axis=2)
        chroma=np.max(sample,axis=2)-np.min(sample,axis=2)
        neutral=(light>.18)&(light<.83)&(chroma<.09)
        portion=float(np.mean(neutral))
        if portion<.03:raise CorrectionHold("insufficient_verified_neutral_pixels")
        means=np.mean(sample[neutral],axis=0)
        level=float(np.mean(means))
        gains=np.clip(level/np.maximum(means,.0001),.93,1.07)
        if float(np.max(np.abs(gains-1)))<.012:
            raise CorrectionHold("neutral_balance_has_no_measurable_color_cast")
        pixels*=gains.astype(np.float32)
        ops.append({"op":"NEUTRAL_GRAY_PATCH_GAIN","gain_rgb":[round(float(x),6) for x in gains],
                    "neutral_fraction":round(portion,6),"bound_gain":[.93,1.07]})
    clipped=float(np.mean(np.any((pixels<0)|(pixels>1),axis=2)))
    if clipped>.007:
        raise CorrectionHold("clipping_exceeds_safe_limit")
    result=np.uint16(np.rint(np.clip(pixels,0,1)*65535.0))
    changed=float(np.mean(np.abs(result.astype(np.int32)-before.astype(np.int32))/65535.0))
    if changed<.003:
        raise CorrectionHold("change_below_objective_significance")
    if result.shape!=before.shape:
        raise CorrectionHold("architecture_pixel_geometry_changed")
    return result,{
      "schema":1,"correction_status":"APPLIED","correction_class":"DETERMINISTIC_GLOBAL_PHOTOMETRY",
      "parent_job_id":plan["parent_job_id"],
      "parent_master_sha256":plan["parent_master_sha256"],
      "remarks_sha256":plan["remarks_sha256"],"categories":cats,
      "operations":ops,"changed_mean_absolute_fraction":round(changed,7),
      "clipped_pixel_fraction":round(clipped,7),
      "geometry_transform":"IDENTITY",
      "lens_profile_applied":False,"perspective_corrected":False,
      "HDR_applied":False,"generative_processing":False,
      "human_visual_QA":"PENDING",
      "source_signal_detail_added":False
    }
