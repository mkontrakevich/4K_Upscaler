"""Automatic bounded neutral photometric correction for TECH MASTER.

Corrects only measurable global exposure and neutral color cast.
No HDR, local tone mapping, relighting, gamut remapping or generative content.
"""
from __future__ import annotations

import math
import numpy as np

ENGINE = "AUTO_NEUTRAL_PHOTOMETRY_V1"
MAX_EV = 0.65
GAIN_MIN = 0.92
GAIN_MAX = 1.08
MAX_CLIPPED = 0.006


def _sample(rgb):
    h, w = rgb.shape[:2]
    step = max(1, int(math.sqrt((h * w) / 320000)))
    return rgb[::step, ::step, :]


def apply(rgb16, source_kind: str):
    if not isinstance(rgb16, np.ndarray) or rgb16.dtype != np.uint16 or rgb16.ndim != 3 or rgb16.shape[2] != 3:
        raise ValueError("invalid_source_uint16_rgb")

    before = rgb16
    pixels = rgb16.astype(np.float32) / 65535.0
    sample = _sample(pixels)
    lum = np.sum(sample * np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), axis=2)
    disp_lum = np.power(np.clip(lum, 0.0, 1.0), 1.0 / 2.2)
    p05, p50, p95, p99 = [float(np.percentile(disp_lum, q)) for q in (5, 50, 95, 99)]

    operations = []
    exposure_status = "NO_CHANGE_NEEDED"
    exposure_reason = "TONAL_DISTRIBUTION_WITHIN_NEUTRAL_RANGE"
    ev = 0.0

    # Conservative global exposure only. Preserve low-key/night scenes when
    # highlights are already present; do not convert night into day.
    if p50 < 0.27 and p95 < 0.78:
        target = 0.34 if p95 > 0.52 else 0.31
        ev = float(np.clip(math.log2(max(1e-4, target) / max(1e-4, p50)), 0.0, MAX_EV))
        if ev >= 0.08:
            exposure_status = "APPLIED"
            exposure_reason = None
    elif p50 > 0.70 or (p99 > 0.997 and p50 > 0.58):
        target = 0.58
        ev = float(np.clip(math.log2(target / max(1e-4, p50)), -MAX_EV, 0.0))
        if abs(ev) >= 0.08:
            exposure_status = "APPLIED"
            exposure_reason = None

    if exposure_status == "APPLIED":
        pixels *= np.float32(2.0 ** ev)
        operations.append({
            "op": "GLOBAL_EXPOSURE",
            "ev": round(ev, 5),
            "display_luminance_before": {
                "p05": round(p05, 5), "p50": round(p50, 5),
                "p95": round(p95, 5), "p99": round(p99, 5),
            },
            "bound_ev": MAX_EV,
        })

    # Neutral-pixel white balance refinement. Camera WB remains the primary RAW
    # basis; this only removes a measurable residual cast when enough neutral
    # evidence exists across midtones.
    sample2 = _sample(pixels)
    light = np.mean(sample2, axis=2)
    mx = np.max(sample2, axis=2)
    mn = np.min(sample2, axis=2)
    rel_chroma = (mx - mn) / np.maximum(mx, 0.04)
    neutral = (light > 0.08) & (light < 0.78) & (rel_chroma < 0.105)
    neutral_fraction = float(np.mean(neutral))
    color_status = "HOLD"
    color_reason = "INSUFFICIENT_NEUTRAL_COLOR_EVIDENCE"
    gains = np.ones(3, dtype=np.float32)

    if neutral_fraction >= 0.022:
        means = np.mean(sample2[neutral], axis=0)
        level = float(np.mean(means))
        gains = np.clip(level / np.maximum(means, 1e-5), GAIN_MIN, GAIN_MAX).astype(np.float32)
        max_delta = float(np.max(np.abs(gains - 1.0)))
        if max_delta < 0.012:
            color_status = "NO_CHANGE_NEEDED"
            color_reason = "NO_MEASURABLE_NEUTRAL_CAST"
        else:
            color_status = "APPLIED"
            color_reason = None
            pixels *= gains
            operations.append({
                "op": "NEUTRAL_WHITE_BALANCE_REFINEMENT",
                "gain_rgb": [round(float(x), 6) for x in gains],
                "neutral_fraction": round(neutral_fraction, 6),
                "gain_bounds": [GAIN_MIN, GAIN_MAX],
                "raw_camera_wb_remains_primary": source_kind == "CAMERA_RAW",
            })

    clipped = float(np.mean(np.any((pixels < 0.0) | (pixels > 1.0), axis=2)))
    if clipped > MAX_CLIPPED:
        # Fail closed: keep original pixels rather than publish a clipped auto correction.
        return before, {
            "schema": 1, "engine": ENGINE, "status": "HOLD",
            "reason": "AUTO_PHOTOMETRY_CLIPPING_LIMIT_EXCEEDED",
            "exposure_status": exposure_status, "color_status": color_status,
            "exposure_ev": round(ev, 5),
            "gain_rgb": [round(float(x), 6) for x in gains],
            "neutral_fraction": round(neutral_fraction, 6),
            "clipped_pixel_fraction": round(clipped, 7),
            "operations": [],
            "HDR_applied": False, "local_tone_mapping": False,
            "generative_processing": False, "human_visual_QA": "PENDING",
        }

    result = np.uint16(np.rint(np.clip(pixels, 0.0, 1.0) * 65535.0))
    changed = float(np.mean(np.abs(result.astype(np.int32) - before.astype(np.int32))) / 65535.0)

    if not operations or changed < 0.0015:
        return before, {
            "schema": 1, "engine": ENGINE, "status": "NO_CHANGE_NEEDED",
            "reason": "NO_OBJECTIVE_PHOTOMETRIC_CORRECTION_REQUIRED",
            "exposure_status": exposure_status, "exposure_reason": exposure_reason,
            "color_status": color_status, "color_reason": color_reason,
            "exposure_ev": round(ev, 5),
            "gain_rgb": [round(float(x), 6) for x in gains],
            "neutral_fraction": round(neutral_fraction, 6),
            "clipped_pixel_fraction": 0.0,
            "changed_mean_absolute_fraction": 0.0,
            "operations": [],
            "HDR_applied": False, "local_tone_mapping": False,
            "generative_processing": False, "human_visual_QA": "PENDING",
        }

    return result, {
        "schema": 1, "engine": ENGINE, "status": "APPLIED", "reason": None,
        "exposure_status": exposure_status, "exposure_reason": exposure_reason,
        "color_status": color_status, "color_reason": color_reason,
        "exposure_ev": round(ev, 5),
        "gain_rgb": [round(float(x), 6) for x in gains],
        "neutral_fraction": round(neutral_fraction, 6),
        "clipped_pixel_fraction": round(clipped, 7),
        "changed_mean_absolute_fraction": round(changed, 7),
        "operations": operations,
        "HDR_applied": False, "local_tone_mapping": False,
        "generative_processing": False, "human_visual_QA": "PENDING",
    }
