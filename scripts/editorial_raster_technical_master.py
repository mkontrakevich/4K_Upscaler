#!/usr/bin/env python3
"""Neutral TECH raster master for editorial upload jobs (JPG/PNG/WebP). NO HDR.
This does not replace the genuine ARW -> 16-bit V10 pipeline for DSC09818.
8-bit sources yield a 16-bit TIFF *container*, not recovered sensor precision.
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
    raw = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if raw is None or raw.ndim not in (2, 3):
        raise ValueError("unsupported_raster")
    if raw.dtype not in (np.uint8, np.uint16):
        raise ValueError("unsupported_source_precision")
    if raw.ndim == 2:
        rgb = np.repeat(raw[:, :, None], 3, axis=2)
    elif raw.shape[2] == 3:
        rgb = raw[:, :, ::-1].copy()
    elif raw.shape[2] == 4:
        # Accept RGBA only if every pixel is fully opaque. No matting or
        # transparent-pixel color changes are permitted in a source master.
        fully_opaque = np.all(raw[:, :, 3] == np.iinfo(raw.dtype).max)
        if not fully_opaque:
            raise ValueError("non_opaque_alpha_requires_explicit_matte_and_QA")
        rgb = raw[:, :, 2::-1].copy()
    else:
        raise ValueError("unsupported_image_channels")
    height, width = rgb.shape[:2]
    if width < 320 or height < 320 or width * height > 50_000_000:
        raise ValueError("invalid_source_dimensions")
    signal_bits = 16 if rgb.dtype == np.uint16 else 8
    # No HDR, virtual exposures, tone recovery, resampling or new details.
    rgb16 = rgb if signal_bits == 16 else rgb.astype(np.uint16) * np.uint16(257)
    correction = None
    if correction_plan is not None:
        from editorial_deterministic_correction import correct
        payload = json.loads(correction_plan.read_text(encoding="utf-8"))
        rgb16, correction = correct(rgb16, payload)
    out.mkdir(parents=True, exist_ok=True)
    master = out / "master16.tif"
    preview = out / "fullres-preview.jpg"
    qa = out / "qa.json"
    tifffile.imwrite(master, rgb16, photometric="rgb", bigtiff=True,
        metadata={"description": "Neutral raster technical master, HDR ABSENT, no generative processing"})
    # Browser preview is 8-bit and full-pixel; never pretend it is the 16-bit TIFF.
    view = np.uint8(np.rint(rgb16.astype(np.float32) / 257))
    ok, compressed = cv2.imencode(".jpg", view[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise ValueError("preview_encode_failed")
    preview.write_bytes(compressed.tobytes())
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
        "source_signal_bits": signal_bits,
        "master_bit_depth": 16,
        "master_container_note": "8-bit source expanded into uint16 container; no additional sensor precision" if signal_bits == 8 else "16-bit raster values retained",
        "width": width,
        "height": height,
        "master_key": f"tropicana-park/editorial-jobs/{job_id}/technical/master16.tif",
        "master_sha256": master_sha,
        "master_bytes": master.stat().st_size,
        "preview_sha256": sha(preview),
        "preview_bytes": preview.stat().st_size,
        "technical_version": "DETERMINISTIC_PHOTOMETRIC_REWORK_V1" if correction else "GENERIC_RASTER_NEUTRAL_NO_HDR_V1",
        "correction": correction,
        "correction_status": correction["correction_status"] if correction else "NOT_REQUESTED",
        "technical_status": "TECHNICAL_QA_PASS",
        "HDR_applied": False,
        "virtual_exposures": False,
        "local_HDR": False,
        "generative_processing": False,
        "geometry": "IDENTITY_NO_CORRECTION",
        "geometry_visual_review": "PENDING",
        "human_visual_QA": "PENDING",
        "opaque_alpha_verified": bool(raw.ndim==3 and raw.shape[2]==4),
        "alpha_handling": "fully_opaque_alpha_removed_losslessly" if raw.ndim==3 and raw.shape[2]==4 else "none",
        "model_input_generated": False,
        "qa_checks": {
            "SOURCE_SHA256": "PASS",
            "BIT_DEPTH_16": "PASS",
            "HDR_ABSENT": "PASS",
            "GEOMETRY_UNCHANGED": "PASS",
            "TIFF_READBACK": "PASS",
            "NO_GENERATIVE_PROCESSING": "PASS",
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
