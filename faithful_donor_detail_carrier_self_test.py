from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import v8_safe_appearance as app


def bind_output(root: Path) -> None:
    app.SOURCE = root
    app.OUTPUT = root / "GEN_QUALITY_ARCH_LOCK_V8_4_WHOLE_SCENE"
    app.REFERENCE_FOLDER = app.OUTPUT / "00_SOURCE_REFERENCE"
    app.CONTROL_FOLDER = app.OUTPUT / "01_STRUCTURE_CONTROL"
    app.RAW_FOLDER = app.OUTPUT / "02_NANO_BANANA_PRO_RAW"
    app.ALIGNED_FOLDER = app.OUTPUT / "03_REGISTERED_DONOR"
    app.MASK_FOLDER = app.OUTPUT / "04_SEMANTIC_TRANSFER_MASKS"
    app.TRANSFER_FOLDER = app.OUTPUT / "05_STRUCTURE_APPEARANCE_SEPARATION"
    app.AUDIT_FOLDER = app.OUTPUT / "06_AUDIT"
    app.REPORT_FOLDER = app.OUTPUT / "07_REPORTS"
    app.FINAL_FOLDER = app.OUTPUT / "4K_FINAL"
    app.REVIEW_FOLDER = app.OUTPUT / "REVIEW_REQUIRED"
    app.DIAG_FOLDER = app.OUTPUT / "_diagnostics"
    app.TRACE_FOLDER = app.DIAG_FOLDER / "api_traces"
    app.STATE_FOLDER = app.DIAG_FOLDER / "states"


def direct_transfer_test() -> dict[str, object]:
    assert app.recovery_direction([{
        "stage": "primary",
        "mandatory_failures": ["generative_detail_gain_present"],
    }]) == "increase_detail"
    assert app.recovery_direction([{
        "stage": "primary",
        "mandatory_failures": ["no_edge_delta_halos"],
    }]) == "reduce_for_safety"
    source = app.synthetic_architecture(1280, 720).astype(np.int16)
    noise = np.random.default_rng(868).normal(0.0, 2.0, source.shape)
    source = np.clip(source + noise, 0, 255).astype(np.uint8)
    donor = np.clip(source.astype(np.float32) * 1.035 + 4.0, 0, 255).astype(np.uint8)
    donor = cv2.GaussianBlur(donor, (0, 0), 0.35)
    resolved_micro = np.random.default_rng(2868).normal(0.0, 3.8, donor[190:].shape)
    donor[190:] = np.clip(donor[190:].astype(np.float32) + resolved_micro, 0, 255).astype(np.uint8)
    # Deliberately invent a different sky/background and facade stripe pattern.
    donor[:190] = np.array([245, 150, 75], dtype=np.uint8)
    for x in range(370, 630, 14):
        cv2.line(donor, (x, 220), (x, 360), (65, 45, 30), 2)

    reference = Image.fromarray(source, "RGB")
    aligned = Image.fromarray(donor, "RGB")
    valid = Image.new("L", reference.size, 255)
    final, _, appearance = app.separate_and_transfer(reference, aligned, valid, 0.98)
    validation, _, _, _ = app.validate_final(reference, final, appearance, aligned)
    # The adversarial donor itself would be rejected by the whole-scene gate;
    # this direct probe verifies that its forbidden broad signals still cannot
    # enter FINAL while valid high-frequency quality does.
    final_array = np.asarray(final)
    sky_source = source[:180].astype(np.float32)
    sky_final = final_array[:180].astype(np.float32)
    sky_donor = donor[:180].astype(np.float32)
    source_final_sky_mae = float(np.mean(np.abs(sky_final - sky_source)))
    donor_source_sky_mae = float(np.mean(np.abs(sky_donor - sky_source)))
    assert source_final_sky_mae < donor_source_sky_mae * 0.04
    assert validation["checks"]["source_background_and_tone_field_locked"]
    assert validation["checks"]["source_colour_field_locked"]
    assert validation["checks"]["source_lighting_direction_locked"]
    assert appearance["donor_low_frequency_transfer"] == 0.0
    assert appearance["donor_chroma_transfer"] == 0.0
    source_l = cv2.cvtColor(source, cv2.COLOR_RGB2LAB)[:, :, 0].astype(np.float32)
    donor_l = cv2.cvtColor(donor, cv2.COLOR_RGB2LAB)[:, :, 0].astype(np.float32)
    final_l = cv2.cvtColor(final_array, cv2.COLOR_RGB2LAB)[:, :, 0].astype(np.float32)
    sigma = float(app.CFG["carrier_high_band_split_sigma"])
    source_high = source_l - cv2.GaussianBlur(source_l, (0, 0), sigmaX=sigma)
    donor_high = donor_l - cv2.GaussianBlur(donor_l, (0, 0), sigmaX=sigma)
    final_high = final_l - cv2.GaussianBlur(final_l, (0, 0), sigmaX=sigma)
    quality_region = (slice(190, 700), slice(30, 340))
    source_to_donor = float(np.mean(np.abs(
        source_high[quality_region] - donor_high[quality_region]
    )))
    final_to_donor = float(np.mean(np.abs(
        final_high[quality_region] - donor_high[quality_region]
    )))
    donor_detail_retention = 1.0 - final_to_donor / max(1e-6, source_to_donor)
    assert donor_detail_retention >= 0.45, donor_detail_retention
    assert appearance["donor_high_frequency_retention"] >= 0.28
    assert validation["donor_detail_evidence_passed"] is True
    assert validation["checks"]["generative_detail_gain_present"] is True
    return {
        "invented_background_transfer_ratio": round(
            source_final_sky_mae / max(1e-6, donor_source_sky_mae), 7
        ),
        "broad_luminance_delta_p99": validation["broad_luminance_delta_p99"],
        "chroma_delta_p99": validation["chroma_delta_p99"],
        "lighting_direction_cosine": validation["lighting_direction_cosine"],
        "donor_low_frequency_transfer": 0.0,
        "donor_chroma_transfer": 0.0,
        "donor_detail_retention": round(donor_detail_retention, 6),
        "mean_carrier_weight": appearance["mean_carrier_weight"],
        "donor_detail_fidelity": validation["donor_detail_fidelity"],
        "donor_detail_energy_ratio": validation["donor_detail_energy_ratio"],
    }


