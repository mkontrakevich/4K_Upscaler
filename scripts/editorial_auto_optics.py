"""Universal profile-backed optical correction for TECH MASTER.

Correction is driven by camera/lens metadata and a verified Lensfun profile,
not by the source filename extension.

Order follows the calibrated optical pipeline:
1) vignetting on linear RAW
2) lateral chromatic aberration (TCA) on linear RAW
3) geometric lens distortion on RAW or rendered sources with trustworthy EXIF

If a profile/stage is unavailable or unsafe, that stage returns HOLD and does
not guess coefficients. A historical Sony distortion profile remains as a
fallback only for the already verified DSC09818 camera/lens combination.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import cv2
import numpy as np

ENGINE = "AUTO_PROFILED_OPTICS_V2"
MAX_DISTORTION_CROP_FRACTION = 0.20
MAX_TCA_SHIFT_FRACTION = 0.010
MAX_TCA_OOB_FRACTION = 0.002
MAX_VIGNETTE_GAIN = 1.55
MAX_VIGNETTE_CLIPPED_FRACTION = 0.006
STRIPE = 192

# Historical verified fallback. Distortion only; CA/vignetting are never
# synthesized from this entry.
MANUAL_DISTORTION_PROFILES = (
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


def _norm(value):
    value = str(value or "").casefold()
    value = re.sub(r"[^a-z0-9]+", "", value)
    for prefix in ("sonycorporation", "nikoncorporation", "canoninc", "fujifilmcorporation"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def _exactish(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 8 and (a in b or b in a))


def _stage(status="HOLD", reason=None, **extra):
    return {
        "status": status,
        "reason": reason,
        "profile_verified": False,
        "HDR_applied": False,
        "generative_processing": False,
        "human_visual_QA": "PENDING",
        **extra,
    }


def _profile_for(meta):
    """Backward-compatible manual Sony fallback lookup used by older tests."""
    make = _norm(meta.get("make"))
    camera = _norm(meta.get("camera"))
    lens = _norm(meta.get("lens"))
    focal = meta.get("focal_mm")
    for p in MANUAL_DISTORTION_PROFILES:
        if make != _norm(p["make"]) or camera != _norm(p["camera"]) or lens != _norm(p["lens"]):
            continue
        if not isinstance(focal, (int, float)) or not math.isfinite(focal):
            continue
        if abs(float(focal) - float(p["focal_mm"])) <= float(p["focal_tolerance_mm"]):
            return p
    return None


def _best_unique(items, expected):
    if not items:
        return None
    matches = [x for x in items if _exactish(getattr(x, "model", ""), expected)]
    if not matches:
        return None
    matches.sort(key=lambda x: int(getattr(x, "score", 0) or 0), reverse=True)
    if len(matches) > 1 and int(getattr(matches[0], "score", 0) or 0) == int(getattr(matches[1], "score", 0) or 0):
        # Same-score ambiguity must be resolved by metadata/user, never guessed.
        return None
    return matches[0]


def _find_lensfun(meta):
    if not meta.get("optics_identity_complete"):
        return None, None, "OPTICS_METADATA_INCOMPLETE"
    try:
        import lensfunpy
    except Exception:
        return None, None, "LENSFUN_RUNTIME_UNAVAILABLE"

    try:
        db = lensfunpy.Database()
        cameras = db.find_cameras(meta.get("make"), meta.get("camera"), loose_search=False)
        cam = _best_unique(cameras, meta.get("camera"))
        if cam is None:
            cameras = db.find_cameras(meta.get("make"), meta.get("camera"), loose_search=True)
            cam = _best_unique(cameras, meta.get("camera"))
        if cam is None:
            return None, None, "LENSFUN_CAMERA_PROFILE_NOT_FOUND"

        maker = meta.get("lens_make")
        lenses = db.find_lenses(cam, maker, meta.get("lens"), loose_search=False)
        lens = _best_unique(lenses, meta.get("lens"))
        if lens is None:
            lenses = db.find_lenses(cam, maker, meta.get("lens"), loose_search=True)
            lens = _best_unique(lenses, meta.get("lens"))
        if lens is None and maker:
            # Some EXIF omits/normalizes maker differently. Camera mount + exact
            # lens model is still acceptable; ambiguity remains blocked.
            lenses = db.find_lenses(cam, None, meta.get("lens"), loose_search=True)
            lens = _best_unique(lenses, meta.get("lens"))
        if lens is None:
            return cam, None, "LENSFUN_LENS_PROFILE_NOT_FOUND"

        focal = float(meta["focal_mm"])
        if focal < float(getattr(lens, "min_focal", focal)) - 0.05 or focal > float(getattr(lens, "max_focal", focal)) + 0.05:
            return cam, None, "LENSFUN_FOCAL_OUTSIDE_PROFILE_RANGE"
        return cam, lens, None
    except Exception as error:
        return None, None, "LENSFUN_PROFILE_LOOKUP_FAILED_" + type(error).__name__


def _profile_summary(cam, lens):
    return {
        "backend": "lensfunpy-1.18.0",
        "camera": {
            "maker": getattr(cam, "maker", None),
            "model": getattr(cam, "model", None),
            "crop_factor": float(getattr(cam, "crop_factor", 0.0) or 0.0),
        },
        "lens": {
            "maker": getattr(lens, "maker", None),
            "model": getattr(lens, "model", None),
            "min_focal": float(getattr(lens, "min_focal", 0.0) or 0.0),
            "max_focal": float(getattr(lens, "max_focal", 0.0) or 0.0),
            "distortion_calibrations": len(getattr(lens, "calib_distortion", []) or []),
            "tca_calibrations": len(getattr(lens, "calib_tca", []) or []),
            "vignetting_calibrations": len(getattr(lens, "calib_vignetting", []) or []),
        },
    }


def _modifier(cam, lens, w, h, focal, aperture, distance, flags, pixel_format, scale=1.0):
    import lensfunpy
    mod = lensfunpy.Modifier(lens, float(cam.crop_factor), int(w), int(h))
    mod.initialize(
        float(focal),
        float(aperture),
        float(distance),
        scale=float(scale),
        pixel_format=pixel_format,
        flags=flags,
    )
    return mod


def _apply_vignetting(rgb16, cam, lens, meta):
    if not getattr(lens, "calib_vignetting", None):
        return rgb16, _stage(reason="PROFILE_HAS_NO_VIGNETTING_CALIBRATION")
    aperture = meta.get("aperture")
    if not isinstance(aperture, (int, float)) or aperture <= 0:
        return rgb16, _stage(reason="APERTURE_METADATA_REQUIRED_FOR_VIGNETTING")
    distance = meta.get("focus_distance_m")
    distance_source = "EXIF_SUBJECT_DISTANCE"
    if not isinstance(distance, (int, float)) or distance <= 0:
        # Lensfun explicitly documents 1000m as the fallback when focus distance
        # is unavailable because its vignetting dependence is small.
        distance = 1000.0
        distance_source = "LENSFUN_DOCUMENTED_INFINITY_DEFAULT"

    try:
        import lensfunpy
        work = rgb16.astype(np.float32) / 65535.0
        before = work.copy()
        mod = _modifier(
            cam, lens, work.shape[1], work.shape[0], meta["focal_mm"], aperture, distance,
            lensfunpy.ModifyFlags.VIGNETTING, np.float32, scale=1.0
        )
        applied = bool(mod.apply_color_modification(work))
        if not applied:
            return rgb16, _stage(reason="PROFILE_VIGNETTING_NOT_APPLICABLE_AT_CAPTURE_SETTINGS",
                                 profile_verified=True)
        safe = before > 0.015
        ratios = work[safe] / np.maximum(before[safe], 1e-6)
        gain_p999 = float(np.percentile(ratios, 99.9)) if ratios.size else 1.0
        clipped = float(np.mean(np.any(work > 1.0, axis=2)))
        if gain_p999 > MAX_VIGNETTE_GAIN or clipped > MAX_VIGNETTE_CLIPPED_FRACTION:
            return rgb16, _stage(
                reason="VIGNETTING_SAFETY_LIMIT",
                profile_verified=True,
                gain_p999=round(gain_p999, 6),
                clipped_pixel_fraction=round(clipped, 7),
            )
        out = np.uint16(np.rint(np.clip(work, 0.0, 1.0) * 65535.0))
        step = max(1, int(math.sqrt((out.shape[0] * out.shape[1]) / 350000)))
        changed = float(np.mean(np.abs(
            out[::step, ::step].astype(np.int32) - rgb16[::step, ::step].astype(np.int32)
        )) / 65535.0)
        return out, _stage(
            status="APPLIED" if changed >= 0.00035 else "NO_CHANGE_NEEDED",
            reason=None if changed >= 0.00035 else "PROFILE_CHANGE_BELOW_SIGNIFICANCE",
            profile_verified=True,
            aperture=round(float(aperture), 5),
            focus_distance_m=round(float(distance), 4),
            focus_distance_source=distance_source,
            gain_p999=round(gain_p999, 6),
            clipped_pixel_fraction=round(clipped, 7),
            changed_mean_absolute_fraction=round(changed, 7),
        )
    except Exception as error:
        return rgb16, _stage(reason="VIGNETTING_ENGINE_FAILED_" + type(error).__name__,
                             profile_verified=True)


def _apply_tca(rgb16, cam, lens, meta):
    if not getattr(lens, "calib_tca", None):
        return rgb16, _stage(reason="PROFILE_HAS_NO_TCA_CALIBRATION")
    try:
        import lensfunpy
        h, w = rgb16.shape[:2]
        diag = math.hypot(w, h)
        aperture = float(meta.get("aperture") or 1.0)  # ignored by TCA model
        distance = float(meta.get("focus_distance_m") or 1000.0)  # ignored by TCA model
        mod = _modifier(
            cam, lens, w, h, meta["focal_mm"], aperture, distance,
            lensfunpy.ModifyFlags.TCA, np.uint16, scale=1.0
        )
        out = np.empty_like(rgb16)
        invalid = 0
        total = 0
        max_shift = 0.0
        applied_any = False
        for y0 in range(0, h, STRIPE):
            hh = min(STRIPE, h - y0)
            coords = mod.apply_subpixel_distortion(0.0, float(y0), w, hh)
            if coords is None:
                return rgb16, _stage(reason="PROFILE_TCA_NOT_APPLICABLE_AT_FOCAL",
                                     profile_verified=True)
            coords = np.asarray(coords, dtype=np.float32)
            if coords.shape != (hh, w, 3, 2):
                return rgb16, _stage(reason="TCA_COORDINATE_SHAPE_INVALID",
                                     profile_verified=True)
            yy, xx = np.mgrid[y0:y0 + hh, 0:w].astype(np.float32)
            stripe = np.empty((hh, w, 3), np.uint16)
            for ch in range(3):
                mx = coords[..., ch, 0]
                my = coords[..., ch, 1]
                valid = np.isfinite(mx) & np.isfinite(my) & (mx >= 0) & (mx <= w - 1) & (my >= 0) & (my <= h - 1)
                invalid += int(valid.size - np.count_nonzero(valid))
                total += int(valid.size)
                shift = np.sqrt((mx - xx) ** 2 + (my - yy) ** 2)
                if shift.size:
                    max_shift = max(max_shift, float(np.nanpercentile(shift, 99.99)))
                mx = np.clip(np.nan_to_num(mx, nan=0.0), 0, w - 1)
                my = np.clip(np.nan_to_num(my, nan=float(y0)), 0, h - 1)
                stripe[..., ch] = cv2.remap(
                    rgb16[..., ch], mx, my, interpolation=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE
                )
            out[y0:y0 + hh] = stripe
            applied_any = True
        invalid_fraction = invalid / max(1, total)
        if invalid_fraction > MAX_TCA_OOB_FRACTION or max_shift > MAX_TCA_SHIFT_FRACTION * diag:
            return rgb16, _stage(
                reason="TCA_SAFETY_LIMIT",
                profile_verified=True,
                invalid_coordinate_fraction=round(invalid_fraction, 7),
                shift_p9999_px=round(max_shift, 4),
            )
        step = max(1, int(math.sqrt((h * w) / 350000)))
        changed = float(np.mean(np.abs(
            out[::step, ::step].astype(np.int32) - rgb16[::step, ::step].astype(np.int32)
        )) / 65535.0)
        if not applied_any or changed < 0.00005:
            return rgb16, _stage(
                status="NO_CHANGE_NEEDED",
                reason="PROFILE_CHANGE_BELOW_SIGNIFICANCE",
                profile_verified=True,
                invalid_coordinate_fraction=round(invalid_fraction, 7),
                shift_p9999_px=round(max_shift, 4),
            )
        return out, _stage(
            status="APPLIED",
            reason=None,
            profile_verified=True,
            invalid_coordinate_fraction=round(invalid_fraction, 7),
            shift_p9999_px=round(max_shift, 4),
            changed_mean_absolute_fraction=round(changed, 7),
        )
    except Exception as error:
        return rgb16, _stage(reason="TCA_ENGINE_FAILED_" + type(error).__name__,
                             profile_verified=True)


def _apply_distortion_lensfun(rgb16, cam, lens, meta):
    if not getattr(lens, "calib_distortion", None):
        return rgb16, _stage(reason="PROFILE_HAS_NO_DISTORTION_CALIBRATION")
    try:
        import lensfunpy
        h, w = rgb16.shape[:2]
        aperture = float(meta.get("aperture") or 1.0)  # not used by distortion
        distance = float(meta.get("focus_distance_m") or 1000.0)  # not used by distortion
        mod = _modifier(
            cam, lens, w, h, meta["focal_mm"], aperture, distance,
            lensfunpy.ModifyFlags.DISTORTION, np.uint16, scale=0.0
        )
        scale = float(getattr(mod, "scale", 1.0) or 1.0)
        crop_fraction = max(0.0, 1.0 - 1.0 / (scale * scale)) if scale > 1 else 0.0
        if crop_fraction > MAX_DISTORTION_CROP_FRACTION:
            return rgb16, _stage(
                reason="DISTORTION_AUTO_SCALE_CROP_LIMIT",
                profile_verified=True,
                scale=round(scale, 6),
                crop_area_fraction=round(crop_fraction, 7),
            )
        out = np.empty_like(rgb16)
        invalid = 0
        total = 0
        for y0 in range(0, h, STRIPE):
            hh = min(STRIPE, h - y0)
            coords = mod.apply_geometry_distortion(0.0, float(y0), w, hh)
            if coords is None:
                return rgb16, _stage(reason="PROFILE_DISTORTION_NOT_APPLICABLE_AT_FOCAL",
                                     profile_verified=True)
            coords = np.asarray(coords, dtype=np.float32)
            if coords.shape != (hh, w, 2):
                return rgb16, _stage(reason="DISTORTION_COORDINATE_SHAPE_INVALID",
                                     profile_verified=True)
            mx, my = coords[..., 0], coords[..., 1]
            valid = np.isfinite(mx) & np.isfinite(my) & (mx >= 0) & (mx <= w - 1) & (my >= 0) & (my <= h - 1)
            invalid += int(valid.size - np.count_nonzero(valid))
            total += int(valid.size)
            mx = np.clip(np.nan_to_num(mx, nan=0.0), 0, w - 1)
            my = np.clip(np.nan_to_num(my, nan=float(y0)), 0, h - 1)
            out[y0:y0 + hh] = cv2.remap(
                rgb16, mx, my, interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE
            )
        invalid_fraction = invalid / max(1, total)
        if invalid_fraction > 0.0002:
            return rgb16, _stage(
                reason="DISTORTION_FULL_RES_VALIDATION_FAILED",
                profile_verified=True,
                invalid_coordinate_fraction=round(invalid_fraction, 7),
            )
        step = max(1, int(math.sqrt((h * w) / 350000)))
        changed = float(np.mean(np.abs(
            out[::step, ::step].astype(np.int32) - rgb16[::step, ::step].astype(np.int32)
        )) / 65535.0)
        if changed < 0.0005:
            return rgb16, _stage(
                status="NO_CHANGE_NEEDED",
                reason="PROFILE_CHANGE_BELOW_SIGNIFICANCE",
                profile_verified=True,
                scale=round(scale, 6),
                crop_area_fraction=round(crop_fraction, 7),
            )
        return out, _stage(
            status="APPLIED",
            reason=None,
            profile_verified=True,
            scale=round(scale, 6),
            crop_area_fraction=round(crop_fraction, 7),
            invalid_coordinate_fraction=round(invalid_fraction, 7),
            changed_mean_absolute_fraction=round(changed, 7),
        )
    except Exception as error:
        return rgb16, _stage(reason="DISTORTION_ENGINE_FAILED_" + type(error).__name__,
                             profile_verified=True)


def _manual_map_for_block(w, h, abc, zoom, y0, y1):
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


def _manual_distortion(rgb16, profile):
    h, w = rgb16.shape[:2]
    chosen = None
    for zoom in np.linspace(1.0, 1.085, 18):
        # Coarse safety check.
        sw, sh = min(w, 192), min(h, 128)
        xs = np.linspace(0, w - 1, sw, dtype=np.float32)
        ys = np.linspace(0, h - 1, sh, dtype=np.float32)
        xx, yy = np.meshgrid(xs, ys)
        cx, cy = (w - 1) * 0.5, (h - 1) * 0.5
        unit = min(w, h) * 0.5
        u = (xx - cx) / (unit * zoom)
        v = (yy - cy) / (unit * zoom)
        r = np.sqrt(u * u + v * v)
        a, b, cc = profile["abc"]
        factor = 1 - a - b - cc + cc * r + b * r * r + a * r * r * r
        derivative = 1 - a - b - cc + 2 * cc * r + 3 * b * r * r + 4 * a * r * r * r
        mx = u * factor * unit + cx
        my = v * factor * unit + cy
        invalid = 1.0 - float(np.mean((mx >= 0) & (mx <= w - 1) & (my >= 0) & (my <= h - 1)))
        if float(np.min(derivative)) > 0 and invalid <= 0.00002:
            chosen = (float(zoom), float(np.min(derivative)))
            break
    if chosen is None:
        return rgb16, _stage(reason="MANUAL_PROFILE_SAFE_CROP_LIMIT", profile_verified=True)
    zoom, derivative = chosen
    crop = 1.0 - 1.0 / (zoom * zoom)
    if crop > 0.14:
        return rgb16, _stage(reason="MANUAL_PROFILE_SAFE_CROP_LIMIT", profile_verified=True)
    out = np.empty_like(rgb16)
    invalid = 0
    total = 0
    for y0 in range(0, h, STRIPE):
        y1 = min(h, y0 + STRIPE)
        mx, my, valid, derivative_local = _manual_map_for_block(w, h, profile["abc"], zoom, y0, y1)
        derivative = min(derivative, derivative_local)
        invalid += int(valid.size - np.count_nonzero(valid))
        total += int(valid.size)
        out[y0:y1] = cv2.remap(
            rgb16, mx, my, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE
        )
    if derivative <= 0 or invalid / max(1, total) > 0.00002:
        return rgb16, _stage(reason="MANUAL_PROFILE_FULL_RES_VALIDATION_FAILED",
                             profile_verified=True)
    step = max(1, int(math.sqrt((h * w) / 350000)))
    changed = float(np.mean(np.abs(
        out[::step, ::step].astype(np.int32) - rgb16[::step, ::step].astype(np.int32)
    )) / 65535.0)
    return out, _stage(
        status="APPLIED" if changed >= 0.0005 else "NO_CHANGE_NEEDED",
        reason=None if changed >= 0.0005 else "PROFILE_CHANGE_BELOW_SIGNIFICANCE",
        profile_verified=True,
        profile_id=profile["id"],
        scale=round(zoom, 6),
        crop_area_fraction=round(crop, 7),
        changed_mean_absolute_fraction=round(changed, 7),
    )


def apply(rgb16, source_path: Path, source_kind: str, source_metadata=None):
    if not isinstance(rgb16, np.ndarray) or rgb16.dtype != np.uint16 or rgb16.ndim != 3 or rgb16.shape[2] != 3:
        raise ValueError("invalid_source_uint16_rgb")

    if source_metadata is None:
        from editorial_source_metadata import extract_source_metadata
        source_metadata = extract_source_metadata(source_path, source_kind)

    evidence = {
        "schema": 2,
        "engine": ENGINE,
        "status": "HOLD",
        "reason": None,
        "metadata": source_metadata,
        "profile_backend": None,
        "profile": None,
        "distortion": _stage(reason="NOT_EVALUATED"),
        "chromatic_aberration": _stage(reason="NOT_EVALUATED"),
        "vignetting": _stage(reason="NOT_EVALUATED"),
        "HDR_applied": False,
        "generative_processing": False,
        "human_visual_QA": "PENDING",
    }

    cam, lens, lookup_reason = _find_lensfun(source_metadata)
    current = rgb16

    if cam is not None and lens is not None:
        evidence["profile_backend"] = "LENSFUN_BUNDLED_DATABASE"
        evidence["profile"] = _profile_summary(cam, lens)

        # Lensfun calibration order: vignetting -> TCA -> distortion.
        if source_kind == "CAMERA_RAW":
            current, evidence["vignetting"] = _apply_vignetting(current, cam, lens, source_metadata)
            current, evidence["chromatic_aberration"] = _apply_tca(current, cam, lens, source_metadata)
        else:
            evidence["vignetting"] = _stage(
                reason="NONLINEAR_RENDERED_SOURCE_UNSAFE_FOR_PROFILE_VIGNETTING",
                profile_verified=True,
            )
            evidence["chromatic_aberration"] = _stage(
                reason="NONLINEAR_RENDERED_SOURCE_UNSAFE_FOR_PROFILE_TCA",
                profile_verified=True,
            )
        current, evidence["distortion"] = _apply_distortion_lensfun(current, cam, lens, source_metadata)
    else:
        evidence["profile_backend"] = "MANUAL_FALLBACK"
        manual = _profile_for(source_metadata)
        if manual is not None:
            evidence["profile"] = {
                "backend": "historical_verified_manual_fallback",
                "id": manual["id"],
                "source": manual["calibration_source"],
                "calibration_type": manual["calibration_type"],
            }
            current, evidence["distortion"] = _manual_distortion(current, manual)
            evidence["chromatic_aberration"] = _stage(reason="MANUAL_FALLBACK_HAS_NO_TCA_CALIBRATION")
            evidence["vignetting"] = _stage(reason="MANUAL_FALLBACK_HAS_NO_VIGNETTING_CALIBRATION")
        else:
            evidence["reason"] = lookup_reason or "OPTICS_PROFILE_NOT_FOUND"
            evidence["distortion"] = _stage(reason=evidence["reason"])
            evidence["chromatic_aberration"] = _stage(reason=evidence["reason"])
            evidence["vignetting"] = _stage(reason=evidence["reason"])

    stages = [
        evidence["distortion"]["status"],
        evidence["chromatic_aberration"]["status"],
        evidence["vignetting"]["status"],
    ]
    applied = sum(x == "APPLIED" for x in stages)
    holds = sum(x == "HOLD" for x in stages)
    if applied:
        evidence["status"] = "APPLIED"
        evidence["completeness"] = "COMPLETE" if holds == 0 else "PARTIAL"
        evidence["reason"] = None if holds == 0 else "ONE_OR_MORE_OPTICAL_STAGES_HELD"
    elif all(x == "NO_CHANGE_NEEDED" for x in stages):
        evidence["status"] = "NO_CHANGE_NEEDED"
        evidence["completeness"] = "COMPLETE"
        evidence["reason"] = "ALL_PROFILE_STAGES_WITHIN_TOLERANCE"
    else:
        evidence["status"] = "HOLD"
        evidence["completeness"] = "PARTIAL" if any(x == "NO_CHANGE_NEEDED" for x in stages) else "NONE"
        evidence["reason"] = evidence["reason"] or "NO_SAFE_OPTICAL_STAGE_APPLIED"

    evidence["chromatic_aberration_corrected"] = evidence["chromatic_aberration"]["status"] == "APPLIED"
    evidence["vignetting_corrected"] = evidence["vignetting"]["status"] == "APPLIED"
    evidence["distortion_corrected"] = evidence["distortion"]["status"] == "APPLIED"
    evidence["profile_verified"] = bool(evidence["profile"])
    evidence["changed_mean_absolute_fraction"] = round(float(np.mean(np.abs(
        current[::max(1, int(math.sqrt((current.shape[0] * current.shape[1]) / 300000))),
                ::max(1, int(math.sqrt((current.shape[0] * current.shape[1]) / 300000)))].astype(np.int32)
        - rgb16[::max(1, int(math.sqrt((rgb16.shape[0] * rgb16.shape[1]) / 300000))),
                ::max(1, int(math.sqrt((rgb16.shape[0] * rgb16.shape[1]) / 300000)))].astype(np.int32)
    ))) / 65535.0, 7)

    # Compatibility fields consumed by existing geometry/QA code.
    d = evidence["distortion"]
    evidence["profile_id"] = (
        evidence["profile"].get("id") if isinstance(evidence["profile"], dict) else None
    ) or (
        (evidence["profile"] or {}).get("lens", {}).get("model")
        if isinstance(evidence["profile"], dict) else None
    )
    evidence["profile_source"] = evidence["profile_backend"]
    evidence["calibration_type"] = "verified_profile_database" if evidence["profile_backend"] == "LENSFUN_BUNDLED_DATABASE" else "historical_verified_manual"
    evidence["crop_area_fraction"] = float(d.get("crop_area_fraction", 0.0) or 0.0)
    evidence["invalid_source_fraction"] = float(d.get("invalid_coordinate_fraction", 0.0) or 0.0)
    return current, evidence
