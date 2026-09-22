from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import v8_safe_appearance as core
from safe_local_upscale import source_only_upscale


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    source_array = core.synthetic_architecture(960, 540)
    source = Image.fromarray(source_array, "RGB")
    safe = source_only_upscale(source)
    if safe.size != (3840, 2160):
        raise AssertionError(f"safe size mismatch: {safe.size}")

    # A coherent generated master contains strong, source-aligned microdetail.
    donor_array = source_array.astype(np.int16)
    gray = cv2.cvtColor(source_array, cv2.COLOR_RGB2GRAY)
    high = gray.astype(np.float32) - cv2.GaussianBlur(
        gray.astype(np.float32), (0, 0), sigmaX=0.72
    )
    donor_array += np.clip(high[:, :, None] * 0.28, -5, 5).astype(np.int16)
    donor = Image.fromarray(np.clip(donor_array, 0, 255).astype(np.uint8), "RGB")

    with tempfile.TemporaryDirectory(prefix="mg_v870_dual_") as name:
        folder = Path(name)
        raw = folder / "accepted_faithful_donor.png"
        final = folder / "strict_generative_final.png"
        donor.save(raw, "PNG")
        evidence = core.atomic_copy_verified_master(raw, final)
        if not evidence["byte_identical"] or digest(raw) != digest(final):
            raise AssertionError("generative master was modified after acceptance")

    # A changed background/direction must still be rejected before publication.
    changed = source_array.copy()
    changed[:210] = (244, 112, 38)
    cv2.line(changed, (0, 30), (959, 190), (255, 255, 255), 18)
    gate, _ = core.whole_scene_quality_gate(source, Image.fromarray(changed, "RGB"))
    if gate["passed"]:
        raise AssertionError("changed background donor passed the strict gate")

    print(json.dumps({
        "status": "passed",
        "safe_mode_api_requests": 0,
        "safe_mode_source_only": True,
        "safe_output_size": list(safe.size),
        "generative_final_equals_accepted_donor_bytes": True,
        "post_acceptance_source_blending": False,
        "changed_background_rejected": True,
        "strict_gate_decision": gate["decision"],
        "real_api_requests": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
