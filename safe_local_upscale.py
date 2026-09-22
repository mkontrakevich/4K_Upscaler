from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from source_queue_policy import discover_source_files, is_explicit_service_artifact


ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SOURCE = Path(CFG["source"])
OUTPUT_ROOT = SOURCE / CFG["output_folder"]
FINAL_ROOT = OUTPUT_ROOT / "4K_SAFE_LOCAL_UPSCALE"
REPORT_ROOT = OUTPUT_ROOT / "07_REPORTS_SAFE_LOCAL"
STATE_PATH = OUTPUT_ROOT / "_diagnostics" / "SAFE_LOCAL_UPSCALE_QUEUE_STATE.json"
SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
TAG = "V874_SAFE_LOCAL_WEB_REVIEW"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def atomic_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    image.save(temporary, "PNG", optimize=True)
    temporary.replace(path)


def relative(path: Path) -> Path:
    return path.resolve(strict=False).relative_to(SOURCE.resolve(strict=False))


def natural_key(path: Path) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", relative(path).as_posix())
    )


def discover() -> list[Path]:
    return discover_source_files(SOURCE, CFG["output_folder"])


def target_size(image: Image.Image) -> tuple[int, int]:
    width, height = image.size
    ratio = width / height
    if width >= height:
        return int(CFG["target_width"]), max(1, round(int(CFG["target_width"]) / ratio))
    return max(1, round(int(CFG["target_height"]) * ratio)), int(CFG["target_height"])


def source_only_upscale(image: Image.Image) -> Image.Image:
    source = ImageOps.exif_transpose(image).convert("RGB")
    # Deliberately deterministic and non-generative. LANCZOS is the complete
    # pixel pipeline: no model, API, donor, hallucinated texture or relighting.
    return source.resize(target_size(source), Image.Resampling.LANCZOS)


def output_paths(source: Path, digest: str) -> tuple[Path, Path]:
    parent = relative(source).parent
    stem = f"{source.stem}_{digest[:16]}_{TAG}"
    return (
        FINAL_ROOT / parent / f"{stem}_FINAL.png",
        REPORT_ROOT / parent / f"{stem}_REPORT.json",
    )


def process_one(source: Path) -> dict[str, Any]:
    before = source.stat()
    source_hash = sha256(source)
    final_path, report_path = output_paths(source, source_hash)
    if final_path.is_file() and report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("source_sha256") == source_hash and report.get("status") == "approved":
            return {"source": str(source), "status": "skipped", "output": str(final_path)}
    with Image.open(source) as opened:
        original_size = list(ImageOps.exif_transpose(opened).size)
        result = source_only_upscale(opened)
    atomic_png(result, final_path)
    after_hash = sha256(source)
    after = source.stat()
    immutable = (
        source_hash == after_hash
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )
    if not immutable:
        final_path.unlink(missing_ok=True)
        raise RuntimeError(f"Source immutability violation: {source}")
    report = {
        "pipeline": TAG,
        "mode": "SAFE_LOCAL_UPSCALE_NO_GENERATION",
        "created_at_utc": utc_now(),
        "source": str(source),
        "source_sha256": source_hash,
        "original_size": original_size,
        "output_size": list(result.size),
        "api_request_count": 0,
        "generator_used": False,
        "donor_used": False,
        "pixel_method": "Pillow LANCZOS source-only resize",
        "camera_geometry_background_material_identity": "pixel-identical source content",
        "source_immutable": True,
        "output": str(final_path),
        "status": "ready_for_review",
    }
    atomic_json(report_path, report)
    return {"source": str(source), "status": "ready_for_review", "output": str(final_path), "report": str(report_path)}


def load_state() -> dict[str, Any]:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    state["pipeline"] = TAG
    state["mode"] = "SAFE_LOCAL_UPSCALE_NO_GENERATION"
    state["updated_at_utc"] = utc_now()
    atomic_json(STATE_PATH, state)


def verified_pending(state: dict[str, Any]) -> dict[str, Any] | None:
    pending = state.get("pending_review")
    if not isinstance(pending, dict):
        return None
    source = Path(str(pending.get("source", "")))
    output = Path(str(pending.get("output", "")))
    if not source.is_file() or not output.is_file():
        return None
    if sha256(source) != pending.get("source_sha256"):
        return None
    if sha256(output) != pending.get("output_sha256"):
        return None
    return pending


