from __future__ import annotations

import json
from pathlib import Path

from quality_gate_reason_catalog import describe_failed_checks, defect_overlays


def main() -> int:
    root = Path(__file__).resolve().parent
    index = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "app.js").read_text(encoding="utf-8")
    css = (root / "web" / "app.css").read_text(encoding="utf-8")
    server = (root / "web_review_server.py").read_text(encoding="utf-8")
    sample = [
        "canvas_integrity.perimeter_low_frequency_continuity",
        "canvas_integrity.no_unsupported_long_border_seam",
        "artifact_control.corrupt_grid_cells",
        "unknown_group.future_check",
    ]
    detailed = describe_failed_checks(sample)
    overlays = defect_overlays(sample)
    checks = {
        "fullscreen_inspection_canvas": 'id="viewer-canvas"' in index,
        "split_mode": 'data-view-mode="split"' in index and 'inspection.mode === "split"' in js,
        "diff_mode": 'data-view-mode="diff"' in index and "buildDiff" in js,
        "blink_mode": 'data-view-mode="blink"' in index and "startBlink" in js,
        "zoom_100": 'data-zoom="1">100%' in index and 'setZoomPreset("1")' in js,
        "zoom_200": 'data-zoom="2">200%' in index,
        "pan_pointer_events": "pointerdown" in js and "pointermove" in js,
        "sync_pan": 'id="viewer-sync"' in index and "inspection.sync" in js,
        "rejection_reason_ui": 'id="failure-details"' in index and "renderFailureDetails" in js,
        "defect_overlay": 'id="viewer-defects"' in index and "drawDefectOverlays" in js,
        "reason_catalog_known": len(detailed) == 4 and detailed[0]["title"].startswith("Нарушение целостности"),
        "reason_catalog_fallback": detailed[-1]["code"] == "unknown_group.future_check" and detailed[-1]["group"] == "UNKNOWN_GROUP",
        "border_overlay_available": any(item.get("type") == "border" for item in overlays),
        "server_exposes_detailed_reasons": '"failed_checks_detailed": describe_failed_checks' in server,
        "server_exposes_overlays": '"defect_overlays": defect_overlays' in server,
        "viewer_capabilities": '"viewer_capabilities"' in server,
        "inspection_css": ".inspection-viewer" in css and ".failure-details" in css,
        "real_api_requests": 0,
    }
    failed = [name for name, passed in checks.items() if name != "real_api_requests" and passed is not True]
    print(json.dumps({"status": "passed" if not failed else "failed", **checks}, ensure_ascii=False, indent=2))
    return 0 if not failed else 20


if __name__ == "__main__":
    raise SystemExit(main())

# R4 localized defect viewer contract
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
assert "analyzeLocalizedDefects" in APP_JS
assert "focusFirstLocalizedDefect" in APP_JS
assert "ОБЛАСТЬ ПРОВЕРКИ" in APP_JS
assert "browser_localizer" in APP_JS
