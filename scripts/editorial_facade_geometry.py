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
    if plan.get("anchor_mode") == "AUTO_FRONT":
        if plan.get("frontality_requested") is not True or not isinstance(plan.get("automatic_anchor_evidence"), dict):
            _hold("automatic_frontality_evidence_required")
    elif plan.get("facade_assumption_confirmed") is not True:
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
        ab=b-a
        bc=c-b
        cross.append(float(ab[0]*bc[1]-ab[1]*bc[0]))
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

def _fit_axis(raw, horizontal, aspect):
    """Find a distributed pencil of lines, rejecting competing directions.
    Coordinates normalized; slopes converted to physical pixel angle for QA.
    """
    rows = []
    for item in raw:
        p = item.get("xyxy_norm")
        if not isinstance(p, list) or len(p) != 4:
            continue
        x1,y1,x2,y2 = map(float,p)
        if not all(math.isfinite(v) for v in (x1,y1,x2,y2)):
            continue
        u1,v1,u2,v2 = (x1,y1,x2,y2) if horizontal else (y1,x1,y2,x2)
        if abs(u2-u1) < .065:
            continue
        slope=(v2-v1)/(u2-u1)
        pixel_factor=1/aspect if horizontal else aspect
        if abs(slope*pixel_factor) > .35:
            continue
        offset=(v1+v2)/2-slope*((u1+u2)/2-.5)
        if not .025 < offset < .975:
            continue
        rows.append((offset,slope,p))
    if len(rows)<7:
        _hold("insufficient_distributed_facade_lines")
    off=np.asarray([r[0] for r in rows]); sl=np.asarray([r[1] for r in rows])
    X=np.column_stack([np.ones(len(off)),off-.5])
    tol=.014/pixel_factor
    best=None
    for i in range(len(rows)):
        for j in range(i+1,len(rows)):
            if abs(off[i]-off[j])<.12:
                continue
            b=(sl[i]-sl[j])/(off[i]-off[j])
            a=sl[i]-b*(off[i]-.5)
            err=np.abs(sl-(a+b*(off-.5)))
            mask=err<=tol
            bins=len(set(np.floor(off[mask]/.055).astype(int)))
            score=(bins,int(mask.sum()),-float(np.median(err[mask])))
            if best is None or score>best[0]:
                best=(score,mask)
    if best is None:
        _hold("facade_line_consensus_missing")
    mask=best[1]
    # Weight distinct levels equally so repeated fragments of one window edge
    # cannot dominate the estimated vanishing direction.
    bins=np.floor(off[mask]/.055).astype(int)
    bx=[]; by=[]
    for k in sorted(set(bins)):
        bx.append(float(np.median(off[mask][bins==k])))
        by.append(float(np.median(sl[mask][bins==k])))
    if len(bx)<5 or max(bx)-min(bx)<.38 or float(mask.mean())<.78:
        _hold("facade_direction_ambiguous_or_not_spatially_distributed")
    coef=np.linalg.lstsq(np.column_stack([np.ones(len(bx)),np.asarray(bx)-.5]),by,rcond=None)[0]
    residual=np.abs(sl-X@coef)*pixel_factor
    final=residual<=.014
    if float(final.mean())<.78 or float(np.median(residual))>.009:
        _hold("unstable_facade_direction")
    if abs(float(coef[1]))>.30 or abs(float(coef[0])*pixel_factor)>.10:
        _hold("frontality_transform_too_strong")
    return coef, np.asarray([r[2] for i,r in enumerate(rows) if final[i]]), {
        "line_count":len(rows),"support_ratio":round(float(final.mean()),5),
        "distinct_levels":len(bx),"spatial_span":round(max(bx)-min(bx),5),
        "median_residual_deg":round(math.degrees(math.atan(float(np.median(residual)))),5),
        "slope_at_center":round(float(coef[0]),7),"slope_gradient":round(float(coef[1]),7),
        "reference_levels":[float(np.quantile(bx,.10)),float(np.quantile(bx,.90))]
    }

