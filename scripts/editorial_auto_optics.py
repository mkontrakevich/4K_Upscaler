"""Automatic profile-backed optical correction for TECH MASTER.

Only applies verified lens profiles. Never invents distortion coefficients.
Unknown/mismatched optics return HOLD and leave pixels unchanged.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import tifffile

ENGINE = "AUTO_PROFILED_OPTICS_V1"
MAX_CROP_FRACTION = 0.14
MAX_ZOOM = 1.085

PROFILES = (
    {
        "id": "SONY_FE1635F28GM_16MM_PTLENS_3RD_PARTY",
        "make": "SONY",
        "camera": "ILCE-7M4",
        "lens": "FE 16-35mm F2.8 GM",
        "focal_mm": 16.0,
        "focal_tolerance_mm": 0.35,
        "abc": (0.021, -0.052, -0.003),
        "calibration_type": "third_party_not_Sony_official",
        "calibration_source": "pixls.us/Hugin-PTLens community calibration",
    },
)


def _text(x):
    if isinstance(x, bytes):
        return x.decode("utf-8", "ignore").strip("\x00 ")
    return str(x).strip("\x00 ")


def _ratio(x):
    if isinstance(x, tuple) and len(x) == 2 and x[1]:
        return float(x[0]) / float(x[1])
    try:
        return float(x)
    except Exception:
        return None


def _metadata(path: Path):
    try:
        with tifffile.TiffFile(path) as f:
            tags = f.pages[0].tags
            exif_tag = tags.get("ExifTag")
            exif = exif_tag.value if exif_tag is not None else {}
            make = _text(tags["Make"].value) if "Make" in tags else ""
            camera = _text(tags["Model"].value) if "Model" in tags else ""
            lens = _text(exif.get("LensModel", "")) if isinstance(exif, dict) else ""
            focal = _ratio(exif.get("FocalLength")) if isinstance(exif, dict) else None
            return {"make": make, "camera": camera, "lens": lens, "focal_mm": focal}
    except Exception:
        return {"make": "", "camera": "", "lens": "", "focal_mm": None}


def _profile_for(meta):
    make = meta.get("make", "").upper()
    camera = meta.get("camera", "")
    lens = meta.get("lens", "")
    focal = meta.get("focal_mm")
    for p in PROFILES:
        if make != p["make"].upper():
            continue
        if camera != p["camera"] or lens != p["lens"]:
            continue
        if not isinstance(focal, (int, float)) or not math.isfinite(focal):
            continue
        if abs(float(focal) - float(p["focal_mm"])) <= float(p["focal_tolerance_mm"]):
            return p
    return None


def _map_for_block(w, h, abc, zoom, y0, y1):
    cx, cy = (w - 1) * 0.5, (h - 1) * 0.5
    unit = min(w, h) * 0.5
    yy, xx = np.mgrid[y0:y1, 0:w].astype(np.float32)
    u = (xx - cx) / (unit * zoom)
    v = (yy - cy) / (unit * zoom)
    r = np.sqrt(u * u + v * v)
    a, b, cc = abc
    factor = 1 - a - b - cc + cc * r + b * r * r + a * r * r * r
    derivative = 1 - a - b - cc + 2 * cc * r + 3 * b * r * r + 4 * a * r * r * r
    mx = u * factor * unit + cx
    my = v * factor * unit + cy
    valid = (mx >= 0) & (mx <= w - 1) & (my >= 0) & (my <= h - 1)
    return mx, my, valid, float(np.min(derivative))


def _coarse_validity(w, h, abc, zoom):
    # Determine a safe zoom on a small grid; the chosen value is then verified
    # exactly while processing full-resolution stripes.
    sw, sh = min(w, 192), min(h, 128)
    xs = np.linspace(0, w - 1, sw, dtype=np.float32)
    ys = np.linspace(0, h - 1, sh, dtype=np.float32)
    xx, yy = np.meshgrid(xs, ys)
    cx, cy = (w - 1) * 0.5, (h - 1) * 0.5
    unit = min(w, h) * 0.5
    u = (xx - cx) / (unit * zoom)
    v = (yy - cy) / (unit * zoom)
    r = np.sqrt(u * u + v * v)
    a, b, cc = abc
    factor = 1 - a - b - cc + cc * r + b * r * r + a * r * r * r
    derivative = 1 - a - b - cc + 2 * cc * r + 3 * b * r * r + 4 * a * r * r * r
    mx = u * factor * unit + cx
    my = v * factor * unit + cy
    valid = (mx >= 0) & (mx <= w - 1) & (my >= 0) & (my <= h - 1)
    return 1.0 - float(np.mean(valid)), float(np.min(derivative))

def apply(rgb16, source_path: Path, source_kind: str):
    if not isinstance(rgb16, np.ndarray) or rgb16.dtype != np.uint16 or rgb16.ndim != 3 or rgb16.shape[2] != 3:
        raise ValueError("invalid_source_uint16_rgb")
    evidence = {
        "schema": 1,
        "engine": ENGINE,
        "status": "HOLD",
        "reason": None,
        "profile_id": None,
        "profile_source": None,
        "calibration_type": None,
        "metadata": None,
        "distortion_model": None,
        "crop_zoom": 1.0,
        "crop_area_fraction": 0.0,
        "invalid_source_fraction": 0.0,
        "minimum_radial_derivative": None,
        "changed_mean_absolute_fraction": 0.0,
        "chromatic_aberration_corrected": False,
        "vignetting_corrected": False,
        "HDR_applied": False,
        "generative_processing": False,
        "human_visual_QA": "PENDING",
    }
    if source_kind != "CAMERA_RAW":
        evidence["reason"] = "OPTICS_METADATA_NOT_TRUSTED_FOR_RASTER_SOURCE"
        return rgb16, evidence

    meta = _metadata(source_path)
    evidence["metadata"] = meta
    profile = _profile_for(meta)
    if profile is None:
        evidence["reason"] = "OPTICS_UNCALIBRATED_NO_VERIFIED_PROFILE_MATCH"
        return rgb16, evidence

    h, w = rgb16.shape[:2]
    chosen = None
    for zoom in np.linspace(1.0, MAX_ZOOM, 18):
        invalid_coarse, derivative = _coarse_validity(w, h, profile["abc"], float(zoom))
        if derivative <= 0:
            evidence["reason"] = "OPTICS_PROFILE_NON_MONOTONIC"
            return rgb16, evidence
        if invalid_coarse <= 0.00002:
            chosen = (float(zoom), derivative)
            break
    if chosen is None:
        evidence["reason"] = "OPTICS_SAFE_CROP_LIMIT_EXCEEDED"
        return rgb16, evidence

    zoom, derivative = chosen
    crop_fraction = 1.0 - 1.0 / (zoom * zoom)
    if crop_fraction > MAX_CROP_FRACTION:
        evidence["reason"] = "OPTICS_SAFE_CROP_LIMIT_EXCEEDED"
        return rgb16, evidence

    corrected = np.empty_like(rgb16)
    invalid_count = 0
    total = h * w
    min_derivative = derivative
    stripe = 192
    for y0 in range(0, h, stripe):
        y1 = min(h, y0 + stripe)
        mx, my, valid, local_derivative = _map_for_block(w, h, profile["abc"], zoom, y0, y1)
        min_derivative = min(min_derivative, local_derivative)
        invalid_count += int(valid.size - np.count_nonzero(valid))
        corrected[y0:y1] = cv2.remap(
            rgb16, mx, my, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0)
        )
    invalid = invalid_count / max(1, total)
    if min_derivative <= 0 or invalid > 0.00002:
        evidence["reason"] = "OPTICS_FULL_RES_VALIDATION_FAILED"
        return rgb16, evidence
    derivative = min_derivative
    step=max(1,int(math.sqrt((h*w)/350000)))
    changed = float(np.mean(
        np.abs(corrected[::step,::step].astype(np.int32)-rgb16[::step,::step].astype(np.int32))
    )/65535.0)
    if changed < 0.0005:
        evidence["status"] = "NO_CHANGE_NEEDED"
        evidence["reason"] = "PROFILE_CHANGE_BELOW_SIGNIFICANCE"
        return rgb16, evidence

    evidence.update({
        "status": "APPLIED",
        "reason": None,
        "profile_id": profile["id"],
        "profile_source": profile["calibration_source"],
        "calibration_type": profile["calibration_type"],
        "distortion_model": {"type": "PTLENS_ABC", "coefficients_abc": list(profile["abc"])},
        "crop_zoom": round(zoom, 6),
        "crop_area_fraction": round(crop_fraction, 7),
        "invalid_source_fraction": round(invalid, 8),
        "minimum_radial_derivative": round(derivative, 6),
        "changed_mean_absolute_fraction": round(changed, 7),
    })
    return corrected, evidence
