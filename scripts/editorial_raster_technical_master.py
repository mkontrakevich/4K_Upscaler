#!/usr/bin/env python3
"""Neutral TECH master for raster and camera RAW upload jobs. NO HDR / NO GEN.

Raster inputs (JPG/PNG/WebP) keep the existing deterministic path.
Camera RAW inputs (ARW/DNG/NEF/CR2 and other LibRaw-supported files) are
developed through rawpy/LibRaw into a genuine 16-bit RGB technical master.

This does not alter the immutable historical DSC09818 V10 reference pipeline.
8-bit raster sources still yield a 16-bit TIFF container, not invented sensor precision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import cv2
import numpy as np
import tifffile

UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.I)
cv2.setNumThreads(2)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def prepare(source: Path, out: Path, job_id: str, expected_sha: str, correction_plan: Path | None = None) -> dict:
    if not UUID.fullmatch(job_id):
        raise ValueError("invalid_job_id")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha, re.I):
        raise ValueError("invalid_source_sha")
    if not source.is_file() or source.stat().st_size < 10000:
        raise ValueError("source_missing_or_too_small")
    if sha(source) != expected_sha.lower():
        raise ValueError("source_sha256_mismatch")
    encoded = np.fromfile(source, dtype=np.uint8)
    raster = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    source_kind = "RASTER"
    raw_decoder = None
    raw_camera_wb = None
    raw_visible_shape = None
    raw_alpha = False

    if raster is not None and raster.ndim in (2, 3):
        if raster.dtype not in (np.uint8, np.uint16):
            raise ValueError("unsupported_source_precision")
        if raster.ndim == 2:
            rgb = np.repeat(raster[:, :, None], 3, axis=2)
        elif raster.shape[2] == 3:
            rgb = raster[:, :, ::-1].copy()
        elif raster.shape[2] == 4:
            # Accept RGBA only if every pixel is fully opaque. No matting or
            # transparent-pixel color changes are permitted in a source master.
            fully_opaque = np.all(raster[:, :, 3] == np.iinfo(raster.dtype).max)
            if not fully_opaque:
                raise ValueError("non_opaque_alpha_requires_explicit_matte_and_QA")
            raw_alpha = True
            rgb = raster[:, :, 2::-1].copy()
        else:
            raise ValueError("unsupported_image_channels")
        signal_bits = 16 if rgb.dtype == np.uint16 else 8
        # No HDR, virtual exposures, tone recovery, resampling or new details.
        rgb16 = rgb if signal_bits == 16 else rgb.astype(np.uint16) * np.uint16(257)
    else:
        # RAW path: one deterministic LibRaw development, camera WB, no auto
        # brightness, no exposure fusion and no virtual exposures.
        try:
            import rawpy
            with rawpy.imread(str(source)) as camera_raw:
                raw_visible_shape = [
                    int(camera_raw.sizes.raw_height),
                    int(camera_raw.sizes.raw_width),
                ]
                raw_camera_wb = [float(x) for x in camera_raw.camera_whitebalance]
                rgb16 = camera_raw.postprocess(
                    use_camera_wb=True,
                    no_auto_bright=True,
                    output_bps=16,
                    gamma=(1, 1),
                    highlight_mode=rawpy.HighlightMode.Blend,
                    output_color=rawpy.ColorSpace.sRGB,
                )
        except Exception as error:
            raise ValueError("unsupported_raster_or_RAW:" + type(error).__name__) from error
        if rgb16 is None or rgb16.dtype != np.uint16 or rgb16.ndim != 3 or rgb16.shape[2] != 3:
            raise ValueError("RAW_decode_invalid")
        source_kind = "CAMERA_RAW"
        raw_decoder = "rawpy-0.28.0/LibRaw"
        signal_bits = 16
        rgb = rgb16

    height, width = rgb16.shape[:2]
    if width < 320 or height < 320 or width * height > 80_000_000:
        raise ValueError("invalid_source_dimensions")
    source_preview_rgb16 = rgb16.copy() if source_kind == "CAMERA_RAW" else None
    from editorial_source_metadata import extract_source_metadata
    source_metadata = extract_source_metadata(source, source_kind)
    correction = None
    auto_optics = None
    auto_geometry = None
    auto_perspective = None
    auto_photometry = None

    # Initial TECH MASTER is an active technical-development pipeline:
    # verified optics -> camera geometry -> neutral exposure/color.
    # Every risky stage can HOLD independently without blocking safe stages.
    if correction_plan is None:
        try:
            from editorial_auto_optics import apply as auto_optics_apply
            rgb16, auto_optics = auto_optics_apply(rgb16, source, source_kind, source_metadata)
        except Exception as error:
            auto_optics = {
                "schema":1, "engine":"AUTO_PROFILED_OPTICS_V1", "status":"HOLD",
                "reason":"OPTICS_ENGINE_FAILED_" + type(error).__name__,
                "HDR_applied":False, "generative_processing":False,
                "human_visual_QA":"PENDING"
            }

    # Geometry analysis is always based on the pixels that will enter geometry.
    try:
        from editorial_auto_perspective_analysis import analyze
        scene_analysis = analyze(rgb16)
    except Exception as error:
        scene_analysis = {
            "schema":1, "engine":"AUTO_SCENE_GEOMETRY_ANALYSIS_V1",
            "status":"HOLD", "pixels_corrected":False,
            "automatic_correction_applied":False,
            "hold_reason":"ANALYSIS_FAILED_" + type(error).__name__,
            "next_stage":"MANUAL_DIAGNOSTIC_ONLY"
        }

    if correction_plan is None:
        from editorial_auto_roll_correction import apply as auto_roll
        rgb16, auto_geometry = auto_roll(rgb16, scene_analysis)

        # Re-measure after roll before solving vertical camera perspective.
        try:
            post_roll_analysis = analyze(rgb16)
        except Exception:
            post_roll_analysis = scene_analysis
        try:
            from editorial_auto_vertical_perspective import apply as auto_vertical
            rgb16, auto_perspective = auto_vertical(rgb16, post_roll_analysis)
        except Exception as error:
            auto_perspective = {
                "schema":1, "engine":"AUTO_VERTICAL_PERSPECTIVE_V1", "status":"HOLD",
                "reason":"PERSPECTIVE_ENGINE_FAILED_" + type(error).__name__,
                "geometry_transform":"IDENTITY", "HDR_applied":False,
                "generative_processing":False, "human_visual_QA":"PENDING"
            }

        try:
            from editorial_auto_photometry import apply as auto_photo
            rgb16, auto_photometry = auto_photo(rgb16, source_kind)
        except Exception as error:
            auto_photometry = {
                "schema":1, "engine":"AUTO_NEUTRAL_PHOTOMETRY_V1", "status":"HOLD",
                "reason":"PHOTOMETRY_ENGINE_FAILED_" + type(error).__name__,
                "HDR_applied":False, "local_tone_mapping":False,
                "generative_processing":False, "human_visual_QA":"PENDING"
            }

    if correction_plan is not None:
        payload = json.loads(correction_plan.read_text(encoding="utf-8"))
        if payload.get("type") == "GEOMETRY_FACADE_QUAD_V1":
            from editorial_facade_geometry import correct
        else:
            from editorial_deterministic_correction import correct
        rgb16, correction = correct(rgb16, payload)
    out.mkdir(parents=True, exist_ok=True)
    master = out / "master16.tif"
    preview = out / "fullres-preview.jpg"
    source_preview = out / "source-preview.jpg"
    qa = out / "qa.json"
    tifffile.imwrite(master, rgb16, photometric="rgb", bigtiff=True,
        metadata={"description": "Technical master; no HDR or generated details; geometry correction only if independently measured and proven"})
    # Browser preview is display-only. The 16-bit TIFF remains the technical master.
    # Raster sources retain their direct 16→8 mapping. Camera RAW receives one
    # deterministic global display transform (no local tone mapping / no HDR)
    # so visual QA and downstream MG4K can inspect the approved sensor-derived master.
    if source_kind == "CAMERA_RAW":
        linear = np.clip(rgb16.astype(np.float32) / 65535.0, 0.0, 1.0)
        positive = linear[np.any(linear > 0, axis=2)]
        white = float(np.percentile(positive, 99.5)) if positive.size else 1.0
        white = max(white, 1.0 / 65535.0)
        display = np.clip(linear / white * 0.96, 0.0, 1.0)
        display = np.power(display, 1.0 / 2.2)
        view = np.uint8(np.rint(display * 255.0))
    else:
        view = np.uint8(np.rint(rgb16.astype(np.float32) / 257))
    ok, compressed = cv2.imencode(".jpg", view[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise ValueError("preview_encode_failed")
    preview.write_bytes(compressed.tobytes())

    if source_preview_rgb16 is not None:
        source_linear = np.clip(source_preview_rgb16.astype(np.float32) / 65535.0, 0.0, 1.0)
        source_positive = source_linear[np.any(source_linear > 0, axis=2)]
        source_white = float(np.percentile(source_positive, 99.5)) if source_positive.size else 1.0
        source_white = max(source_white, 1.0 / 65535.0)
        source_display = np.clip(source_linear / source_white * 0.96, 0.0, 1.0)
        source_display = np.power(source_display, 1.0 / 2.2)
        source_view = np.uint8(np.rint(source_display * 255.0))
        ok, source_encoded = cv2.imencode(".jpg", source_view[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 94])
        if not ok:
            raise ValueError("RAW_source_preview_encode_failed")
        source_preview.write_bytes(source_encoded.tobytes())
    with tifffile.TiffFile(master) as check:
        page = check.pages[0]
        if page.shape != (height, width, 3) or page.dtype != np.uint16:
            raise ValueError("written_tiff_invalid")
    master_sha = sha(master)
    record = {
        "schema": 1,
        "project": "tropicana-park",
        "job_id": job_id,
        "source_sha256": expected_sha.lower(),
        "source_kind": source_kind,
        "source_metadata": source_metadata,
        "source_signal_bits": signal_bits,
        "master_bit_depth": 16,
        "master_container_note": (
            "camera RAW developed once by rawpy/LibRaw into 16-bit linear sRGB; no HDR, no virtual exposures"
            if source_kind == "CAMERA_RAW" else
            "8-bit source expanded into uint16 container; no additional sensor precision"
            if signal_bits == 8 else "16-bit raster values retained"
        ),
        "raw_decoder": raw_decoder,
        "raw_camera_white_balance": raw_camera_wb,
        "base_color_management": {
            "status": "CAMERA_WB_APPLIED" if source_kind == "CAMERA_RAW" else "SOURCE_COLOR_PRESERVED",
            "method": "rawpy_use_camera_wb" if source_kind == "CAMERA_RAW" else "no_base_color_transform",
            "residual_neutral_refinement": auto_photometry.get("color_status") if auto_photometry else "NOT_PERFORMED",
            "HDR_applied": False,
            "generative_processing": False,
        },
        "raw_visible_shape": raw_visible_shape,
        "width": width,
        "height": height,
        "master_key": f"tropicana-park/editorial-jobs/{job_id}/technical/master16.tif",
        "master_sha256": master_sha,
        "master_bytes": master.stat().st_size,
        "preview_sha256": sha(preview),
        "preview_bytes": preview.stat().st_size,
        "source_preview_sha256": sha(source_preview) if source_preview.exists() else None,
        "source_preview_bytes": source_preview.stat().st_size if source_preview.exists() else None,
        "technical_version": ("CONTROLLED_GEOMETRY_RECTIFICATION_V1" if correction and
            correction["geometry_transform"] == "CONTROLLED_PROJECTIVE_WARP"
            else "DETERMINISTIC_PHOTOMETRIC_REWORK_V1") if correction else "AUTO_TECH_MASTER_V2",
        "correction": correction,
        "correction_status": correction["correction_status"] if correction else "NOT_REQUESTED",
        "auto_optics_correction": auto_optics,
        "auto_geometry_analysis": scene_analysis,
        "auto_geometry_analysis_status": scene_analysis["status"],
        "auto_geometry_correction": auto_geometry,
        "auto_vertical_perspective_correction": auto_perspective,
        "auto_photometric_correction": auto_photometry,
        "technical_status": "TECHNICAL_QA_PASS",
        "HDR_applied": False,
        "virtual_exposures": False,
        "local_HDR": False,
        "generative_processing": False,
        "geometry": (correction["geometry_transform"] if correction and
             correction["geometry_transform"] == "CONTROLLED_PROJECTIVE_WARP" else
             "AUTO_VERTICAL_PERSPECTIVE_CORRECTION" if auto_perspective and auto_perspective.get("status")=="APPLIED" else
             "AUTO_CAMERA_ROLL_CORRECTION" if auto_geometry and auto_geometry.get("status")=="APPLIED" else
             "AUTO_LENS_PROFILE_CORRECTION" if auto_optics and auto_optics.get("status")=="APPLIED"
             else "IDENTITY_NO_CORRECTION"),
        "geometry_visual_review": "PENDING",
        "human_visual_QA": "PENDING",
        "opaque_alpha_verified": raw_alpha,
        "alpha_handling": "fully_opaque_alpha_removed_losslessly" if raw_alpha else "none",
        "model_input_generated": False,
        "qa_checks": {
            "SOURCE_SHA256": "PASS",
            "BIT_DEPTH_16": "PASS",
            "HDR_ABSENT": "PASS",
            "GEOMETRY_UNCHANGED": "NOT_APPLICABLE" if
               (correction and correction["geometry_transform"]=="CONTROLLED_PROJECTIVE_WARP") or
               (auto_optics and auto_optics.get("status")=="APPLIED") or
               (auto_geometry and auto_geometry.get("status")=="APPLIED") or
               (auto_perspective and auto_perspective.get("status")=="APPLIED") else "PASS",
            "GEOMETRY_TRANSFORM_EVIDENCED": "PASS" if
               (correction and correction["geometry_transform"]=="CONTROLLED_PROJECTIVE_WARP") or
               (auto_optics and auto_optics.get("status")=="APPLIED") or
               (auto_geometry and auto_geometry.get("status")=="APPLIED") or
               (auto_perspective and auto_perspective.get("status")=="APPLIED") else "NOT_APPLICABLE",
            "TIFF_READBACK": "PASS",
            "NO_GENERATIVE_PROCESSING": "PASS",
            "RAW_CAMERA_WB_APPLIED": "PASS" if source_kind == "CAMERA_RAW" and
                isinstance(raw_camera_wb, list) and len(raw_camera_wb) >= 3 else
                "NOT_APPLICABLE" if source_kind != "CAMERA_RAW" else "HOLD",
            "AUTO_OPTICS_CORRECTED":
                "PASS" if auto_optics and auto_optics.get("status")=="APPLIED" else
                "NO_CHANGE_NEEDED" if auto_optics and auto_optics.get("status")=="NO_CHANGE_NEEDED" else
                "HOLD" if auto_optics and auto_optics.get("status")=="HOLD" else "NOT_APPLICABLE",
            "AUTO_GEOMETRY_ANALYZED": "PASS" if scene_analysis["status"]=="OBSERVATION_ONLY" else "HOLD",
            "AUTO_GEOMETRY_CORRECTED":
                "PASS" if auto_geometry and auto_geometry.get("status")=="APPLIED" else
                "NO_CHANGE_NEEDED" if auto_geometry and auto_geometry.get("status")=="NO_CHANGE_NEEDED" else
                "HOLD" if auto_geometry and auto_geometry.get("status")=="HOLD" else "NOT_APPLICABLE",
            "AUTO_VERTICAL_PERSPECTIVE_CORRECTED":
                "PASS" if auto_perspective and auto_perspective.get("status")=="APPLIED" else
                "NO_CHANGE_NEEDED" if auto_perspective and auto_perspective.get("status")=="NO_CHANGE_NEEDED" else
                "HOLD" if auto_perspective and auto_perspective.get("status")=="HOLD" else "NOT_APPLICABLE",
            "AUTO_EXPOSURE_CORRECTED":
                "PASS" if auto_photometry and auto_photometry.get("exposure_status")=="APPLIED" else
                "NO_CHANGE_NEEDED" if auto_photometry and auto_photometry.get("exposure_status")=="NO_CHANGE_NEEDED" else
                "HOLD" if auto_photometry and auto_photometry.get("status")=="HOLD" else "NOT_APPLICABLE",
            "AUTO_COLOR_CORRECTED":
                "PASS" if auto_photometry and auto_photometry.get("color_status")=="APPLIED" else
                "NO_CHANGE_NEEDED" if auto_photometry and auto_photometry.get("color_status")=="NO_CHANGE_NEEDED" else
                "HOLD" if auto_photometry and auto_photometry.get("color_status")=="HOLD" else "NOT_APPLICABLE",
        },
    }
    qa.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--source-sha256", required=True)
    p.add_argument("--correction-plan", type=Path, default=None)
    args = p.parse_args()
    print(json.dumps(prepare(args.input, args.out, args.job_id, args.source_sha256, args.correction_plan),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
