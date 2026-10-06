from __future__ import annotations

import importlib.util
import json
from pathlib import Path


EXPECTED_VERSION = "8.8.1"
EXPECTED_PIPELINE_TAG = "V874_CANVAS_INTEGRITY_GATE"
EXPECTED_RELEASE_TAG = "V881_SCENE_STABILITY_PROFILE"
EXPECTED_MODEL = "google/gemini-3-pro-image"


def main() -> int:
    root = Path(__file__).resolve().parent
    try:
        version = json.loads((root / "VERSION.json").read_text(encoding="utf-8"))
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        pipeline = (root / "v8_safe_appearance.py").read_text(encoding="utf-8")
        safe = (root / "safe_local_upscale.py").read_text(encoding="utf-8")
        server = (root / "web_review_server.py").read_text(encoding="utf-8")
        bootstrap = (root / "web_console_bootstrap.py").read_text(encoding="utf-8")
        launcher = (root / "00_START_MG_WEB_REVIEW.bat").read_text(encoding="ascii")
        spec = importlib.util.spec_from_file_location("mg_v879_bootstrap_guard", root / "web_console_bootstrap.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("bootstrap module cannot be loaded")
        bootstrap_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap_module)
        bootstrap_command = bootstrap_module.server_command(8742)
    except Exception as exc:
        print(f"ERROR: build identity files cannot be read: {exc}")
        return 20
    checks = {
        "version": version.get("version") == EXPECTED_VERSION,
        "pipeline_tag_compatible": version.get("pipeline_tag") == EXPECTED_PIPELINE_TAG,
        "application_release_tag": version.get("application_release_tag") == EXPECTED_RELEASE_TAG,
        "pipeline_source_tag": f'PIPELINE_TAG = "{EXPECTED_PIPELINE_TAG}"' in pipeline,
        "nano_banana_pro": config.get("model") == EXPECTED_MODEL,
        "generative_master_enabled": config.get("verified_donor_is_final_master") is True,
        "generative_no_source_blend": "atomic_copy_verified_master(raw_path, final_path)" in pipeline,
        "strict_whole_scene_gate": "whole_scene_quality_gate(reference, donor)" in pipeline,
        "material_identity_gate": "no_material_texture_substitution" in pipeline,
        "safe_api_zero": '"api_request_count": 0' in safe,
        "safe_no_generator": '"generator_used": False' in safe,
        "separate_safe_output": "4K_SAFE_LOCAL_UPSCALE" in safe,
        "separate_generative_output": "4K_GENERATIVE_FINAL" in pipeline,
        "separate_state": "SEQUENTIAL_GENERATIVE_REVIEW_STATE.json" in pipeline,
        "web_console": (root / "web_review_server.py").is_file(),
        "self_healing_bootstrap": (root / "web_console_bootstrap.py").is_file(),
        "launcher_uses_bootstrap": "web_console_bootstrap.py" in launcher,
        "bootstrap_version": 'APP_VERSION = "8.8.1"' in bootstrap,
        "server_version": 'APP_VERSION = "8.8.1"' in server,
        "inspection_build_server": 'BUILD_ID = "r4"' in server,
        "inspection_build_bootstrap": 'BUILD_ID = "r4"' in bootstrap,
        "failure_reason_catalog": (root / "quality_gate_reason_catalog.py").is_file(),
        "inspection_self_test": (root / "viewer_inspection_self_test.py").is_file(),
        "inspection_viewer_ui": 'id="viewer-canvas"' in (root / "web" / "index.html").read_text(encoding="utf-8") and "buildDiff" in (root / "web" / "app.js").read_text(encoding="utf-8"),
        "health_before_browser": "is_current_server" in bootstrap and "open_local_url" in bootstrap,
        "fallback_port_range": "tuple(range(8743, 8753))" in bootstrap,
        "bootstrap_no_generation": "--force-generation" not in bootstrap_command,
        "responsive_assets": all((root / "web" / name).is_file() for name in ("index.html", "app.css", "app.js", "favicon.svg")),
        "loopback_only": 'host="127.0.0.1"' in server,
        "paid_confirmation": "paid_confirmed" in server,
        "normal_process_never_generates": 'return base + ["--limit", "1"]' in server,
        "source_sky_recovery": "def recover_original_source_sky(" in pipeline,
        "source_sky_recovery_test": (root / "source_sky_recovery_self_test.py").is_file(),
        "canvas_integrity_gate": "def canvas_integrity_gate(" in pipeline,
        "frame_within_frame_blocked": "no_paired_inset_side_seams" in pipeline,
        "exact_signage_lock": "EXACT TEXT AND SIGNAGE LOCK" in pipeline,
        "single_launcher": (root / "00_START_MG_WEB_REVIEW.bat").is_file(),
        "manual_approval": config.get("auto_approve_strict_verified_master") is False,
        "source_queue_policy": (root / "source_queue_policy.py").is_file(),
        "source_queue_isolation_test": (root / "source_queue_isolation_self_test.py").is_file(),
        "folder_picker_endpoint": "/api/source-folder/select" in server,
        "folder_picker_ui": 'id="select-source-folder"' in (root / "web" / "index.html").read_text(encoding="utf-8"),
        "folder_picker_test": (root / "source_folder_picker_self_test.py").is_file(),
        "sibling_donor_test": (root / "sibling_donor_recovery_self_test.py").is_file(),
        "sibling_donor_lookup": 'SOURCE.name.casefold() == "base"' in pipeline and '"searched_raw_roots": search_roots' in pipeline,
        "report": (root / "PROJECT_REPORT_V881.md").is_file(),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        print("ERROR: Wrong or incomplete V8.8.1 build:", ", ".join(failed))
        return 21
    print(f"VERSION GUARD PASSED: V{EXPECTED_VERSION} | self-healing web bootstrap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
