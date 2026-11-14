"""Controlled 4-corner facade rectification, not AI geometry inference.

A human marks TL, TR, BR, BL corners of a single planar rectangular facade.
The global homography straightens that ONE plane. Out-of-plane parts (streets,
cars, adjacent buildings) can distort and must be reviewed by a human at 100%.
If the warp would be too destructive, return HOLD instead of fabricating pixels.
"""
from __future__ import annotations

import math
import cv2
import numpy as np

class GeometryHold(ValueError):
    def __init__(self, reason):
        super().__init__("CORRECTION_HOLD:" + reason)
        self.reason = reason

def _hold(reason):
    raise GeometryHold(reason)

def validate(plan):
    if not isinstance(plan, dict) or plan.get("schema") != 1 or plan.get("type") != "GEOMETRY_FACADE_QUAD_V1":
        _hold("invalid_geometry_plan")
    if plan.get("geometry_transform") != "CONTROLLED_PROJECTIVE_WARP" or plan.get("HDR_allowed") is not False:
        _hold("unapproved_geometry_or_hdr_mode")
    if plan.get("categories") != ["geometry"]:
        _hold("geometry_plan_must_be_scoped_to_one_category")
    for k in ("parent_job_id", "parent_master_sha256", "remarks_sha256"):
        v = plan.get(k)
        if not isinstance(v, str) or (len(v) != (36 if k == "parent_job_id" else 64)):
            _hold("missing_geometry_lineage_" + k)
    if plan.get("facade_assumption_confirmed") is not True:
        _hold("human_must_confirm_rectangular_planar_facade")
    quad = plan.get("source_quad_norm")
    if not isinstance(quad, list) or len(quad) != 4:
        _hold("four_façade_corners_required")
    if any(not isinstance(p, list) or len(p) != 2 or
           any(isinstance(x, bool) or not isinstance(x, (int, float)) or
               not math.isfinite(x) or x < .015 or x > .985 for x in p) for p in quad):
        _hold("normalized_points_invalid_or_outside_safe_frame")
    q = np.asarray(quad, dtype=np.float64)
    tl, tr, br, bl = q
    if not (tl[0] < tr[0] and bl[0] < br[0] and tl[1] < bl[1] and tr[1] < br[1]):
        _hold("corner_order_invalid_TL_TR_BR_BL")
    cross = []
    for i in range(4):
        a, b, c = q[i], q[(i+1) % 4], q[(i+2) % 4]
        cross.append(float(np.cross(b-a, c-b)))
    if min(cross) <= .0025:
        _hold("nonconvex_or_degenerate_facade")
    area = abs(float(cv2.contourArea(q.astype(np.float32))))
    if area < .035 or area > .78:
        _hold("facade_area_outside_safe_range")
    lx = (tl[0] + bl[0])/2
    rx = (tr[0] + br[0])/2
    top = (tl[1] + tr[1])/2
    bottom = (bl[1] + br[1])/2
    if rx-lx < .22 or bottom-top < .18:
        _hold("facade_width_or_height_too_small")
    if (max(abs(tl[0]-bl[0]), abs(tr[0]-br[0])) > .17 or
        max(abs(tl[1]-tr[1]), abs(bl[1]-br[1])) > .17):
        _hold("unusually_large_perspective_change_needs_manual_review")
    return q, np.array([[lx,top],[rx,top],[rx,bottom],[lx,bottom]], dtype=np.float64)

