from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

# This migration test exercises only JSON state handling. Lightweight stubs
# keep it independent from native OpenCV/NumPy loading; the image tests in the
# installer load the real Windows wheels before this test runs.
sys.modules.setdefault("cv2", types.ModuleType("cv2"))
sys.modules.setdefault("numpy", types.ModuleType("numpy"))
sys.modules.setdefault("requests", types.ModuleType("requests"))

import v8_safe_appearance as app


def main() -> int:
    original_diag = app.DIAG_FOLDER
    try:
        with tempfile.TemporaryDirectory(prefix="mg_v874_retry_budget_") as temporary:
            app.DIAG_FOLDER = Path(temporary) / "_diagnostics"
            state_path = app.sequential_state_path()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({
                "schema_version": 1,
                "pipeline": "V867_SOURCE_FAITHFUL_UPSCALER",
                "mode": "ONE_IMAGE_THEN_EXPLICIT_FINAL_APPROVAL",
                "batch_processing_enabled": False,
                "approved": [],
                "rejected": [],
                "active": {
                    "source": "C:\\fixture\\mg (4).jpeg",
                    "source_relative": "base/mg (4).jpeg",
                    "source_sha256": "07d82716aa274902" + "0" * 48,
                    "status": "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
                    "automatic_donor_generation_attempts": 3,
                    "replacement_donor_generation_attempts": 2,
                    "technical_decision": "REJECTED_WHOLE_SCENE",
                },
            }), encoding="utf-8")

            migrated = app.load_sequential_state()
            active = migrated["active"]
            assert migrated["pipeline"] == app.PIPELINE_TAG
            assert active["source"].endswith("mg (4).jpeg")
            assert active["status"] == "AWAITING_EXPLICIT_DONOR_GENERATION"
            assert active["v869_automatic_donor_generation_attempts"] == 0
            assert active["v869_retry_budget_migration"]["legacy_automatic_attempts"] == 3
            assert active["v869_retry_budget_migration"]["legacy_replacement_attempts"] == 2
            assert int(app.CFG["max_automatic_donor_generations_per_source"]) == 3

            app.save_sequential_state(migrated)
            reloaded = app.load_sequential_state()
            assert reloaded["active"]["v869_automatic_donor_generation_attempts"] == 0
            assert reloaded["active"]["v869_retry_budget_migration"] == active["v869_retry_budget_migration"]

            source = Path(temporary) / "mg (34).jpeg"
            donor = Path(temporary) / "mg (34)_V868_FAITHFUL_DONOR_DETAIL_CARRIER_NANO_BANANA_PRO_APPEARANCE_DONOR.png"
            held_final = Path(temporary) / "held_final.png"
            source.write_bytes(b"source-fixture")
            donor.write_bytes(b"eligible-donor-fixture")
            held_final.write_bytes(b"held-final-fixture")
            state_path.write_text(json.dumps({
                "schema_version": 1,
                "pipeline": "V868_FAITHFUL_DONOR_DETAIL_CARRIER",
                "mode": "ONE_IMAGE_THEN_EXPLICIT_FINAL_APPROVAL",
                "batch_processing_enabled": False,
                "approved": [],
                "rejected": [],
                "active": {
                    "source": str(source),
                    "source_relative": source.name,
                    "source_sha256": "4482bb823b0a09e9" + "0" * 48,
                    "status": "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
                    "donor": str(donor),
                    "final": str(held_final),
                    "technical_decision": "REVIEW",
                    "v868_automatic_donor_generation_attempts": 3,
                },
            }), encoding="utf-8")
            recovered_v868 = app.load_sequential_state()
            recovered_active = recovered_v868["active"]
            assert recovered_active["source"] == str(source)
            assert recovered_active["donor"] == str(donor)
            assert recovered_active["status"] == "AWAITING_RAW_REVIEW"
            assert "final" not in recovered_active
            assert recovered_active["v869_automatic_donor_generation_attempts"] == 0
            assert recovered_active["v869_retry_budget_migration"]["legacy_automatic_attempts"] == 3

        print("V8.7.4 VERSIONED RETRY BUDGET MIGRATION SELF-TEST PASSED")
        print(json.dumps({
            "unfinished_mg_4_preserved": True,
            "legacy_v867_attempts_archived": True,
            "fresh_v869_paid_attempt_allowance": 3,
            "migration_is_idempotent": True,
            "unfinished_mg_34_preserved": True,
            "existing_v868_donor_preserved": True,
            "held_v868_final_invalidated": True,
            "real_api_requests": 0,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        app.DIAG_FOLDER = original_diag


if __name__ == "__main__":
    raise SystemExit(main())
