"""MARINS AUTO PERSPECTIVE LOCK — observational geometry engine, stage S3.

Automatic line / vanishing-direction diagnostics from original pixels.
This module NEVER changes pixels, crops a photograph, invents a lens profile,
or claims a complete perspective correction. Safe candidates require an
independent follow-up solver and spatially protected QA before transformation.
"""
from __future__ import annotations

import math
import cv2
import numpy as np

VERSION = "AUTO_SCENE_GEOMETRY_ANALYSIS_V1"
MAX_EDGE = 1600
MAX_DIAGNOSTIC_LINES = 60


def _weighted_median(values, weights):
    order = np.argsort(values)
    v = np.asarray(values)[order]
    w = np.asarray(weights)[order]
    return float(v[np.searchsorted(np.cumsum(w), 0.5 * np.sum(w))])


def _span(points):
    if not points:
        return 0.0
    return float(max(points) - min(points))


def _line_features(gray):
    h, w = gray.shape
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    raw = lsd.detect(gray)[0]
    if raw is None:
        return []
    found = []
    min_length = max(30.0, math.hypot(w, h) * .035)
    for x1, y1, x2, y2 in raw.reshape(-1, 4):
        dx, dy = float(x2-x1), float(y2-y1)
        length = math.hypot(dx, dy)
        if length < min_length:
            continue
        # Modulo 180: all line orientation measurements are independent
        # of endpoint order.
        if dy < 0:
            x1, x2 = x2, x1
            y1, y2 = y2, y1
            dx, dy = -dx, -dy
        orientation = math.degrees(math.atan2(dy, dx))
        if orientation < 0:
            orientation += 180
        vertical_tilt = math.degrees(math.atan2(dx, dy)) if dy >= 0 else None
        found.append({
            "p": [float(x1/w), float(y1/h), float(x2/w), float(y2/h)],
            "angle_deg": float(orientation),
            "vertical_tilt_deg": float(vertical_tilt),
            "length": length,
            "mid_x": float((x1+x2)/(2*w)),
            "mid_y": float((y1+y2)/(2*h)),
        })
    return found


def _intersection(a, b):
    p, q = a["p"], b["p"]
    l1 = np.cross([p[0], p[1], 1.0], [p[2], p[3], 1.0])
    l2 = np.cross([q[0], q[1], 1.0], [q[2], q[3], 1.0])
    cross = np.cross(l1, l2)
    if abs(float(cross[2])) < 1e-6:
        return None
    x, y = float(cross[0]/cross[2]), float(cross[1]/cross[2])
    if not (math.isfinite(x) and math.isfinite(y)) or max(abs(x), abs(y)) > 100:
        return None
    return [round(x, 5), round(y, 5)]


def _vanishing_direction(lines):
    """Evidence only; do not return an arbitrary single-line 'vanishing point'."""
    if len(lines) < 4:
        return {"status": "INSUFFICIENT_LINES", "point": None, "support": 0}
    ordered = sorted(lines, key=lambda x: x["length"], reverse=True)[:18]
    candidates = []
    for i, a in enumerate(ordered):
        for b in ordered[i+1:]:
            delta = abs(a["angle_deg"] - b["angle_deg"])
            if min(delta, 180-delta) < 2.5:
                continue
            point = _intersection(a, b)
            if point is None:
                continue
            candidates.append(point)
    if not candidates:
        return {"status": "PARALLEL_OR_UNSTABLE", "point": None, "support": 0}
    xy = np.asarray(candidates)
    # Robust median of intersection hypotheses, but no geometric correction
    # without later physical camera-model verification.
    med = np.median(xy, axis=0)
    residual = np.linalg.norm(xy-med, axis=1)
    support = int(np.count_nonzero(residual < .16))
    ratio = support/max(1, len(xy))
    if support < 3 or ratio < .25:
        return {"status": "UNSTABLE", "point": None, "support": support}
    return {"status": "CANDIDATE", "point": [round(float(z), 5) for z in med],
            "support": support, "support_ratio": round(float(ratio), 4)}


