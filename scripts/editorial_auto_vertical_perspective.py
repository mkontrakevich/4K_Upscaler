"""Conservative automatic vertical-perspective correction for TECH MASTER.

Rectifies global camera pitch/vertical keystone only when distributed vertical
lines agree on a stable vertical vanishing point. It never frontally rectifies
a facade or removes intentional horizontal depth perspective.
"""
from __future__ import annotations

import math
import cv2
import numpy as np

ENGINE = "AUTO_VERTICAL_PERSPECTIVE_V1"
MAX_CROP_FRACTION = 0.30
MAX_ZOOM = 1.20
MAX_CORNER_DRIFT = 0.19


def _line_coeff(p):
    x1, y1, x2, y2 = p
    l = np.cross(np.array([x1, y1, 1.0], dtype=np.float64),
                 np.array([x2, y2, 1.0], dtype=np.float64))
    n = math.hypot(float(l[0]), float(l[1]))
    return l / max(n, 1e-12)


def _intersection(a, b):
    p = np.cross(a, b)
    if abs(float(p[2])) < 1e-8:
        return None
    x, y = float(p[0] / p[2]), float(p[1] / p[2])
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    if max(abs(x), abs(y)) > 40:
        return None
    return np.array([x, y], dtype=np.float64)


def _vertical_vp(analysis):
    raw = analysis.get("diagnostic_lines") or []
    lines = []
    for item in raw:
        p = item.get("xyxy_norm")
        angle = item.get("orientation_deg")
        if not isinstance(p, list) or len(p) != 4 or not isinstance(angle, (int, float)):
            continue
        if 72.0 <= float(angle) <= 108.0 and abs(float(p[3]) - float(p[1])) >= 0.075:
            lines.append((_line_coeff([float(x) for x in p]), [float(x) for x in p], float(angle)))
    if len(lines) < 7:
        return None, {"reason": "VERTICAL_VP_INSUFFICIENT_LINES", "line_count": len(lines)}

    pts = []
    for i, (a, _, aa) in enumerate(lines[:24]):
        for b, _, ab in lines[i + 1:24]:
            delta = abs(aa - ab)
            if delta < 0.45:
                continue
            p = _intersection(a, b)
            if p is not None:
                pts.append(p)
    if len(pts) < 8:
        return None, {"reason": "VERTICAL_VP_INSUFFICIENT_INTERSECTIONS", "line_count": len(lines)}

    arr = np.asarray(pts)
    med = np.median(arr, axis=0)
    vx, vy = float(med[0]), float(med[1])

    # Evaluate the candidate against all vertical lines by angular agreement.
    errors = []
    for _, p, _ in lines:
        x1, y1, x2, y2 = p
        mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        dx, dy = x2 - x1, y2 - y1
        vxline, vyline = vx - mx, vy - my
        denom = max(1e-9, math.hypot(dx, dy) * math.hypot(vxline, vyline))
        s = max(-1.0, min(1.0, abs(dx * vyline - dy * vxline) / denom))
        errors.append(math.degrees(math.asin(s)))
    support = [e for e in errors if e <= 1.25]
    ratio = len(support) / max(1, len(errors))
    med_error = float(np.median(errors)) if errors else 999.0

    evidence = {
        "line_count": len(lines),
        "intersection_count": len(pts),
        "vanishing_point_norm": [round(vx, 6), round(vy, 6)],
        "angular_support_ratio": round(ratio, 5),
        "median_angular_error_deg": round(med_error, 5),
    }
    # Distributed architectural lines can include windows/railings from
    # several depth planes. 0.62 support remains conservative when the median
    # angular residual is <=1.10° and at least 8 lines independently agree.
    if len(support) < 8 or ratio < 0.62 or med_error > 1.10:
        evidence["reason"] = "VERTICAL_VP_UNSTABLE"
        return None, evidence
    return (vx, vy), evidence


def _pixel_homography(w, h, vp):
    # Work in centered pixel coordinates so x/y share physical pixel units.
    # Projective P sends the measured vertical VP to infinity. R then rotates
    # that infinite direction exactly onto the image vertical axis.
    vx, vy = vp
    xvp = (vx - 0.5) * w
    yvp = (vy - 0.5) * h
    if abs(yvp) < 1.1 * h:
        return None, "VERTICAL_VP_TOO_CLOSE_FOR_SAFE_RECTIFICATION"
    if abs(xvp) > 0.28 * w:
        return None, "VERTICAL_VP_OFF_AXIS_ROLL_OR_MULTIPLANE_RISK"
    g = -1.0 / yvp
    if abs(g * h) > 0.62:
        return None, "VERTICAL_PERSPECTIVE_STRENGTH_TOO_HIGH"

    theta = math.atan2(xvp, yvp)
    # A vanishing direction is unoriented: up and down describe the same
    # architectural vertical. Fold the angle to the nearest vertical axis.
    if theta > math.pi / 2:
        theta -= math.pi
    elif theta < -math.pi / 2:
        theta += math.pi
    if abs(math.degrees(theta)) > 4.0:
        return None, "VERTICAL_DIRECTION_ROLL_TOO_HIGH"

    cx, cy = (w - 1) * 0.5, (h - 1) * 0.5
    C = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], dtype=np.float64)
    Ci = np.array([[1, 0, cx], [0, 1, cy], [0, 0, 1]], dtype=np.float64)
    P = np.array([[1, 0, 0], [0, 1, 0], [0, g, 1]], dtype=np.float64)
    ct, st = math.cos(theta), math.sin(theta)
    R = np.array([[ct, -st, 0], [st, ct, 0], [0, 0, 1]], dtype=np.float64)
    H = Ci @ R @ P @ C
    return H, None