def pipeline_and_v868_recovery_test(root: Path) -> dict[str, object]:
    bind_output(root)
    source_path = root / "source.png"
    source_pixels = app.synthetic_architecture(1280, 720).astype(np.int16)
    source_pixels = np.clip(
        source_pixels + np.random.default_rng(1868).normal(0.0, 2.4, source_pixels.shape),
        0,
        255,
    ).astype(np.uint8)
    Image.fromarray(source_pixels, "RGB").save(source_path, "PNG", optimize=True)
    source_bytes = source_path.read_bytes()
    source_mtime = source_path.stat().st_mtime_ns

    old_target = app.TARGET
    old_generate = app.generate_image
    old_offline = app.OFFLINE_SELF_TEST_MODE

    def fake_generate(key: str, reference: Path, trace_name: str, seed: int):
        with Image.open(reference) as opened:
            pixels = np.asarray(opened.convert("RGB")).astype(np.float32)
        sharp = pixels + (pixels - cv2.GaussianBlur(pixels, (0, 0), 0.8)) * 0.24
        generated = np.clip(sharp * 1.025 + 4.0, 0, 255).astype(np.uint8)
        return Image.fromarray(generated, "RGB"), {
            "mode": "offline_nano_banana_pro_stub",
            "cost": 0,
            "native_aspect_request": {"provider_parameter_sent": True},
        }

    try:
        app.TARGET = (1280, 720)
        app.OFFLINE_SELF_TEST_MODE = True
        app.generate_image = fake_generate
        first = app.process(
            source_path,
            key="offline-test-key",
            force_generation=True,
            force_processing=True,
            allow_generation=True,
        )
    finally:
        app.TARGET = old_target
        app.generate_image = old_generate
        app.OFFLINE_SELF_TEST_MODE = old_offline

    assert first["status"] == "approved"
    raw = Path(first["donor"])
    final = Path(first["output"])
    report_path = Path(first["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert raw.read_bytes() == final.read_bytes()
    assert report["final_master_policy"]["source_is_pixel_master"] is False
    assert report["final_master_policy"]["verified_donor_is_final"] is True
    assert report["validation"]["checks"]["whole_scene_gate_passed"]
    assert report["validation"]["checks"]["raw_final_byte_identity"]
    assert report["validation"]["checks"]["no_resize"]
    assert source_path.read_bytes() == source_bytes
    assert source_path.stat().st_mtime_ns == source_mtime

    state = app._new_sequential_state()
    state["active"] = {
        "source": str(source_path),
        "source_relative": source_path.name,
        "source_sha256": app.file_sha256(source_path),
        "status": "AWAITING_FINAL_APPROVAL",
        "final": str(final),
        "final_sha256": app.file_sha256(final),
        "report": str(report_path),
    }
    app.save_sequential_state(state)
    approved_record = app.approve_current_final()
    assert approved_record["final_sha256"] == app.file_sha256(final)

    reference_path = Path(report["artifacts"]["source_reference"])
    legacy_raw = raw.with_name(raw.name.replace(app.PIPELINE_TAG, "V868_FAITHFUL_DONOR_DETAIL_CARRIER"))
    legacy_reference = reference_path.with_name(
        reference_path.name.replace(app.PIPELINE_TAG, "V868_FAITHFUL_DONOR_DETAIL_CARRIER")
    )
    raw.replace(legacy_raw)
    reference_path.replace(legacy_reference)
    final.unlink()
    report_path.unlink()

    def must_not_generate(*args, **kwargs):
        raise AssertionError("V8.6.8 eligible FAITHFUL DONOR recovery attempted a new API request")

    old_generate = app.generate_image
    old_target = app.TARGET
    try:
        app.TARGET = (1280, 720)
        app.generate_image = must_not_generate
        recovered = app.process(source_path, force_processing=True, allow_generation=False)
    finally:
        app.TARGET = old_target
        app.generate_image = old_generate
    assert recovered["checkpoint_reused"] is True
    assert Path(recovered["output"]).read_bytes() == legacy_raw.read_bytes()
    return {
        "source_immutable": True,
        "accepted_raw_is_byte_identical_final": True,
        "existing_v868_faithful_donor_reused": True,
        "verified_donor_final_approval": True,
        "additional_api_requests": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct-only", action="store_true")
    args = parser.parse_args()
    direct = direct_transfer_test()
    if args.direct_only:
        print("V8.6.9 FAITHFUL DONOR DETAIL CARRIER SELF-TEST PASSED")
        print(json.dumps(direct, ensure_ascii=False, indent=2))
        return 0
    with tempfile.TemporaryDirectory(prefix="mg_v868_detail_carrier_") as temp_name:
        pipeline = pipeline_and_v868_recovery_test(Path(temp_name))
    print("V8.7.4 VERIFIED DONOR FINAL MASTER PIPELINE SELF-TEST PASSED")
    print(json.dumps({**direct, **pipeline}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