def analyze(rgb16):
    if not isinstance(rgb16, np.ndarray) or rgb16.ndim != 3 or rgb16.shape[2] != 3:
        raise ValueError("invalid_geometry_analysis_rgb")
    if rgb16.dtype not in (np.uint16, np.uint8):
        raise ValueError("unsupported_geometry_analysis_bit_depth")
    h, w = rgb16.shape[:2]
    if min(h, w) < 320 or h*w > 50_000_000:
        raise ValueError("invalid_geometry_analysis_dimensions")

    scale = min(1., MAX_EDGE / max(h, w))
    dst = cv2.resize(rgb16, (max(1, round(w*scale)), max(1, round(h*scale))),
                     interpolation=cv2.INTER_AREA) if scale < 1 else rgb16

    # Computer-vision proxy only: linear RAW can be visually almost black when
    # naively divided by 257. Build one deterministic global display transform
    # for line detection. It NEVER changes the source/TECH pixels.
    proxy_white = None
    if dst.dtype == np.uint16:
        linear = np.clip(dst.astype(np.float32) / 65535.0, 0.0, 1.0)
        luminance = np.sum(linear * np.array([.2126,.7152,.0722],dtype=np.float32), axis=2)
        positive = luminance[luminance > 1.0/65535.0]
        proxy_white = float(np.percentile(positive, 99.5)) if positive.size else 1.0
        proxy_white = max(proxy_white, 1.0/65535.0)
        display = np.clip(linear / proxy_white * .96, 0.0, 1.0)
        display = np.power(display, 1.0 / 2.2)
        rgb8 = np.uint8(np.rint(display * 255.0))
    else:
        rgb8 = dst
    gray = cv2.cvtColor(rgb8, cv2.COLOR_RGB2GRAY)
    # Contrast normalization is analysis-only and keeps geometric positions fixed.
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(gray)
    detected = _line_features(gray)
    # Spatially distributed verticals, never a single facade/window edge.
    verts = [v for v in detected if abs(v["vertical_tilt_deg"]) <= 12 and
             abs(v["p"][3]-v["p"][1]) >= .045]
    horizontal = [v for v in detected if
                  min(abs(v["angle_deg"]), abs(180-v["angle_deg"])) <= 38 and
                  abs(v["p"][2]-v["p"][0]) >= .035]
    vertical_len = [v["length"] for v in verts]
    tilt = _weighted_median([v["vertical_tilt_deg"] for v in verts], vertical_len) if verts else None
    mad = _weighted_median([abs(v["vertical_tilt_deg"]-tilt) for v in verts], vertical_len) if verts else None
    xspan = _span([v["mid_x"] for v in verts])
    # Full-length architectural columns share the same midpoint: use vertical
    # endpoint *coverage*, not midpoint spread, to avoid a false HOLD.
    yspan = _span([y for v in verts for y in (v["p"][1],v["p"][3])])

    # Plausible global roll (not camera pitch or per-facade projective warp).
    reliable = bool(len(verts) >= 6 and mad <= 1.1 and xspan >= .22 and yspan >= .13)
    roll_candidate = (-tilt if reliable and .55 <= abs(tilt) <= 5.0 else None)
    if reliable and abs(tilt) < .55:
        vertical_status = "NO_CHANGE_NEEDED"
    elif roll_candidate is not None:
        vertical_status = "CANDIDATE_FOR_VALIDATION"
    else:
        vertical_status = "HOLD"

    left = [x for x in horizontal if 6 < x["angle_deg"] < 38]
    right = [x for x in horizontal if 142 < x["angle_deg"] < 174]
    flat = [x for x in horizontal if x not in left and x not in right]
    # Conservative intent classification; no model is allowed to force FRONT.
    if reliable and len(left) >= 4 and len(right) >= 4:
        intent = "ANGLE"
        intent_reason = "two_horizontal_line_families"
    elif reliable and len(flat) >= 4 and len(horizontal) >= 6:
        intent = "FRONT_CANDIDATE"
        intent_reason = "dominant_horizontal_lines"
    else:
        intent = "UNKNOWN"
        intent_reason = "insufficient_multiplane_evidence"

    confidence = 0.
    if verts:
        density = min(1., len(verts)/12)
        spread = min(1., xspan/.4)*min(1., yspan/.22)
        coherence = max(0., 1-min(1., (mad or 0)/2.))
        confidence = float(round(.40*density+.30*spread+.30*coherence, 4))
    if not reliable:
        confidence = min(confidence, .69)

    sorted_lines = sorted(detected, key=lambda v: v["length"], reverse=True)
    vp = _vanishing_direction(horizontal)
    return {
      "schema": 1, "engine": VERSION,
      "status": "OBSERVATION_ONLY",
      "pixels_corrected": False, "automatic_correction_applied": False,
      "source_dimensions": [w, h],
      "analysis_dimensions": [gray.shape[1], gray.shape[0]],
      "analysis_proxy": {
        "type": "GLOBAL_PERCENTILE_GAMMA_CLAHE_GEOMETRY_ONLY" if rgb16.dtype == np.uint16 else "CLAHE_GEOMETRY_ONLY",
        "source_pixels_modified": False,
        "white_percentile": 99.5 if rgb16.dtype == np.uint16 else None,
        "white_linear_value": round(proxy_white, 7) if proxy_white is not None else None,
        "gamma": round(1.0/2.2, 7) if rgb16.dtype == np.uint16 else None,
      },
      "detected_line_count": len(detected),
      "vertical_inlier_count": len(verts),
      "horizontal_line_count": len(horizontal),
      "verticals": {
        "status": vertical_status,
        "median_tilt_deg": round(float(tilt), 4) if tilt is not None else None,
        "weighted_mad_deg": round(float(mad), 4) if mad is not None else None,
        "spatial_x_span": round(xspan, 4),
        "spatial_y_span": round(yspan, 4),
        "candidate_roll_correction_deg": round(float(roll_candidate), 4) if roll_candidate is not None else None,
      },
      "scene": {"intent": intent, "reason": intent_reason,
                "confidence": confidence, "not_approved_for_transform": True},
      "vanishing_point": vp,
      "diagnostic_lines": [
        {"xyxy_norm": [round(float(x), 5) for x in v["p"]],
         "orientation_deg": round(v["angle_deg"], 3)}
        for v in sorted_lines[:MAX_DIAGNOSTIC_LINES]
      ],
      "lens_status": "UNCALIBRATED",
      "crop_status": "NOT_PERFORMED",
      "HDR_applied": False,
      "human_visual_QA": "PENDING",
      "hold_reason": None if reliable else "INSUFFICIENT_RELIABLE_DISTRIBUTED_VERTICALS",
      "next_stage": "CAMERA_GEOMETRY_SOLVER_PENDING",
    }
