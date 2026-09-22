from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from PIL import Image
import v8_safe_appearance as pipeline


def main() -> int:
    old_source, old_raw, old_cfg = pipeline.SOURCE, pipeline.RAW_FOLDER, pipeline.CFG
    old_gate = pipeline.whole_scene_quality_gate
    try:
        with tempfile.TemporaryDirectory(prefix="mg_v879_sibling_donor_") as temporary:
            root = Path(temporary) / "MG"
            base = root / "base"
            base.mkdir(parents=True)
            source = base / "mg (2).jpeg"
            Image.new("RGB", (768, 512), "#707890").save(source)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            output_name = "GEN_QUALITY_ARCH_LOCK_V8_4_WHOLE_SCENE"
            sibling = root / output_name / "02_NANO_BANANA_PRO_RAW" / "base"
            sibling.mkdir(parents=True)
            wrong = sibling / f"mg (2)_{'0'*16}_V874_CANVAS_INTEGRITY_GATE_NANO_BANANA_PRO_APPEARANCE_DONOR.png"
            correct = sibling / f"mg (2)_{digest[:16]}_V874_CANVAS_INTEGRITY_GATE_NANO_BANANA_PRO_APPEARANCE_DONOR.png"
            Image.new("RGB", (768, 512), "#8899aa").save(wrong)
            Image.new("RGB", (768, 512), "#8899bb").save(correct)
            unrelated = root / "unrelated" / correct.name
            unrelated.parent.mkdir()
            Image.new("RGB", (768, 512), "#8899bb").save(unrelated)
            pipeline.SOURCE = base
            pipeline.RAW_FOLDER = base / output_name / "02_NANO_BANANA_PRO_RAW"
            pipeline.CFG = {**old_cfg, "output_folder": output_name}
            matches = pipeline._current_v84_donor_candidates(source)
            assert set(matches) == {wrong, correct}, matches
            checks = []
            def gate(reference: Image.Image, donor: Image.Image):
                checks.append((reference.size, donor.size))
                return {"passed": True, "camera_view_lock": {"metrics": {"inliers": 50}}}, None
            pipeline.whole_scene_quality_gate = gate
            selected, detail = pipeline.compatible_current_v84_donor(source, digest, Image.open(source))
            assert selected == correct, (selected, detail)
            assert len(checks) == 1, "only exact SHA candidate may enter the whole-scene gate"
            assert detail["method"] == "same_version_exact_source_sha256_and_camera_lock"
            assert str(sibling.parent) in detail["searched_raw_roots"]
            assert not pipeline.RAW_FOLDER.exists(), "queue must stay in MG/base"
            assert "force-generation" not in str(detail)
            print('V8.7.9 SIBLING DONOR RECOVERY SELF-TEST PASSED')
            print('{"base_queue_preserved":true,"parent_raw_discovered":true,"sha_gate":true,"scene_gate_rechecked":true,"api_requests":0}')
    finally:
        pipeline.SOURCE = old_source
        pipeline.RAW_FOLDER = old_raw
        pipeline.CFG = old_cfg
        pipeline.whole_scene_quality_gate = old_gate
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
