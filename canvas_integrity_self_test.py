from __future__ import annotations

import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import v8_safe_appearance as app


def faithful_detail_pass(source: np.ndarray) -> np.ndarray:
    floating = source.astype(np.float32)
    high = floating - cv2.GaussianBlur(floating, (0, 0), sigmaX=0.72)
    return np.clip(floating + np.clip(high * 0.22, -3.0, 3.0), 0, 255).astype(np.uint8)


def broken_composited_canvas(source: np.ndarray) -> np.ndarray:
    height, width = source.shape[:2]
    shifted = np.roll(source, round(width * 0.08), axis=1)
    result = shifted.copy()
    inset_x = round(width * 0.055)
    inset_y = 3
    plate = cv2.resize(
        source,
        (width - inset_x * 2, height - inset_y * 2),
        interpolation=cv2.INTER_LANCZOS4,
    )
    result[inset_y:height - inset_y, inset_x:width - inset_x] = plate
    cv2.rectangle(
        result,
        (inset_x, inset_y),
        (width - inset_x - 1, height - inset_y - 1),
        (238, 238, 238),
        3,
    )
    return result


def main() -> int:
    source = app.synthetic_architecture(1280, 720)
    valid = np.ones(source.shape[:2], dtype=bool)
    faithful = app.canvas_integrity_gate(source, faithful_detail_pass(source), valid)
    broken = app.canvas_integrity_gate(source, broken_composited_canvas(source), valid)
    if not faithful["passed"]:
        raise AssertionError(f"Faithful detail reconstruction was rejected: {faithful}")
    if broken["passed"]:
        raise AssertionError("A frame-within-frame donor passed the canvas integrity gate")
    if broken["checks"]["perimeter_low_frequency_continuity"]:
        raise AssertionError("Invented side margins were not detected")
    if broken["checks"]["no_paired_inset_side_seams"]:
        raise AssertionError("Paired inset seams were not detected")
    original_diag = app.DIAG_FOLDER
    try:
        with tempfile.TemporaryDirectory(prefix="mg_v874_migration_") as folder_name:
            folder = Path(folder_name)
            app.DIAG_FOLDER = folder / "_diagnostics"
            donor_path = folder / "pending_v873_donor.png"
            final_path = folder / "pending_v873_final.png"
            Image.fromarray(faithful_detail_pass(source), "RGB").save(donor_path, "PNG")
            Image.fromarray(source, "RGB").save(final_path, "PNG")
            state_path = app.sequential_state_path()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({
                "schema_version": 1,
                "pipeline": "V873_LOCAL_WEB_REVIEW_CONSOLE",
                "mode": "ONE_IMAGE_THEN_EXPLICIT_FINAL_APPROVAL",
                "approved": [],
                "rejected": [],
                "active": {
                    "source": str(folder / "source.jpg"),
                    "source_relative": "source.jpg",
                    "source_sha256": "0" * 64,
                    "status": "AWAITING_FINAL_APPROVAL",
                    "donor": str(donor_path),
                    "final": str(final_path),
                    "final_sha256": "1" * 64,
                },
            }), encoding="utf-8")
            migrated = app.load_sequential_state()
            if migrated["pipeline"] != app.PIPELINE_TAG:
                raise AssertionError("V8.7.3 queue did not migrate to V8.7.4")
            if migrated["active"]["status"] != "AWAITING_RAW_REVIEW":
                raise AssertionError("Pending V8.7.3 donor was not returned to local review")
            if "final" in migrated["active"]:
                raise AssertionError("Unreviewed V8.7.3 FINAL survived the V8.7.4 migration")
    finally:
        app.DIAG_FOLDER = original_diag
    print("V8.7.4 CANVAS INTEGRITY GATE SELF-TEST PASSED")
    print(json.dumps({
        "faithful_detail_passed": True,
        "frame_within_frame_rejected": True,
        "invented_side_margins_rejected": True,
        "paired_inset_seams_rejected": True,
        "v873_pending_donor_forced_to_free_revalidation": True,
        "real_api_requests": 0,
        "faithful_metrics": faithful["metrics"],
        "broken_metrics": broken["metrics"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