def apply(rgb16, analysis):
    if not isinstance(rgb16, np.ndarray) or rgb16.dtype != np.uint16 or rgb16.ndim != 3 or rgb16.shape[2] != 3:
        raise ValueError("invalid_source_uint16_rgb")
    h, w = rgb16.shape[:2]
    base = {
        "schema": 1, "engine": ENGINE, "status": "HOLD", "reason": None,
        "geometry_transform": "IDENTITY", "operations": [],
        "crop_zoom": 1.0, "crop_area_fraction": 0.0,
        "invalid_border_fraction": 0.0, "max_corner_drift_fraction": 0.0,
        "changed_mean_absolute_fraction": 0.0,
        "HDR_applied": False, "generative_processing": False,
        "facade_frontality_changed": False, "horizontal_depth_preserved_by_design": True,
        "human_visual_QA": "PENDING",
    }
    if not isinstance(analysis, dict) or analysis.get("status") != "OBSERVATION_ONLY":
        base["reason"] = "GEOMETRY_ANALYSIS_UNAVAILABLE"
        return rgb16, base

    vp, evidence = _vertical_vp(analysis)
    base["evidence"] = evidence
    if vp is None:
        base["reason"] = evidence.get("reason", "VERTICAL_VP_UNAVAILABLE")
        return rgb16, base

    # A very remote VP means vertical convergence is already negligible.
    vx, vy = vp
    if abs(vy - 0.5) > 9.0:
        base.update({"status": "NO_CHANGE_NEEDED", "reason": "VERTICALS_ALREADY_EFFECTIVELY_PARALLEL"})
        return rgb16, base

    hbase, reason = _pixel_homography(w, h, vp)
    if hbase is None:
        base["reason"] = reason
        return rgb16, base

    diag = math.hypot(w, h)
    corners = np.float32([[[0, 0]], [[w - 1, 0]], [[w - 1, h - 1]], [[0, h - 1]]])
    chosen = None
    for zoom in np.linspace(1.0, MAX_ZOOM, 21):
        z = np.array([[zoom, 0, (1 - zoom) * (w - 1) * 0.5],
                      [0, zoom, (1 - zoom) * (h - 1) * 0.5],
                      [0, 0, 1]], dtype=np.float64)
        H = z @ hbase
        mask = cv2.warpPerspective(np.ones((h, w), np.uint8), H, (w, h),
                                   flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        invalid = 1.0 - float(np.mean(mask))
        if invalid <= 0.00002:
            moved = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
            drift = float(np.max(np.linalg.norm(moved - corners.reshape(-1, 2), axis=1)) / diag)
            crop = 1.0 - 1.0 / (float(zoom) ** 2)
            if drift <= MAX_CORNER_DRIFT and crop <= MAX_CROP_FRACTION:
                chosen = (H, float(zoom), invalid, drift, crop)
                break
    if chosen is None:
        base["reason"] = "VERTICAL_PERSPECTIVE_SAFE_CROP_OR_DRIFT_LIMIT"
        return rgb16, base

    H, zoom, invalid, drift, crop = chosen
    corrected = cv2.warpPerspective(rgb16, H, (w, h), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))

    from editorial_auto_perspective_analysis import analyze
    after = analyze(corrected)
    v = after.get("verticals") or {}
    residual = v.get("median_tilt_deg")
    if not isinstance(residual, (int, float)) or not math.isfinite(float(residual)) or abs(float(residual)) > 0.45:
        base["reason"] = "VERTICAL_PERSPECTIVE_POST_QA_FAILED"
        base["candidate_crop_zoom"] = round(zoom, 6)
        base["candidate_crop_area_fraction"] = round(crop, 7)
        base["candidate_max_corner_drift_fraction"] = round(drift, 7)
        base["post_qa"] = {
            "vertical_residual_after_deg": round(float(residual), 5) if isinstance(residual,(int,float)) and math.isfinite(float(residual)) else None,
            "vertical_lines_after": int(after.get("vertical_inlier_count") or 0),
            "detected_lines_after": int(after.get("detected_line_count") or 0),
            "vertical_status_after": v.get("status"),
            "analysis_confidence_after": (after.get("scene") or {}).get("confidence"),
        }
        return rgb16, base

    changed = float(np.mean(np.abs(corrected.astype(np.int32) - rgb16.astype(np.int32))) / 65535.0)
    if changed < 0.002:
        base.update({"status": "NO_CHANGE_NEEDED", "reason": "VERTICAL_PERSPECTIVE_CHANGE_BELOW_SIGNIFICANCE"})
        return rgb16, base

    base.update({
        "status": "APPLIED", "reason": None,
        "geometry_transform": "AUTO_VERTICAL_PERSPECTIVE_CORRECTION",
        "homography": [[round(float(x), 10) for x in row] for row in H],
        "crop_zoom": round(zoom, 6),
        "crop_area_fraction": round(crop, 7),
        "invalid_border_fraction": round(invalid, 8),
        "max_corner_drift_fraction": round(drift, 7),
        "vertical_residual_after_deg": round(float(residual), 5),
        "vertical_lines_after": int(after.get("vertical_inlier_count") or 0),
        "changed_mean_absolute_fraction": round(changed, 7),
        "strong_crop_review_required": bool(crop > 0.20),
        "operations": [{
            "op": "AUTO_VERTICAL_PERSPECTIVE",
            "vanishing_point_norm": [round(float(vx), 6), round(float(vy), 6)],
            "crop_zoom": round(zoom, 6),
            "interpolation": "LINEAR",
        }],
    })
    return corrected, base
