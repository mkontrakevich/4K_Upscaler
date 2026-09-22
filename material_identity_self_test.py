from __future__ import annotations

import json

import cv2
import numpy as np
from PIL import Image

import v8_safe_appearance as app


def main() -> int:
    source_array = app.synthetic_architecture(1280, 720)
    source = Image.fromarray(source_array, "RGB")

    # A restrained tonal/micro-detail improvement must keep the material lock.
    faithful_array = app.synthetic_donor(source_array, geometry_mutation=False)
    faithful = Image.fromarray(faithful_array, "RGB")
    faithful_gate, _ = app.whole_scene_quality_gate(source, faithful)
    assert faithful_gate["checks"]["material_identity.no_material_texture_substitution"], faithful_gate
    assert faithful_gate["checks"]["material_identity.no_material_colour_family_substitution"], faithful_gate

    # Simulate the reported failure: smooth rendered facades are converted to
    # a visibly jointed masonry/panel texture without moving the camera.
    substituted = faithful_array.copy()
    scale_x = 1280 / 960
    scale_y = 720 / 540
    facades = [(55, 180, 285, 360), (350, 205, 640, 375), (690, 175, 915, 355)]
    for left, top, right, bottom in facades:
        x0, y0 = round(left * scale_x), round(top * scale_y)
        x1, y1 = round(right * scale_x), round(bottom * scale_y)
        for y in range(y0 + 8, y1, 13):
            cv2.line(substituted, (x0 + 3, y), (x1 - 3, y), (115, 110, 105), 2)
        for row, y in enumerate(range(y0 + 8, y1, 13)):
            offset = 15 if row % 2 else 0
            for x in range(x0 + offset, x1, 34):
                cv2.line(substituted, (x, y), (x, min(y + 13, y1)), (115, 110, 105), 2)
    substituted_gate, _ = app.whole_scene_quality_gate(
        source, Image.fromarray(substituted, "RGB")
    )
    assert not substituted_gate["checks"]["material_identity.no_material_texture_substitution"], substituted_gate
    assert substituted_gate["decision"] in {
        "REJECTED_MATERIAL_IDENTITY", "REJECTED_CAMERA_VIEW"
    }, substituted_gate
    failed_cells = [
        cell for cell in substituted_gate["grid"]["cells"]
        if "material_texture_substitution" in cell["issues"]
    ]
    assert failed_cells, substituted_gate

    print("V8.6.9 MATERIAL IDENTITY LOCK SELF-TEST PASSED")
    print(json.dumps({
        "faithful_microdetail_allowed": True,
        "smooth_plaster_to_masonry_rejected": True,
        "material_failure_has_grid_coordinates": True,
        "decision": substituted_gate["decision"],
        "failed_material_cells": len(failed_cells),
        "real_api_requests": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
