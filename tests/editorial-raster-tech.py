#!/usr/bin/env python3
"""Synthetic, cost-free evidence for the generic raster technical stage."""
import importlib.util
import json
import hashlib
import tempfile
from pathlib import Path

import cv2
import numpy as np
import tifffile

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tech", HERE / "scripts" / "editorial_raster_technical_master.py")
tech = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tech)
JOB = "123e4567-e89b-42d3-a456-426614174000"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # Three-channel full-pixel 8-bit input.
    pixels = np.zeros((380, 510, 3), dtype=np.uint8)
    pixels[:, :, 0] = 49
    pixels[:, :, 1] = np.arange(510, dtype=np.uint8)[None, :]
    pixels[:, :, 2] = 195
    src = root / "source.png"
    assert cv2.imwrite(str(src), pixels)
    info = tech.prepare(src, root / "result8", JOB, sha(src))
    result = tifffile.imread(root / "result8" / "master16.tif")
    assert info["source_signal_bits"] == 8
    assert info["master_bit_depth"] == 16
    assert info["technical_status"] == "TECHNICAL_QA_PASS"
    assert info["HDR_applied"] is False and info["virtual_exposures"] is False
    assert info["human_visual_QA"] == "PENDING"
    assert info["geometry"] == "IDENTITY_NO_CORRECTION"
    assert info["model_input_generated"] is False
    assert result.dtype == np.uint16
    np.testing.assert_array_equal(result[:, :, ::-1], pixels.astype(np.uint16) * 257)
    assert sha(root / "result8" / "master16.tif") == info["master_sha256"]
    assert (root / "result8" / "fullres-preview.jpg").stat().st_size > 1000

    # No 8-bit precision downgrade for genuine 16-bit PNG inputs.
    deep = (pixels.astype(np.uint16) * 244) + 91
    src16 = root / "source16.png"
    assert cv2.imwrite(str(src16), deep)
    info16 = tech.prepare(src16, root / "result16", JOB, sha(src16))
    result16 = tifffile.imread(root / "result16" / "master16.tif")
    assert info16["source_signal_bits"] == 16
    np.testing.assert_array_equal(result16[:, :, ::-1], deep)

    try:
        tech.prepare(src, root / "bad", JOB, "0" * 64)
    except ValueError as err:
        assert "sha256_mismatch" in str(err)
    else:
        raise AssertionError("corrupt source digest passed")

    alpha = root / "alpha.png"
    assert cv2.imwrite(str(alpha), np.random.default_rng(3).integers(0, 255, size=(380, 510, 4), dtype=np.uint8))
    try:
        tech.prepare(alpha, root / "bad-alpha", JOB, sha(alpha))
    except ValueError as err:
        assert "alpha" in str(err)
    else:
        raise AssertionError("alpha input silently lost channel")
print("GENERIC_EDITORIAL_TECH_MASTER_PASS")