def correct(rgb16, plan):
    src_quad, target_quad = validate(plan)
    if not isinstance(rgb16, np.ndarray) or rgb16.ndim != 3 or rgb16.shape[2] != 3 or rgb16.dtype != np.uint16:
        _hold("expected_original_uint16_RGB")
    h,w = rgb16.shape[:2]
    if min(w,h) < 320 or w*h > 50_000_000:
        _hold("invalid_image_dimensions")
    scale_xy = np.array([w-1,h-1], dtype=np.float64)
    src = np.float32(src_quad*scale_xy)
    dst = np.float32(target_quad*scale_xy)
    mat = cv2.getPerspectiveTransform(src,dst)
    if not np.all(np.isfinite(mat)) or abs(np.linalg.det(mat)) < .00001:
        _hold("singular_projective_transform")
    center_x,center_y=(w-1)/2,(h-1)/2
    destination=np.array([[[1.,1.]],[[w-2.,1.]],[[w-2.,h-2.]],[[1.,h-2.]]],np.float64)
    def compose(zoom):
        z=np.array([[zoom,0,center_x*(1-zoom)],
                    [0,zoom,center_y*(1-zoom)],[0,0,1]],np.float64)
        return z@mat
    def safe(zoom):
        H=compose(zoom)
        try:
            p=cv2.perspectiveTransform(destination,np.linalg.inv(H)).reshape(-1,2)
        except cv2.error:
            return False
        return bool(np.all(np.isfinite(p)) and
                    p[:,0].min()>=1 and p[:,0].max()<=w-2 and
                    p[:,1].min()>=1 and p[:,1].max()<=h-2)
    zoom=1.0
    while zoom<=1.1200001 and not safe(zoom):
        zoom+=.0025
    if zoom>1.12 or not safe(zoom):
        _hold("required_crop_exceeds_12_percent_zoom_limit")
    matrix=compose(zoom)
    valid=cv2.warpPerspective(np.ones((h,w),np.uint8),matrix,(w,h),
                             flags=cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
    missing_fraction=1-float(np.mean(valid))
    if missing_fraction>.001:
        _hold("warp_would_require_generating_missing_image_area")
    # Bound displacement over the entire frame, including off-plane objects.
    grid=np.array([[[i*(w-1)/8,j*(h-1)/8] for i in range(9)]
                   for j in range(9)],np.float64).reshape(-1,1,2)
    mapped=cv2.perspectiveTransform(grid,matrix)
    distance=np.linalg.norm(mapped-grid,axis=2).reshape(-1)/math.hypot(w,h)
    max_shift=float(np.max(distance))
    if max_shift>.18:
        _hold("projective_displacement_exceeds_safe_bound")
    if max_shift<.0025:
        _hold("no_meaningful_geometric_correction")
    corrected=cv2.warpPerspective(rgb16,matrix,(w,h),flags=cv2.INTER_CUBIC,
                                  borderMode=cv2.BORDER_CONSTANT,borderValue=(0,0,0))
    changed=float(np.mean(np.abs(corrected.astype(np.int32)-rgb16.astype(np.int32)))/65535.0)
    if changed<.003:
        _hold("geometry_pixel_change_below_evidence_threshold")
    target=cv2.perspectiveTransform(src.reshape(1,4,2).astype(np.float64),
                                     matrix).reshape(4,2)/scale_xy
    upright_delta=float(max(abs(target[0,0]-target[3,0]),abs(target[1,0]-target[2,0]),
                            abs(target[0,1]-target[1,1]),abs(target[3,1]-target[2,1])))
    if upright_delta>.0002:
        _hold("facade_rectification_validation_failed")
    proof={
        "schema":1,"correction_status":"APPLIED","correction_class":"HUMAN_ANCHORED_PROJECTIVE",
        "parent_job_id":plan["parent_job_id"],"parent_master_sha256":plan["parent_master_sha256"],
        "remarks_sha256":plan["remarks_sha256"],"categories":["geometry"],
        "geometry_transform":"CONTROLLED_PROJECTIVE_WARP",
        "source_quad_norm":plan["source_quad_norm"],
        "corrected_quad_norm":[[round(float(v),7) for v in row] for row in target],
        "homography_matrix":[[round(float(v),10) for v in row] for row in matrix/matrix[2,2]],
        "crop_zoom":round(float(zoom),5),
        "crop_area_fraction":round(float(1-1/(zoom*zoom)),6),
        "invalid_border_fraction":round(missing_fraction,7),
        "max_normalized_displacement":round(max_shift,7),
        "facade_upright_residual_norm":round(upright_delta,7),
        "changed_mean_absolute_fraction":round(changed,7),
        "operations":[{"op":"PROJECTIVE_RECTIFY_MARKED_FACADE",
                       "point_count":4,"interpolation":"CUBIC","zoom":round(float(zoom),5)}],
        "lens_profile_applied":False,"perspective_corrected":True,
        "off_plane_geometry_review_required":True,
        "HDR_applied":False,"generative_processing":False,
        "human_visual_QA":"PENDING","source_signal_detail_added":False
    }
    return corrected,proof