def approve_pending() -> int:
    state = load_state()
    pending = verified_pending(state)
    if pending is None:
        print("SAFE APPROVAL BLOCKED | no unchanged result is awaiting review")
        return 2
    key = str(pending["source_relative"])
    state.setdefault("completed", {})[key] = {
        "source_sha256": pending["source_sha256"],
        "output": pending["output"],
        "output_sha256": pending["output_sha256"],
        "approved_at_utc": utc_now(),
        "approval": "EXPLICIT_USER_CONFIRMATION",
    }
    report = Path(str(pending.get("report", "")))
    if report.is_file():
        payload = json.loads(report.read_text(encoding="utf-8"))
        payload["status"] = "approved"
        payload["operator_final_approval"] = {
            "required": True, "status": "APPROVED_BY_USER", "approved_at_utc": utc_now()
        }
        atomic_json(report, payload)
    state["pending_review"] = None
    state["active_source"] = None
    state["status"] = "APPROVED_NEXT_IMAGE_UNLOCKED"
    save_state(state)
    print(f"SAFE FINAL APPROVED | {pending['source']} | next image unlocked")
    return 0


def reject_pending() -> int:
    state = load_state()
    pending = verified_pending(state)
    if pending is None:
        print("SAFE REJECTION BLOCKED | no unchanged result is awaiting review")
        return 2
    pending["status"] = "REJECTED_BY_USER_SAME_IMAGE_ACTIVE"
    pending["rejected_at_utc"] = utc_now()
    state["pending_review"] = pending
    state["status"] = "REJECTED_BY_USER_SAME_IMAGE_ACTIVE"
    save_state(state)
    print(f"SAFE FINAL REJECTED | {pending['source']} | next image remains blocked")
    return 4


def run_queue(limit: int = 0) -> int:
    files = discover()
    state = load_state()
    stale_source = Path(str(state.get("active_source", "")))
    if is_explicit_service_artifact(stale_source, SOURCE, CFG["output_folder"]):
        state.setdefault("excluded_service_source_recoveries", []).append({
            "source": str(stale_source),
            "recovered_at_utc": utc_now(),
            "reason": "V8.7.6 excluded an application or UI asset from the user image queue",
        })
        state["active_source"] = None
        state["pending_review"] = None
        state["status"] = "SERVICE_SOURCE_EXCLUDED_QUEUE_RESUMED"
        save_state(state)
    completed = state.setdefault("completed", {})
    pending = verified_pending(state)
    if pending is not None:
        state["status"] = "AWAITING_USER_APPROVAL"
        save_state(state)
        print(f"SAFE FINAL WAITING FOR APPROVAL | {pending['output']}")
        return 4
    if state.get("pending_review"):
        state["pending_review"] = None
    processed = 0
    for source in files:
        digest = sha256(source)
        key = relative(source).as_posix()
        if completed.get(key, {}).get("source_sha256") == digest:
            continue
        state.update({
            "pipeline": TAG,
            "mode": "SAFE_LOCAL_UPSCALE_NO_GENERATION",
            "active_source": str(source),
            "active_source_sha256": digest,
            "status": "PROCESSING",
            "updated_at_utc": utc_now(),
        })
        atomic_json(STATE_PATH, state)
        try:
            result = process_one(source)
        except Exception as exc:
            state.update({"status": "PAUSED_ON_UNFINISHED_IMAGE", "error": str(exc), "updated_at_utc": utc_now()})
            atomic_json(STATE_PATH, state)
            print(f"SAFE UPSCALE PAUSED: {source}: {exc}")
            print("Run the same SAFE launcher again; it will resume this image first.")
            return 1
        state["pending_review"] = {
            "source": str(source),
            "source_relative": key,
            "source_sha256": digest,
            "output": result["output"],
            "output_sha256": sha256(Path(result["output"])),
            "report": result.get("report"),
            "status": "AWAITING_USER_APPROVAL",
            "created_at_utc": utc_now(),
        }
        state["status"] = "AWAITING_USER_APPROVAL"
        save_state(state)
        processed += 1
        print(f"SAFE FINAL READY FOR APPROVAL | {result['output']}")
        return 4
    state.update({"active_source": None, "status": "ALL_IMAGES_COMPLETED", "completed_at_utc": utc_now()})
    atomic_json(STATE_PATH, state)
    print("SAFE LOCAL UPSCALE COMPLETE | API requests: 0")
    return 0


def self_test() -> int:
    source = np.zeros((117, 203, 3), dtype=np.uint8)
    source[:, :, 0] = np.arange(203, dtype=np.uint8)[None, :]
    image = Image.fromarray(source, "RGB")
    result = source_only_upscale(image)
    expected = target_size(image)
    if result.size != expected or result.width / result.height <= 1.70:
        print("SAFE LOCAL UPSCALE SELF-TEST FAILED")
        return 10
    print(json.dumps({
        "status": "passed",
        "mode": "SAFE_LOCAL_UPSCALE_NO_GENERATION",
        "api_requests": 0,
        "generator_used": False,
        "source_aspect_preserved": True,
        "output_size": list(result.size),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MG V8.7.4 safe local web review")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--approve", action="store_true")
    actions.add_argument("--reject", action="store_true")
    actions.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.approve:
        return approve_pending()
    if args.reject:
        return reject_pending()
    if args.status:
        print(json.dumps(load_state(), ensure_ascii=False, indent=2))
        return 0
    return run_queue(max(0, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
