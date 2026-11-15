"""MARINS AUTO PERSPECTIVE LOCK / B2 — conservative camera roll correction.

Only undo global in-plane camera rotation when independent detected architectural
verticals agree. No guesswork on yaw, pitch, lens distortion, 3D facade planes,
semantic crop or scene reconstruction. Source remains unchanged. A high-confidence
HOLD is better than trimming important architecture at the image boundaries.
"""
from __future__ import annotations
import math
import cv2
import numpy as np

ENGINE="AUTO_CAMERA_ROLL_SAFE_CROP_V1"
MAX_PIXELS=16_000_000
MAX_ROLL_DEG=3.25
MAX_CROP_FRACTION=.15
MAX_LOST_EDGES=.055


def _evidence(status, reason, analysis):
    vertical=analysis.get("verticals") or {}
    scene=analysis.get("scene") or {}
    return {
      "schema":1, "engine":ENGINE, "status":status, "reason":reason,
      "geometry_transform":"IDENTITY" if status!="APPLIED" else "AUTO_CAMERA_ROLL_CORRECTION",
      "analysis_engine":analysis.get("engine"),
      "analysis_confidence":scene.get("confidence"),
      "scene_intent":scene.get("intent","UNKNOWN"),
      "vertical_lines_before":int(analysis.get("vertical_inlier_count") or 0),
      "vertical_residual_before_deg":vertical.get("median_tilt_deg"),
      "vertical_residual_after_deg":None,
      "camera_roll_correction_deg":None,
      "crop_zoom":1.0,"crop_area_fraction":0.0,
      "lost_salient_edge_fraction":0.0,"invalid_border_fraction":0.0,
      "changed_mean_absolute_fraction":0.0,
      "operations":[],
      "lens_profile_applied":False,"camera_pitch_corrected":False,
      "facade_perspective_corrected":False,"semantic_crop_completed":False,
      "HDR_applied":False,"generative_processing":False,
      "source_signal_detail_added":False,"human_visual_QA":"PENDING"
    }


