"""Offline source-sky and border-registration regressions. Zero API calls."""
from __future__ import annotations

import json

import cv2
import numpy as np
from PIL import Image

import v8_safe_appearance as app


def main() -> int:
    source = app.synthetic_architecture(1280, 720)
    reference = Image.fromarray(source, "RGB")
    preview_mask, hard_mask, metrics = app._source_sky_recovery_mask(reference)
    assert 0.07 <= metrics["mask_fraction"] <= 0.55
    damaged = source.copy()
    sky = np.asarray(preview_mask) > 245
    damaged[sky] = np.uint8(np.clip(damaged[sky].astype(np.int16) + 32, 0, 255))
    damaged[620:640, 400:430] = (120, 116, 108)  # Donor material detail outside sky.
    recovered, provenance = app.recover_original_source_sky(reference, Image.fromarray(damaged, "RGB"))
    result = np.asarray(recovered)
    assert np.max(np.abs(result[sky].astype(np.int16) - source[sky].astype(np.int16))) <= 1
    assert np.array_equal(result[620:640, 400:430], damaged[620:640, 400:430])
    assert provenance["model_api_requests"] == 0
    assert provenance["donor_resolution_preserved"] == [1280, 720]

    cells = [{"column": col, "row": row, "issues": []}
             for row in range(1, 7) for col in range(1, 11)]
    checks = {
        "canvas_integrity.perimeter_low_frequency_continuity": False,
        "artifact_control.corrupt_grid_cells": False,
        "geometry_and_identity.exact_camera_view": True,
    }
    report = {"passed": False, "camera_view_lock": {"passed": True},
              "checks": checks, "grid": {"columns": 10, "rows": 6, "cells": cells}}
    cells[17]["issues"] = ["structure_loss"]  # 8:2, source sky.
    assert app._source_sky_recovery_eligible(reference, report)
    cells[17]["issues"] = []
    cells[44]["issues"] = ["structure_loss"]  # 5:5, foreground.
    assert not app._source_sky_recovery_eligible(reference, report)

    # A subpixel camera fit must not invent a long line on the outside pixel.
    h, w = source.shape[:2]
    transform = np.array([[1, 0, 0.3], [0, 1, -0.2], [0, 0, 1]], dtype=np.float64)
    shifted = cv2.warpPerspective(source, transform, (w, h),
                                  flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
    valid = cv2.warpPerspective(np.full((h, w), 255, np.uint8), transform, (w, h),
                                flags=cv2.INTER_NEAREST) > 0
    border = app.canvas_integrity_gate(source, shifted, valid)
    assert border["checks"]["no_unsupported_long_border_seam"]
    assert border["checks"]["no_paired_inset_side_seams"]

    print("V8.8.1 SOURCE SKY RECOVERY SELF-TEST PASSED")
    print(json.dumps({"original_sky_restored": True, "donor_foreground_preserved": True,
                      "foreground_corruption_still_blocked": True,
                      "subpixel_border_artifact_ignored": True, "api_requests": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