def infer_front_quad(analysis, width, height):
    if analysis.get("status")!="OBSERVATION_ONLY":
        _hold("facade_analysis_unavailable")
    if (analysis.get("scene") or {}).get("intent")=="ANGLE":
        _hold("multiple_architectural_directions_detected")
    raw=analysis.get("diagnostic_lines") or []
    aspect=width/height
    hc,hl,he=_fit_axis(raw,True,aspect)
    vc,vl,ve=_fit_axis(raw,False,aspect)
    # Intersect two representative levels of each fitted line family.
    # These are inferred reference-grid intersections, not semantic corners.
    def cross(x,y):
        m=float(hc[0]+hc[1]*(y-.5))
        n=float(vc[0]+vc[1]*(x-.5))
        A=np.array([[1.,-n],[-m,1.]])
        b=np.array([x-.5*n,y-.5*m])
        return np.linalg.solve(A,b).tolist()
    l,r=ve["reference_levels"]; t,b=he["reference_levels"]
    q=[cross(l,t),cross(r,t),cross(r,b),cross(l,b)]
    return q,hl,vl,{"horizontal":he,"vertical":ve,"reference_kind":"INFERRED_LINE_GRID"}

def correct(rgb16, plan):
    auto=plan.get("anchor_mode")=="AUTO_FRONT"
    if auto:
        if plan.get("frontality_requested") is not True:
            _hold("explicit_frontality_request_required")
        from editorial_auto_perspective_analysis import analyze
        h,w=rgb16.shape[:2]
        q,hl,vl,evidence=infer_front_quad(analyze(rgb16),w,h)
        plan={**plan,"source_quad_norm":q,"automatic_anchor_evidence":evidence}
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
    if auto:
        # Independent residual measurement on all supporting segments, not only
        # the four synthesized grid intersections.
        def slopes(lines, horizontal, transform=None):
            points=lines.reshape(-1,2,2)*scale_xy
            if transform is not None:
                points=cv2.perspectiveTransform(points.reshape(-1,1,2),transform).reshape(-1,2,2)
            d=points[:,1]-points[:,0]
            return np.degrees(np.arctan2(np.abs(d[:,1] if horizontal else d[:,0]),
                                         np.abs(d[:,0] if horizontal else d[:,1])))
        before_h=slopes(hl,True); after_h=slopes(hl,True,matrix)
        before_v=slopes(vl,False); after_v=slopes(vl,False,matrix)
        if (np.quantile(after_h,.9)>1.2 or np.quantile(after_v,.9)>1.2 or
            np.median(after_h)>=np.median(before_h)*.75 or
            np.quantile(after_v,.9)>max(.65,np.quantile(before_v,.9)+.20)):
            _hold("automatic_frontality_residual_QA_failed")
        evidence["residual_QA"]={"horizontal_before_p90_deg":float(np.quantile(before_h,.9)),
            "horizontal_after_p90_deg":float(np.quantile(after_h,.9)),
            "vertical_before_p90_deg":float(np.quantile(before_v,.9)),
            "vertical_after_p90_deg":float(np.quantile(after_v,.9))}
    proof={
        "schema":1,"correction_status":"APPLIED","correction_class":"AUTO_INFERRED_PROJECTIVE" if auto else "HUMAN_ANCHORED_PROJECTIVE",
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
        "anchor_mode":"AUTO_FRONT" if auto else "HUMAN",
        "automatic_anchor_evidence":evidence if auto else None,
        "operations":[{"op":"AUTO_PROJECTIVE_RECTIFY_LINE_GRID" if auto else "PROJECTIVE_RECTIFY_MARKED_FACADE",
                       "point_count":4,"interpolation":"CUBIC","zoom":round(float(zoom),5)}],
        "lens_profile_applied":False,"perspective_corrected":True,
        "off_plane_geometry_review_required":True,
        "HDR_applied":False,"generative_processing":False,
        "human_visual_QA":"PENDING","source_signal_detail_added":False
    }
    return corrected,proof