def apply(rgb16, analysis):
    if not isinstance(rgb16,np.ndarray) or rgb16.ndim!=3 or rgb16.shape[2]!=3 or rgb16.dtype!=np.uint16:
        raise ValueError("invalid_source_uint16_rgb")
    h,w=rgb16.shape[:2]
    if not 320<=min(h,w) or w*h>50_000_000:
        raise ValueError("invalid_image_dimensions")
    if not isinstance(analysis,dict) or analysis.get("status")!="OBSERVATION_ONLY":
        return rgb16,_evidence("HOLD","AUTO_LINE_ANALYSIS_UNAVAILABLE",analysis or {})
    v=analysis.get("verticals") or {}
    scene=analysis.get("scene") or {}
    tilt=v.get("median_tilt_deg")
    mad=v.get("weighted_mad_deg")
    conf=scene.get("confidence")
    if (v.get("status")=="NO_CHANGE_NEEDED" and tilt is not None and
        math.isfinite(float(tilt))):
        return rgb16,_evidence("NO_CHANGE_NEEDED","ALREADY_LEVEL_WITHIN_TOLERANCE",analysis)
    n=analysis.get("vertical_inlier_count") or 0
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (tilt,mad,conf)):
        return rgb16,_evidence("HOLD","UNRELIABLE_VERTICAL_MEASUREMENTS",analysis)
    if (v.get("status")!="CANDIDATE_FOR_VALIDATION" or n<10 or mad>.65 or
        float(conf)<.90 or v.get("spatial_x_span",0)<.30 or
        v.get("spatial_y_span",0)<.25):
        return rgb16,_evidence("HOLD","INDEPENDENT_VERTICAL_SUPPORT_INSUFFICIENT",analysis)
    if w*h>MAX_PIXELS:
        return rgb16,_evidence("HOLD","SOURCE_EXCEEDS_SAFE_AUTOMATIC_WARP_LIMIT",analysis)
    angle=float(v.get("candidate_roll_correction_deg") or 0)
    if not .65<=abs(angle)<=MAX_ROLL_DEG or abs(angle+float(tilt))>.10:
        return rgb16,_evidence("HOLD","CAMERA_ROLL_UNCERTAIN_OR_LARGE",analysis)
    rad=math.radians(angle)
    ratio=max(w/h,h/w)
    # Zoom-to-fill prevents invented black corners; output retains original
    # dimensions/aspect. This is geometric cropping, NOT semantic composition.
    zoom=math.cos(rad)+ratio*abs(math.sin(rad))+.003
    removed=1-1/(zoom*zoom)
    if removed>MAX_CROP_FRACTION or zoom>1.085:
        return rgb16,_evidence("HOLD","AUTO_CROP_TOO_LARGE_FOR_PROTECTED_SCENE",analysis)
    # Evidence from actual original pixels: reject if the rotation would remove
    # a substantial share of meaningful high-contrast edges at the boundaries.
    scale=min(1.,900/max(w,h))
    sw,sh=max(2,round(w*scale)),max(2,round(h*scale))
    thumb=np.uint8(np.rint(cv2.resize(rgb16,(sw,sh),
      interpolation=cv2.INTER_AREA).astype(np.float32)/257))
    gray=cv2.cvtColor(thumb,cv2.COLOR_RGB2GRAY)
    edges=cv2.Canny(gray,60,140)>0
    small_m=cv2.getRotationMatrix2D(((sw-1)/2,(sh-1)/2),angle,zoom)
    yy,xx=np.indices((sh,sw),dtype=np.float32)
    xx2=small_m[0,0]*xx+small_m[0,1]*yy+small_m[0,2]
    yy2=small_m[1,0]*xx+small_m[1,1]*yy+small_m[1,2]
    lost=(xx2<0)|(xx2>sw-1)|(yy2<0)|(yy2>sh-1)
    total_edges=int(np.count_nonzero(edges))
    if total_edges<100:
        return rgb16,_evidence("HOLD","LOW_TEXTURE_NO_RELIABLE_CROP_MASK",analysis)
    lost_edges=float(np.count_nonzero(edges & lost))/total_edges
    if lost_edges>MAX_LOST_EDGES:
        return rgb16,_evidence("HOLD","CROP_WOULD_CUT_SALIENT_IMAGE_EDGES",analysis)
    m=cv2.getRotationMatrix2D(((w-1)/2,(h-1)/2),angle,zoom)
    valid=cv2.warpAffine(np.ones((h,w),np.uint8),m,(w,h),
        flags=cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
    invalid=1-float(np.mean(valid))
    if invalid>0:
        return rgb16,_evidence("HOLD","ROTATION_WOULD_CREATE_EMPTY_BORDERS",analysis)
    corrected=cv2.warpAffine(rgb16,m,(w,h),flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT,borderValue=(0,0,0))
    from editorial_auto_perspective_analysis import analyze
    after=analyze(corrected)
    vv=after.get("verticals") or {}
    residual=vv.get("median_tilt_deg")
    if (not isinstance(residual,(int,float)) or
        not math.isfinite(residual) or
        abs(residual)>.40 or abs(residual)>abs(tilt)*.40 or
        (after.get("vertical_inlier_count") or 0)<6):
        return rgb16,_evidence("HOLD","RESIDUAL_VERTICAL_QA_FAILED",analysis)
    changed=float(np.mean(np.abs(corrected.astype(np.int32)-rgb16.astype(np.int32)))/65535.0)
    if changed<.003:
        return rgb16,_evidence("HOLD","INSUFFICIENT_MEASURABLE_PIXEL_CHANGE",analysis)
    result=_evidence("APPLIED",None,analysis)
    result.update({
        "geometry_transform":"AUTO_CAMERA_ROLL_CORRECTION",
        "vertical_residual_after_deg":round(float(residual),5),
        "camera_roll_correction_deg":round(float(angle),5),
        "crop_zoom":round(float(zoom),6),
        "crop_area_fraction":round(float(removed),7),
        "lost_salient_edge_fraction":round(float(lost_edges),7),
        "invalid_border_fraction":round(float(invalid),7),
        "changed_mean_absolute_fraction":round(changed,7),
        "vertical_lines_after":after.get("vertical_inlier_count"),
        "affine_matrix":[[round(float(c),9) for c in row] for row in m],
        "operations":[{"op":"AUTOMATIC_CAMERA_ROLL_LEVEL","angle_deg":round(float(angle),5),
                       "geometric_crop_zoom":round(float(zoom),6),
                       "interpolation":"LINEAR"}]
    })
    return corrected,result
