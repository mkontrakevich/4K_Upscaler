from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import io
import json
import logging
import math
import os
import random
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests
from PIL import Image, ImageOps

from source_queue_policy import discover_source_files, recover_service_active_state


ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SOURCE = Path(os.environ.get("MG4K_SOURCE", str(CFG["source"])))
OUTPUT = SOURCE / CFG["output_folder"]
REFERENCE_FOLDER = OUTPUT / "00_SOURCE_REFERENCE"
CONTROL_FOLDER = OUTPUT / "01_STRUCTURE_CONTROL"
RAW_FOLDER = OUTPUT / "02_NANO_BANANA_PRO_RAW"
ALIGNED_FOLDER = OUTPUT / "03_REGISTERED_DONOR"
MASK_FOLDER = OUTPUT / "04_SEMANTIC_TRANSFER_MASKS"
TRANSFER_FOLDER = OUTPUT / "05_STRUCTURE_APPEARANCE_SEPARATION"
AUDIT_FOLDER = OUTPUT / "06_AUDIT"
REPORT_FOLDER = OUTPUT / "07_REPORTS"
FINAL_FOLDER = OUTPUT / "4K_GENERATIVE_FINAL"
REVIEW_FOLDER = OUTPUT / "GENERATIVE_REVIEW_REQUIRED"
DIAG_FOLDER = OUTPUT / "_diagnostics"
TRACE_FOLDER = DIAG_FOLDER / "api_traces"
STATE_FOLDER = DIAG_FOLDER / "states"
KEY_FILE = Path(CFG["key_file"])
TARGET = (int(CFG["target_width"]), int(CFG["target_height"]))
SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
PIPELINE_TAG = "V874_CANVAS_INTEGRITY_GATE"
OFFLINE_SELF_TEST_MODE = False
EXIT_WAITING_FOR_FINAL_APPROVAL = 4
EXIT_WAITING_FOR_DONOR = 5
EXIT_ALL_IMAGES_APPROVED = 6
EXIT_REPLACEMENT_DONOR_APPROVAL_REQUIRED = 7

# Nano Banana Pro/OpenRouter accepts discrete canvas ratios. The closest supported
# ratio is selected from the immutable source dimensions and sent in the paid
# request itself; prompt wording alone is not a framing control.
GENERATION_ASPECT_RATIOS: tuple[tuple[str, float], ...] = (
    ("1:1", 1.0),
    ("4:3", 4.0 / 3.0),
    ("3:4", 3.0 / 4.0),
    ("3:2", 3.0 / 2.0),
    ("2:3", 2.0 / 3.0),
    ("16:9", 16.0 / 9.0),
    ("9:16", 9.0 / 16.0),
    ("21:9", 21.0 / 9.0),
)


def active_cloud_lock_profile() -> dict[str, str]:
    """Optional per-job lock profile supplied by the Cloudflare intake bridge."""
    defaults = {
        "camera": "hard",
        "geometry": "hard",
        "architecture": "hard",
        "textures": "hard",
        "vegetation": "hard",
        "people_vehicles": "hard",
        "lighting": "soft",
        "sky": "soft",
        "water": "soft",
    }
    raw = os.environ.get("MG4K_LOCK_PROFILE_JSON", "").strip()
    if not raw:
        return defaults
    try:
        incoming = json.loads(raw)
    except Exception:
        logging.warning("LOCK PROFILE | Invalid MG4K_LOCK_PROFILE_JSON; canonical defaults retained.")
        return defaults
    for key in defaults:
        value = str(incoming.get(key, defaults[key])).lower()
        defaults[key] = value if value in {"hard", "soft", "free"} else defaults[key]
    return defaults


def cloud_lock_prompt() -> str:
    """Build the authoritative per-job DONOR instructions from the UI Lock Profile."""
    profile = active_cloud_lock_profile()
    labels = {
        "camera": "CAMERA",
        "geometry": "GEOMETRY",
        "architecture": "ARCHITECTURE",
        "textures": "TEXTURES / MATERIAL IDENTITY",
        "vegetation": "VEGETATION",
        "people_vehicles": "PEOPLE / VEHICLES / SMALL OBJECTS",
        "lighting": "LIGHTING",
        "sky": "SKY / CLOUDS",
        "water": "WATER / REFLECTIONS / SHADOWS",
    }
    rules: dict[str, dict[str, str]] = {
        "camera": {
            "hard": (
                "HARD_LOCK. Preserve exact viewpoint, camera height, azimuth, tilt, roll, focal length, "
                "field of view, perspective, crop, horizon and normalized landmark coordinates. No reframing."
            ),
            "soft": (
                "SOFT_LOCK. Preserve the recognizable viewpoint, composition, perspective family and subject scale. "
                "Only subtle optical/framing correction is allowed; do not choose a new viewpoint."
            ),
            "free": (
                "FREE. Controlled reframing or perspective refinement is allowed when it materially improves image quality, "
                "but the same scene and primary subject must remain unmistakably continuous with the source."
            ),
        },
        "geometry": {
            "hard": (
                "HARD_LOCK. Preserve exact contours, massing, proportions, structural edges, terrain boundaries "
                "and spatial relationships. Enhancement only."
            ),
            "soft": (
                "SOFT_LOCK. Preserve the same massing and spatial logic; allow only minor cleanup of ambiguous local edges "
                "that does not change proportions or object placement."
            ),
            "free": (
                "FREE. Local geometric reconstruction may resolve unclear source detail, but must not transform the scene "
                "into a different building, landscape or spatial arrangement."
            ),
        },
        "architecture": {
            "hard": (
                "HARD_LOCK. Preserve all architectural elements, facade axes, floor count, openings, rooflines, "
                "window rhythm, balconies, railings and design language exactly. No redesign, additions or deletions."
            ),
            "soft": (
                "SOFT_LOCK. Preserve architectural identity and all major elements; minor cleanup of unresolved small details "
                "is allowed without changing facade logic or design language."
            ),
            "free": (
                "FREE. Architectural micro-detail may be interpreted where the source is ambiguous, but the building must "
                "remain the same recognizable project with the same major massing and function."
            ),
        },
        "textures": {
            "hard": (
                "HARD_LOCK. Preserve exact material category, colour family, finish, joint logic, texture orientation and scale. "
                "Recover existing micro-detail only; never substitute or invent a new material pattern."
            ),
            "soft": (
                "SOFT_LOCK. Preserve material identity and colour family while allowing subtle physically plausible refinement "
                "of roughness, pores and fine texture."
            ),
            "free": (
                "FREE. Surface appearance may be refined more actively for realism, but do not create implausible or unrelated "
                "materials and do not obscure architectural structure."
            ),
        },
        "vegetation": {
            "hard": (
                "HARD_LOCK. Preserve count, position, crown silhouette, scale, volume, density and vegetation character exactly."
            ),
            "soft": (
                "SOFT_LOCK. Preserve placement, type and overall crown mass; allow natural local leaf/branch refinement."
            ),
            "free": (
                "FREE. Vegetation detail, density and local shape may be improved for realism while preserving scene coherence "
                "and avoiding unrelated new landscaping."
            ),
        },
        "people_vehicles": {
            "hard": (
                "HARD_LOCK. Preserve count, position, scale, orientation, silhouette and identity of people, vehicles "
                "and small objects. Do not add, remove, move or replace them."
            ),
            "soft": (
                "SOFT_LOCK. Preserve count and placement; allow local detail cleanup while keeping identity and orientation."
            ),
            "free": (
                "FREE. Small people, vehicles and secondary objects may be plausibly reconstructed where unclear, provided "
                "they do not alter the scene narrative or obstruct locked architecture."
            ),
        },
        "lighting": {
            "hard": (
                "HARD_LOCK. Preserve exact time-of-day reading, exposure relationship, main light direction, shadow logic, "
                "contrast hierarchy and scene mood."
            ),
            "soft": (
                "SOFT_LOCK. Preserve time of day, main light direction and mood; allow moderate local exposure, contrast "
                "and shadow-quality refinement."
            ),
            "free": (
                "FREE. Lighting may be enhanced more substantially for photographic quality, while maintaining physically "
                "coherent illumination and avoiding fantasy or unrelated relighting."
            ),
        },
        "sky": {
            "hard": (
                "HARD_LOCK. Preserve cloud distribution, silhouettes, weather, atmosphere, horizon tone and sky structure exactly."
            ),
            "soft": (
                "SOFT_LOCK. Preserve overall cloud mass balance, weather and atmosphere; minor local cloud-shape refinement is allowed."
            ),
            "free": (
                "FREE. Sky detail and cloud structure may vary for quality and realism, but weather and lighting must remain "
                "physically coherent with the scene."
            ),
        },
        "water": {
            "hard": (
                "HARD_LOCK. Preserve water boundaries, reflection placement, shadow geometry, direction, scale and causal logic exactly."
            ),
            "soft": (
                "SOFT_LOCK. Preserve location and global causal logic; allow local water microstructure, reflection texture "
                "and shadow softness refinement."
            ),
            "free": (
                "FREE. Water, reflection and shadow microstructure may be interpreted more freely for realism, while remaining "
                "physically consistent with visible objects and lighting."
            ),
        },
    }

    lines = [
        "",
        "ACTIVE USER LOCK PROFILE — HIGHEST-PRIORITY PER-PARAMETER POLICY FOR THIS DONOR REQUEST.",
        "These levels are supplied by the user interface for this exact job. They supersede any generic default lock labels.",
    ]
    for key, label in labels.items():
        level = profile[key]
        lines.append(f"- {label} [{level.upper()}]: {rules[key][level]}")
    lines.extend([
        "",
        "GLOBAL SAFETY BOUNDARY:",
        "- Return one coherent photographic scene, never a collage, frame-within-frame, inset, poster, screen or duplicated canvas.",
        "- Preserve existing text, logos and signage; never invent readable wording that is absent or illegible in SOURCE.",
        "- Do not introduce corruption, duplicated objects, melted forms, blank zones, border seams, fake UI or watermarks.",
        "- A FREE parameter grants freedom only to that parameter. It never silently unlocks the other parameters.",
    ])
    return "\n".join(lines)


CLOUD_GENERATION_PROMPT = """NANO BANANA PRO — SOURCE-FAITHFUL 4K DONOR RECONSTRUCTION.

Create one high-resolution photographic DONOR from the supplied SOURCE. The objective is to recover clarity, optical fidelity,
surface readability and high-frequency detail while respecting the ACTIVE USER LOCK PROFILE appended below.

The ACTIVE USER LOCK PROFILE is the sole authority for HARD / SOFT / FREE behaviour of each listed visual parameter.
Do not infer a stricter or looser lock level from generic restoration language. Apply each parameter independently.

The source image remains the identity reference for the scene. Even when one parameter is FREE, do not turn the task into an
unrelated redesign, a different project, a different location or a new narrative. Locked parameters must remain independent
and must not drift merely because another parameter is allowed to vary.

EXACT TEXT AND SIGNAGE SAFETY. Preserve existing letters, numerals, logos, road markings and symbols in position and silhouette.
If text is too small to resolve reliably, preserve its visual shape instead of guessing or rewriting it.

EDGE-TO-EDGE CANVAS INTEGRITY. Return one complete coherent image. Never create picture-in-picture, inset plates, inner frames,
side wedges, reflected margins, duplicated edge strips, outpainted surrounds, blank borders, fake screens or poster-like layouts.

QUALITY TARGET. Natural photographic restoration, believable material response, clean fine detail and stable local continuity.
No plastic CGI look, fantasy styling, watermark, excessive sharpening, synthetic halos, melted forms or unfinished regions.
"""


def donor_generation_prompt() -> str:
    """Preserve the legacy local R4 prompt; use dynamic Lock Profile only for cloud jobs."""
    if os.environ.get("MG4K_LOCK_PROFILE_JSON", "").strip():
        return CLOUD_GENERATION_PROMPT + cloud_lock_prompt()
    return GENERATION_PROMPT


GENERATION_PROMPT = """NANO BANANA PRO — SOURCE-FAITHFUL DETAIL RECONSTRUCTION FOR UPSCALING.

ABSOLUTE CAMERA LOCK. Treat the input as a locked camera plate, not as a visual reference for a new composition.
Every source landmark must remain at the same normalized x/y coordinate: building corners, rooflines, horizon,
mountain silhouette, shoreline, piers, beach boundaries and frame edges. Keep the exact camera position, height,
azimuth, tilt, roll, focal length, field of view, crop, horizon and perspective. Do not zoom out, widen the lens,
recenter the resort, reveal more terrain or sea, relocate a building, or redesign the coastline. If an alternative
view would look more impressive, reject that alternative and preserve the source view exactly.

The supplied source is the only authority for camera, crop, perspective, geometry, architecture, object identity,
object count, facade axes, floors, windows, rooflines, terrain, shoreline, landscaping, furniture and infrastructure.
Reconstruct the exact same complete native-aspect frame at high resolution. This is an UPSCALER task, not a redesign,
new render, relighting or reinterpretation. The output must look like the same photograph with cleaner, better-resolved
versions of details already visible in the source. The source owns the background, terrain, vegetation, materials, texture orientation and colour as HARD-LOCK evidence.
Lighting, sky/clouds, water, reflections and shadows are SOFT-LOCK evidence: preserve their global structure, causal logic,
direction, scale, atmosphere and scene character. Minor local natural variation is allowed only where it does not alter
the identity or reading of the scene. Never replace any of them with a plausible alternative. When uncertain, reproduce
the simpler source evidence exactly.

Do not pan, tilt, roll, zoom, crop, reframe, change focal length, move a vanishing point, redesign architecture,
change materials, alter floor count, add or remove objects, move trees, change the beach layout or invent details.
Improve only the resolution and clarity of already existing content. Preserve the source daylight, atmosphere,
depth, material response, glazing, vegetation, ground, water, sky and tonal separation. Never change the main light direction, time of day, horizon background or the global layout/causal logic of shadows,
cloud masses and reflections. Minor local refinement of cloud shape, water microstructure, reflection texture and shadow
softness is permitted only inside SOFT_LOCK and must not alter the overall composition or atmosphere.

ABSOLUTE MATERIAL IDENTITY LOCK. Every source surface keeps its original material category, finish, colour family,
joint logic and texture scale. White smooth plaster must remain white smooth plaster. Painted render must remain
painted render; concrete remains concrete; stone remains the same stone; brick remains the same brick; wood,
metal, glass, roofing and paving remain their respective original materials. Never replace plaster with stone,
brick, concrete panels, tiles, wood, metal cladding or a decorative relief. Never invent masonry joints, panel
seams, veins, grain, blocks, tiles, grooves, rust, cracks, ornaments or facade patterns that are absent in the
source. Do not redesign a smooth surface into a visibly textured one. Improve only physically plausible
micro-roughness, pores and light response inherent to the exact existing finish; new detail must not create a new
material reading at normal viewing distance. When the source texture is subtle or ambiguous, preserve the simpler
source interpretation instead of inventing a richer material.

Every visible region matters equally. Preserve architectural rhythm, silhouettes, shoreline, landscape,
infrastructure, furniture, people and vehicles wherever they already exist. Do not hide weak or unresolved
areas with bloom, haze, darkness, blur, reflections, excessive contrast or decorative effects. No duplicated
objects, melted forms, broken edges, flat bands, seams, black voids, clipped highlights, synthetic color halos,
plastic foliage, mirror surfaces, fake detail, unfinished borders or inconsistent zones.

EXACT TEXT AND SIGNAGE LOCK. Preserve every existing letter, numeral, logo, sign, road marking and symbol in its
original position, size, spacing, line count and silhouette. Never rewrite, translate, correct, beautify, duplicate,
remove or invent text. If a character is too small to read, preserve its source pixel shape instead of guessing it.



CANONICAL SCENE STABILITY PROFILE — THIS POLICY IS MANDATORY FOR GENERATION.
HARD_LOCK: CAMERA; GEOMETRY; ARCHITECTURE; TEXTURES; VEGETATION; PEOPLE / VEHICLES / SMALL_OBJECTS.
For every HARD_LOCK class preserve exact position, scale, proportions, orientation, silhouette, perspective, count,
material identity, structural relationships and spatial layout. Do not add, remove, replace, move, redesign or reinterpret.

SOFT_LOCK: LIGHTING; SKY_CLOUDS; WATER / REFLECTIONS / SHADOWS.
SOFT_LOCK permits only minor local natural variation while preserving the original global distribution, direction, scale,
weather, time of day, scene mood and physical causality. SOFT_LOCK is not permission to redesign or beautify the scene.

DONOR POLICY: donor data may guide detail density, clarity and surface readability only. It must never overwrite SOURCE
camera, geometry, architecture, object layout, vegetation identity or texture pattern.

FINAL INTENT: the exact same source scene with higher optical fidelity and cleaner high-frequency detail, never a newly
generated interpretation.

EDGE-TO-EDGE CANVAS LOCK. The source must fill the output continuously. Never place the source photograph inside
another scene, frame, panel, poster, card, screen, window, tilted sheet or picture-in-picture composition. Never add
an inner rectangular border, side wedge, reflected margin, duplicated edge strip, outpainted surround or seam near
any canvas edge. Do not show the source as a layer. Return only the same photograph itself, edge to edge.

Neutral source-faithful photographic restoration, natural dynamic range and unchanged colour response. No artistic
restyling, cinematic reinterpretation, fantasy lighting, invented signage, watermark or plastic CGI. Return one
complete coherent image at the source aspect ratio. Never extend, pad, crop or squeeze the frame. Any supplied masks
or structure controls are quality-control references only and must never create blank or partially rendered areas.
"""


class ApiFailure(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ConfigurationFailure(RuntimeError):
    pass


class RejectedDonorAvailable(RuntimeError):
    def __init__(self, candidate: str | None, failed_checks: list[str], message: str):
        super().__init__(message)
        self.candidate = candidate
        self.failed_checks = failed_checks



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def setup_log() -> Path:
    DIAG_FOLDER.mkdir(parents=True, exist_ok=True)
    TRACE_FOLDER.mkdir(parents=True, exist_ok=True)
    STATE_FOLDER.mkdir(parents=True, exist_ok=True)
    path = DIAG_FOLDER / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
        force=True,
    )
    return path


def close_log_handlers() -> None:
    logger = logging.getLogger()
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.flush()
        except Exception:
            pass
        try:
            handler.close()
        except Exception:
            pass
    logging.shutdown()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def out_path(folder: Path, source: Path, sha256: str, suffix: str, extension: str = ".png") -> Path:
    try:
        relative_parent = source.relative_to(SOURCE).parent
    except ValueError:
        relative_parent = Path()
    path = folder / relative_parent / f"{source.stem}_{sha256[:16]}_{PIPELINE_TAG}_{suffix}{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def atomic_save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    image.save(temporary, "PNG", optimize=True)
    temporary.replace(path)


def atomic_copy_verified_master(source: Path, destination: Path) -> dict[str, Any]:
    """Publish a verified RAW as FINAL without changing a single encoded byte."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    shutil.copyfile(source, temporary)
    source_sha256 = file_sha256(source)
    copied_sha256 = file_sha256(temporary)
    if copied_sha256 != source_sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            "DONOR MASTER COPY FAILURE: temporary FINAL is not byte-identical to the verified RAW."
        )
    temporary.replace(destination)
    final_sha256 = file_sha256(destination)
    if final_sha256 != source_sha256:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            "DONOR MASTER COPY FAILURE: published FINAL is not byte-identical to the verified RAW."
        )
    return {
        "raw_sha256": source_sha256,
        "final_sha256": final_sha256,
        "byte_identical": True,
        "raw_bytes": source.stat().st_size,
        "final_bytes": destination.stat().st_size,
    }


def valid_image_checkpoint(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.width >= 512 and image.height >= 512
    except Exception:
        return False


def _current_v84_donor_candidates(source: Path) -> list[Path]:
    """Find eligible RAWs in this output root and, for a base queue, its parent output.

    The parent is a known output location when a user selects MG/base instead
    of MG.  Never scan arbitrary nearby image folders or REVIEW_REQUIRED.
    The caller still demands exact source SHA and reruns the whole-scene gate.
    """
    roots = [RAW_FOLDER]
    if SOURCE.name.casefold() == "base":
        sibling = SOURCE.parent / str(CFG["output_folder"]) / "02_NANO_BANANA_PRO_RAW"
        if sibling != RAW_FOLDER and sibling.is_dir():
            roots.append(sibling)
    prefix = source.stem + "_"
    candidates: list[Path] = []
    for donor_root in roots:
        if not donor_root.is_dir():
            continue
        for path in donor_root.rglob("*"):
            if not path.is_file() or not path.name.startswith(prefix):
                continue
            if not any(tag in path.name for tag in (
                "_V861_NANO_BANANA_PRO_GENERATIVE_RAW_",
                "_V862_DONOR_QUALITY_FINAL_LOCK_",
                "_V863_VERIFIED_DONOR_IS_FINAL_MASTER_",
                "_V864_RESUMABLE_AUTO_RETRY_QUEUE_",
                "_V865_VERSIONED_RETRY_BUDGET_",
                "_V866_MATERIAL_IDENTITY_LOCK_",
                "_V867_SOURCE_FAITHFUL_UPSCALER_",
                "_V868_FAITHFUL_DONOR_DETAIL_CARRIER_",
                "_V869_DONOR_DETAIL_EVIDENCE_RECOVERY_",
                "_V870_DUAL_MODE_GENERATIVE_MASTER_",
                "_V871_VISUAL_REVIEW_CONSOLE_",
                "_V872_PC_REVIEW_CONSOLE_",
                "_V873_LOCAL_WEB_REVIEW_CONSOLE_",
                f"_{PIPELINE_TAG}_",
            )):
                continue
            if "NANO_BANANA_PRO_APPEARANCE_DONOR" not in path.name:
                continue
            if valid_image_checkpoint(path):
                candidates.append(path)
    return sorted(set(candidates), key=lambda item: str(item).lower())


def _checkpoint_token(candidate: Path) -> str | None:
    match = re.search(r"_([0-9a-fA-F]{16})_V(?:861|862|863|864|865|866|867|868|869|870|871|872|873|874)_", candidate.name)
    return match.group(1).lower() if match else None


def _historical_source_reference(source: Path, candidate: Path) -> Path | None:
    token = _checkpoint_token(candidate)
    if not token or not REFERENCE_FOLDER.exists():
        return None
    prefix = source.stem + f"_{token}_"
    matches = [
        path
        for path in REFERENCE_FOLDER.rglob("*")
        if path.is_file()
        and path.name.startswith(prefix)
        and any(tag in path.name for tag in (
            "_V861_NANO_BANANA_PRO_GENERATIVE_RAW_",
            "_V862_DONOR_QUALITY_FINAL_LOCK_",
            "_V863_VERIFIED_DONOR_IS_FINAL_MASTER_",
            "_V864_RESUMABLE_AUTO_RETRY_QUEUE_",
            "_V865_VERSIONED_RETRY_BUDGET_",
            "_V866_MATERIAL_IDENTITY_LOCK_",
            "_V867_SOURCE_FAITHFUL_UPSCALER_",
            "_V868_FAITHFUL_DONOR_DETAIL_CARRIER_",
            "_V869_DONOR_DETAIL_EVIDENCE_RECOVERY_",
            "_V870_DUAL_MODE_GENERATIVE_MASTER_",
            "_V871_VISUAL_REVIEW_CONSOLE_",
            "_V872_PC_REVIEW_CONSOLE_",
            "_V873_LOCAL_WEB_REVIEW_CONSOLE_",
            f"_{PIPELINE_TAG}_",
        ))
        and "SOURCE_REFERENCE_UHD4K" in path.name
        and valid_image_checkpoint(path)
    ]
    return sorted(matches, key=lambda item: str(item).lower())[0] if matches else None


def _same_scene_checkpoint_match(reference: Image.Image, source: Path, candidate: Path) -> dict[str, Any]:
    historical_path = _historical_source_reference(source, candidate)
    if historical_path is None:
        return {
            "passed": False,
            "score": 0.0,
            "reason": "matching_v8_4_source_reference_not_found",
        }
    work_width = min(1280, int(CFG["transfer_work_width"]), reference.width)
    work_height = round(work_width * reference.height / reference.width)
    current_array = np.asarray(reference.resize((work_width, work_height), Image.Resampling.LANCZOS).convert("RGB"))
    with Image.open(historical_path) as raw:
        historical_array = np.asarray(raw.convert("RGB").resize((work_width, work_height), Image.Resampling.LANCZOS))
    registration = _feature_registration(current_array, historical_array)
    inliers = int(registration.get("inliers", 0))
    inlier_ratio = float(registration.get("inlier_ratio", 0.0))
    median_error = float(registration.get("median_reprojection_error_px", 9999.0))
    if registration.get("homography_donor_to_source") is not None:
        homography = np.asarray(registration["homography_donor_to_source"], dtype=np.float64)
        historical_aligned = cv2.warpPerspective(
            historical_array,
            homography,
            (work_width, work_height),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_REFLECT_101,
        )
    else:
        historical_aligned = historical_array
    current_gray = cv2.cvtColor(current_array, cv2.COLOR_RGB2GRAY)
    historical_gray = cv2.cvtColor(historical_aligned, cv2.COLOR_RGB2GRAY)
    reference_ssim = _ssim_gray(current_gray, historical_gray)
    low_sigma = max(3.0, work_width / 160.0)
    current_low = cv2.GaussianBlur(current_array.astype(np.float32), (0, 0), sigmaX=low_sigma)
    historical_low = cv2.GaussianBlur(historical_aligned.astype(np.float32), (0, 0), sigmaX=low_sigma)
    low_rms = float(np.sqrt(np.mean((current_low - historical_low) ** 2)) / 255.0)
    shift, response = cv2.phaseCorrelate(current_gray.astype(np.float32), historical_gray.astype(np.float32))
    shift_magnitude = float(math.hypot(shift[0], shift[1]))
    passed = (
        bool(registration.get("passed"))
        and inliers >= int(CFG["checkpoint_match_min_inliers"])
        and inlier_ratio >= float(CFG["checkpoint_match_min_inlier_ratio"])
        and median_error <= float(CFG["checkpoint_match_max_median_error_px"])
        and reference_ssim >= float(CFG["checkpoint_match_min_source_reference_ssim"])
        and low_rms <= float(CFG["checkpoint_match_max_source_reference_low_rms"])
        and shift_magnitude <= float(CFG["checkpoint_match_max_source_reference_shift_px"])
    )
    score = (inliers * inlier_ratio * max(0.01, reference_ssim)) / max(0.20, median_error) if passed else 0.0
    return {
        "passed": passed,
        "score": round(float(score), 6),
        "reason": "same_source_reference" if passed else "source_reference_identity_threshold_failed",
        "historical_source_reference": str(historical_path),
        "source_reference_ssim": round(reference_ssim, 7),
        "source_reference_low_rms": round(low_rms, 7),
        "source_reference_shift_px": round(shift_magnitude, 6),
        "source_reference_phase_response": round(float(response), 6),
        "registration": registration,
        "thresholds": {
            "min_inliers": int(CFG["checkpoint_match_min_inliers"]),
            "min_inlier_ratio": float(CFG["checkpoint_match_min_inlier_ratio"]),
            "max_median_error_px": float(CFG["checkpoint_match_max_median_error_px"]),
            "min_source_reference_ssim": float(CFG["checkpoint_match_min_source_reference_ssim"]),
            "max_source_reference_low_rms": float(CFG["checkpoint_match_max_source_reference_low_rms"]),
            "max_source_reference_shift_px": float(CFG["checkpoint_match_max_source_reference_shift_px"]),
        },
    }


def compatible_current_v84_donor(
    source: Path,
    sha256: str,
    reference: Image.Image,
) -> tuple[Path | None, dict[str, Any]]:
    candidates = _current_v84_donor_candidates(source)
    search_roots = [str(RAW_FOLDER)]
    if SOURCE.name.casefold() == "base":
        search_roots.append(str(SOURCE.parent / str(CFG["output_folder"]) / "02_NANO_BANANA_PRO_RAW"))
    exact_token = f"_{sha256[:16]}_"
    exact = [path for path in candidates if exact_token in path.name]
    if exact:
        eligible: list[tuple[int, Path, dict[str, Any]]] = []
        rejected_exact: list[dict[str, Any]] = []
        sky_recovery_candidates: list[tuple[Path, dict[str, Any]]] = []
        seen_donors: dict[str, tuple[dict[str, Any], Path]] = {}
        for path in exact:
            try:
                donor_sha256 = file_sha256(path)
                if donor_sha256 in seen_donors:
                    gate, original_path = seen_donors[donor_sha256]
                else:
                    with Image.open(path) as handle:
                        gate, _ = whole_scene_quality_gate(reference, handle.convert("RGB"))
                    original_path = path
                    seen_donors[donor_sha256] = (gate, path)
            except Exception as exc:
                rejected_exact.append({"candidate": str(path), "reason": f"read_or_gate_error: {exc}"})
                continue
            if gate["passed"]:
                inliers = int(gate["camera_view_lock"]["metrics"].get("inliers", 0))
                eligible.append((inliers, path, gate))
            else:
                rejected_exact.append({
                    "candidate": str(path), "decision": gate["decision"],
                    "failed_checks": sorted(name for name, passed in gate["checks"].items() if not passed),
                    "duplicate_of": str(original_path) if original_path != path else None,
                })
                if original_path == path and _source_sky_recovery_eligible(reference, gate):
                    sky_recovery_candidates.append((path, gate))
        if eligible:
            eligible.sort(key=lambda item: (-item[0], str(item[1]).lower()))
            selected = eligible[0]
            return selected[1], {
                "method": "same_version_exact_source_sha256_and_camera_lock",
                "candidate_count": len(candidates),
                "searched_raw_roots": search_roots,
                "exact_candidate_count": len(exact),
                "selected": str(selected[1]),
                "camera_view_lock": selected[2]["camera_view_lock"],
                "rejected_exact_candidates": rejected_exact,
            }
        # The previously paid donor stays untouched. Only a new source-faithful
        # candidate that passes the complete technical gate enters FINAL.
        for path, original_gate in sky_recovery_candidates:
            try:
                with Image.open(path) as handle:
                    recovered, recovery = recover_original_source_sky(reference, handle.convert("RGB"))
                recovered_gate, recovered_audit = whole_scene_quality_gate(reference, recovered)
                if recovered_gate["passed"]:
                    recovered_path = out_path(REVIEW_FOLDER, source, sha256, "SOURCE_SKY_RECOVERY_CANDIDATE")
                    recovered_board_path = out_path(AUDIT_FOLDER, source, sha256, "SOURCE_SKY_RECOVERY_REVIEW_BOARD")
                    atomic_save_png(recovered, recovered_path)
                    atomic_save_png(
                        whole_scene_review_board(reference, recovered, recovered_audit),
                        recovered_board_path,
                    )
                    logging.info("LOCAL SKY RECOVERY | original RAW retained: %s", path)
                    return recovered_path, {
                        "method": "same_version_exact_source_sha256_source_sky_recovery",
                        "candidate_count": len(candidates),
                        "searched_raw_roots": search_roots,
                        "source_sky_recovery": {
                            **recovery,
                            "original_donor": str(path),
                            "original_donor_sha256": file_sha256(path),
                            "recovered_donor_sha256": file_sha256(recovered_path),
                            "original_failed_checks": sorted(
                                name for name, passed in original_gate["checks"].items() if not passed
                            ),
                            "review_board": str(recovered_board_path),
                            "api_request_made": False,
                        },
                        "camera_view_lock": recovered_gate["camera_view_lock"],
                        "rejected_exact_candidates": rejected_exact,
                    }
                rejected_exact.append({
                    "candidate": str(path), "decision": "LOCAL_SKY_RECOVERY_NOT_ELIGIBLE",
                    "failed_checks_after_recovery": sorted(
                        name for name, passed in recovered_gate["checks"].items() if not passed
                    ),
                })
            except Exception as exc:
                rejected_exact.append({"candidate": str(path), "reason": f"local_recovery_error: {exc}"})
        return None, {
            "method": "same_version_exact_source_sha256_all_candidates_rejected",
            "candidate_count": len(candidates),
            "searched_raw_roots": search_roots,
            "rejected_exact_candidates": rejected_exact,
            "api_request_made": False,
        }

    # V8.6.2 deliberately refuses visual recovery after a source re-encode.
    # A checkpoint is reusable only when its filename token matches the exact
    # SHA-256 of the current source bytes.
    return None, {
        "method": "exact_source_sha256_required",
        "scope": "current_and_base_parent_v8_4_raw_folders_only",
        "searched_raw_roots": search_roots,
        "candidate_count": len(candidates),
        "compatible_candidate_count": 0,
        "legacy_donor_search_enabled": False,
        "source_reencode_recovery_enabled": False,
        "api_request_made": False,
    }

    evaluated: list[tuple[float, Path, dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            match = _same_scene_checkpoint_match(reference, source, candidate)
        except Exception as exc:
            rejected.append({"candidate": str(candidate), "reason": f"read_or_match_error: {exc}"})
            continue
        if match["passed"]:
            try:
                with Image.open(candidate) as handle:
                    donor_gate, _ = whole_scene_quality_gate(reference, handle.convert("RGB"))
            except Exception as exc:
                rejected.append({"candidate": str(candidate), "reason": f"donor_gate_error: {exc}"})
                continue
            if donor_gate["passed"]:
                match["donor_camera_view_lock"] = donor_gate["camera_view_lock"]
                evaluated.append((float(match["score"]), candidate, match))
            else:
                rejected.append({
                    "candidate": str(candidate),
                    "reason": "donor_technical_gate_failed",
                    "decision": donor_gate["decision"],
                    "camera_view_lock": donor_gate["camera_view_lock"],
                })
        else:
            rejected.append({
                "candidate": str(candidate),
                "reason": "visual_identity_threshold_failed",
                "registration": match["registration"],
            })
    if not evaluated:
        return None, {
            "method": "none",
            "scope": "current_v8_4_raw_folder_only",
            "candidate_count": len(candidates),
            "compatible_candidate_count": 0,
            "rejected_candidates": rejected,
            "legacy_donor_search_enabled": False,
            "api_request_made": False,
        }
    evaluated.sort(key=lambda item: (-item[0], str(item[1]).lower()))
    score, selected, match = evaluated[0]
    return selected, {
        "method": "same_version_visual_registration_after_source_reencode",
        "scope": "current_v8_4_raw_folder_only",
        "candidate_count": len(candidates),
        "compatible_candidate_count": len(evaluated),
        "selected": str(selected),
        "score": round(score, 6),
        "registration": match["registration"],
        "legacy_donor_search_enabled": False,
        "api_request_made": False,
    }


def save_state(source: Path, sha256: str, stage: str, status: str, detail: dict[str, Any] | None = None) -> Path:
    path = out_path(STATE_FOLDER, source, sha256, "STATE", ".json")
    payload = {
        "pipeline": PIPELINE_TAG,
        "source": str(source),
        "source_sha256": sha256,
        "updated_at_utc": utc_now(),
        "stage": stage,
        "status": status,
        "detail": detail or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _extract_api_key(text: str) -> str | None:
    keys = re.findall(r"sk-or-v1-[A-Za-z0-9_-]+", text)
    return keys[0] if keys else None


def api_key(allow_prompt: bool = True) -> str:
    environment_key = _extract_api_key(os.environ.get("OPENROUTER_API_KEY", ""))
    if environment_key:
        logging.info("API KEY | OpenRouter key loaded from OPENROUTER_API_KEY environment variable.")
        return environment_key

    candidates = [
        KEY_FILE,
        SOURCE / "API.txt",
        SOURCE.parent / "API.txt",
        ROOT / "API.txt",
        ROOT / "OPENROUTER_API_KEY.txt",
    ]
    seen: set[str] = set()
    for path in candidates:
        identity = str(path.resolve(strict=False)).lower()
        if identity in seen:
            continue
        seen.add(identity)
        if not path.exists():
            continue
        key = _extract_api_key(path.read_text(encoding="utf-8-sig", errors="replace"))
        if key:
            logging.info("API KEY | OpenRouter key loaded from local key file: %s", path)
            return key

    if allow_prompt and sys.stdin is not None and sys.stdin.isatty():
        print()
        print("OpenRouter API key was not found in the migrated D:\\Work\\AI project paths.")
        print("Paste the key locally now. Input is hidden and is not written to diagnostics.")
        entered = getpass.getpass("OpenRouter key (sk-or-v1-...): ").strip()
        key = _extract_api_key(entered)
        if key:
            logging.info("API KEY | OpenRouter key received from hidden local console input; not saved.")
            return key
        raise ConfigurationFailure("The entered OpenRouter key has an invalid format. No API request was made.")

    raise ConfigurationFailure(
        "OpenRouter key was not found. Put API.txt in the project root, next to the program, "
        "or set OPENROUTER_API_KEY. No API request was made."
    )


def image_data(path: Path, max_edge: int = 4096) -> str:
    with Image.open(path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=96, subsampling=0)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def generation_aspect_request(path: Path) -> dict[str, Any]:
    """Derive the provider canvas from the immutable, EXIF-oriented source."""
    with Image.open(path) as raw:
        source = ImageOps.exif_transpose(raw)
        width, height = source.size
    if width < 1 or height < 1:
        raise ConfigurationFailure(f"Invalid source dimensions: {path}")
    source_ratio = width / height
    label, requested_ratio = min(
        GENERATION_ASPECT_RATIOS,
        key=lambda item: abs(math.log(source_ratio / item[1])),
    )
    relative_error = abs(requested_ratio - source_ratio) / source_ratio
    return {
        "source_size": [width, height],
        "source_aspect_ratio": round(source_ratio, 8),
        "aspect_ratio": label,
        "requested_aspect_ratio": round(requested_ratio, 8),
        "nearest_ratio_relative_error": round(relative_error, 8),
    }


def _trace_safe(payload: dict[str, Any]) -> dict[str, Any]:
    safe = json.loads(json.dumps(payload))
    for reference in safe.get("input_references", []):
        url = reference.get("image_url", {}).get("url", "")
        reference["image_url"]["url"] = f"<embedded image {len(url)} chars>"
    return safe


def _write_trace(name: str, payload: dict[str, Any], response: dict[str, Any]) -> None:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    token = hashlib.sha256(f"{time.time_ns()}:{name}".encode()).hexdigest()[:10]
    path = TRACE_FOLDER / f"{stamp}_{token}_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"created_at_utc": utc_now(), "request": _trace_safe(payload), "response": response}, ensure_ascii=False, indent=2), encoding="utf-8")


def _retry_delay(retry: int) -> float:
    schedule = [0.0, 14.0, 38.0, 80.0]
    return schedule[min(retry, len(schedule) - 1)] + random.uniform(0.0, 2.0)


def _post_json(endpoint: str, key: str, payload: dict[str, Any], trace_name: str, timeout: int) -> dict[str, Any]:
    last: Exception | None = None
    max_retries = int(CFG["max_retries"])
    for retry in range(1, max_retries + 1):
        started = time.monotonic()
        try:
            response = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=(int(CFG["connect_timeout_seconds"]), timeout),
            )
            elapsed = round(time.monotonic() - started, 3)
            if response.status_code >= 400:
                _write_trace(trace_name, payload, {"status_code": response.status_code, "elapsed_seconds": elapsed, "body_excerpt": response.text[:1200], "retry": retry})
                raise ApiFailure(f"{trace_name} HTTP {response.status_code}: {response.text[:700]}", response.status_code)
            body = response.json()
            _write_trace(trace_name, payload, {"status_code": response.status_code, "elapsed_seconds": elapsed, "usage": body.get("usage", {}), "retry": retry})
            return body
        except ApiFailure as exc:
            last = exc
            if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 429:
                break
        except (requests.ConnectionError, requests.Timeout, ConnectionResetError) as exc:
            last = exc
            _write_trace(trace_name, payload, {"error": type(exc).__name__, "message": str(exc)[:1200], "retry": retry})
        except Exception as exc:
            last = exc
            _write_trace(trace_name, payload, {"error": type(exc).__name__, "message": str(exc)[:1200], "retry": retry})
        logging.warning("%s retry %d/%d failed: %s", trace_name, retry, max_retries, last)
        if retry < max_retries:
            delay = _retry_delay(retry)
            logging.info("Network backoff %.1f seconds; completed donor checkpoints will be reused.", delay)
            time.sleep(delay)
    raise RuntimeError(f"{trace_name} failed after retries: {last}")


def generate_image(key: str, reference: Path, trace_name: str, seed: int) -> tuple[Image.Image, dict[str, Any]]:
    aspect = generation_aspect_request(reference)
    if os.environ.get("MG4K_LOCK_PROFILE_JSON", "").strip():
        request_prompt = (
            donor_generation_prompt()
            + "\nOUTPUT CANVAS CONTRACT: return exactly one complete "
            + str(aspect["aspect_ratio"])
            + " image. The scene must fill the canvas edge to edge with no padding, frame, inset or duplicated border. "
              "Camera/framing behaviour must follow the ACTIVE CAMERA lock level for this job."
        )
    else:
        request_prompt = (
            donor_generation_prompt()
            + "\nOUTPUT CANVAS CONTRACT: return exactly one complete "
            + str(aspect["aspect_ratio"])
            + " image. Preserve the source framing inside that canvas; do not crop, pad, extend, "
              "recenter, zoom, rotate or change the camera."
        )
    payload: dict[str, Any] = {
        "model": CFG["model"],
        "prompt": request_prompt,
        "input_references": [{"type": "image_url", "image_url": {"url": image_data(reference)}}],
        "resolution": CFG["generation_resolution"],
        "aspect_ratio": aspect["aspect_ratio"],
        "output_format": "png",
        "n": 1,
        "seed": int(seed),
    }
    logging.info(
        "NATIVE ASPECT REQUEST | source=%sx%s | source_ratio=%.8f | provider_ratio=%s",
        aspect["source_size"][0], aspect["source_size"][1],
        aspect["source_aspect_ratio"], aspect["aspect_ratio"],
    )
    body = _post_json("https://openrouter.ai/api/v1/images", key, payload, trace_name, int(CFG["generation_timeout_seconds"]))
    provider_model = body.get("model")
    if provider_model and str(provider_model) != str(CFG["model"]):
        raise RuntimeError(
            f"Provider returned model {provider_model!r}, but Nano Banana Pro "
            f"({CFG['model']}) was required. RAW was not accepted."
        )
    raw = base64.b64decode(body["data"][0]["b64_json"])
    image = Image.open(io.BytesIO(raw))
    image.load()
    output = image.convert("RGB")
    generated_ratio = output.width / output.height
    source_ratio = float(aspect["source_aspect_ratio"])
    output_error = abs(generated_ratio - source_ratio) / source_ratio
    logging.info(
        "PROVIDER CANVAS VERIFIED | generated=%sx%s | generated_ratio=%.8f | source_error=%.6f",
        output.width, output.height, generated_ratio, output_error,
    )
    usage = dict(body.get("usage", {}))
    usage["generator"] = str(CFG.get("generator_display_name", "Nano Banana Pro"))
    usage["requested_model"] = str(CFG["model"])
    usage["provider_model"] = str(provider_model or CFG["model"])
    usage["native_aspect_request"] = {
        **aspect,
        "generated_size": [output.width, output.height],
        "generated_aspect_ratio": round(generated_ratio, 8),
        "generated_to_source_relative_error": round(output_error, 8),
        "provider_parameter_sent": True,
    }
    return output, usage


def prepare_reference(image: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    source = ImageOps.exif_transpose(image).convert("RGB")
    source_ratio = source.width / source.height
    if source.width >= source.height:
        target_size = (int(TARGET[0]), max(1, round(int(TARGET[0]) / source_ratio)))
    else:
        target_size = (max(1, round(int(TARGET[1]) * source_ratio)), int(TARGET[1]))
    return source.resize(target_size, Image.Resampling.LANCZOS), {
        "original_size": [source.width, source.height],
        "original_aspect": round(source_ratio, 8),
        "target_size": list(target_size),
        "target_aspect": round(target_size[0] / target_size[1], 8),
        "non_destructive_padding_original_pixels": [0, 0, 0, 0],
        "cropped": False,
        "warped": False,
        "aspect_ratio_preserved": True,
    }


def build_structure_control(reference: Image.Image) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
    rgb = np.asarray(reference.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    horizontal = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((1, 17), np.uint8))
    vertical = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((17, 1), np.uint8))
    structural = cv2.max(edges, cv2.max(horizontal, vertical))
    radius = int(CFG["structure_guard_radius_px_at_target"])
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    guard = cv2.dilate(structural, kernel)
    overlay = rgb.copy()
    overlay[structural > 0] = (0, 235, 255)
    overlay = cv2.addWeighted(rgb, 0.72, overlay, 0.28, 0.0)
    return Image.fromarray(overlay, "RGB"), Image.fromarray(guard, "L"), {
        "source_edge_fraction": round(float(np.mean(edges > 0)), 6),
        "structural_guard_fraction": round(float(np.mean(guard > 0)), 6),
        "control_role": "audit and transfer lock only; not sent to generator",
    }


def _feature_registration(source: np.ndarray, donor: np.ndarray) -> dict[str, Any]:
    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    donor_gray = cv2.cvtColor(donor, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    source_gray = clahe.apply(source_gray)
    donor_gray = clahe.apply(donor_gray)
    orb = cv2.ORB_create(nfeatures=12000, fastThreshold=5, edgeThreshold=19)
    source_points, source_desc = orb.detectAndCompute(source_gray, None)
    donor_points, donor_desc = orb.detectAndCompute(donor_gray, None)
    if source_desc is None or donor_desc is None:
        return {"passed": False, "reason": "no_descriptors", "matches": 0}
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False).knnMatch(donor_desc, source_desc, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < float(CFG["registration_ratio_test"]) * pair[1].distance]
    if len(good) < int(CFG["registration_min_matches"]):
        return {"passed": False, "reason": "insufficient_matches", "matches": len(good)}
    donor_xy = np.float32([donor_points[item.queryIdx].pt for item in good]).reshape(-1, 1, 2)
    source_xy = np.float32([source_points[item.trainIdx].pt for item in good]).reshape(-1, 1, 2)
    homography, mask = cv2.findHomography(donor_xy, source_xy, cv2.RANSAC, float(CFG["registration_ransac_px"]))
    if homography is None or mask is None:
        return {"passed": False, "reason": "homography_failed", "matches": len(good)}
    inliers = mask.ravel().astype(bool)
    count = int(inliers.sum())
    ratio = count / max(1, len(good))
    projected = cv2.perspectiveTransform(donor_xy[inliers], homography).reshape(-1, 2)
    residuals = np.linalg.norm(projected - source_xy[inliers].reshape(-1, 2), axis=1)
    passed = count >= int(CFG["registration_min_inliers"]) and ratio >= float(CFG["registration_min_inlier_ratio"])
    source_inlier_xy = source_xy[inliers].reshape(-1, 2)
    donor_inlier_xy = donor_xy[inliers].reshape(-1, 2)

    def spatial_support(points: np.ndarray, width: int, height: int) -> tuple[float, float]:
        if len(points) < 3:
            return 0.0, 0.0
        rows = int(CFG["camera_lock_support_rows"])
        columns = int(CFG["camera_lock_support_columns"])
        occupied = np.zeros((rows, columns), dtype=bool)
        occupied[
            np.clip((points[:, 1] / max(1.0, height) * rows).astype(int), 0, rows - 1),
            np.clip((points[:, 0] / max(1.0, width) * columns).astype(int), 0, columns - 1),
        ] = True
        hull_fraction = float(cv2.contourArea(cv2.convexHull(points.astype(np.float32)))) / max(1.0, width * height)
        return float(np.mean(occupied)), hull_fraction

    source_support_cells, source_hull_fraction = spatial_support(source_inlier_xy, source.shape[1], source.shape[0])
    donor_support_cells, donor_hull_fraction = spatial_support(donor_inlier_xy, donor.shape[1], donor.shape[0])
    return {
        "passed": passed,
        "reason": "ok" if passed else "weak_global_registration",
        "matches": len(good),
        "inliers": count,
        "inlier_ratio": round(ratio, 6),
        "median_reprojection_error_px": round(float(np.median(residuals)), 5),
        "mean_reprojection_error_px": round(float(np.mean(residuals)), 5),
        "source_inlier_support_cell_fraction": round(source_support_cells, 6),
        "donor_inlier_support_cell_fraction": round(donor_support_cells, 6),
        "source_inlier_hull_fraction": round(source_hull_fraction, 6),
        "donor_inlier_hull_fraction": round(donor_hull_fraction, 6),
        "homography_donor_to_source": homography.tolist(),
    }


def camera_view_lock(registration: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """Reject a new viewpoint; homography is diagnostic, never permission to reframe."""
    homography_payload = registration.get("homography_donor_to_source")
    if not registration.get("passed") or homography_payload is None:
        return {
            "passed": False,
            "decision": "REJECTED_CAMERA_VIEW",
            "reason": "global_registration_failed",
            "camera_score": 0.0,
            "metrics": {
                "inliers": int(registration.get("inliers", 0)),
                "inlier_ratio": float(registration.get("inlier_ratio", 0.0)),
                "source_inlier_support_cell_fraction": float(registration.get("source_inlier_support_cell_fraction", 0.0)),
                "source_inlier_hull_fraction": float(registration.get("source_inlier_hull_fraction", 0.0)),
            },
        }
    homography = np.asarray(homography_payload, dtype=np.float64)
    homography /= max(1e-12, homography[2, 2])
    anchors = np.float32([
        [[0.0, 0.0]], [[width - 1.0, 0.0]], [[width - 1.0, height - 1.0]], [[0.0, height - 1.0]],
        [[0.5 * (width - 1.0), 0.5 * (height - 1.0)]],
        [[0.25 * (width - 1.0), 0.25 * (height - 1.0)]], [[0.75 * (width - 1.0), 0.25 * (height - 1.0)]],
        [[0.25 * (width - 1.0), 0.75 * (height - 1.0)]], [[0.75 * (width - 1.0), 0.75 * (height - 1.0)]],
    ])
    projected = cv2.perspectiveTransform(anchors, homography).reshape(-1, 2)
    displacement = np.linalg.norm(projected - anchors.reshape(-1, 2), axis=1)
    diagonal = math.hypot(width, height)
    corner_max = float(np.max(displacement[:4]) / diagonal)
    anchor_max = float(np.max(displacement) / diagonal)
    center = float(displacement[4] / diagonal)
    inliers = int(registration.get("inliers", 0))
    ratio = float(registration.get("inlier_ratio", 0.0))
    support = min(
        float(registration.get("source_inlier_support_cell_fraction", 0.0)),
        float(registration.get("donor_inlier_support_cell_fraction", 0.0)),
    )
    hull = min(
        float(registration.get("source_inlier_hull_fraction", 0.0)),
        float(registration.get("donor_inlier_hull_fraction", 0.0)),
    )
    checks = {
        "minimum_inliers": inliers >= int(CFG["camera_lock_min_inliers"]),
        "minimum_inlier_ratio": ratio >= float(CFG["camera_lock_min_inlier_ratio"]),
        "distributed_support": support >= float(CFG["camera_lock_min_support_cell_fraction"]),
        "global_hull_coverage": hull >= float(CFG["camera_lock_min_inlier_hull_fraction"]),
        "center_anchor": center <= float(CFG["camera_lock_max_center_displacement_fraction"]),
        "corner_anchors": corner_max <= float(CFG["camera_lock_max_corner_displacement_fraction"]),
        "all_anchors": anchor_max <= float(CFG["camera_lock_max_anchor_displacement_fraction"]),
    }
    passed = all(checks.values())
    camera_score = 100.0 * sum(bool(value) for value in checks.values()) / len(checks)
    return {
        "passed": passed,
        "decision": "CAMERA_VIEW_LOCKED" if passed else "REJECTED_CAMERA_VIEW",
        "reason": "exact_view_supported" if passed else "camera_geometry_drift",
        "camera_score": round(camera_score, 1),
        "checks": checks,
        "metrics": {
            "inliers": inliers,
            "inlier_ratio": round(ratio, 6),
            "support_cell_fraction": round(support, 6),
            "inlier_hull_fraction": round(hull, 6),
            "center_displacement_fraction_of_diagonal": round(center, 6),
            "max_corner_displacement_fraction_of_diagonal": round(corner_max, 6),
            "max_anchor_displacement_fraction_of_diagonal": round(anchor_max, 6),
        },
    }


def register_donor(reference: Image.Image, donor: Image.Image) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
    work_width = min(int(CFG["transfer_work_width"]), reference.width)
    work_height = round(work_width * reference.height / reference.width)
    source = np.asarray(reference.resize((work_width, work_height), Image.Resampling.LANCZOS))
    normalized = np.asarray(donor.convert("RGB").resize((work_width, work_height), Image.Resampling.LANCZOS))
    registration = _feature_registration(source, normalized)
    if not registration.get("passed"):
        raise RuntimeError(f"Appearance donor registration failed: {registration}")
    homography = np.asarray(registration["homography_donor_to_source"], dtype=np.float64)
    aligned = cv2.warpPerspective(normalized, homography, (work_width, work_height), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT_101)
    valid = cv2.warpPerspective(np.full((work_height, work_width), 255, np.uint8), homography, (work_width, work_height), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    metrics = {
        "candidate_original_size": list(donor.size),
        "candidate_original_aspect": round(donor.width / donor.height, 8),
        "work_size": [work_width, work_height],
        "valid_coverage": round(float(np.mean(valid > 0)), 6),
        "global_registration": registration,
    }
    return Image.fromarray(aligned, "RGB"), Image.fromarray(valid, "L"), metrics


def _soft_range(channel: np.ndarray, low: float, high: float, feather: float) -> np.ndarray:
    enter = np.clip((channel - (low - feather)) / max(1e-6, feather), 0.0, 1.0)
    leave = np.clip(((high + feather) - channel) / max(1e-6, feather), 0.0, 1.0)
    return enter * leave


def semantic_masks(source: np.ndarray) -> dict[str, np.ndarray]:
    height, width = source.shape[:2]
    hsv = cv2.cvtColor(source, cv2.COLOR_RGB2HSV).astype(np.float32)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    yy = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    blue = _soft_range(hue, 82.0, 132.0, 14.0) * np.clip((saturation - 24.0) / 80.0, 0.0, 1.0)
    green = _soft_range(hue, 30.0, 92.0, 12.0) * np.clip((saturation - 25.0) / 95.0, 0.0, 1.0)
    top = np.clip((0.62 - yy) / 0.22, 0.0, 1.0)
    bottom = np.clip((yy - 0.62) / 0.20, 0.0, 1.0)
    sky = blue * top * np.clip((value - 85.0) / 120.0, 0.0, 1.0)
    water = blue * bottom
    nature = green * (1.0 - 0.55 * sky) * (1.0 - 0.55 * water)
    for item in (sky, water, nature):
        item[:] = cv2.GaussianBlur(item.astype(np.float32), (0, 0), sigmaX=float(CFG["semantic_mask_blur_sigma"]))
    combined = np.clip(sky + water + nature, 0.0, 1.0)
    hardscape = 1.0 - combined
    total = np.maximum(1.0, sky + water + nature + hardscape)
    return {"sky": sky / total, "water": water / total, "nature": nature / total, "hardscape": hardscape / total}


def structure_agreement(source: np.ndarray, donor: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    donor_gray = cv2.cvtColor(donor, cv2.COLOR_RGB2GRAY)
    source_edges = cv2.Canny(source_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    donor_edges = cv2.Canny(donor_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    distance_source = cv2.distanceTransform(255 - source_edges, cv2.DIST_L2, 3)
    tolerance = float(CFG["donor_edge_tolerance_px_at_work"])
    donor_new_edge = (donor_edges > 0) & (distance_source > tolerance)
    radius = int(CFG["new_edge_guard_radius_px_at_work"])
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    new_edge_guard = 1.0 - cv2.dilate(donor_new_edge.astype(np.uint8) * 255, kernel).astype(np.float32) / 255.0

    sigma = float(CFG["structure_agreement_sigma"])
    source_low = cv2.GaussianBlur(source.astype(np.float32), (0, 0), sigmaX=sigma)
    donor_low = cv2.GaussianBlur(donor.astype(np.float32), (0, 0), sigmaX=sigma)
    difference = np.mean(np.abs(source_low - donor_low), axis=2)
    start = float(CFG["structure_difference_start"])
    stop = float(CFG["structure_difference_stop"])
    agreement = 1.0 - np.clip((difference - start) / max(1.0, stop - start), 0.0, 1.0)
    agreement = agreement * agreement * (3.0 - 2.0 * agreement)
    agreement = cv2.GaussianBlur(agreement.astype(np.float32), (0, 0), sigmaX=1.25)
    metrics = {
        "mean_structure_agreement": round(float(agreement.mean()), 6),
        "donor_new_edge_fraction": round(float(np.mean(donor_new_edge)), 6),
        "new_edge_guarded_fraction": round(float(np.mean(new_edge_guard < 0.5)), 6),
    }
    return agreement, new_edge_guard, metrics


def _robust_cell_delta(
    source_lab: np.ndarray,
    donor_lab: np.ndarray,
    valid: np.ndarray,
    agreement: np.ndarray,
    masks: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Extract a deliberately low-bandwidth appearance field.

    V8 never copies a donor pixel, donor edge, or donor high frequency.  It
    samples only robust colour/illumination statistics from large cells whose
    contents agree with the source.  The coarse grid is then strongly blurred.
    """
    height, width = valid.shape
    rows = max(3, int(CFG["illumination_grid_rows"]))
    columns = max(4, int(CFG["illumination_grid_columns"]))
    cell_l = np.zeros((rows, columns), dtype=np.float32)
    cell_ab = np.zeros((rows, columns, 2), dtype=np.float32)
    cell_weight = np.zeros((rows, columns), dtype=np.float32)
    delta = donor_lab - source_lab
    natural = np.clip(masks["sky"] + masks["water"] + masks["nature"], 0.0, 1.0)
    confidence = valid * agreement * agreement * (0.30 + 0.70 * natural)
    for row in range(rows):
        y0 = round(row * height / rows)
        y1 = round((row + 1) * height / rows)
        for column in range(columns):
            x0 = round(column * width / columns)
            x1 = round((column + 1) * width / columns)
            conf = confidence[y0:y1, x0:x1]
            keep = conf >= 0.32
            if int(np.count_nonzero(keep)) < 96:
                continue
            values = delta[y0:y1, x0:x1][keep]
            weights = conf[keep]
            order = np.argsort(values[:, 0])
            values = values[order]
            weights = weights[order]
            cumulative = np.cumsum(weights)
            centre = int(np.searchsorted(cumulative, cumulative[-1] * 0.5))
            cell_l[row, column] = float(values[min(centre, len(values) - 1), 0])
            cell_ab[row, column] = np.median(values[:, 1:3], axis=0)
            cell_weight[row, column] = min(1.0, float(np.mean(conf[keep])) * 1.4)
    if not np.any(cell_weight > 0):
        return (
            np.zeros((height, width), dtype=np.float32),
            np.zeros((height, width, 2), dtype=np.float32),
            {"grid_valid_fraction": 0.0, "grid_size": [columns, rows]},
        )
    for _ in range(rows + columns):
        missing = cell_weight <= 0
        if not np.any(missing):
            break
        next_l = cell_l.copy()
        next_ab = cell_ab.copy()
        next_weight = cell_weight.copy()
        for row, column in np.argwhere(missing):
            neighbours: list[tuple[int, int]] = []
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                yy, xx = row + dy, column + dx
                if 0 <= yy < rows and 0 <= xx < columns and cell_weight[yy, xx] > 0:
                    neighbours.append((yy, xx))
            if neighbours:
                weights = np.array([cell_weight[yy, xx] for yy, xx in neighbours], dtype=np.float32)
                weights /= max(1e-6, float(weights.sum()))
                next_l[row, column] = sum(float(weights[i]) * cell_l[yy, xx] for i, (yy, xx) in enumerate(neighbours))
                next_ab[row, column] = sum(weights[i] * cell_ab[yy, xx] for i, (yy, xx) in enumerate(neighbours))
                next_weight[row, column] = min(float(cell_weight[yy, xx]) for yy, xx in neighbours) * 0.86
        cell_l, cell_ab, cell_weight = next_l, next_ab, next_weight
    grid_l = cv2.resize(cell_l, (width, height), interpolation=cv2.INTER_CUBIC)
    grid_ab = cv2.resize(cell_ab, (width, height), interpolation=cv2.INTER_CUBIC)
    sigma = float(CFG["illumination_smoothing_sigma"])
    grid_l = cv2.GaussianBlur(grid_l, (0, 0), sigmaX=sigma)
    grid_ab = cv2.GaussianBlur(grid_ab, (0, 0), sigmaX=sigma)
    grid_l = np.clip(grid_l, -float(CFG["max_illumination_delta"]), float(CFG["max_illumination_delta"]))
    grid_ab = np.clip(grid_ab, -float(CFG["max_chroma_delta"]), float(CFG["max_chroma_delta"]))
    return grid_l, grid_ab, {
        "grid_valid_fraction": round(float(np.mean(cell_weight > 0)), 6),
        "grid_size": [columns, rows],
        "illumination_field_abs_mean": round(float(np.mean(np.abs(grid_l))), 6),
        "chroma_field_abs_mean": round(float(np.mean(np.abs(grid_ab))), 6),
    }


def _legacy_separate_and_transfer(
    reference: Image.Image,
    aligned_donor: Image.Image,
    valid_image: Image.Image,
    intensity: float = 1.0,
) -> tuple[Image.Image, dict[str, Image.Image], dict[str, Any]]:
    work_size = aligned_donor.size
    source = np.asarray(reference.resize(work_size, Image.Resampling.LANCZOS).convert("RGB"))
    donor = np.asarray(aligned_donor.convert("RGB"))
    valid = np.asarray(valid_image.convert("L"), dtype=np.float32) / 255.0
    masks = semantic_masks(source)
    agreement, new_edge_guard, agreement_metrics = structure_agreement(source, donor)

    source_lab = cv2.cvtColor(source, cv2.COLOR_RGB2LAB).astype(np.float32)
    donor_lab = cv2.cvtColor(donor, cv2.COLOR_RGB2LAB).astype(np.float32)
    illumination_delta, chroma_delta, field_metrics = _robust_cell_delta(source_lab, donor_lab, valid, agreement, masks)

    donor_strength = (
        masks["sky"] * float(CFG["photoreal_sky_donor_strength"])
        + masks["water"] * float(CFG["photoreal_water_donor_strength"])
        + masks["nature"] * float(CFG["photoreal_nature_donor_strength"])
        + masks["hardscape"] * float(CFG["photoreal_hardscape_donor_strength"])
    )
    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    source_edges = cv2.Canny(source_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    source_float = source.astype(np.float32)

    # Geometry is owned exclusively by the source. The donor contributes broad
    # illumination/chroma plus a bounded material band only inside edge-free,
    # high-agreement interiors. Donor contours never reach the FINAL.
    illumination_strength = (
        masks["sky"] * float(CFG["sky_illumination_strength"])
        + masks["water"] * float(CFG["water_illumination_strength"])
        + masks["nature"] * float(CFG["nature_illumination_strength"])
        + masks["hardscape"] * float(CFG["hardscape_illumination_strength"])
    )
    chroma_strength = (
        masks["sky"] * float(CFG["sky_chroma_strength"])
        + masks["water"] * float(CFG["water_chroma_strength"])
        + masks["nature"] * float(CFG["nature_chroma_strength"])
        + masks["hardscape"] * float(CFG["hardscape_chroma_strength"])
    )
    field_weight = np.clip(donor_strength * valid * intensity, 0.0, 1.0)
    result_lab = source_lab.copy()
    applied_illumination = illumination_delta * illumination_strength * field_weight
    applied_chroma = chroma_delta * chroma_strength[:, :, None] * field_weight[:, :, None]
    result_lab[:, :, 0] += applied_illumination
    result_lab[:, :, 1:3] += applied_chroma

    detail_sigma = float(CFG["source_detail_sigma"])
    source_l = source_lab[:, :, 0]
    donor_l = donor_lab[:, :, 0]
    source_high = source_l - cv2.GaussianBlur(source_l, (0, 0), sigmaX=detail_sigma)
    detail_strength = (
        masks["sky"] * float(CFG["sky_detail_strength"])
        + masks["water"] * float(CFG["water_detail_strength"])
        + masks["nature"] * float(CFG["nature_detail_strength"])
        + masks["hardscape"] * float(CFG["hardscape_detail_strength"])
    )
    # The donor may steer only a slowly varying local-contrast GAIN.  The detail
    # signal itself always comes from the source, so a shifted donor balcony,
    # tree, umbrella or cable can never be copied into FINAL.
    donor_high = donor_l - cv2.GaussianBlur(donor_l, (0, 0), sigmaX=detail_sigma)
    stat_sigma = float(CFG["donor_contrast_stat_sigma"])
    source_energy = cv2.GaussianBlur(np.abs(source_high), (0, 0), sigmaX=stat_sigma)
    donor_energy = cv2.GaussianBlur(np.abs(donor_high), (0, 0), sigmaX=stat_sigma)
    contrast_gain = np.clip(
        donor_energy / np.maximum(0.75, source_energy),
        float(CFG["donor_contrast_gain_min"]),
        float(CFG["donor_contrast_gain_max"]),
    )
    contrast_gain = cv2.GaussianBlur(
        contrast_gain,
        (0, 0),
        sigmaX=float(CFG["donor_contrast_gain_smoothing_sigma"]),
    )
    contrast_multiplier = 1.0 + (
        contrast_gain - 1.0
    ) * float(CFG["donor_contrast_stat_influence"])
    source_detail = np.clip(
        source_high * detail_strength * contrast_multiplier * intensity,
        -float(CFG["max_source_detail_delta"]),
        float(CFG["max_source_detail_delta"]),
    )
    mid_sigma = float(CFG["source_material_mid_sigma"])
    source_mid = cv2.GaussianBlur(source_l, (0, 0), sigmaX=detail_sigma) - cv2.GaussianBlur(
        source_l, (0, 0), sigmaX=mid_sigma
    )
    mid_strength = (
        masks["sky"] * float(CFG["source_material_mid_sky_strength"])
        + masks["water"] * float(CFG["source_material_mid_water_strength"])
        + masks["nature"] * float(CFG["source_material_mid_nature_strength"])
        + masks["hardscape"] * float(CFG["source_material_mid_hardscape_strength"])
    )
    source_material_depth = np.clip(
        source_mid * mid_strength * contrast_multiplier * intensity,
        -float(CFG["source_material_mid_max_delta"]),
        float(CFG["source_material_mid_max_delta"]),
    )
    broad_sigma = float(CFG["source_material_broad_sigma"])
    source_broad = cv2.GaussianBlur(source_l, (0, 0), sigmaX=mid_sigma) - cv2.GaussianBlur(
        source_l, (0, 0), sigmaX=broad_sigma
    )
    broad_strength = (
        masks["sky"] * float(CFG["source_material_broad_sky_strength"])
        + masks["water"] * float(CFG["source_material_broad_water_strength"])
        + masks["nature"] * float(CFG["source_material_broad_nature_strength"])
        + masks["hardscape"] * float(CFG["source_material_broad_hardscape_strength"])
    )
    source_broad_depth = np.clip(
        source_broad * broad_strength * intensity,
        -float(CFG["source_material_broad_max_delta"]),
        float(CFG["source_material_broad_max_delta"]),
    )
    centered_l = source_l - 128.0
    source_midtone_curve = (
        centered_l * (1.0 - np.clip(np.abs(centered_l) / 128.0, 0.0, 1.0))
        * float(CFG["source_midtone_curve_strength"])
        * intensity
    )
    saturation_strength = (
        masks["sky"] * float(CFG["source_saturation_sky_strength"])
        + masks["water"] * float(CFG["source_saturation_water_strength"])
        + masks["nature"] * float(CFG["source_saturation_nature_strength"])
        + masks["hardscape"] * float(CFG["source_saturation_hardscape_strength"])
    )
    source_saturation = (source_lab[:, :, 1:3] - 128.0) * saturation_strength[:, :, None] * intensity
    donor_inner = cv2.GaussianBlur(
        donor_l, (0, 0), sigmaX=float(CFG["donor_material_band_inner_sigma"])
    )
    donor_outer = cv2.GaussianBlur(
        donor_l, (0, 0), sigmaX=float(CFG["donor_material_band_outer_sigma"])
    )
    source_inner = cv2.GaussianBlur(
        source_l, (0, 0), sigmaX=float(CFG["donor_material_band_inner_sigma"])
    )
    source_outer = cv2.GaussianBlur(
        source_l, (0, 0), sigmaX=float(CFG["donor_material_band_outer_sigma"])
    )
    donor_material_residual = (donor_inner - donor_outer) - (source_inner - source_outer)
    donor_edges = cv2.Canny(
        cv2.cvtColor(donor, cv2.COLOR_RGB2GRAY),
        int(CFG["canny_low"]),
        int(CFG["canny_high"]),
    )
    combined_edges = ((source_edges > 0) | (donor_edges > 0)).astype(np.uint8)
    distance_from_edges = cv2.distanceTransform(1 - combined_edges, cv2.DIST_L2, 5)
    edge_safe_material_weight = np.clip(
        (distance_from_edges - float(CFG["donor_material_edge_exclusion_px"]))
        / max(1.0, float(CFG["donor_material_edge_fade_px"])),
        0.0,
        1.0,
    )
    material_strength = (
        masks["sky"] * float(CFG["donor_material_sky_strength"])
        + masks["water"] * float(CFG["donor_material_water_strength"])
        + masks["nature"] * float(CFG["donor_material_nature_strength"])
        + masks["hardscape"] * float(CFG["donor_material_hardscape_strength"])
    )
    donor_material_transfer = np.clip(
        donor_material_residual
        * material_strength
        * edge_safe_material_weight
        * agreement
        * agreement
        * valid
        * intensity,
        -float(CFG["donor_material_max_delta"]),
        float(CFG["donor_material_max_delta"]),
    )
    # Copy only genuinely generated microtexture that lies well inside the
    # same registered material region.  Both source and donor contours are
    # excluded before this signal is allowed into FINAL, so this cannot create
    # a second balcony/tree/umbrella outline.
    donor_micro = donor_l - cv2.GaussianBlur(
        donor_l, (0, 0), sigmaX=float(CFG["donor_micro_sigma"])
    )
    source_micro = source_l - cv2.GaussianBlur(
        source_l, (0, 0), sigmaX=float(CFG["donor_micro_sigma"])
    )
    micro_strength = (
        masks["water"] * float(CFG["donor_micro_water_strength"])
        + masks["nature"] * float(CFG["donor_micro_nature_strength"])
        + masks["hardscape"] * float(CFG["donor_micro_hardscape_strength"])
    )
    donor_micro_transfer = np.clip(
        (donor_micro - source_micro)
        * micro_strength
        * edge_safe_material_weight
        * agreement
        * agreement
        * valid
        * intensity,
        -float(CFG["donor_micro_max_delta"]),
        float(CFG["donor_micro_max_delta"]),
    )
    result_lab[:, :, 0] += source_detail + source_material_depth + source_broad_depth + source_midtone_curve
    result_lab[:, :, 0] += donor_material_transfer + donor_micro_transfer
    result_lab[:, :, 1:3] += source_saturation
    result = cv2.cvtColor(np.clip(result_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)

    # V8.6.2 quality carrier: the registered Nano Banana Pro RAW owns material
    # fidelity inside camera-consistent regions. Source pixels still own every
    # architectural contour. Only large new donor edge components are treated
    # as possible geometry; small, isolated donor edges are retained as useful
    # material/vegetation/water microdetail.
    source_edge_radius = max(1, int(CFG["donor_quality_source_edge_radius_px"]))
    source_edge_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (source_edge_radius * 2 + 1, source_edge_radius * 2 + 1),
    )
    source_geometry_guard = cv2.dilate((source_edges > 0).astype(np.uint8), source_edge_kernel) > 0
    source_distance = cv2.distanceTransform(1 - (source_edges > 0).astype(np.uint8), cv2.DIST_L2, 5)
    donor_new_edge = (donor_edges > 0) & (
        source_distance > float(CFG["donor_edge_tolerance_px_at_work"])
    )
    component_count, component_labels, component_stats, _ = cv2.connectedComponentsWithStats(
        donor_new_edge.astype(np.uint8),
        connectivity=8,
    )
    structural_area = max(
        24,
        round(
            float(CFG["donor_quality_min_structural_component_area_at_1280"])
            * (work_size[0] / 1280.0) ** 2
        ),
    )
    significant_new_edges = np.zeros_like(donor_new_edge, dtype=np.uint8)
    for component in range(1, component_count):
        if int(component_stats[component, cv2.CC_STAT_AREA]) >= structural_area:
            significant_new_edges[component_labels == component] = 1
    significant_radius = max(1, int(CFG["donor_quality_significant_edge_radius_px"]))
    significant_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (significant_radius * 2 + 1, significant_radius * 2 + 1),
    )
    significant_geometry_guard = cv2.dilate(significant_new_edges, significant_kernel) > 0
    geometry_guard = np.maximum(
        source_geometry_guard.astype(np.float32),
        significant_geometry_guard.astype(np.float32),
    )
    geometry_guard = cv2.GaussianBlur(
        geometry_guard,
        (0, 0),
        sigmaX=float(CFG["donor_quality_guard_feather_sigma"]),
    )
    geometry_guard = np.clip(geometry_guard, 0.0, 1.0)

    local_radius = max(1, int(CFG["donor_quality_local_source_radius"]))
    local_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (local_radius * 2 + 1, local_radius * 2 + 1),
    )
    local_min = cv2.erode(source, local_kernel).astype(np.float32)
    local_max = cv2.dilate(source, local_kernel).astype(np.float32)
    local_margin = float(CFG["donor_quality_local_source_margin"])
    donor_quality = np.clip(donor.astype(np.float32), local_min - local_margin, local_max + local_margin)
    quality_semantic_strength = (
        masks["sky"] * float(CFG["donor_quality_mix_sky_strength"])
        + masks["water"] * float(CFG["donor_quality_mix_water_strength"])
        + masks["nature"] * float(CFG["donor_quality_mix_nature_strength"])
        + masks["hardscape"] * float(CFG["donor_quality_mix_hardscape_strength"])
    )
    donor_quality_weight = np.clip(
        quality_semantic_strength
        * valid
        * np.power(np.clip(agreement, 0.0, 1.0), 1.25)
        * (1.0 - geometry_guard)
        * intensity,
        0.0,
        0.92,
    )
    pre_quality_result = result.astype(np.float32)
    result = np.clip(
        pre_quality_result * (1.0 - donor_quality_weight[:, :, None])
        + donor_quality * donor_quality_weight[:, :, None],
        0,
        255,
    ).astype(np.uint8)
    donor_quality_transfer = np.mean(
        np.abs(result.astype(np.float32) - pre_quality_result),
        axis=2,
    )
    result_gray = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY)
    dark_void_risk = (result_gray < 24) & (source_gray > 45) & (
        (source_gray.astype(np.int16) - result_gray.astype(np.int16)) > 30
    )
    if np.any(dark_void_risk):
        safe_lift = np.maximum(0, 26 - result_gray[dark_void_risk].astype(np.int16))
        result[dark_void_risk] = np.clip(
            result[dark_void_risk].astype(np.int16) + safe_lift[:, None], 0, 255
        ).astype(np.uint8)
    clip_regression = ((result_gray <= 3) & (source_gray > 16)) | ((result_gray >= 252) & (source_gray < 244))
    if np.any(clip_regression):
        result[clip_regression] = source[clip_regression]

    target_result = Image.fromarray(result, "RGB").resize(reference.size, Image.Resampling.LANCZOS)
    geometry_gate = 1.0 - geometry_guard
    significant_new_edges = significant_geometry_guard.astype(np.float32)
    ghost_risk = geometry_guard
    hardscape_anchor = np.zeros_like(field_weight, dtype=np.float32)
    donor_weight = donor_quality_weight
    texture_delta = np.zeros_like(source_high, dtype=np.float32)
    texture_weight = np.zeros_like(source_high, dtype=np.float32)
    artifacts = {
        "sky_mask": Image.fromarray(np.clip(masks["sky"] * 255, 0, 255).astype(np.uint8), "L"),
        "water_mask": Image.fromarray(np.clip(masks["water"] * 255, 0, 255).astype(np.uint8), "L"),
        "nature_mask": Image.fromarray(np.clip(masks["nature"] * 255, 0, 255).astype(np.uint8), "L"),
        "hardscape_mask": Image.fromarray(np.clip(masks["hardscape"] * 255, 0, 255).astype(np.uint8), "L"),
        "structure_agreement": Image.fromarray(np.clip(agreement * 255, 0, 255).astype(np.uint8), "L"),
        "new_edge_guard": Image.fromarray(np.clip(new_edge_guard * 255, 0, 255).astype(np.uint8), "L"),
        "illumination_field": Image.fromarray(np.clip(illumination_delta + 128, 0, 255).astype(np.uint8), "L"),
        "chroma_field": Image.fromarray(np.clip(np.linalg.norm(chroma_delta, axis=2) * 14, 0, 255).astype(np.uint8), "L"),
        "source_detail_recovery": Image.fromarray(np.clip(np.abs(source_high) * 12, 0, 255).astype(np.uint8), "L"),
        "source_material_depth": Image.fromarray(np.clip(np.abs(source_material_depth) * 18, 0, 255).astype(np.uint8), "L"),
        "source_broad_depth": Image.fromarray(np.clip(np.abs(source_broad_depth) * 22, 0, 255).astype(np.uint8), "L"),
        "donor_contrast_statistics": Image.fromarray(np.clip((contrast_gain - 0.75) * 170, 0, 255).astype(np.uint8), "L"),
        "donor_material_safe_zone": Image.fromarray(np.clip(edge_safe_material_weight * 255, 0, 255).astype(np.uint8), "L"),
        "donor_material_transfer": Image.fromarray(np.clip(np.abs(donor_material_transfer) * 28, 0, 255).astype(np.uint8), "L"),
        "donor_microtexture_transfer": Image.fromarray(np.clip(np.abs(donor_micro_transfer) * 32, 0, 255).astype(np.uint8), "L"),
        "donor_quality_transfer": Image.fromarray(np.clip(donor_quality_transfer * 18, 0, 255).astype(np.uint8), "L"),
        "donor_texture_recovery": Image.fromarray(np.clip(np.abs(texture_delta * texture_weight) * 24, 0, 255).astype(np.uint8), "L"),
        "photoreal_donor_weight": Image.fromarray(np.clip(donor_weight * 255, 0, 255).astype(np.uint8), "L"),
        "geometry_confidence": Image.fromarray(np.clip(geometry_gate * 255, 0, 255).astype(np.uint8), "L"),
        "significant_new_edge_guard": Image.fromarray(np.clip(significant_new_edges * 255, 0, 255).astype(np.uint8), "L"),
        "edge_ghost_suppression_mask": Image.fromarray(np.clip(ghost_risk * 255, 0, 255).astype(np.uint8), "L"),
    }
    donor_distance = np.abs(donor.astype(np.float32) - source_float)
    transferred_distance = np.abs(result.astype(np.float32) - source_float)
    donor_transfer_ratio = float(np.mean(transferred_distance) / max(1e-6, float(np.mean(donor_distance))))
    appearance_metrics = {
        **agreement_metrics,
        **field_metrics,
        "intensity": round(float(intensity), 4),
        "mean_illumination_transfer": round(float(np.mean(np.abs(applied_illumination))), 6),
        "mean_chroma_transfer": round(float(np.mean(np.abs(applied_chroma))), 6),
        "mean_detail_transfer": round(float(np.mean(np.abs(source_detail + source_material_depth + source_broad_depth))), 6),
        "mean_source_material_depth": round(float(np.mean(np.abs(source_material_depth))), 6),
        "mean_source_broad_depth": round(float(np.mean(np.abs(source_broad_depth))), 6),
        "mean_source_midtone_curve": round(float(np.mean(np.abs(source_midtone_curve))), 6),
        "mean_source_saturation": round(float(np.mean(np.abs(source_saturation))), 6),
        "mean_donor_contrast_gain": round(float(np.mean(contrast_gain)), 6),
        "donor_high_frequency_transfer": round(float(np.mean(np.abs(donor_material_transfer + donor_micro_transfer))), 6),
        "donor_microtexture_transfer": round(float(np.mean(np.abs(donor_micro_transfer))), 6),
        "mean_donor_quality_transfer": round(float(np.mean(donor_quality_transfer)), 6),
        "donor_quality_pixel_mix_mean": round(float(np.mean(donor_quality_weight)), 6),
        "donor_geometry_transfer": 0.0,
        "donor_material_safe_zone_fraction": round(float(np.mean(edge_safe_material_weight > 0.05)), 6),
        "photoreal_donor_mix_mean": round(float(np.mean(donor_weight)), 6),
        "donor_appearance_transfer_ratio": round(donor_transfer_ratio, 6),
        "significant_geometry_guard_fraction": round(float(np.mean(significant_new_edges)), 6),
        "semantic_fractions": {key: round(float(value.mean()), 6) for key, value in masks.items()},
        "source_structure_reinjected_fraction": round(float(np.mean(geometry_guard > 0.05)), 6),
        "edge_ghost_protected_fraction": round(float(np.mean(ghost_risk > 0.05)), 6),
        "edge_ghost_suppression_mean": round(float(np.mean(ghost_risk)), 6),
        "work_size": list(work_size),
        "requires_generative_detail_gain": True,
    }
    return target_result, artifacts, appearance_metrics


def _v867_source_evidence_transfer(
    reference: Image.Image,
    aligned_donor: Image.Image,
    valid_image: Image.Image,
    intensity: float = 1.0,
) -> tuple[Image.Image, dict[str, Image.Image], dict[str, Any]]:
    """Source-faithful super-resolution with a strictly high-frequency donor.

    The source is the final image's complete low/mid-frequency and chroma plate.
    Nano Banana Pro may contribute luminance microdetail only where the source
    already proves the same texture and the registered donor agrees in phase.
    Consequently a new sky, facade material, texture direction, object, shadow
    or illumination field has no signal path into FINAL.
    """
    work_size = aligned_donor.size
    source = np.asarray(
        reference.resize(work_size, Image.Resampling.LANCZOS).convert("RGB")
    )
    donor = np.asarray(aligned_donor.convert("RGB"))
    valid = np.asarray(valid_image.convert("L"), dtype=np.float32) / 255.0
    masks = semantic_masks(source)
    agreement, new_edge_guard, agreement_metrics = structure_agreement(source, donor)

    source_lab = cv2.cvtColor(source, cv2.COLOR_RGB2LAB).astype(np.float32)
    donor_lab = cv2.cvtColor(donor, cv2.COLOR_RGB2LAB).astype(np.float32)
    source_l = source_lab[:, :, 0]
    donor_l = donor_lab[:, :, 0]

    # Two narrow detail bands only. There is deliberately no donor colour,
    # low-frequency illumination, broad contrast or direct donor-pixel mix.
    micro_sigma = float(CFG.get("upscaler_micro_sigma", 0.68))
    fine_inner = float(CFG.get("upscaler_fine_inner_sigma", 1.25))
    fine_outer = float(CFG.get("upscaler_fine_outer_sigma", 2.8))
    source_micro = source_l - cv2.GaussianBlur(source_l, (0, 0), sigmaX=micro_sigma)
    donor_micro = donor_l - cv2.GaussianBlur(donor_l, (0, 0), sigmaX=micro_sigma)
    source_fine = cv2.GaussianBlur(source_l, (0, 0), sigmaX=fine_inner) - cv2.GaussianBlur(
        source_l, (0, 0), sigmaX=fine_outer
    )
    donor_fine = cv2.GaussianBlur(donor_l, (0, 0), sigmaX=fine_inner) - cv2.GaussianBlur(
        donor_l, (0, 0), sigmaX=fine_outer
    )

    stat_sigma = float(CFG.get("upscaler_texture_stat_sigma", 7.0))

    def local_rms(signal: np.ndarray) -> np.ndarray:
        return np.sqrt(
            np.maximum(
                0.0,
                cv2.GaussianBlur(signal * signal, (0, 0), sigmaX=stat_sigma),
            )
        )

    source_rms = local_rms(source_micro)
    donor_rms = local_rms(donor_micro)
    covariance = cv2.GaussianBlur(source_micro * donor_micro, (0, 0), sigmaX=stat_sigma)
    correlation = covariance / np.maximum(0.35, source_rms * donor_rms)
    correlation_gate = np.clip(
        (correlation - float(CFG.get("upscaler_min_texture_correlation", 0.10)))
        / float(CFG.get("upscaler_texture_correlation_fade", 0.50)),
        0.0,
        1.0,
    )
    correlation_gate = correlation_gate * correlation_gate * (3.0 - 2.0 * correlation_gate)

    # A donor texture is allowed only where texture already exists in source.
    evidence_start = float(CFG.get("upscaler_source_texture_evidence_start", 0.55))
    evidence_stop = float(CFG.get("upscaler_source_texture_evidence_stop", 2.4))
    texture_evidence = np.clip(
        (source_rms - evidence_start) / max(0.01, evidence_stop - evidence_start),
        0.0,
        1.0,
    )
    texture_evidence = texture_evidence * texture_evidence * (3.0 - 2.0 * texture_evidence)

    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    donor_gray = cv2.cvtColor(donor, cv2.COLOR_RGB2GRAY)
    source_edges = cv2.Canny(source_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    donor_edges = cv2.Canny(donor_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    combined_edges = ((source_edges > 0) | (donor_edges > 0)).astype(np.uint8)
    distance_from_edges = cv2.distanceTransform(1 - combined_edges, cv2.DIST_L2, 5)
    edge_safe = np.clip(
        (distance_from_edges - float(CFG.get("upscaler_edge_exclusion_px", 1.5)))
        / float(CFG.get("upscaler_edge_fade_px", 4.0)),
        0.0,
        1.0,
    )
    sign_agreement = cv2.GaussianBlur(
        (source_micro * donor_micro >= 0.0).astype(np.float32), (0, 0), sigmaX=0.7
    )
    semantic_strength = (
        masks["sky"] * float(CFG.get("upscaler_donor_sky_strength", 0.0))
        + masks["water"] * float(CFG.get("upscaler_donor_water_strength", 0.24))
        + masks["nature"] * float(CFG.get("upscaler_donor_nature_strength", 0.30))
        + masks["hardscape"] * float(CFG.get("upscaler_donor_hardscape_strength", 0.26))
    )
    safe_weight = np.clip(
        valid
        * np.power(np.clip(agreement, 0.0, 1.0), 4.0)
        * new_edge_guard
        * edge_safe
        * texture_evidence
        * correlation_gate
        * sign_agreement
        * semantic_strength
        * intensity,
        0.0,
        float(CFG.get("upscaler_max_donor_weight", 0.34)),
    )

    # Locally cap donor detail energy relative to proven source detail. This
    # blocks invented bricks, panel seams, grass direction and cloud texture.
    energy_cap = np.maximum(
        float(CFG.get("upscaler_absolute_detail_rms_cap", 1.2)),
        source_rms * float(CFG.get("upscaler_relative_detail_rms_cap", 1.75)),
    )
    energy_scale = np.minimum(1.0, energy_cap / np.maximum(0.01, donor_rms))
    donor_micro_residual = (donor_micro - source_micro) * energy_scale
    donor_fine_residual = (donor_fine - source_fine) * energy_scale
    donor_detail = np.clip(
        donor_micro_residual
        + donor_fine_residual * float(CFG.get("upscaler_fine_band_strength", 0.28)),
        -float(CFG.get("upscaler_max_donor_detail_delta", 5.5)),
        float(CFG.get("upscaler_max_donor_detail_delta", 5.5)),
    ) * safe_weight

    # Source-derived reconstruction is always safe and gives visible crispness
    # even when the donor is rejected everywhere by the identity masks.
    source_reconstruction = np.clip(
        source_micro * float(CFG.get("upscaler_source_micro_strength", 0.42))
        + source_fine * float(CFG.get("upscaler_source_fine_strength", 0.13)),
        -float(CFG.get("upscaler_max_source_detail_delta", 4.8)),
        float(CFG.get("upscaler_max_source_detail_delta", 4.8)),
    ) * intensity

    result_lab = source_lab.copy()
    result_lab[:, :, 0] = np.clip(source_l + source_reconstruction + donor_detail, 0.0, 255.0)
    # LAB a/b remain exactly source-owned; conversion round-off is the only
    # possible chroma difference. Broad luminance also remains source-owned.
    result = cv2.cvtColor(result_lab.astype(np.uint8), cv2.COLOR_LAB2RGB)
    target_result = Image.fromarray(result, "RGB").resize(reference.size, Image.Resampling.LANCZOS)

    donor_distance = np.abs(donor.astype(np.float32) - source.astype(np.float32))
    transferred_distance = np.abs(result.astype(np.float32) - source.astype(np.float32))
    transfer_ratio = float(
        np.mean(transferred_distance) / max(1e-6, float(np.mean(donor_distance)))
    )
    artifacts = {
        "sky_mask": Image.fromarray(np.clip(masks["sky"] * 255, 0, 255).astype(np.uint8), "L"),
        "water_mask": Image.fromarray(np.clip(masks["water"] * 255, 0, 255).astype(np.uint8), "L"),
        "nature_mask": Image.fromarray(np.clip(masks["nature"] * 255, 0, 255).astype(np.uint8), "L"),
        "hardscape_mask": Image.fromarray(np.clip(masks["hardscape"] * 255, 0, 255).astype(np.uint8), "L"),
        "structure_agreement": Image.fromarray(np.clip(agreement * 255, 0, 255).astype(np.uint8), "L"),
        "new_edge_guard": Image.fromarray(np.clip(new_edge_guard * 255, 0, 255).astype(np.uint8), "L"),
        "source_texture_evidence": Image.fromarray(np.clip(texture_evidence * 255, 0, 255).astype(np.uint8), "L"),
        "texture_phase_agreement": Image.fromarray(np.clip(correlation_gate * 255, 0, 255).astype(np.uint8), "L"),
        "donor_detail_safe_weight": Image.fromarray(np.clip(safe_weight * 255 / 0.34, 0, 255).astype(np.uint8), "L"),
        "source_detail_reconstruction": Image.fromarray(np.clip(np.abs(source_reconstruction) * 30, 0, 255).astype(np.uint8), "L"),
        "donor_microdetail_transfer": Image.fromarray(np.clip(np.abs(donor_detail) * 42, 0, 255).astype(np.uint8), "L"),
    }
    appearance_metrics = {
        **agreement_metrics,
        "intensity": round(float(intensity), 4),
        "mean_illumination_transfer": 0.0,
        "mean_chroma_transfer": 0.0,
        "mean_detail_transfer": round(float(np.mean(np.abs(source_reconstruction))), 6),
        "mean_donor_quality_transfer": round(float(np.mean(np.abs(donor_detail))), 6),
        "donor_high_frequency_transfer": round(float(np.mean(np.abs(donor_detail))), 6),
        "donor_appearance_transfer_ratio": round(transfer_ratio, 6),
        "source_texture_evidence_fraction": round(float(np.mean(texture_evidence > 0.05)), 6),
        "donor_safe_transfer_fraction": round(float(np.mean(safe_weight > 0.01)), 6),
        "mean_texture_correlation": round(float(np.mean(correlation)), 6),
        "donor_geometry_transfer": 0.0,
        "donor_chroma_transfer": 0.0,
        "donor_low_frequency_transfer": 0.0,
        "source_low_frequency_authority": 1.0,
        "source_chroma_authority": 1.0,
        "background_replacement_allowed": False,
        "material_replacement_allowed": False,
        "lighting_direction_change_allowed": False,
        "requires_generative_detail_gain": True,
        "work_size": list(work_size),
    }
    return target_result, artifacts, appearance_metrics


def separate_and_transfer(
    reference: Image.Image,
    aligned_donor: Image.Image,
    valid_image: Image.Image,
    intensity: float = 1.0,
) -> tuple[Image.Image, dict[str, Image.Image], dict[str, Any]]:
    """Carry verified donor resolution while source owns the photographed scene.

    V8.6.7 required source microtexture before donor detail could pass. A weak
    source cannot contain the detail an upscaler is meant to reconstruct, so
    that rule collapsed FINAL to a sharpened resize. V8.6.9 instead replaces
    only the narrow luminance high band with the registered, technically
    approved donor band. Source low/mid frequencies and chroma remain exact.
    Unsupported structural donor edges are still removed.
    """
    work_size = aligned_donor.size
    source = np.asarray(
        reference.resize(work_size, Image.Resampling.LANCZOS).convert("RGB")
    )
    donor = np.asarray(aligned_donor.convert("RGB"))
    valid = np.asarray(valid_image.convert("L"), dtype=np.float32) / 255.0
    masks = semantic_masks(source)
    agreement, _, agreement_metrics = structure_agreement(source, donor)

    source_lab = cv2.cvtColor(source, cv2.COLOR_RGB2LAB).astype(np.float32)
    donor_lab = cv2.cvtColor(donor, cv2.COLOR_RGB2LAB).astype(np.float32)
    source_l = source_lab[:, :, 0]
    donor_l = donor_lab[:, :, 0]
    split_sigma = float(CFG.get("carrier_high_band_split_sigma", 2.35))
    source_low = cv2.GaussianBlur(source_l, (0, 0), sigmaX=split_sigma)
    donor_low = cv2.GaussianBlur(donor_l, (0, 0), sigmaX=split_sigma)
    source_high = source_l - source_low
    donor_high = donor_l - donor_low

    # Detect only unsupported structural contours after suppressing texture.
    structural_sigma = float(CFG.get("carrier_structural_edge_sigma", 1.65))
    source_struct = cv2.GaussianBlur(source_l, (0, 0), sigmaX=structural_sigma)
    donor_struct = cv2.GaussianBlur(donor_l, (0, 0), sigmaX=structural_sigma)
    source_edges = cv2.Canny(
        np.clip(source_struct, 0, 255).astype(np.uint8),
        int(CFG["canny_low"]), int(CFG["canny_high"]),
    )
    donor_edges = cv2.Canny(
        np.clip(donor_struct, 0, 255).astype(np.uint8),
        int(CFG["canny_low"]), int(CFG["canny_high"]),
    )
    tolerance = int(CFG.get("carrier_structural_edge_tolerance_px", 4))
    support_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (tolerance * 2 + 1, tolerance * 2 + 1)
    )
    source_support = cv2.dilate(source_edges, support_kernel) > 0
    unsupported = (donor_edges > 0) & ~source_support
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        unsupported.astype(np.uint8), connectivity=8
    )
    area_limit = max(
        18,
        round(
            float(CFG.get("carrier_min_unsupported_structural_area_at_1280", 42))
            * (work_size[0] / 1280.0) ** 2
        ),
    )
    structural_rejection = np.zeros_like(unsupported, dtype=np.uint8)
    for component in range(1, count):
        if int(stats[component, cv2.CC_STAT_AREA]) >= area_limit:
            structural_rejection[labels == component] = 1
    guard_radius = int(CFG.get("carrier_structural_guard_radius_px", 7))
    guard_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (guard_radius * 2 + 1, guard_radius * 2 + 1)
    )
    structural_guard = cv2.dilate(structural_rejection, guard_kernel).astype(np.float32)
    structural_guard = cv2.GaussianBlur(
        structural_guard, (0, 0), sigmaX=float(CFG.get("carrier_guard_feather_sigma", 2.0))
    )
    structural_guard = np.clip(structural_guard, 0.0, 1.0)

    # Eligible donor detail has strong authority. Agreement is a soft weight,
    # not a requirement for source microtexture, because reconstruction must
    # create resolution that the low-quality source cannot already contain.
    semantic_strength = (
        masks["sky"] * float(CFG.get("carrier_sky_strength", 0.56))
        + masks["water"] * float(CFG.get("carrier_water_strength", 0.86))
        + masks["nature"] * float(CFG.get("carrier_nature_strength", 0.90))
        + masks["hardscape"] * float(CFG.get("carrier_hardscape_strength", 0.84))
    )
    agreement_floor = float(CFG.get("carrier_agreement_floor", 0.42))
    carrier_weight = np.clip(
        semantic_strength
        * valid
        * (agreement_floor + (1.0 - agreement_floor) * np.clip(agreement, 0.0, 1.0))
        * (1.0 - structural_guard)
        * intensity,
        0.0,
        float(CFG.get("carrier_max_weight", 0.92)),
    )
    anchor_radius = int(CFG.get("carrier_source_edge_anchor_radius_px", 2))
    anchor_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (anchor_radius * 2 + 1, anchor_radius * 2 + 1)
    )
    source_edge_anchor = cv2.dilate((source_edges > 0).astype(np.uint8), anchor_kernel).astype(np.float32)
    source_edge_anchor = cv2.GaussianBlur(
        source_edge_anchor, (0, 0), sigmaX=float(CFG.get("carrier_source_edge_anchor_feather", 0.9))
    )
    source_edge_anchor = np.clip(source_edge_anchor, 0.0, 1.0)
    edge_carrier_ceiling = float(CFG.get("carrier_source_edge_weight_ceiling", 0.28))
    carrier_weight = np.minimum(
        carrier_weight,
        1.0 - source_edge_anchor * (1.0 - edge_carrier_ceiling),
    )

    # Donor high-band amplitude is bounded, but no longer collapsed to source
    # energy. This is the actual quality carrier missing from V8.6.7.
    donor_high = np.clip(
        donor_high * float(CFG.get("carrier_donor_detail_gain", 1.08)),
        -float(CFG.get("carrier_max_high_band_delta", 18.0)),
        float(CFG.get("carrier_max_high_band_delta", 18.0)),
    )
    source_high_gain = float(CFG.get("carrier_source_high_gain", 1.10))
    blended_high = (
        source_high * source_high_gain * (1.0 - carrier_weight)
        + donor_high * carrier_weight
    )
    result_lab = source_lab.copy()
    result_lab[:, :, 0] = np.clip(source_low + blended_high, 0.0, 255.0)
    # Source a/b is unchanged: donor colour, weather and lighting cannot enter.
    result = cv2.cvtColor(result_lab.astype(np.uint8), cv2.COLOR_LAB2RGB)

    # A small local clamp prevents isolated donor speckles while retaining
    # genuinely resolved texture and vegetation detail.
    local_radius = int(CFG.get("carrier_local_source_radius", 2))
    local_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (local_radius * 2 + 1, local_radius * 2 + 1)
    )
    source_min = cv2.erode(source, local_kernel).astype(np.float32)
    source_max = cv2.dilate(source, local_kernel).astype(np.float32)
    margin = float(CFG.get("carrier_local_source_margin", 20.0))
    result = np.clip(result.astype(np.float32), source_min - margin, source_max + margin)
    result = np.clip(result, 0, 255).astype(np.uint8)
    target_result = Image.fromarray(result, "RGB").resize(reference.size, Image.Resampling.LANCZOS)

    transferred_high = blended_high - source_high
    donor_high_difference = donor_high - source_high
    effective_retention = float(
        np.mean(np.abs(transferred_high))
        / max(1e-6, float(np.mean(np.abs(donor_high_difference))))
    )
    artifacts = {
        "sky_mask": Image.fromarray(np.clip(masks["sky"] * 255, 0, 255).astype(np.uint8), "L"),
        "water_mask": Image.fromarray(np.clip(masks["water"] * 255, 0, 255).astype(np.uint8), "L"),
        "nature_mask": Image.fromarray(np.clip(masks["nature"] * 255, 0, 255).astype(np.uint8), "L"),
        "hardscape_mask": Image.fromarray(np.clip(masks["hardscape"] * 255, 0, 255).astype(np.uint8), "L"),
        "structure_agreement": Image.fromarray(np.clip(agreement * 255, 0, 255).astype(np.uint8), "L"),
        "unsupported_structural_edge_guard": Image.fromarray(np.clip(structural_guard * 255, 0, 255).astype(np.uint8), "L"),
        "source_structural_edge_anchor": Image.fromarray(np.clip(source_edge_anchor * 255, 0, 255).astype(np.uint8), "L"),
        "faithful_donor_detail_weight": Image.fromarray(np.clip(carrier_weight * 255, 0, 255).astype(np.uint8), "L"),
        "donor_high_frequency_carrier": Image.fromarray(np.clip(np.abs(donor_high) * 16, 0, 255).astype(np.uint8), "L"),
        "transferred_high_frequency": Image.fromarray(np.clip(np.abs(transferred_high) * 22, 0, 255).astype(np.uint8), "L"),
    }
    appearance_metrics = {
        **agreement_metrics,
        "intensity": round(float(intensity), 4),
        "mean_illumination_transfer": 0.0,
        "mean_chroma_transfer": 0.0,
        "mean_detail_transfer": round(float(np.mean(np.abs(blended_high))), 6),
        "mean_donor_quality_transfer": round(float(np.mean(np.abs(transferred_high))), 6),
        "donor_high_frequency_transfer": round(float(np.mean(np.abs(transferred_high))), 6),
        "donor_appearance_transfer_ratio": round(effective_retention, 6),
        "donor_high_frequency_retention": round(effective_retention, 6),
        "mean_carrier_weight": round(float(np.mean(carrier_weight)), 6),
        "carrier_active_fraction": round(float(np.mean(carrier_weight > 0.20)), 6),
        "unsupported_structural_guard_fraction": round(float(np.mean(structural_guard > 0.05)), 6),
        "donor_geometry_transfer": 0.0,
        "donor_chroma_transfer": 0.0,
        "donor_low_frequency_transfer": 0.0,
        "source_low_frequency_authority": 1.0,
        "source_chroma_authority": 1.0,
        "source_microtexture_evidence_required": False,
        "verified_donor_is_high_frequency_master": True,
        "requires_generative_detail_gain": True,
        "work_size": list(work_size),
    }
    return target_result, artifacts, appearance_metrics


def _ssim_gray(first: np.ndarray, second: np.ndarray) -> float:
    a, b = first.astype(np.float64), second.astype(np.float64)
    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)
    sigma_a = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a * mu_a
    sigma_b = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b * mu_b
    sigma_ab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_a * mu_b
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    score = ((2 * mu_a * mu_b + c1) * (2 * sigma_ab + c2)) / ((mu_a * mu_a + mu_b * mu_b + c1) * (sigma_a + sigma_b + c2))
    return float(np.mean(score))


def _parallel_edge_ghost_audit(source_edges: np.ndarray, result_edges: np.ndarray) -> tuple[np.ndarray, float]:
    strict_tolerance = max(0, int(CFG.get("validation_parallel_edge_strict_tolerance_px", 1)))
    search_radius = max(strict_tolerance + 1, int(CFG.get("validation_parallel_edge_search_radius_px", 5)))
    strict_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (strict_tolerance * 2 + 1, strict_tolerance * 2 + 1),
    )
    search_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (search_radius * 2 + 1, search_radius * 2 + 1),
    )
    strict_source_support = cv2.dilate(source_edges, strict_kernel) > 0
    nearby_source_support = cv2.dilate(source_edges, search_kernel) > 0
    ghosts = (result_edges > 0) & nearby_source_support & ~strict_source_support
    fraction = float(np.count_nonzero(ghosts) / max(1, np.count_nonzero(result_edges)))
    return ghosts, fraction


def validate_final(
    reference: Image.Image,
    final: Image.Image,
    appearance: dict[str, Any],
    registered_donor: Image.Image | None = None,
) -> tuple[dict[str, Any], Image.Image, Image.Image, Image.Image]:
    size = (int(CFG["validation_width"]), round(int(CFG["validation_width"]) * reference.height / reference.width))
    source = np.asarray(reference.resize(size, Image.Resampling.LANCZOS).convert("RGB"))
    result = np.asarray(final.resize(size, Image.Resampling.LANCZOS).convert("RGB"))
    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    result_gray = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY)
    source_edges = cv2.Canny(source_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    result_edges = cv2.Canny(result_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    final_registration = _feature_registration(source, result)
    final_camera_lock = camera_view_lock(final_registration, size[0], size[1])
    tolerance = int(CFG["validation_edge_tolerance_px"])
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (tolerance * 2 + 1, tolerance * 2 + 1))
    source_support = cv2.dilate(source_edges, kernel) > 0
    result_support = cv2.dilate(result_edges, kernel) > 0
    preservation = float(np.mean(result_support[source_edges > 0])) if np.any(source_edges > 0) else 1.0
    agreement = float(np.mean(source_support[result_edges > 0])) if np.any(result_edges > 0) else 1.0
    parallel_edge_ghosts, parallel_edge_ghost_fraction = _parallel_edge_ghost_audit(source_edges, result_edges)
    shift, response = cv2.phaseCorrelate(source_gray.astype(np.float32), result_gray.astype(np.float32))
    shift_magnitude = float(math.hypot(shift[0], shift[1]))
    low_source = cv2.GaussianBlur(source.astype(np.float32), (0, 0), sigmaX=float(CFG["validation_low_sigma"]))
    low_result = cv2.GaussianBlur(result.astype(np.float32), (0, 0), sigmaX=float(CFG["validation_low_sigma"]))
    low_rms = float(np.sqrt(np.mean((low_source - low_result) ** 2)) / 255.0)
    ssim = _ssim_gray(source_gray, result_gray)
    perceptual_change = float(np.mean(np.abs(source.astype(np.float32) - result.astype(np.float32))))
    sharpness_gain = float(np.var(cv2.Laplacian(result_gray, cv2.CV_32F)) / max(1e-6, np.var(cv2.Laplacian(source_gray, cv2.CV_32F))))
    donor_detail_fidelity = 0.0
    donor_detail_energy_ratio = 0.0
    donor_detail_evidence_pixels = 0
    if registered_donor is not None:
        donor_rgb = np.asarray(
            registered_donor.resize(size, Image.Resampling.LANCZOS).convert("RGB")
        )
        donor_gray = cv2.cvtColor(donor_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        source_gray_f = source_gray.astype(np.float32)
        result_gray_f = result_gray.astype(np.float32)
        detail_sigma = float(CFG.get("carrier_high_band_split_sigma", 2.35))
        source_detail = source_gray_f - cv2.GaussianBlur(source_gray_f, (0, 0), sigmaX=detail_sigma)
        donor_detail = donor_gray - cv2.GaussianBlur(donor_gray, (0, 0), sigmaX=detail_sigma)
        result_detail = result_gray_f - cv2.GaussianBlur(result_gray_f, (0, 0), sigmaX=detail_sigma)
        desired_detail = donor_detail - source_detail
        realized_detail = result_detail - source_detail
        structural_exclusion = cv2.dilate(
            source_edges,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
        ) > 0
        magnitude = np.abs(desired_detail)
        evidence_threshold = max(0.75, float(np.percentile(magnitude, 55.0)))
        evidence_mask = (magnitude >= evidence_threshold) & ~structural_exclusion
        donor_detail_evidence_pixels = int(np.count_nonzero(evidence_mask))
        if donor_detail_evidence_pixels >= 256:
            desired_values = desired_detail[evidence_mask].astype(np.float64)
            realized_values = realized_detail[evidence_mask].astype(np.float64)
            denominator = float(np.linalg.norm(desired_values) * np.linalg.norm(realized_values))
            if denominator > 1e-8:
                donor_detail_fidelity = float(
                    np.clip(np.dot(desired_values, realized_values) / denominator, -1.0, 1.0)
                )
            donor_detail_energy_ratio = float(
                np.mean(np.abs(realized_values)) / max(1e-6, float(np.mean(np.abs(desired_values))))
            )
    donor_detail_evidence_passed = (
        donor_detail_fidelity >= float(CFG.get("validation_min_donor_detail_fidelity", 0.32))
        and donor_detail_energy_ratio >= float(CFG.get("validation_min_donor_detail_energy_ratio", 0.18))
        and float(appearance.get("mean_carrier_weight", 0.0))
        >= float(CFG.get("validation_min_detail_carrier_weight", 0.20))
        and float(appearance.get("donor_high_frequency_retention", 0.0))
        >= float(CFG["validation_min_donor_appearance_transfer_ratio"])
    )
    appearance_gain = (
        appearance["mean_illumination_transfer"]
        + appearance["mean_chroma_transfer"]
        + appearance["mean_detail_transfer"]
        + float(appearance.get("mean_donor_quality_transfer", 0.0))
    )
    rgb_delta = np.max(np.abs(result.astype(np.int16) - source.astype(np.int16)), axis=2)
    p99_rgb_delta = float(np.percentile(rgb_delta, 99.0))
    signed_rgb_delta = result.astype(np.float32) - source.astype(np.float32)
    smooth_rgb_delta = cv2.GaussianBlur(signed_rgb_delta, (0, 0), sigmaX=3.0)
    edge_delta_residual = np.max(np.abs(signed_rgb_delta - smooth_rgb_delta), axis=2)
    wide_edge_band = cv2.dilate(
        source_edges,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
    ) > 0
    exact_edge_core = cv2.dilate(
        source_edges,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    ) > 0
    displaced_edge_ring = wide_edge_band & ~exact_edge_core
    edge_delta_values = edge_delta_residual[displaced_edge_ring]
    edge_delta_halo_threshold = float(CFG["validation_edge_delta_halo_threshold"])
    edge_delta_halo_fraction = (
        float(np.mean(edge_delta_values > edge_delta_halo_threshold))
        if edge_delta_values.size else 0.0
    )
    edge_delta_halo_p99 = float(np.percentile(edge_delta_values, 99.0)) if edge_delta_values.size else 0.0
    edge_delta_halo = displaced_edge_ring & (edge_delta_residual > edge_delta_halo_threshold)
    source_lab = cv2.cvtColor(source, cv2.COLOR_RGB2LAB).astype(np.float32)
    result_lab = cv2.cvtColor(result, cv2.COLOR_RGB2LAB).astype(np.float32)
    chroma_delta = np.linalg.norm(result_lab[:, :, 1:3] - source_lab[:, :, 1:3], axis=2)
    chroma_p99 = float(np.percentile(chroma_delta, 99.0))
    broad_source_l = cv2.GaussianBlur(source_lab[:, :, 0], (0, 0), sigmaX=12.0)
    broad_result_l = cv2.GaussianBlur(result_lab[:, :, 0], (0, 0), sigmaX=12.0)
    broad_luminance_delta_p99 = float(
        np.percentile(np.abs(broad_result_l - broad_source_l), 99.0)
    )
    source_gx = cv2.Sobel(broad_source_l, cv2.CV_32F, 1, 0, ksize=3)
    source_gy = cv2.Sobel(broad_source_l, cv2.CV_32F, 0, 1, ksize=3)
    result_gx = cv2.Sobel(broad_result_l, cv2.CV_32F, 1, 0, ksize=3)
    result_gy = cv2.Sobel(broad_result_l, cv2.CV_32F, 0, 1, ksize=3)
    direction_denominator = np.sqrt(
        (source_gx * source_gx + source_gy * source_gy)
        * (result_gx * result_gx + result_gy * result_gy)
    )
    directional_pixels = direction_denominator > 0.12
    lighting_direction_cosine = float(
        np.mean(
            (source_gx[directional_pixels] * result_gx[directional_pixels]
             + source_gy[directional_pixels] * result_gy[directional_pixels])
            / direction_denominator[directional_pixels]
        )
    ) if np.any(directional_pixels) else 1.0
    structural_sigma = float(CFG.get("carrier_structural_edge_sigma", 1.65))
    source_structural_edges = cv2.Canny(
        cv2.GaussianBlur(source_gray, (0, 0), sigmaX=structural_sigma),
        int(CFG["canny_low"]), int(CFG["canny_high"]),
    )
    result_structural_edges = cv2.Canny(
        cv2.GaussianBlur(result_gray, (0, 0), sigmaX=structural_sigma),
        int(CFG["canny_low"]), int(CFG["canny_high"]),
    )
    structural_support = cv2.dilate(
        source_structural_edges,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
    ) > 0
    new_final_edge_fraction = float(
        np.mean((result_structural_edges > 0) & ~structural_support)
    )
    edge_band = cv2.dilate(source_edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
    chroma_residual = np.abs(chroma_delta - cv2.GaussianBlur(chroma_delta, (0, 0), sigmaX=2.2))
    chroma_halo = edge_band & (chroma_residual > 13.0) & (rgb_delta > 28)
    dark_void = (result_gray < 18) & (source_gray > 45) & ((source_gray.astype(np.int16) - result_gray.astype(np.int16)) > 32)
    bright_clip_growth = (result_gray >= 252) & (source_gray < 244)
    dark_clip_growth = (result_gray <= 3) & (source_gray > 16)
    count, _, stats, _ = cv2.connectedComponentsWithStats(dark_void.astype(np.uint8), 8)
    largest_dark_void_area = int(stats[1:, cv2.CC_STAT_AREA].max()) if count > 1 else 0
    validation_masks = semantic_masks(source)
    sky_pixels = validation_masks["sky"] > float(CFG["validation_sky_mask_threshold"])
    non_sky_pixels = ~sky_pixels
    hardscape_pixels = validation_masks["hardscape"] > 0.62
    sky_p99_delta = float(np.percentile(rgb_delta[sky_pixels], 99.0)) if np.any(sky_pixels) else 0.0
    non_sky_p99_delta = float(np.percentile(rgb_delta[non_sky_pixels], 99.0)) if np.any(non_sky_pixels) else 0.0
    hardscape_p99_delta = float(np.percentile(rgb_delta[hardscape_pixels], 99.0)) if np.any(hardscape_pixels) else 0.0
    dark_void_fraction = float(np.mean(dark_void))
    bright_clip_growth_fraction = float(np.mean(bright_clip_growth))
    dark_clip_growth_fraction = float(np.mean(dark_clip_growth))
    chroma_halo_fraction = float(np.mean(chroma_halo))
    grid_cols = int(CFG["validation_grid_columns"])
    grid_rows = int(CFG["validation_grid_rows"])
    tile_reports: list[dict[str, Any]] = []
    failed_tiles = 0
    for row in range(grid_rows):
        y0, y1 = round(row * size[1] / grid_rows), round((row + 1) * size[1] / grid_rows)
        for col in range(grid_cols):
            x0, x1 = round(col * size[0] / grid_cols), round((col + 1) * size[0] / grid_cols)
            tile_dark = float(np.mean(dark_void[y0:y1, x0:x1]))
            tile_halo = float(np.mean(chroma_halo[y0:y1, x0:x1]))
            tile_clip = float(np.mean((bright_clip_growth | dark_clip_growth)[y0:y1, x0:x1]))
            tile_p99 = float(np.percentile(rgb_delta[y0:y1, x0:x1], 99.0))
            tile_sky_fraction = float(np.mean(sky_pixels[y0:y1, x0:x1]))
            tile_delta_limit = (
                float(CFG["validation_max_sky_tile_p99_rgb_delta"])
                if tile_sky_fraction >= float(CFG["validation_min_sky_tile_fraction_for_relaxed_delta"])
                else float(CFG["validation_max_tile_p99_rgb_delta"])
            )
            issues: list[str] = []
            if tile_dark > float(CFG["validation_max_tile_dark_void_fraction"]):
                issues.append("dark_void")
            if tile_halo > float(CFG["validation_max_tile_chroma_halo_fraction"]):
                issues.append("chroma_halo")
            if tile_clip > float(CFG["validation_max_tile_clip_growth_fraction"]):
                issues.append("clip_growth")
            if tile_p99 > tile_delta_limit:
                issues.append("excessive_local_change")
            failed_tiles += int(bool(issues))
            tile_reports.append({
                "column": col + 1,
                "row": row + 1,
                "issues": issues,
                "dark_void_fraction": round(tile_dark, 7),
                "chroma_halo_fraction": round(tile_halo, 7),
                "clip_growth_fraction": round(tile_clip, 7),
                "p99_rgb_delta": round(tile_p99, 4),
                "sky_fraction": round(tile_sky_fraction, 7),
                "p99_rgb_delta_limit": round(tile_delta_limit, 4),
            })
    failed_tile_fraction = failed_tiles / max(1, grid_cols * grid_rows)
    checks = {
        "exact_output_size": final.size == reference.size,
        "exact_camera_view": bool(final_camera_lock["passed"]),
        "coordinate_lock": shift_magnitude <= float(CFG["validation_max_shift_px"]),
        "source_edge_preservation": preservation >= float(CFG["validation_min_source_edge_preservation"]),
        "new_edge_control": agreement >= float(CFG["validation_min_final_edge_agreement"]),
        "no_parallel_edge_ghosting": parallel_edge_ghost_fraction
        <= float(CFG["validation_max_parallel_edge_ghost_fraction"]),
        "structural_similarity": ssim >= float(CFG["validation_min_ssim"]),
        "low_frequency_bounded": low_rms <= float(CFG["validation_max_low_rms"]),
        "source_background_and_tone_field_locked": broad_luminance_delta_p99
        <= float(CFG["validation_max_broad_luminance_delta_p99"]),
        "source_colour_field_locked": chroma_p99
        <= float(CFG["validation_max_chroma_delta_p99"]),
        "source_lighting_direction_locked": lighting_direction_cosine
        >= float(CFG["validation_min_lighting_direction_cosine"]),
        "no_new_final_contours": new_final_edge_fraction
        <= float(CFG["validation_max_new_final_edge_fraction"]),
        "appearance_change_present": perceptual_change >= float(CFG["validation_min_perceptual_change"]),
        "appearance_transfer_present": appearance_gain >= float(CFG["validation_min_appearance_gain"]),
        "generative_detail_gain_present": (
            not bool(appearance.get("requires_generative_detail_gain", False))
            or sharpness_gain >= float(CFG["validation_min_sharpness_gain"])
            or donor_detail_evidence_passed
        ),
        "detail_gain_bounded": sharpness_gain <= float(CFG["validation_max_sharpness_gain"]),
        "no_dark_void_artifacts": dark_void_fraction <= float(CFG["validation_max_dark_void_fraction"]),
        "no_bright_clip_growth": bright_clip_growth_fraction <= float(CFG["validation_max_bright_clip_growth"]),
        "no_dark_clip_growth": dark_clip_growth_fraction <= float(CFG["validation_max_dark_clip_growth"]),
        "no_chroma_halos": chroma_halo_fraction <= float(CFG["validation_max_chroma_halo_fraction"]),
        "no_edge_delta_halos": (
            edge_delta_halo_fraction <= float(CFG["validation_max_edge_delta_halo_fraction"])
            and edge_delta_halo_p99 <= float(CFG["validation_max_edge_delta_halo_p99"])
        ),
        "local_rgb_delta_bounded": (
            non_sky_p99_delta <= float(CFG["validation_max_p99_rgb_delta"])
            and sky_p99_delta <= float(CFG["validation_max_sky_p99_rgb_delta"])
        ),
        "hardscape_delta_bounded": hardscape_p99_delta <= float(CFG["validation_max_hardscape_p99_delta"]),
        "dark_void_component_bounded": largest_dark_void_area <= int(CFG["validation_max_largest_dark_void_area"]),
        "all_grid_cells_clean": failed_tile_fraction <= float(CFG["validation_max_failed_tile_fraction"]),
    }
    if "donor_appearance_transfer_ratio" in appearance:
        checks["photoreal_donor_appearance_present"] = (
            float(appearance["donor_appearance_transfer_ratio"])
            >= float(CFG["validation_min_donor_appearance_transfer_ratio"])
        )
    edge_audit = np.zeros_like(source)
    edge_audit[(source_edges > 0) & result_support] = (0, 220, 210)
    edge_audit[(source_edges > 0) & ~result_support] = (255, 35, 75)
    edge_audit[(result_edges > 0) & ~source_support] = (255, 165, 0)
    edge_audit[parallel_edge_ghosts] = (255, 0, 255)
    edge_audit[edge_delta_halo] = (0, 80, 255)
    diff = rgb_delta.astype(np.uint8)
    heatmap = cv2.applyColorMap(np.clip(diff.astype(np.float32) * 7.0, 0, 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    heatmap = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)
    defect_audit = source.copy()
    defect_audit[chroma_halo] = (0, 105, 255)
    defect_audit[edge_delta_halo] = (0, 80, 255)
    defect_audit[bright_clip_growth] = (255, 235, 0)
    defect_audit[dark_clip_growth] = (210, 0, 255)
    defect_audit[dark_void] = (255, 25, 25)
    for tile in tile_reports:
        if not tile["issues"]:
            continue
        col, row = int(tile["column"]) - 1, int(tile["row"]) - 1
        x0, x1 = round(col * size[0] / grid_cols), round((col + 1) * size[0] / grid_cols)
        y0, y1 = round(row * size[1] / grid_rows), round((row + 1) * size[1] / grid_rows)
        cv2.rectangle(defect_audit, (x0, y0), (max(x0, x1 - 1), max(y0, y1 - 1)), (255, 35, 35), 3)
        cv2.putText(defect_audit, f"{col + 1}:{row + 1}", (x0 + 5, y0 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 35, 35), 1, cv2.LINE_AA)
    validation = {
        "checks": checks,
        "passed": all(checks.values()),
        "decision": "approve" if all(checks.values()) else "review",
        "camera_view_lock": final_camera_lock,
        "coordinate_shift_px": {"x": round(float(shift[0]), 6), "y": round(float(shift[1]), 6), "magnitude": round(shift_magnitude, 6), "response": round(float(response), 6)},
        "source_edge_preservation": round(preservation, 6),
        "final_edge_agreement": round(agreement, 6),
        "parallel_edge_ghost_fraction": round(parallel_edge_ghost_fraction, 8),
        "edge_delta_halo_fraction": round(edge_delta_halo_fraction, 8),
        "edge_delta_halo_p99": round(edge_delta_halo_p99, 6),
        "ssim": round(ssim, 7),
        "low_frequency_rms": round(low_rms, 7),
        "broad_luminance_delta_p99": round(broad_luminance_delta_p99, 6),
        "chroma_delta_p99": round(chroma_p99, 6),
        "lighting_direction_cosine": round(lighting_direction_cosine, 8),
        "new_final_edge_fraction": round(new_final_edge_fraction, 8),
        "mean_perceptual_change": round(perceptual_change, 6),
        "sharpness_gain": round(sharpness_gain, 6),
        "donor_detail_fidelity": round(donor_detail_fidelity, 6),
        "donor_detail_energy_ratio": round(donor_detail_energy_ratio, 6),
        "donor_detail_evidence_pixels": donor_detail_evidence_pixels,
        "donor_detail_evidence_passed": donor_detail_evidence_passed,
        "appearance_gain_score": round(float(appearance_gain), 6),
        "p99_rgb_delta": round(p99_rgb_delta, 6),
        "non_sky_p99_rgb_delta": round(non_sky_p99_delta, 6),
        "sky_p99_rgb_delta": round(sky_p99_delta, 6),
        "hardscape_p99_delta": round(hardscape_p99_delta, 6),
        "dark_void_fraction": round(dark_void_fraction, 8),
        "bright_clip_growth_fraction": round(bright_clip_growth_fraction, 8),
        "dark_clip_growth_fraction": round(dark_clip_growth_fraction, 8),
        "chroma_halo_fraction": round(chroma_halo_fraction, 8),
        "largest_dark_void_area": largest_dark_void_area,
        "failed_tile_fraction": round(failed_tile_fraction, 8),
        "failed_tile_count": failed_tiles,
        "grid": {"columns": grid_cols, "rows": grid_rows, "cells": tile_reports},
    }
    return validation, Image.fromarray(edge_audit, "RGB"), Image.fromarray(heatmap, "RGB"), Image.fromarray(defect_audit, "RGB")


def final_release_policy(validation: dict[str, Any]) -> dict[str, Any]:
    checks = validation.get("checks", {})
    failures = sorted(name for name, passed in checks.items() if not passed)
    reviewable_soft_checks = set(CFG.get("operator_reviewable_soft_checks", []))
    soft_warnings = [name for name in failures if name in reviewable_soft_checks]
    mandatory_failures = [name for name in failures if name not in reviewable_soft_checks]
    exact_camera = bool(checks.get("exact_camera_view"))
    mandatory_safety_passed = exact_camera and not mandatory_failures
    return {
        "strictly_passed": bool(validation.get("passed")),
        "mandatory_safety_passed": mandatory_safety_passed,
        "operator_reviewable": mandatory_safety_passed and bool(soft_warnings),
        "failed_checks": failures,
        "mandatory_failures": mandatory_failures,
        "aesthetic_warnings": soft_warnings,
        "failed_tile_count": int(validation.get("failed_tile_count", 0)),
        "failed_tiles": [
            {"column": item.get("column"), "row": item.get("row"), "issues": item.get("issues", [])}
            for item in validation.get("grid", {}).get("cells", [])
            if item.get("issues")
        ][:12],
    }


def recovery_direction(candidate_results: list[dict[str, Any]]) -> str:
    """Choose the only recovery direction consistent with observed failures."""
    detail_only = any(
        set(item.get("mandatory_failures", [])) == {"generative_detail_gain_present"}
        for item in candidate_results
        if item.get("stage") == "primary"
    )
    return "increase_detail" if detail_only else "reduce_for_safety"


def comparison(reference: Image.Image, donor: Image.Image, final: Image.Image) -> Image.Image:
    width = int(CFG["comparison_panel_width"])
    height = round(width * reference.height / reference.width)
    panels = [np.asarray(item.resize((width, height), Image.Resampling.LANCZOS).convert("RGB")) for item in (reference, donor, final)]
    canvas = np.concatenate(panels, axis=1)
    labels = ["SOURCE / PIXEL MASTER", "NANO BANANA PRO / RAW", "V8 / SAFE FINAL"]
    for index, label in enumerate(labels):
        left = index * width
        cv2.rectangle(canvas, (left, 0), (left + 320, 42), (0, 0, 0), -1)
        cv2.putText(canvas, label, (left + 12, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        if index:
            cv2.line(canvas, (left, 0), (left, height - 1), (255, 255, 255), 2)
    return Image.fromarray(canvas, "RGB")


def canvas_integrity_gate(
    source: np.ndarray,
    donor: np.ndarray,
    valid: np.ndarray | None = None,
) -> dict[str, Any]:
    """Reject composited plates, invented margins and frame-within-frame output.

    A normal generative detail pass may add fine local edges. It may not change
    the broad field at the perimeter, erase source structure, or introduce long
    unsupported seams close to the canvas boundary. These tests deliberately
    operate after camera registration so a coherent image is not punished for
    sub-pixel provider alignment.
    """
    height, width = source.shape[:2]
    if donor.shape[:2] != (height, width):
        raise ValueError("Canvas integrity inputs must have identical dimensions")
    if valid is None:
        valid = np.ones((height, width), dtype=bool)
    else:
        valid = valid.astype(bool)

    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    donor_gray = cv2.cvtColor(donor, cv2.COLOR_RGB2GRAY)
    canny_low = int(CFG["canny_low"])
    canny_high = int(CFG["canny_high"])
    source_edges = cv2.Canny(source_gray, canny_low, canny_high)
    donor_edges = cv2.Canny(donor_gray, canny_low, canny_high)

    strict_support = cv2.dilate(
        donor_edges,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    ) > 0
    source_edge_pixels = source_edges > 0
    strict_source_edge_preservation = (
        float(np.mean(strict_support[source_edge_pixels & valid]))
        if np.any(source_edge_pixels & valid) else 1.0
    )

    edge_density = cv2.GaussianBlur(
        source_edge_pixels.astype(np.float32), (0, 0), sigmaX=max(3.0, width / 320.0)
    )
    density_samples = edge_density[source_edge_pixels & valid]
    density_threshold = float(np.percentile(density_samples, 68.0)) if density_samples.size else 1.0
    critical_edges = source_edge_pixels & (edge_density >= density_threshold) & valid
    critical_edge_preservation = (
        float(np.mean(strict_support[critical_edges])) if np.any(critical_edges) else 1.0
    )

    border_fraction = float(CFG.get("canvas_integrity_border_fraction", 0.12))
    yy, xx = np.ogrid[:height, :width]
    perimeter = (
        (xx < width * border_fraction)
        | (xx >= width * (1.0 - border_fraction))
        | (yy < height * border_fraction)
        | (yy >= height * (1.0 - border_fraction))
    ) & valid
    low_sigma = max(4.0, width / 180.0)
    source_low = cv2.GaussianBlur(source.astype(np.float32), (0, 0), sigmaX=low_sigma)
    donor_low = cv2.GaussianBlur(donor.astype(np.float32), (0, 0), sigmaX=low_sigma)
    low_delta = np.mean(np.abs(donor_low - source_low), axis=2)
    border_values = low_delta[perimeter]
    border_low_delta_p95 = float(np.percentile(border_values, 95.0)) if border_values.size else 255.0
    border_delta_threshold = float(CFG.get("canvas_integrity_border_delta_threshold", 32.0))
    border_low_delta_fraction = (
        float(np.mean(border_values > border_delta_threshold)) if border_values.size else 1.0
    )

    source_loose_support = cv2.dilate(
        source_edges,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
    ) > 0
    # Subpixel registration can expose black pixels at the outermost border
    # while the nearest-neighbour validity mask still considers them valid.
    # Inset seams remain visible to this gate more than two pixels inside.
    line_valid = valid.copy()
    outer_guard = max(2, round(min(width, height) * 0.003))
    line_valid[:outer_guard, :] = False
    line_valid[-outer_guard:, :] = False
    line_valid[:, :outer_guard] = False
    line_valid[:, -outer_guard:] = False
    unsupported = ((donor_edges > 0) & ~source_loose_support & line_valid).astype(np.uint8) * 255
    minimum_line = max(30, round(min(width, height) * 0.15))
    lines = cv2.HoughLinesP(
        unsupported,
        1,
        np.pi / 360.0,
        threshold=max(24, round(minimum_line * 0.20)),
        minLineLength=minimum_line,
        maxLineGap=max(8, round(min(width, height) * 0.018)),
    )
    border_lines: list[dict[str, Any]] = []
    left_side = False
    right_side = False
    if lines is not None:
        for values in lines[:, 0]:
            x1, y1, x2, y2 = (int(value) for value in values)
            length = float(math.hypot(x2 - x1, y2 - y1))
            midpoint_x = (x1 + x2) * 0.5
            midpoint_y = (y1 + y2) * 0.5
            close_to_border = (
                midpoint_x < width * 0.20
                or midpoint_x > width * 0.80
                or midpoint_y < height * 0.16
                or midpoint_y > height * 0.84
            )
            if not close_to_border:
                continue
            verticalish = abs(y2 - y1) >= abs(x2 - x1) * 1.45
            if verticalish and midpoint_x < width * 0.20:
                left_side = True
            if verticalish and midpoint_x > width * 0.80:
                right_side = True
            border_lines.append({
                "points": [x1, y1, x2, y2],
                "length_fraction": round(length / max(1.0, float(min(width, height))), 6),
                "verticalish": verticalish,
            })
    max_unsupported_border_line_fraction = max(
        (float(item["length_fraction"]) for item in border_lines), default=0.0
    )
    paired_inset_side_seams = left_side and right_side

    checks = {
        "strict_source_edge_preservation": strict_source_edge_preservation
        >= float(CFG.get("canvas_integrity_min_strict_source_edge_preservation", 0.70)),
        "critical_structure_preservation": critical_edge_preservation
        >= float(CFG.get("canvas_integrity_min_critical_edge_preservation", 0.66)),
        "perimeter_low_frequency_continuity": (
            border_low_delta_p95
            <= float(CFG.get("canvas_integrity_max_border_low_delta_p95", 32.0))
            and border_low_delta_fraction
            <= float(CFG.get("canvas_integrity_max_border_low_delta_fraction", 0.08))
        ),
        "no_unsupported_long_border_seam": max_unsupported_border_line_fraction
        <= float(CFG.get("canvas_integrity_max_unsupported_border_line_fraction", 0.26)),
        "no_paired_inset_side_seams": not paired_inset_side_seams,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": {
            "strict_source_edge_preservation": round(strict_source_edge_preservation, 6),
            "critical_edge_preservation": round(critical_edge_preservation, 6),
            "border_low_delta_p95": round(border_low_delta_p95, 6),
            "border_low_delta_fraction": round(border_low_delta_fraction, 6),
            "border_delta_threshold": border_delta_threshold,
            "max_unsupported_border_line_fraction": round(max_unsupported_border_line_fraction, 6),
            "unsupported_border_line_count": len(border_lines),
            "paired_inset_side_seams": paired_inset_side_seams,
        },
        "unsupported_border_lines": border_lines,
    }


def _source_sky_recovery_mask(reference: Image.Image) -> tuple[Image.Image, np.ndarray, dict[str, Any]]:
    """Find only source sky connected to the image's upper edge."""
    width = int(CFG["whole_scene_preview_width"])
    height = max(1, round(width * reference.height / reference.width))
    source = np.asarray(reference.convert("RGB").resize((width, height), Image.Resampling.LANCZOS))
    hsv = cv2.cvtColor(source, cv2.COLOR_RGB2HSV)
    yy = np.arange(height, dtype=np.int32)[:, None]
    blue = ((hsv[:, :, 0] >= 82) & (hsv[:, :, 0] <= 132)
            & (hsv[:, :, 1] >= 30) & (hsv[:, :, 2] >= 88))
    cloud = (hsv[:, :, 1] < 88) & (hsv[:, :, 2] > 120) & (yy < round(height * 0.52))
    candidate = (blue | cloud) & (yy < round(height * 0.55))
    _, labels, _, _ = cv2.connectedComponentsWithStats(candidate.astype(np.uint8), 8)
    top_labels = np.unique(labels[0, :]); top_labels = top_labels[top_labels != 0]
    if not len(top_labels):
        raise ValueError("Source sky cannot be established from the upper edge")
    sky = np.isin(labels, top_labels).astype(np.uint8)
    filled = sky.copy()
    cv2.floodFill(filled, np.zeros((height + 2, width + 2), np.uint8), (0, height - 1), 1)
    sky = ((sky > 0) | ((filled == 0) & (yy < round(height * 0.53)))).astype(np.uint8)
    sky = cv2.erode(sky, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    fraction = float(np.mean(sky > 0))
    if not 0.07 <= fraction <= 0.55:
        raise ValueError("Source sky mask has implausible coverage")
    feather = np.clip(cv2.GaussianBlur(sky.astype(np.float32), (0, 0), sigmaX=2.8), 0.0, 1.0)
    return Image.fromarray(np.uint8(np.round(feather * 255)), "L"), sky, {
        "mask_fraction": round(fraction, 6), "mask_preview_size": [width, height],
    }


def _source_sky_recovery_eligible(reference: Image.Image, gate: dict[str, Any]) -> bool:
    """Recover only sky contour loss plus excess perimeter tone change."""
    failed = {name for name, passed in gate.get("checks", {}).items() if not passed}
    if gate["passed"] or not gate.get("camera_view_lock", {}).get("passed") or failed != {
        "canvas_integrity.perimeter_low_frequency_continuity", "artifact_control.corrupt_grid_cells"
    }:
        return False
    try:
        _, sky, _ = _source_sky_recovery_mask(reference)
    except ValueError:
        return False
    rows, cols = int(gate["grid"]["rows"]), int(gate["grid"]["columns"])
    height, width = sky.shape
    broken = [cell for cell in gate["grid"]["cells"] if cell["issues"]]
    if not broken:
        return False
    for cell in broken:
        if cell["issues"] != ["structure_loss"]:
            return False
        col, row = int(cell["column"]) - 1, int(cell["row"]) - 1
        x0, x1 = round(col * width / cols), round((col + 1) * width / cols)
        y0, y1 = round(row * height / rows), round((row + 1) * height / rows)
        if float(np.mean(sky[y0:y1, x0:x1])) < 0.80:
            return False
    return True


def recover_original_source_sky(reference: Image.Image, donor: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    """Keep donor detail at native resolution outside source-authored sky."""
    mask_preview, _, metrics = _source_sky_recovery_mask(reference)
    mask = mask_preview.resize(donor.size, Image.Resampling.BILINEAR)
    source_at_donor_size = reference.convert("RGB").resize(donor.size, Image.Resampling.LANCZOS)
    recovered = Image.composite(source_at_donor_size, donor.convert("RGB"), mask)
    return recovered, {**metrics, "source_sky_is_authoritative": True,
        "original_donor_untouched": True, "donor_resolution_preserved": list(donor.size),
        "model_api_requests": 0}


def whole_scene_quality_gate(reference: Image.Image, donor: Image.Image) -> tuple[dict[str, Any], Image.Image]:
    """Technical review of the complete RAW donor, never of one privileged region.

    The gate checks every grid cell plus global geometry, exposure, continuity and
    artifact metrics.  It intentionally does not claim aesthetic or semantic human
    judgment: running stage 02 is the operator's approval of the review board.
    """
    preview_w = int(CFG["whole_scene_preview_width"])
    preview_h = round(preview_w * reference.height / reference.width)
    size = (preview_w, preview_h)
    source = np.asarray(reference.convert("RGB").resize(size, Image.Resampling.LANCZOS))
    candidate = np.asarray(donor.convert("RGB").resize(size, Image.Resampling.LANCZOS))
    aspect_deviation = abs((donor.width / donor.height) - (reference.width / reference.height)) / max(
        1e-6, reference.width / reference.height
    )

    registration: dict[str, Any]
    registration_ok = False
    try:
        registration = _feature_registration(source, candidate)
        registration_ok = bool(registration.get("passed"))
        if registration_ok:
            homography = np.asarray(registration["homography_donor_to_source"], dtype=np.float64)
            aligned = cv2.warpPerspective(candidate, homography, size, flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
            valid = cv2.warpPerspective(np.full((preview_h, preview_w), 255, dtype=np.uint8), homography, size, flags=cv2.INTER_NEAREST) > 0
        else:
            aligned = candidate
            valid = np.zeros((preview_h, preview_w), dtype=bool)
    except Exception as exc:
        registration_ok = False
        registration = {"error": str(exc), "inlier_ratio": 0.0, "inliers": 0, "matches": 0}
        aligned = candidate
        valid = np.zeros((preview_h, preview_w), dtype=bool)
    camera_lock = camera_view_lock(registration, preview_w, preview_h)

    source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    donor_gray = cv2.cvtColor(aligned, cv2.COLOR_RGB2GRAY)
    source_gray_float = source_gray.astype(np.float32)
    source_edges = cv2.Canny(source_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    donor_edges = cv2.Canny(donor_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    source_support = cv2.dilate(source_edges, kernel) > 0
    donor_support = cv2.dilate(donor_edges, kernel) > 0
    source_edge_preservation = float(np.mean(donor_support[source_edges > 0])) if np.any(source_edges > 0) else 1.0
    donor_edge_agreement = float(np.mean(source_support[donor_edges > 0])) if np.any(donor_edges > 0) else 1.0
    masks = semantic_masks(source)
    hardscape_weight = masks["hardscape"]
    hardscape = hardscape_weight > 0.62
    hardscape_edges = (source_edges > 0) & hardscape
    hardscape_edge_preservation = float(np.mean(donor_support[hardscape_edges])) if np.any(hardscape_edges) else 1.0
    valid_coverage = float(np.mean(valid))
    canvas_integrity = canvas_integrity_gate(source, aligned, valid)

    donor_float = aligned.astype(np.float32)
    donor_gray_float = donor_gray.astype(np.float32)
    luminance_p01, luminance_p99 = np.percentile(donor_gray_float[valid] if np.any(valid) else donor_gray_float, [1.0, 99.0])
    dynamic_range = float(luminance_p99 - luminance_p01)
    clip_fraction = float(np.mean((donor_gray <= 3) | (donor_gray >= 252)))
    hsv = cv2.cvtColor(aligned, cv2.COLOR_RGB2HSV)
    source_hsv = cv2.cvtColor(source, cv2.COLOR_RGB2HSV)
    extreme_saturation_fraction = float(np.mean((hsv[:, :, 1] >= 245) & (hsv[:, :, 2] >= 45)))
    source_sharpness = float(np.var(cv2.Laplacian(source_gray, cv2.CV_32F)))
    donor_sharpness = float(np.var(cv2.Laplacian(donor_gray, cv2.CV_32F)))
    sharpness_ratio = donor_sharpness / max(1e-6, source_sharpness)
    valid_pixels = valid[:, :, None]
    generator_mean_rgb_delta = float(
        np.mean(np.abs(donor_float - source.astype(np.float32))[np.broadcast_to(valid_pixels, source.shape)])
    ) if np.any(valid) else 0.0

    sigma = max(5.0, preview_w / 110.0)
    source_low = cv2.GaussianBlur(source_gray_float, (0, 0), sigmaX=sigma)
    donor_low = cv2.GaussianBlur(donor_gray_float, (0, 0), sigmaX=sigma)
    illumination_delta = donor_low - source_low
    row_energy = np.mean(np.abs(np.diff(illumination_delta, axis=0)), axis=1)
    col_energy = np.mean(np.abs(np.diff(illumination_delta, axis=1)), axis=0)
    seam_energy = np.concatenate([row_energy, col_energy])
    new_seam_ratio = float(np.max(seam_energy) / max(0.75, float(np.percentile(seam_energy, 85.0)))) if seam_energy.size else 0.0

    # Material identity is measured at a mid-frequency scale: fine pores and
    # physically credible micro-roughness may improve, while newly invented
    # brick joints, stone veins, panel seams or relief patterns are blocked.
    material_inner_sigma = max(0.8, preview_w / 1600.0)
    material_outer_sigma = max(4.0, preview_w / 240.0)
    source_midband = np.abs(
        cv2.GaussianBlur(source_gray_float, (0, 0), sigmaX=material_inner_sigma)
        - cv2.GaussianBlur(source_gray_float, (0, 0), sigmaX=material_outer_sigma)
    )
    donor_midband = np.abs(
        cv2.GaussianBlur(donor_gray_float, (0, 0), sigmaX=material_inner_sigma)
        - cv2.GaussianBlur(donor_gray_float, (0, 0), sigmaX=material_outer_sigma)
    )
    new_material_edges = (donor_edges > 0) & ~source_support

    grid_cols = int(CFG["whole_scene_grid_columns"])
    grid_rows = int(CFG["whole_scene_grid_rows"])
    cell_reports: list[dict[str, Any]] = []
    corrupt_cells = 0
    flat_collapse_cells = 0
    material_cells_evaluated = 0
    retextured_material_cells = 0
    recolored_material_cells = 0
    audit = aligned.copy()
    for seam in canvas_integrity["unsupported_border_lines"]:
        x1, y1, x2, y2 = seam["points"]
        cv2.line(audit, (x1, y1), (x2, y2), (255, 35, 35), 4, cv2.LINE_AA)
    for row in range(grid_rows):
        y0, y1 = round(row * preview_h / grid_rows), round((row + 1) * preview_h / grid_rows)
        for col in range(grid_cols):
            x0, x1 = round(col * preview_w / grid_cols), round((col + 1) * preview_w / grid_cols)
            s = source_gray[y0:y1, x0:x1]
            d = donor_gray[y0:y1, x0:x1]
            v = valid[y0:y1, x0:x1]
            local_hsv = hsv[y0:y1, x0:x1]
            local_source_edges = source_edges[y0:y1, x0:x1]
            local_donor_support = donor_support[y0:y1, x0:x1]
            local_hardscape = hardscape[y0:y1, x0:x1] & v
            local_hardscape_fraction = float(np.mean(local_hardscape))
            local_valid = float(np.mean(v))
            local_clip = float(np.mean((d <= 3) | (d >= 252)))
            local_saturation = float(np.mean((local_hsv[:, :, 1] >= 245) & (local_hsv[:, :, 2] >= 45)))
            source_std, donor_std = float(np.std(s)), float(np.std(d))
            source_lap = float(np.var(cv2.Laplacian(s, cv2.CV_32F)))
            donor_lap = float(np.var(cv2.Laplacian(d, cv2.CV_32F)))
            source_gx = float(np.mean(np.abs(np.diff(s.astype(np.float32), axis=1))))
            source_gy = float(np.mean(np.abs(np.diff(s.astype(np.float32), axis=0))))
            donor_gx = float(np.mean(np.abs(np.diff(d.astype(np.float32), axis=1))))
            donor_gy = float(np.mean(np.abs(np.diff(d.astype(np.float32), axis=0))))
            source_directional_bias = source_gx / max(0.05, source_gy)
            donor_directional_bias = donor_gx / max(0.05, donor_gy)
            directional_bias_growth = donor_directional_bias / max(0.05, source_directional_bias)
            source_directional_energy = 0.5 * (source_gx + source_gy)
            local_edge_preservation = (
                float(np.mean(local_donor_support[local_source_edges > 0])) if np.any(local_source_edges > 0) else 1.0
            )
            flat_collapse = source_std > 9.0 and donor_std < 5.0 and donor_std < source_std * 0.22
            blur_collapse = source_lap > 18.0 and donor_lap < source_lap * 0.06
            source_material_midband = 0.0
            donor_material_midband = 0.0
            material_midband_growth = 1.0
            new_material_edge_fraction = 0.0
            source_material_saturation = 0.0
            donor_material_saturation = 0.0
            smooth_sample_fraction = 0.0
            material_texture_substitution = False
            material_colour_substitution = False
            if (
                bool(CFG.get("material_identity_lock_enabled", True))
                and local_hardscape_fraction
                >= float(CFG["material_identity_min_hardscape_cell_fraction"])
                and np.any(local_hardscape)
            ):
                local_source_midband = source_midband[y0:y1, x0:x1]
                local_donor_midband = donor_midband[y0:y1, x0:x1]
                local_new_material_edges = new_material_edges[y0:y1, x0:x1]
                local_source_saturation = source_hsv[y0:y1, x0:x1, 1]
                local_donor_saturation = hsv[y0:y1, x0:x1, 1]
                smooth_sample = local_hardscape & (
                    local_source_midband
                    <= float(CFG["material_identity_source_smooth_midband_max"])
                )
                smooth_sample_fraction = float(np.count_nonzero(smooth_sample)) / max(
                    1, int(np.count_nonzero(local_hardscape))
                )
                if smooth_sample_fraction >= float(CFG["material_identity_min_smooth_sample_fraction"]):
                    material_cells_evaluated += 1
                    source_material_midband = float(np.mean(local_source_midband[smooth_sample]))
                    donor_material_midband = float(np.mean(local_donor_midband[smooth_sample]))
                    material_midband_growth = donor_material_midband / max(0.35, source_material_midband)
                    new_material_edge_fraction = float(np.mean(local_new_material_edges[smooth_sample]))
                    source_material_saturation = float(np.median(local_source_saturation[smooth_sample]))
                    donor_material_saturation = float(np.median(local_donor_saturation[smooth_sample]))
                    smooth_surface_retextured = (
                        donor_material_midband
                        >= float(CFG["material_identity_donor_midband_min"])
                        and material_midband_growth
                        >= float(CFG["material_identity_max_midband_growth"])
                        and new_material_edge_fraction
                        >= float(CFG["material_identity_min_new_edge_fraction"])
                    )
                    aggressive_new_pattern = (
                        donor_material_midband >= max(12.0, source_material_midband * 3.2)
                        and new_material_edge_fraction
                        >= max(0.06, float(CFG["material_identity_min_new_edge_fraction"]))
                    )
                    material_texture_substitution = smooth_surface_retextured or aggressive_new_pattern
                    material_colour_substitution = (
                        source_material_saturation <= 48.0
                        and donor_material_saturation - source_material_saturation
                        > float(CFG["material_identity_max_saturation_growth"])
                    )
                    retextured_material_cells += int(material_texture_substitution)
                    recolored_material_cells += int(material_colour_substitution)
            issues: list[str] = []
            if local_valid < 0.90:
                issues.append("incomplete")
            if local_clip > 0.22:
                issues.append("clipping")
            if local_saturation > 0.24:
                issues.append("extreme_chroma")
            if flat_collapse:
                issues.append("flat_collapse")
            if blur_collapse:
                issues.append("blur_collapse")
            if (
                source_directional_energy > float(CFG["whole_scene_min_source_directional_energy"])
                and
                donor_directional_bias > float(CFG["whole_scene_min_directional_bias_for_flag"])
                and directional_bias_growth > float(CFG["whole_scene_max_directional_bias_growth"])
            ):
                issues.append("directional_streaks")
            if (
                local_edge_preservation
                < float(CFG.get("whole_scene_cell_min_source_edge_preservation", 0.58))
                and int(np.count_nonzero(local_source_edges)) > 45
            ):
                issues.append("structure_loss")
            if abs(float(np.mean(d)) - float(np.mean(s))) > 92.0:
                issues.append("tone_discontinuity")
            if material_texture_substitution:
                issues.append("material_texture_substitution")
            if material_colour_substitution:
                issues.append("material_colour_family_substitution")
            corrupt = bool(issues)
            corrupt_cells += int(corrupt)
            flat_collapse_cells += int(flat_collapse or blur_collapse)
            color = (255, 45, 45) if corrupt else (0, 210, 130)
            cv2.rectangle(audit, (x0, y0), (max(x0, x1 - 1), max(y0, y1 - 1)), color, 2 if corrupt else 1)
            if corrupt:
                cv2.putText(audit, f"{col + 1}:{row + 1}", (x0 + 5, y0 + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)
            cell_reports.append({
                "column": col + 1,
                "row": row + 1,
                "issues": issues,
                "valid_coverage": round(local_valid, 5),
                "clip_fraction": round(local_clip, 5),
                "extreme_saturation_fraction": round(local_saturation, 5),
                "source_edge_preservation": round(local_edge_preservation, 5),
                "source_std": round(source_std, 4),
                "donor_std": round(donor_std, 4),
                "source_directional_bias": round(source_directional_bias, 4),
                "donor_directional_bias": round(donor_directional_bias, 4),
                "directional_bias_growth": round(directional_bias_growth, 4),
                "source_directional_energy": round(source_directional_energy, 4),
                "hardscape_fraction": round(local_hardscape_fraction, 5),
                "source_material_midband": round(source_material_midband, 5),
                "donor_material_midband": round(donor_material_midband, 5),
                "material_midband_growth": round(material_midband_growth, 5),
                "new_material_edge_fraction": round(new_material_edge_fraction, 5),
                "source_material_saturation": round(source_material_saturation, 4),
                "donor_material_saturation": round(donor_material_saturation, 4),
                "smooth_material_sample_fraction": round(smooth_sample_fraction, 5),
            })

    total_cells = max(1, grid_cols * grid_rows)
    corrupt_cell_fraction = corrupt_cells / total_cells
    flat_collapse_cell_fraction = flat_collapse_cells / total_cells
    retextured_material_cell_fraction = (
        retextured_material_cells / max(1, material_cells_evaluated)
    )
    recolored_material_cell_fraction = (
        recolored_material_cells / max(1, material_cells_evaluated)
    )
    groups = {
        "geometry_and_identity": {
            "feature_registration": registration_ok,
            "exact_camera_view": bool(camera_lock["passed"]),
            "source_edge_preservation": source_edge_preservation >= float(CFG["whole_scene_min_source_edge_preservation"]),
            "donor_edge_agreement": donor_edge_agreement >= float(CFG["whole_scene_min_donor_edge_agreement"]),
            "hardscape_edge_preservation": hardscape_edge_preservation >= float(CFG["whole_scene_min_hardscape_edge_preservation"]),
        },
        "frame_completeness": {
            "aspect_ratio": aspect_deviation <= float(CFG["max_source_aspect_deviation"]),
            "registered_coverage": valid_coverage >= float(CFG["whole_scene_min_valid_coverage"]),
            "minimum_resolution": donor.width >= 1024 and donor.height >= 576,
        },
        "canvas_integrity": dict(canvas_integrity["checks"]),
        "photographic_quality": {
            "generator_effect_present": generator_mean_rgb_delta >= float(CFG["whole_scene_min_generator_mean_rgb_delta"]),
            "dynamic_range": dynamic_range >= float(CFG["whole_scene_min_dynamic_range"]),
            "global_clipping": clip_fraction <= float(CFG["whole_scene_max_clip_fraction"]),
            "sharpness": sharpness_ratio >= float(CFG["whole_scene_min_sharpness_ratio"]),
        },
        "physical_and_tonal_coherence": {
            "no_new_full_frame_seam": new_seam_ratio <= float(CFG["whole_scene_max_new_seam_ratio"]),
            "no_flat_or_blurred_zone_collapse": flat_collapse_cell_fraction <= float(CFG["whole_scene_max_flat_collapse_cell_fraction"]),
        },
        "material_identity": {
            "no_material_texture_substitution": retextured_material_cell_fraction
            <= float(CFG["material_identity_max_retextured_cell_fraction"]),
            "no_material_colour_family_substitution": recolored_material_cell_fraction
            <= float(CFG["material_identity_max_recolored_cell_fraction"]),
        },
        "artifact_control": {
            "extreme_saturation": extreme_saturation_fraction <= float(CFG["whole_scene_max_extreme_saturation_fraction"]),
            "corrupt_grid_cells": corrupt_cell_fraction <= float(CFG["whole_scene_max_corrupt_cell_fraction"]),
        },
    }
    checks = {f"{group}.{name}": passed for group, values in groups.items() for name, passed in values.items()}
    passed = all(checks.values())
    decision = "DONOR_READY_FOR_WHOLE_SCENE_REVIEW" if passed else (
        "REJECTED_CAMERA_VIEW" if not camera_lock["passed"] else (
            "REJECTED_NO_GENERATOR_EFFECT"
            if not groups["photographic_quality"]["generator_effect_present"]
            else (
                "REJECTED_CANVAS_INTEGRITY"
                if not all(groups["canvas_integrity"].values())
                else (
                    "REJECTED_MATERIAL_IDENTITY"
                    if not all(groups["material_identity"].values())
                    else "REJECTED_WHOLE_SCENE"
                )
            )
        )
    )
    category_scores = {
        group: round(100.0 * sum(bool(value) for value in values.values()) / max(1, len(values)), 1)
        for group, values in groups.items()
    }
    header_color = (0, 210, 130) if passed else (255, 45, 45)
    overlay = audit.copy()
    cv2.rectangle(overlay, (0, 0), (preview_w, 78), (0, 0, 0), -1)
    audit = cv2.addWeighted(overlay, 0.74, audit, 0.26, 0)
    cv2.putText(audit, "WHOLE SCENE TECHNICAL REVIEW", (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.78, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(
        audit,
        ("READY FOR VISUAL REVIEW" if passed else (
            "BLOCKED: CAMERA VIEW CHANGED" if decision == "REJECTED_CAMERA_VIEW" else (
                "BLOCKED: GENERATOR RETURNED NO VISIBLE UPGRADE"
                if decision == "REJECTED_NO_GENERATOR_EFFECT" else (
                    "BLOCKED: COMPOSITED OR BROKEN CANVAS"
                    if decision == "REJECTED_CANVAS_INTEGRITY"
                    else (
                        "BLOCKED: SOURCE MATERIAL OR TEXTURE CHANGED"
                        if decision == "REJECTED_MATERIAL_IDENTITY"
                        else "BLOCKED: REVIEW RED CELLS"
                    )
                )
            )
        )),
        (18, 62),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.70,
        header_color,
        2,
        cv2.LINE_AA,
    )
    result = {
        "passed": passed,
        "decision": decision,
        "checks": checks,
        "category_scores": category_scores,
        "metrics": {
            "aspect_deviation": round(aspect_deviation, 6),
            "valid_coverage": round(valid_coverage, 6),
            "source_edge_preservation": round(source_edge_preservation, 6),
            "donor_edge_agreement": round(donor_edge_agreement, 6),
            "hardscape_edge_preservation": round(hardscape_edge_preservation, 6),
            "dynamic_range": round(dynamic_range, 4),
            "clip_fraction": round(clip_fraction, 6),
            "extreme_saturation_fraction": round(extreme_saturation_fraction, 6),
            "sharpness_ratio": round(sharpness_ratio, 6),
            "generator_mean_rgb_delta": round(generator_mean_rgb_delta, 6),
            "generator_mean_rgb_delta_minimum": float(CFG["whole_scene_min_generator_mean_rgb_delta"]),
            "new_seam_ratio": round(new_seam_ratio, 6),
            "corrupt_cell_fraction": round(corrupt_cell_fraction, 6),
            "flat_collapse_cell_fraction": round(flat_collapse_cell_fraction, 6),
            "material_cells_evaluated": material_cells_evaluated,
            "retextured_material_cells": retextured_material_cells,
            "retextured_material_cell_fraction": round(retextured_material_cell_fraction, 6),
            "recolored_material_cells": recolored_material_cells,
            "recolored_material_cell_fraction": round(recolored_material_cell_fraction, 6),
            **canvas_integrity["metrics"],
        },
        "registration": registration,
        "camera_view_lock": camera_lock,
        "grid": {"columns": grid_cols, "rows": grid_rows, "cells": cell_reports},
        "material_identity_contract": {
            "source_material_category_is_authoritative": True,
            "source_surface_finish_is_authoritative": True,
            "new_facade_texture_patterns_allowed": False,
            "microdetail_improvement_without_material_reclassification_allowed": True,
        },
        "canvas_integrity_contract": {
            "frame_within_frame_allowed": False,
            "invented_side_margins_allowed": False,
            "duplicated_or_reflected_edge_strips_allowed": False,
            "existing_text_and_signage_must_keep_source_shapes": True,
        },
        "human_review_contract": (
            "Automated checks cover the full frame but do not replace art-direction judgment. "
            "Running stage 02 confirms visual review of architecture, materials, landscape, objects, light and atmosphere."
        ),
    }
    return result, Image.fromarray(audit, "RGB")


def whole_scene_review_board(reference: Image.Image, donor: Image.Image, audit: Image.Image) -> Image.Image:
    width = int(CFG["comparison_panel_width"])
    height = round(width * reference.height / reference.width)
    panels = [np.asarray(item.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)) for item in (reference, donor, audit)]
    canvas = np.concatenate(panels, axis=1)
    labels = ["SOURCE / TRUTH", "RAW DONOR / REVIEW ALL", "AUTOMATED WHOLE-SCENE AUDIT"]
    for index, label in enumerate(labels):
        left = index * width
        cv2.rectangle(canvas, (left, 0), (left + 390, 42), (0, 0, 0), -1)
        cv2.putText(canvas, label, (left + 12, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
        if index:
            cv2.line(canvas, (left, 0), (left, height - 1), (255, 255, 255), 2)
    return Image.fromarray(canvas, "RGB")


def _process_impl(
    source_path: Path,
    key: str | None = None,
    force_generation: bool = False,
    force_processing: bool = False,
    allow_generation: bool = False,
    donor_only: bool = False,
) -> dict[str, Any]:
    sha256 = file_sha256(source_path)
    final_path = out_path(FINAL_FOLDER, source_path, sha256, "STRICT_GENERATIVE_FINAL")
    review_path = out_path(REVIEW_FOLDER, source_path, sha256, "STRICT_GENERATIVE_REVIEW")
    report_path = out_path(REPORT_FOLDER, source_path, sha256, "FINAL_REPORT", ".json")
    if final_path.exists() and not force_generation and not force_processing:
        return {"file": str(source_path), "status": "skipped", "output": str(final_path), "report": str(report_path)}

    save_state(source_path, sha256, "reference", "running")
    with Image.open(source_path) as raw:
        reference, framing = prepare_reference(raw)
    reference_path = out_path(REFERENCE_FOLDER, source_path, sha256, "SOURCE_REFERENCE_UHD4K")
    atomic_save_png(reference, reference_path)
    control, structure_guard, control_metrics = build_structure_control(reference)
    control_path = out_path(CONTROL_FOLDER, source_path, sha256, "SOURCE_STRUCTURE_CONTROL")
    structure_guard_path = out_path(CONTROL_FOLDER, source_path, sha256, "SOURCE_STRUCTURE_GUARD")
    atomic_save_png(control, control_path)
    atomic_save_png(structure_guard, structure_guard_path)

    raw_path = out_path(RAW_FOLDER, source_path, sha256, "NANO_BANANA_PRO_APPEARANCE_DONOR")
    resumed = valid_image_checkpoint(raw_path) and not force_generation
    recovered_from: str | None = None
    checkpoint_match: dict[str, Any] = {
        "method": "current_v8_4_exact_checkpoint" if resumed else "not_checked",
        "legacy_donor_search_enabled": False,
    }
    if not resumed and not force_generation:
        saved_path, checkpoint_match = compatible_current_v84_donor(source_path, sha256, reference)
        if saved_path is not None:
            logging.info(
                "CHECKPOINT | Recovering exact-SHA camera-locked RAW (%s): %s",
                checkpoint_match["method"],
                saved_path,
            )
            with Image.open(saved_path) as recovered_raw:
                atomic_save_png(recovered_raw.convert("RGB"), raw_path)
            resumed = True
            recovered_from = str(saved_path)
    usage: dict[str, Any] = {}
    save_state(source_path, sha256, "generation", "resuming" if resumed else "running")
    if resumed:
        logging.info("CHECKPOINT | Reusing verified Nano Banana Pro RAW: %s", raw_path)
        with Image.open(raw_path) as raw:
            donor = raw.convert("RGB")
    else:
        if not (allow_generation or force_generation):
            save_state(
                source_path,
                sha256,
                "generation",
                "blocked_no_checkpoint",
                {"api_request_made": False, "checkpoint_match": checkpoint_match},
            )
            if checkpoint_match.get("method") == "same_version_exact_source_sha256_all_candidates_rejected":
                first = next((c for c in reversed(checkpoint_match.get("rejected_exact_candidates", []))
                    if c.get("failed_checks_after_recovery") or c.get("failed_checks")), {})
                failed = first.get("failed_checks_after_recovery") or first.get("failed_checks") or []
                candidate = first.get("candidate")
                raise RejectedDonorAvailable(
                    str(candidate) if candidate else None,
                    list(failed),
                    "Existing exact-source RAW donors were found but failed technical review. "
                    f"Failed checks: {', '.join(failed) or 'see checkpoint diagnostics'}. "
                    "No API request was made; original RAWs and queue position are retained."
                )
            raise RuntimeError(
                "This source has no V8.4 whole-scene-reviewed RAW checkpoint. No API request was made. "
                "Run 00_GENERATE_NEW_DONOR_ONLY.bat to authorize exactly one new donor generation."
            )
        active_key = key or api_key()
        if OFFLINE_SELF_TEST_MODE:
            logging.info("SELF-TEST | Creating deterministic offline donor. No API request is made.")
        else:
            logging.info("GENERATION | Calling Nano Banana Pro (google/gemini-3-pro-image). Legacy Seedream RAWs are blocked.")
        seed = random.SystemRandom().randint(1, 2_147_483_646) if force_generation else int(CFG["seed_base"]) + int(sha256[:8], 16) % 1_000_000
        donor, usage = generate_image(active_key, reference_path, f"{sha256[:10]}_appearance_donor", seed)
        whole_scene_gate, whole_scene_audit = whole_scene_quality_gate(reference, donor)
        attempt_label = f"ATTEMPT_{seed}"
        whole_scene_audit_path = out_path(AUDIT_FOLDER, source_path, sha256, f"WHOLE_SCENE_TECHNICAL_AUDIT_{attempt_label}")
        review_board_path = out_path(AUDIT_FOLDER, source_path, sha256, f"WHOLE_SCENE_REVIEW_BOARD_{attempt_label}")
        atomic_save_png(whole_scene_audit, whole_scene_audit_path)
        atomic_save_png(whole_scene_review_board(reference, donor, whole_scene_audit), review_board_path)

        # A provider response is not an approved RAW.  Keep rejected pixels out
        # of the canonical checkpoint folder so a later FINAL can never resume
        # from a camera-mutated donor.
        if whole_scene_gate["passed"]:
            committed_raw_path = raw_path
        else:
            committed_raw_path = out_path(
                REVIEW_FOLDER,
                source_path,
                sha256,
                f"{whole_scene_gate['decision']}_RAW_{attempt_label}",
            )
        atomic_save_png(donor, committed_raw_path)
        committed_raw_sha256 = hashlib.sha256(committed_raw_path.read_bytes()).hexdigest()
        provenance = {
            "pipeline": PIPELINE_TAG,
            "created_at_utc": utc_now(),
            "source_sha256": sha256,
            "donor_sha256": committed_raw_sha256,
            "model": CFG["model"],
            "generator": CFG.get("generator_display_name", "Nano Banana Pro"),
            "seed": seed,
            "api_request_count": 1,
            "checkpoint_reused": False,
            "fresh_generation": True,
            "raw": str(committed_raw_path),
            "canonical_checkpoint_committed": bool(whole_scene_gate["passed"]),
            "technical_eligibility_decision": str(whole_scene_gate["decision"]),
            "size": list(donor.size),
            "usage": usage,
            "native_aspect_request": usage.get("native_aspect_request", {}),
            "whole_scene_quality_gate": whole_scene_gate,
            "generator_effect_verified": bool(whole_scene_gate["checks"].get("photographic_quality.generator_effect_present")),
            "whole_scene_technical_audit": str(whole_scene_audit_path),
            "whole_scene_review_board": str(review_board_path),
            "operator_approval_required_before_final": True,
        }
        provenance_path = out_path(REPORT_FOLDER, source_path, sha256, f"DONOR_PROVENANCE_{attempt_label}", ".json")
        provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
        generation_receipt_path = out_path(REPORT_FOLDER, source_path, sha256, f"FRESH_API_GENERATION_RECEIPT_{attempt_label}", ".json")
        generation_receipt_path.write_text(json.dumps({
            "pipeline": PIPELINE_TAG,
            "source": str(source_path),
            "source_sha256": sha256,
            "model": CFG["model"],
            "generator": CFG.get("generator_display_name", "Nano Banana Pro"),
            "seed": seed,
            "api_endpoint": "https://openrouter.ai/api/v1/images",
            "api_request_count": 1,
            "fresh_generation": True,
            "checkpoint_reused": False,
            "generated_at_utc": utc_now(),
            "raw": str(committed_raw_path),
            "raw_sha256": committed_raw_sha256,
            "canonical_checkpoint_committed": bool(whole_scene_gate["passed"]),
            "technical_eligibility_decision": str(whole_scene_gate["decision"]),
            "generator_effect_verified": bool(whole_scene_gate["checks"].get("photographic_quality.generator_effect_present")),
            "generator_mean_rgb_delta": whole_scene_gate["metrics"].get("generator_mean_rgb_delta"),
            "usage": usage,
            "native_aspect_request": usage.get("native_aspect_request", {}),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info("GENERATION VERIFIED | fresh RAW receipt: %s", generation_receipt_path)
        save_state(source_path, sha256, "generation", "checkpoint_saved", provenance)

        if not whole_scene_gate["passed"]:
            rejection = str(whole_scene_gate["decision"])
            save_state(source_path, sha256, "whole_scene_quality_gate", rejection, {"report": str(provenance_path), **whole_scene_gate})
            if donor_only:
                return {
                    "file": str(source_path), "status": rejection, "donor": str(committed_raw_path),
                    "provenance": str(provenance_path), "generation_receipt": str(generation_receipt_path),
                    "failed_checks": sorted(
                        name for name, passed in whole_scene_gate.get("checks", {}).items()
                        if not bool(passed)
                    ),
                    "request_count": 1, "checkpoint_reused": False,
                }

    if donor_only:
        if resumed:
            raise RuntimeError("DONOR-ONLY mode requires a genuinely new API generation; checkpoint reuse was blocked.")
        return {
            "file": str(source_path), "status": "DONOR_READY_FOR_WHOLE_SCENE_REVIEW", "donor": str(raw_path),
            "provenance": str(provenance_path), "generation_receipt": str(generation_receipt_path),
            "request_count": 1, "checkpoint_reused": False,
        }

    current_whole_scene_gate, current_whole_scene_audit = whole_scene_quality_gate(reference, donor)
    current_whole_scene_audit_path = out_path(AUDIT_FOLDER, source_path, sha256, "WHOLE_SCENE_TECHNICAL_AUDIT")
    current_review_board_path = out_path(AUDIT_FOLDER, source_path, sha256, "WHOLE_SCENE_REVIEW_BOARD")
    atomic_save_png(current_whole_scene_audit, current_whole_scene_audit_path)
    atomic_save_png(whole_scene_review_board(reference, donor, current_whole_scene_audit), current_review_board_path)
    if not current_whole_scene_gate["passed"]:
        rejection = str(current_whole_scene_gate["decision"])
        save_state(source_path, sha256, "whole_scene_quality_gate", rejection, current_whole_scene_gate)
        raise RuntimeError(
            f"This RAW donor failed technical review: {rejection}. FINAL is blocked. "
            "Inspect WHOLE_SCENE_REVIEW_BOARD and generate a new donor from the original source."
        )

    # V8.7.0 generative mode: the technically verified Nano Banana Pro RAW is
    # already the quality master. Publish its exact encoded bytes. Any source
    # blend would destroy the generated resolution and recreate a resized source.
    if bool(CFG.get("verified_donor_is_final_master", False)):
        master_copy = atomic_copy_verified_master(raw_path, final_path)
        final_checks = {
            "exact_camera_view": bool(
                current_whole_scene_gate.get("camera_view_lock", {}).get("passed")
            ),
            "whole_scene_gate_passed": bool(current_whole_scene_gate["passed"]),
            "raw_final_byte_identity": bool(master_copy["byte_identical"]),
            "native_generated_resolution_preserved": True,
            "no_resize": True,
            "no_blending": True,
            "no_upscale": True,
            "no_reencoding": True,
        }
        final_checks.update({
            f"donor_gate.{name}": bool(passed)
            for name, passed in current_whole_scene_gate.get("checks", {}).items()
        })
        validation = {
            "checks": final_checks,
            "passed": all(final_checks.values()),
            "decision": "approve" if all(final_checks.values()) else "review",
            "camera_view_lock": current_whole_scene_gate.get("camera_view_lock", {}),
            "failed_tile_count": sum(
                1
                for cell in current_whole_scene_gate.get("grid", {}).get("cells", [])
                if cell.get("issues")
            ),
            "grid": current_whole_scene_gate.get("grid", {}),
            "master_copy": master_copy,
        }
        release = final_release_policy(validation)
        if not validation["passed"]:
            final_path.unlink(missing_ok=True)
            raise RuntimeError(
                "Verified donor could not be published as an identical FINAL master."
            )
        comparison_path = out_path(
            AUDIT_FOLDER, source_path, sha256, "SOURCE_DONOR_FINAL_COMPARISON"
        )
        atomic_save_png(comparison(reference, donor, donor), comparison_path)
        report = {
            "pipeline": PIPELINE_TAG,
            "created_at_utc": utc_now(),
            "source": str(source_path),
            "source_sha256": sha256,
            "model": CFG["model"],
            "architecture": (
                "the technically verified Nano Banana Pro RAW is the FINAL master; "
                "publishing performs an atomic byte-for-byte copy only"
            ),
            "framing": framing,
            "request_count": 0 if resumed else 1,
            "checkpoint_reused": resumed,
            "fresh_generation": not resumed,
            "generation_evidence": {
                "api_request_count": 0 if resumed else 1,
                "fresh_generation": not resumed,
                "checkpoint_reused": resumed,
                "receipt": None if resumed else str(generation_receipt_path),
            },
            "legacy_donor_search_enabled": False,
            "checkpoint_match": checkpoint_match,
            "checkpoint_recovered_after_source_reencode": False,
            "checkpoint_recovered_by_exact_source_sha256": recovered_from is not None,
            "checkpoint_recovered_from": recovered_from,
            "usage": usage,
            "whole_scene_donor_review": current_whole_scene_gate,
            "operator_approval": (
                "The displayed FINAL is the exact verified donor, not a reconstructed image."
            ),
            "operator_final_approval": {
                "required": True,
                "status": "AWAITING_USER_CONFIRMATION",
                "next_source_blocked_until_confirmation": True,
                "state_file": str(sequential_state_path()),
            },
            "final_master_policy": {
                "verified_donor_is_final": True,
                "strict_whole_scene_gate_required": True,
                "source_is_pixel_master": False,
                "pixel_processing_after_gate": False,
                "byte_identity": master_copy,
            },
            "release_policy": release,
            "validation": validation,
            "artifacts": {
                "source_reference": str(reference_path),
                "structure_control": str(control_path),
                "structure_guard": str(structure_guard_path),
                "appearance_donor": str(raw_path),
                "whole_scene_technical_audit": str(current_whole_scene_audit_path),
                "whole_scene_review_board": str(current_review_board_path),
                "comparison": str(comparison_path),
                "released_output": str(final_path),
            },
            "status": "approved",
            "mode": "STRICT_GENERATIVE_UPSCALE",
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        save_state(
            source_path,
            sha256,
            "complete",
            "approved",
            {"output": str(final_path), "report": str(report_path)},
        )
        logging.info(
            "VERIFIED DONOR MASTER PUBLISHED | RAW SHA-256 == FINAL SHA-256 | %s",
            master_copy["final_sha256"],
        )
        return {
            "file": str(source_path),
            "status": "approved",
            "output": str(final_path),
            "report": str(report_path),
            "donor": str(raw_path),
            "checkpoint_reused": resumed,
            "raw_final_byte_identical": True,
        }

    save_state(source_path, sha256, "donor_registration", "running")
    aligned, valid, registration = register_donor(reference, donor)
    aligned_path = out_path(ALIGNED_FOLDER, source_path, sha256, "REGISTERED_DONOR")
    valid_path = out_path(ALIGNED_FOLDER, source_path, sha256, "REGISTERED_VALID_MASK")
    atomic_save_png(aligned, aligned_path)
    atomic_save_png(valid, valid_path)

    save_state(source_path, sha256, "structure_appearance_separation", "running")
    candidate_results: list[dict[str, Any]] = []
    selected: tuple[Image.Image, dict[str, Image.Image], dict[str, Any], dict[str, Any], Image.Image, Image.Image, Image.Image] | None = None
    passing: list[tuple[float, float, tuple[Image.Image, dict[str, Image.Image], dict[str, Any], dict[str, Any], Image.Image, Image.Image, Image.Image]]] = []
    reviewable: list[tuple[float, float, tuple[Image.Image, dict[str, Image.Image], dict[str, Any], dict[str, Any], Image.Image, Image.Image, Image.Image]]] = []
    evaluated: list[tuple[int, int, float, tuple[Image.Image, dict[str, Image.Image], dict[str, Any], dict[str, Any], Image.Image, Image.Image, Image.Image]]] = []
    adaptive_recovery_used = False

    def evaluate_candidate(intensity: float, stage: str) -> bool:
        candidate, candidate_masks, candidate_appearance = separate_and_transfer(reference, aligned, valid, intensity)
        candidate_validation, candidate_edge, candidate_heat, candidate_defects = validate_final(
            reference, candidate, candidate_appearance, aligned
        )
        release = final_release_policy(candidate_validation)
        candidate_validation["release_policy"] = release
        candidate_path = out_path(
            TRANSFER_FOLDER,
            source_path,
            sha256,
            f"CANDIDATE_{stage.upper()}_{intensity:.2f}".replace(".", "P"),
        )
        atomic_save_png(candidate, candidate_path)
        candidate_results.append({
            "intensity": intensity,
            "stage": stage,
            "passed": candidate_validation["passed"],
            "operator_reviewable": release["operator_reviewable"],
            "failed_checks": release["failed_checks"],
            "mandatory_failures": release["mandatory_failures"],
            "path": str(candidate_path),
            "validation": candidate_validation,
            "appearance_gain": candidate_validation["appearance_gain_score"],
        })
        bundle = (
            candidate, candidate_masks, candidate_appearance, candidate_validation,
            candidate_edge, candidate_heat, candidate_defects,
        )
        preferred = float(CFG["preferred_intensity"])
        distance = abs(intensity - preferred)
        evaluated.append((len(release["mandatory_failures"]), len(release["failed_checks"]), distance, bundle))
        logging.info(
            "FINAL CANDIDATE | %s | intensity=%.2f | %s%s",
            stage,
            intensity,
            "PASSED" if candidate_validation["passed"] else "REVIEW",
            "" if not release["failed_checks"] else " | failed=" + ", ".join(release["failed_checks"]),
        )
        if candidate_validation["passed"]:
            passing.append((distance, -float(candidate_validation["appearance_gain_score"]), bundle))
            return True
        if release["operator_reviewable"]:
            reviewable.append((distance, -float(candidate_validation["appearance_gain_score"]), bundle))
        return False

    for intensity in [float(item) for item in CFG["candidate_intensities"]]:
        evaluate_candidate(intensity, "primary")

    selected_recovery_direction = recovery_direction(candidate_results)
    if not passing and selected_recovery_direction == "increase_detail":
        adaptive_recovery_used = True
        logging.warning(
            "DETAIL-EVIDENCE RECOVERY | primary candidates lack verified donor detail; "
            "increasing donor high-frequency strength without changing camera or making an API request"
        )
        tested = {round(float(item["intensity"]), 6) for item in candidate_results}
        for intensity in [float(item) for item in CFG.get("detail_recovery_intensities", [])]:
            if round(intensity, 6) in tested:
                continue
            if evaluate_candidate(intensity, "detail_recovery"):
                break
    elif not passing and bool(CFG.get("adaptive_final_recovery_enabled", False)):
        adaptive_recovery_used = True
        logging.warning(
            "SAFETY RECOVERY | primary candidates failed non-detail safety checks; reducing donor strength "
            "without changing the camera or making an API request"
        )
        tested = {round(float(item["intensity"]), 6) for item in candidate_results}
        for intensity in [float(item) for item in CFG.get("adaptive_recovery_intensities", [])]:
            if round(intensity, 6) in tested:
                continue
            if evaluate_candidate(intensity, "adaptive"):
                break

    if passing:
        passing.sort(key=lambda item: (item[0], item[1]))
        selected = passing[0][2]
    elif reviewable:
        reviewable.sort(key=lambda item: (item[0], item[1]))
        selected = reviewable[0][2]
        logging.warning(
            "FINAL READY FOR OPERATOR | camera, architecture and artifact checks passed; "
            "aesthetic warnings require visual approval: %s",
            ", ".join(selected[3]["release_policy"]["aesthetic_warnings"]),
        )
    elif evaluated:
        evaluated.sort(key=lambda item: (item[0], item[1], item[2]))
        selected = evaluated[0][3]
    if selected is None:
        raise RuntimeError("FINAL recovery has no configured appearance candidates.")
    final, masks, appearance, validation, edge_audit, heatmap, defect_audit = selected
    release = validation.get("release_policy", final_release_policy(validation))
    reviewable_output = bool(release["operator_reviewable"])
    separated_path = out_path(TRANSFER_FOLDER, source_path, sha256, "SELECTED_SEPARATED_TRANSFER")
    atomic_save_png(final, separated_path)
    mask_paths: dict[str, str] = {}
    for name, image in masks.items():
        path = out_path(MASK_FOLDER, source_path, sha256, name.upper())
        atomic_save_png(image, path)
        mask_paths[name] = str(path)

    edge_path = out_path(AUDIT_FOLDER, source_path, sha256, "EDGE_AUDIT")
    heatmap_path = out_path(AUDIT_FOLDER, source_path, sha256, "CHANGE_HEATMAP")
    defect_path = out_path(AUDIT_FOLDER, source_path, sha256, "LOCAL_DEFECT_AUDIT")
    comparison_path = out_path(AUDIT_FOLDER, source_path, sha256, "SOURCE_DONOR_FINAL_COMPARISON")
    atomic_save_png(edge_audit, edge_path)
    atomic_save_png(heatmap, heatmap_path)
    atomic_save_png(defect_audit, defect_path)
    atomic_save_png(comparison(reference, aligned.resize(reference.size, Image.Resampling.LANCZOS), final), comparison_path)
    destination = final_path if validation["passed"] or reviewable_output else review_path
    atomic_save_png(final, destination)

    report = {
        "pipeline": PIPELINE_TAG,
        "created_at_utc": utc_now(),
        "source": str(source_path),
        "source_sha256": sha256,
        "model": CFG["model"],
        "architecture": "source owns background, colour, broad illumination, materials, geometry and structural contours; the verified registered donor is the strong high-frequency luminance master after unsupported structural edges are removed",
        "framing": framing,
        "request_count": 0 if resumed else 1,
        "checkpoint_reused": resumed,
        "fresh_generation": not resumed,
        "generation_evidence": {
            "api_request_count": 0 if resumed else 1,
            "fresh_generation": not resumed,
            "checkpoint_reused": resumed,
            "receipt": None if resumed else str(generation_receipt_path),
        },
        "legacy_donor_search_enabled": False,
        "checkpoint_match": checkpoint_match,
        "checkpoint_recovered_after_source_reencode": False,
        "checkpoint_recovered_by_exact_source_sha256": recovered_from is not None,
        "checkpoint_recovered_from": recovered_from,
        "usage": usage,
        "whole_scene_donor_review": current_whole_scene_gate,
        "final_master_policy": {
            "source_is_pixel_master": True,
            "verified_donor_is_final": False,
            "direct_donor_pixel_mix": False,
            "donor_low_frequency_transfer": False,
            "donor_chroma_transfer": False,
            "donor_is_high_frequency_master": True,
            "donor_microdetail_requires_source_texture_evidence": False,
            "unsupported_donor_structural_edges_blocked": True,
            "background_replacement_allowed": False,
            "material_replacement_allowed": False,
            "lighting_direction_change_allowed": False,
        },
        "operator_approval": "Running stage 02 confirms whole-frame visual review of the RAW donor.",
        "operator_final_approval": {
            "required": True,
            "status": "AWAITING_USER_CONFIRMATION",
            "next_source_blocked_until_confirmation": True,
                "state_file": str(sequential_state_path()),
        },
        "control": control_metrics,
        "registration": registration,
        "appearance_transfer": appearance,
        "candidate_ladder": candidate_results,
        "adaptive_final_recovery": {
            "enabled": bool(CFG.get("adaptive_final_recovery_enabled", False)),
            "used": adaptive_recovery_used,
            "candidate_count": len(candidate_results),
            "direction": selected_recovery_direction,
            "selected_stage": next(
                (
                    item["stage"] for item in candidate_results
                    if float(item["intensity"]) == float(appearance["intensity"])
                ),
                "unknown",
            ),
        },
        "release_policy": release,
        "selected_intensity": appearance["intensity"],
        "validation": validation,
        "artifacts": {
            "source_reference": str(reference_path),
            "structure_control": str(control_path),
            "structure_guard": str(structure_guard_path),
            "appearance_donor": str(raw_path),
            "whole_scene_technical_audit": str(current_whole_scene_audit_path),
            "whole_scene_review_board": str(current_review_board_path),
            "registered_donor": str(aligned_path),
            "registered_valid_mask": str(valid_path),
            "semantic_and_transfer_masks": mask_paths,
            "separated_transfer": str(separated_path),
            "edge_audit": str(edge_path),
            "change_heatmap": str(heatmap_path),
            "local_defect_audit": str(defect_path),
            "comparison": str(comparison_path),
            "released_output": str(destination),
        },
        "status": "approved" if validation["passed"] else ("operator_review" if reviewable_output else "review"),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save_state(source_path, sha256, "complete", report["status"], {"output": str(destination), "report": str(report_path)})
    return {"file": str(source_path), "status": report["status"], "output": str(destination), "report": str(report_path), "donor": str(raw_path), "checkpoint_reused": resumed}


def process(
    source_path: Path,
    key: str | None = None,
    force_generation: bool = False,
    force_processing: bool = False,
    allow_generation: bool = False,
    donor_only: bool = False,
) -> dict[str, Any]:
    """Run with byte- and timestamp-level immutability sentinels around the source."""
    source_path = Path(source_path)
    before_sha256 = file_sha256(source_path)
    before_stat = source_path.stat()
    result: dict[str, Any] | None = None
    try:
        result = _process_impl(
            source_path,
            key=key,
            force_generation=force_generation,
            force_processing=force_processing,
            allow_generation=allow_generation,
            donor_only=donor_only,
        )
    finally:
        if not source_path.is_file():
            raise RuntimeError(f"SOURCE IMMUTABILITY VIOLATION: source disappeared: {source_path}")
        after_sha256 = file_sha256(source_path)
        after_stat = source_path.stat()
        if (
            after_sha256 != before_sha256
            or after_stat.st_size != before_stat.st_size
            or after_stat.st_mtime_ns != before_stat.st_mtime_ns
        ):
            raise RuntimeError(
                "SOURCE IMMUTABILITY VIOLATION: input file changed during processing. "
                f"before={before_sha256}/{before_stat.st_size}/{before_stat.st_mtime_ns}, "
                f"after={after_sha256}/{after_stat.st_size}/{after_stat.st_mtime_ns}, source={source_path}"
            )
    if result is None:
        raise RuntimeError("Pipeline returned no result")
    evidence = {
        "verified": True,
        "source_sha256_before": before_sha256,
        "source_sha256_after": after_sha256,
        "source_size_before": before_stat.st_size,
        "source_size_after": after_stat.st_size,
        "source_mtime_ns_before": before_stat.st_mtime_ns,
        "source_mtime_ns_after": after_stat.st_mtime_ns,
    }
    for key_name in ("report", "provenance"):
        evidence_path = result.get(key_name)
        if not evidence_path:
            continue
        path = Path(evidence_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["source_immutability"] = evidence
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            logging.warning("SOURCE IMMUTABILITY EVIDENCE WRITE SKIPPED | %s", path)
    result["source_immutability"] = evidence
    return result


def synthetic_architecture(width: int = 960, height: int = 540) -> np.ndarray:
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:] = (175, 206, 230)
    cv2.rectangle(canvas, (0, 365), (width, height), (58, 132, 169), -1)
    cv2.fillPoly(canvas, [np.array([[0, 290], [180, 190], [350, 270], [570, 155], [780, 255], [960, 205], [960, 380], [0, 380]])], (75, 122, 82))
    for left, top, right, bottom in [(55, 180, 285, 360), (350, 205, 640, 375), (690, 175, 915, 355)]:
        cv2.rectangle(canvas, (left, top), (right, bottom), (196, 180, 157), -1)
        cv2.rectangle(canvas, (left, top), (right, bottom), (42, 51, 62), 3)
        for x in range(left + 16, right - 10, 31):
            for y in range(top + 17, bottom - 10, 27):
                cv2.rectangle(canvas, (x, y), (x + 15, y + 12), (47, 83, 105), -1)
    cv2.line(canvas, (0, 430), (960, 405), (225, 218, 195), 13)
    for x in range(30, 940, 58):
        cv2.circle(canvas, (x, 394 + (x % 3) * 3), 16, (35, 104, 55), -1)
    return canvas


def synthetic_donor(source: np.ndarray, geometry_mutation: bool = True) -> np.ndarray:
    donor = source.astype(np.float32)
    height, width = source.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    glow = np.exp(-((xx - width * 0.72) ** 2 + (yy - height * 0.28) ** 2) / (2.0 * (width * 0.16) ** 2))
    donor[:, :, 0] += glow * 26
    donor[:, :, 1] += glow * 17
    donor[:, :, 2] += glow * 4
    donor = cv2.convertScaleAbs(donor, alpha=1.06, beta=2)
    texture = np.random.default_rng(700).normal(0, 2.2, donor.shape).astype(np.float32)
    donor = np.clip(donor.astype(np.float32) + texture, 0, 255).astype(np.uint8)
    if geometry_mutation:
        cv2.rectangle(donor, (430, 225), (515, 310), (235, 220, 195), -1)
        for y in range(235, 300, 18):
            cv2.line(donor, (435, y), (510, y), (30, 50, 70), 2)
    return donor


def offline_self_test(write_demo: bool = False, compact: bool = False) -> int:
    global TARGET
    original_target = TARGET
    TARGET = (1280, 720)
    try:
        with tempfile.TemporaryDirectory(prefix="mg_v8_safe_") as temp_name:
            temp = Path(temp_name)
            source = Image.fromarray(synthetic_architecture(), "RGB").resize(TARGET, Image.Resampling.LANCZOS)
            donor_array = synthetic_donor(np.asarray(source.resize((1280, 720))), True)
            donor = Image.fromarray(donor_array, "RGB")
            aligned, valid, registration = register_donor(source, donor)
            final, masks, appearance = separate_and_transfer(source, aligned, valid)
            validation, edge_audit, heatmap, defect_audit = validate_final(source, final, appearance)
            if not validation["passed"]:
                print("V8 SELF-TEST FAILED: safe tonal output was rejected")
                print(json.dumps({"registration": registration, "appearance": appearance, "validation": validation}, ensure_ascii=False, indent=2))
                return 10
            source_np = np.asarray(source)
            final_np = np.asarray(final)
            mutation = (slice(225, 311), slice(430, 516))
            clean_donor_array = synthetic_donor(np.asarray(source.resize((1280, 720))), False)
            clean_aligned, clean_valid, _ = register_donor(source, Image.fromarray(clean_donor_array, "RGB"))
            clean_final, _, _ = separate_and_transfer(source, clean_aligned, clean_valid)
            clean_final_np = np.asarray(clean_final)
            mutation_mae = float(np.mean(np.abs(
                clean_final_np[mutation].astype(np.float32) - final_np[mutation].astype(np.float32)
            )))
            donor_mae = float(np.mean(np.abs(source_np[mutation].astype(np.float32) - donor_array[mutation].astype(np.float32))))
            if mutation_mae >= donor_mae * float(CFG["selftest_max_mutation_transfer_ratio"]):
                print("V8 SELF-TEST FAILED: donor geometry leaked into final", mutation_mae, donor_mae)
                return 11
            if write_demo:
                destination = ROOT / "offline_demo"
                destination.mkdir(parents=True, exist_ok=True)
                source.save(destination / "01_SOURCE.png")
                donor.save(destination / "02_DONOR_WITH_GEOMETRY_MUTATION.png")
                aligned.save(destination / "03_REGISTERED_DONOR.png")
                final.save(destination / "04_FINAL_STRUCTURE_LOCKED.png")
                edge_audit.save(destination / "05_EDGE_AUDIT.png")
                heatmap.save(destination / "06_CHANGE_HEATMAP.png")
                defect_audit.save(destination / "07_LOCAL_DEFECT_AUDIT.png")
                comparison(source, aligned, final).save(destination / "08_COMPARISON.png")
                for name, image in masks.items():
                    image.save(destination / f"MASK_{name.upper()}.png")
                (destination / "demo_report.json").write_text(json.dumps({"registration": registration, "appearance": appearance, "validation": validation, "mutation_mae": mutation_mae, "donor_mutation_mae": donor_mae}, ensure_ascii=False, indent=2), encoding="utf-8")
            print("V8 SAFE TONAL TRANSFER SELF-TEST PASSED")
            mutation_ratio = round(mutation_mae / max(1e-6, donor_mae), 6)
            if compact:
                payload = {
                    "camera_score": validation["camera_view_lock"]["camera_score"],
                    "source_edge_preservation": validation["source_edge_preservation"],
                    "donor_appearance_transfer_ratio": appearance["donor_appearance_transfer_ratio"],
                    "mutation_transfer_ratio": mutation_ratio,
                    "failed_grid_cells": validation["failed_tile_count"],
                    "parallel_edge_ghost_fraction": validation["parallel_edge_ghost_fraction"],
                    "edge_delta_halo_fraction": validation["edge_delta_halo_fraction"],
                    "edge_delta_halo_p99": validation["edge_delta_halo_p99"],
                    "donor_high_frequency_transfer": appearance["donor_high_frequency_transfer"],
                }
            else:
                payload = {
                    "registration": registration,
                    "appearance": appearance,
                    "validation": validation,
                    "donor_geometry_mutation_suppressed": True,
                    "mutation_transfer_ratio": mutation_ratio,
                }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        TARGET = original_target


def regression_validator_self_test() -> int:
    original_target = TARGET
    source = Image.fromarray(synthetic_architecture(), "RGB").resize(original_target, Image.Resampling.LANCZOS)
    damaged = np.asarray(source).copy()
    height, width = damaged.shape[:2]
    cv2.rectangle(damaged, (round(width * 0.10), round(height * 0.31)), (round(width * 0.16), round(height * 0.38)), (0, 0, 0), -1)
    cv2.line(damaged, (round(width * 0.20), round(height * 0.12)), (round(width * 0.42), round(height * 0.20)), (0, 85, 255), max(3, round(width / 900)))
    damaged_image = Image.fromarray(damaged, "RGB")
    appearance = {"mean_illumination_transfer": 1.0, "mean_chroma_transfer": 1.0, "mean_detail_transfer": 1.0}
    validation, _, _, _ = validate_final(source, damaged_image, appearance)
    if validation["passed"]:
        print("V8 REGRESSION VALIDATOR SELF-TEST FAILED: known bad image was approved")
        return 12
    required_rejection = (not validation["checks"]["no_dark_clip_growth"]) or (not validation["checks"]["local_rgb_delta_bounded"])
    if not required_rejection:
        print("V8 REGRESSION VALIDATOR SELF-TEST FAILED: local artifacts were not detected")
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 13
    print("V8 REGRESSION VALIDATOR SELF-TEST PASSED")
    return 0


def sequential_state_path() -> Path:
    path = DIAG_FOLDER / "SEQUENTIAL_GENERATIVE_REVIEW_STATE.json"
    legacy = DIAG_FOLDER / "SEQUENTIAL_REVIEW_STATE.json"
    if not path.exists() and legacy.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(legacy, path)
    return path


def _new_sequential_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pipeline": PIPELINE_TAG,
        "mode": "ONE_IMAGE_THEN_EXPLICIT_FINAL_APPROVAL",
        "batch_processing_enabled": False,
        "updated_at_utc": utc_now(),
        "active": None,
        "approved": [],
        "rejected": [],
    }


def _revalidate_legacy_edge_approvals(state: dict[str, Any]) -> None:
    """Reopen legacy approvals that fail either doubled-contour guard."""
    kept: list[dict[str, Any]] = []
    invalidated = state.setdefault("edge_ghost_invalidated_approvals", [])
    limit = float(CFG["validation_max_parallel_edge_ghost_fraction"])
    for record in state.get("approved", []):
        source_path = Path(str(record.get("source", "")))
        final_path = Path(str(record.get("final", "")))
        if not source_path.is_file() or not final_path.is_file():
            kept.append(record)
            continue
        try:
            with Image.open(source_path) as opened_source, Image.open(final_path) as opened_final:
                reference, _ = prepare_reference(opened_source)
                size = (
                    int(CFG["validation_width"]),
                    round(int(CFG["validation_width"]) * reference.height / reference.width),
                )
                source_rgb = np.asarray(reference.resize(size, Image.Resampling.LANCZOS).convert("RGB"))
                final_rgb = np.asarray(opened_final.resize(size, Image.Resampling.LANCZOS).convert("RGB"))
                source_gray = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY)
                final_gray = cv2.cvtColor(final_rgb, cv2.COLOR_RGB2GRAY)
            source_edges = cv2.Canny(source_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
            final_edges = cv2.Canny(final_gray, int(CFG["canny_low"]), int(CFG["canny_high"]))
            _, fraction = _parallel_edge_ghost_audit(source_edges, final_edges)
            signed_delta = final_rgb.astype(np.float32) - source_rgb.astype(np.float32)
            smooth_delta = cv2.GaussianBlur(signed_delta, (0, 0), sigmaX=3.0)
            edge_residual = np.max(np.abs(signed_delta - smooth_delta), axis=2)
            edge_band = cv2.dilate(
                source_edges,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
            ) > 0
            exact_edge_core = cv2.dilate(
                source_edges,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            ) > 0
            displaced_edge_ring = edge_band & ~exact_edge_core
            edge_values = edge_residual[displaced_edge_ring]
            halo_fraction = (
                float(np.mean(edge_values > float(CFG["validation_edge_delta_halo_threshold"])))
                if edge_values.size else 0.0
            )
            halo_p99 = float(np.percentile(edge_values, 99.0)) if edge_values.size else 0.0
        except (OSError, ValueError, cv2.error) as exc:
            logging.warning("EDGE GHOST REVALIDATION SKIPPED | %s | %s", source_path, exc)
            kept.append(record)
            continue
        halo_clean = (
            halo_fraction <= float(CFG["validation_max_edge_delta_halo_fraction"])
            and halo_p99 <= float(CFG["validation_max_edge_delta_halo_p99"])
        )
        if fraction <= limit and halo_clean:
            kept.append(record)
            continue
        invalidated.append({
            "source": str(source_path),
            "final": str(final_path),
            "parallel_edge_ghost_fraction": round(fraction, 8),
            "limit": limit,
            "edge_delta_halo_fraction": round(halo_fraction, 8),
            "edge_delta_halo_p99": round(halo_p99, 6),
            "invalidated_at_utc": utc_now(),
        })
        logging.warning(
            "LEGACY FINAL REOPENED | %s | parallel=%.6f/%.6f | delta_halo=%.6f p99=%.3f",
            source_path,
            fraction,
            limit,
            halo_fraction,
            halo_p99,
        )
    state["approved"] = kept


def _reopen_low_impact_approvals(state: dict[str, Any]) -> None:
    """Reopen filtered FINALs that did not prove a fresh generative detail pass."""
    kept: list[dict[str, Any]] = []
    reopened = state.setdefault("v855_low_impact_reopened_approvals", [])
    for record in state.get("approved", []):
        final_name = Path(str(record.get("final", ""))).name
        if not any(token in final_name for token in (
            "_V855_", "_V856_", "_V857_", "_V858_", "_V859_", "_V860_", "_V861_", "_V862_",
            "_V863_", "_V864_", "_V865_", "_V866_", "_V867_"
        )):
            kept.append(record)
            continue
        reopened.append({
            "source": str(record.get("source", "")),
            "final": str(record.get("final", "")),
            "reason": "previous FINAL was not built by the V8.6.9 faithful donor detail carrier",
            "reopened_at_utc": utc_now(),
        })
        logging.warning("LOW-IMPACT FINAL REOPENED FOR VERIFIED GENERATION | %s", record.get("source", ""))
    state["approved"] = kept


def _project_data_root() -> Path:
    r"""Return the stable MG project root, independent of the selected source queue.

    The web console is allowed to persist ``...\MG\base`` as the active source
    folder. Path relocation, however, always targets ``...\MG``. The string-based
    suffix check also keeps this self-test deterministic when the Windows bundle is
    inspected on a non-Windows host.
    """
    source_text = str(SOURCE).rstrip("\\/")
    normalized = source_text.replace("/", "\\")
    if normalized.casefold().endswith("\\base"):
        return Path(source_text[:-5])
    return SOURCE


def _remap_moved_project_paths(payload: Any) -> tuple[Any, int]:
    """Remap persisted absolute paths after the complete MG project moved to D:."""
    legacy_roots = [str(value).rstrip("\\/") for value in CFG.get("legacy_source_roots", [])]
    new_root = str(_project_data_root()).rstrip("\\/")

    def remap(value: Any) -> tuple[Any, int]:
        if isinstance(value, dict):
            changed = 0
            result: dict[str, Any] = {}
            for key, item in value.items():
                result[key], item_changed = remap(item)
                changed += item_changed
            return result, changed
        if isinstance(value, list):
            changed = 0
            result_list: list[Any] = []
            for item in value:
                mapped, item_changed = remap(item)
                result_list.append(mapped)
                changed += item_changed
            return result_list, changed
        if not isinstance(value, str):
            return value, 0
        folded = value.casefold()
        for old_root in legacy_roots:
            old_folded = old_root.casefold()
            if folded == old_folded:
                return new_root, 1
            if folded.startswith(old_folded + "\\") or folded.startswith(old_folded + "/"):
                suffix = value[len(old_root):].lstrip("\\/").replace("/", "\\")
                return new_root + "\\" + suffix, 1
        return value, 0

    return remap(payload)


def load_sequential_state() -> dict[str, Any]:
    path = sequential_state_path()
    if not path.exists():
        return _new_sequential_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationFailure(f"Sequential review state cannot be read: {path}: {exc}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("approved"), list):
        raise ConfigurationFailure(f"Invalid sequential review state: {path}")
    state, relocated_path_count = _remap_moved_project_paths(state)
    if relocated_path_count:
        state["path_relocation_migration"] = {
            "destination_root": str(_project_data_root()),
            "relocated_path_count": relocated_path_count,
            "migrated_at_utc": utc_now(),
            "reason": "complete project moved from Desktop MG to D drive",
        }
        logging.info(
            "PROJECT PATH MIGRATION | remapped %s persisted paths to %s",
            relocated_path_count,
            _project_data_root(),
        )
    previous_pipeline = str(state.get("pipeline", ""))
    if previous_pipeline in {
        "V870_DUAL_MODE_GENERATIVE_MASTER",
        "V871_VISUAL_REVIEW_CONSOLE",
        "V872_PC_REVIEW_CONSOLE",
        "V873_LOCAL_WEB_REVIEW_CONSOLE",
    }:
        state["v874_canvas_integrity_migration"] = {
            "migrated_at_utc": utc_now(),
            "previous_pipeline": previous_pipeline,
            "reason": (
                "preserve approvals but force the current unapproved donor through the new "
                "canvas-integrity and signage-preservation gates without an API request"
            ),
        }
        migration_active = state.get("active")
        if isinstance(migration_active, dict) and migration_active.get("status") in {
            "AWAITING_FINAL_APPROVAL",
            "AWAITING_RAW_REVIEW",
        }:
            recovered_donor = Path(str(migration_active.get("donor", "")))
            for key_name in (
                "final", "report", "final_sha256", "technical_decision",
                "failed_checks", "mandatory_failures", "diagnostic_package",
            ):
                migration_active.pop(key_name, None)
            migration_active["status"] = (
                "AWAITING_RAW_REVIEW"
                if recovered_donor.is_file()
                else "AWAITING_EXPLICIT_DONOR_GENERATION"
            )
            migration_active["migration_reason"] = "v874_mandatory_canvas_integrity_revalidation"
        previous_pipeline = PIPELINE_TAG
    state.setdefault("rejected", [])
    state.setdefault("active", None)
    if previous_pipeline == "V869_DONOR_DETAIL_EVIDENCE_RECOVERY":
        state["v870_archived_v869_approvals"] = list(state.get("approved", []))
        state["approved"] = []
        state["v870_mode_isolation_migration"] = {
            "migrated_at_utc": utc_now(),
            "reason": "V8.6.9 source-blended FINAL approvals cannot complete the separate generative queue",
        }
    if previous_pipeline and previous_pipeline != PIPELINE_TAG:
        _revalidate_legacy_edge_approvals(state)
        _reopen_low_impact_approvals(state)
    active = state.get("active")
    if isinstance(active, dict) and previous_pipeline != PIPELINE_TAG:
        # V8.6.9 uses a release-scoped paid-attempt counter. Historical values
        # remain in the audit record but cannot consume the new retry budget.
        active["v869_retry_budget_migration"] = {
            "previous_pipeline": previous_pipeline,
            "legacy_automatic_attempts": max(
                int(active.get("automatic_donor_generation_attempts", 0)),
                int(active.get("v868_automatic_donor_generation_attempts", 0)),
            ),
            "legacy_replacement_attempts": int(active.get("replacement_donor_generation_attempts", 0)),
            "migrated_at_utc": utc_now(),
            "reason": "legacy counters are archived and cannot block V8.6.9 donor recovery",
        }
        active["v869_automatic_donor_generation_attempts"] = 0
        active["v869_retry_budget_source_sha256"] = active.get("source_sha256")
        # V8.6.1 already produced the requested Nano Banana Pro RAW. Preserve
        # that paid, exact-source checkpoint and invalidate only its overly
        # source-dominated FINAL. V8.6.2 will re-run the camera/whole-scene
        # gate before accepting the RAW, so this migration cannot bypass any
        # mandatory technical review and makes no API request.
        if previous_pipeline == "V861_NANO_BANANA_PRO_GENERATIVE_RAW":
            active["v862_recovered_v861_nano_banana_raw"] = {
                "donor": active.get("donor"),
                "generation_receipt": active.get("generation_receipt"),
                "technical_decision": active.get("technical_decision"),
                "recovered_at_utc": utc_now(),
                "reason": "rebuild FINAL with donor-quality carrier; preserve paid RAW",
            }
            for key_name in (
                "final", "report", "final_sha256", "technical_decision",
                "review_image", "error", "failed_checks", "mandatory_failures",
                "diagnostic_package",
            ):
                active.pop(key_name, None)
            active["status"] = "AWAITING_RAW_REVIEW"
            active["migration_reason"] = "v862_rebuild_final_from_v861_nano_banana_raw"
            logging.info(
                "V8.6.1 NANO BANANA PRO RAW PRESERVED | rebuilding FINAL without API"
            )

        # V8.6.2 may already hold the ideal, paid Nano Banana Pro RAW while its
        # reconstructed FINAL is source-dominated. Preserve the RAW, discard
        # only that derived FINAL and publish the verified donor itself.
        if previous_pipeline == "V862_DONOR_QUALITY_FINAL_LOCK":
            active["v863_recovered_v862_nano_banana_raw"] = {
                "donor": active.get("donor"),
                "generation_receipt": active.get("generation_receipt"),
                "recovered_at_utc": utc_now(),
                "reason": "publish verified donor as byte-identical FINAL; preserve paid RAW",
            }
            for key_name in (
                "final", "report", "final_sha256", "technical_decision",
                "review_image", "error", "failed_checks", "mandatory_failures",
                "diagnostic_package",
            ):
                active.pop(key_name, None)
            active["status"] = "AWAITING_RAW_REVIEW"
            active["migration_reason"] = "v863_publish_verified_v862_raw_as_final"
            logging.info(
                "V8.6.2 NANO BANANA PRO RAW PRESERVED | publishing exact donor as FINAL without API"
            )

        # V8.6.3 already uses the correct byte-identical donor master. Preserve
        # its active item exactly, including a failed/incomplete mg (4).jpeg,
        # so the resumable queue restarts from that image rather than advancing.
        if previous_pipeline == "V863_VERIFIED_DONOR_IS_FINAL_MASTER":
            active["v864_recovered_v863_active_image"] = {
                "status": active.get("status"),
                "donor": active.get("donor"),
                "final": active.get("final"),
                "recovered_at_utc": utc_now(),
                "reason": "resume exact unfinished image with bounded automatic retries",
            }
            active["migration_reason"] = "v864_resume_v863_active_image"
            logging.info(
                "V8.6.3 ACTIVE IMAGE PRESERVED | resumable queue will retry the same image"
            )

        # V8.6.5 already has the resumable queue and byte-identical donor
        # master. Preserve the current source and any eligible paid RAW; V8.6.6
        # revalidates it with the new material-identity gate before release.
        if previous_pipeline == "V865_VERSIONED_RETRY_BUDGET":
            active["v866_recovered_v865_active_image"] = {
                "status": active.get("status"),
                "donor": active.get("donor"),
                "final": active.get("final"),
                "recovered_at_utc": utc_now(),
                "reason": "revalidate current donor with material identity lock",
            }
            active["migration_reason"] = "v866_material_identity_revalidation"
            recovered_donor = Path(str(active.get("donor", "")))
            if recovered_donor.is_file():
                for key_name in (
                    "final", "report", "final_sha256", "technical_decision",
                    "failed_checks", "mandatory_failures", "diagnostic_package",
                ):
                    active.pop(key_name, None)
                active["status"] = "AWAITING_RAW_REVIEW"
            logging.info(
                "V8.6.5 ACTIVE IMAGE PRESERVED | material identity will be revalidated"
            )

        # V8.6.6 published the verified donor directly. Preserve its paid RAW,
        # discard only the donor-master FINAL, then rebuild from the immutable
        # source through the source-faithful high-frequency upscaler.
        if previous_pipeline == "V866_MATERIAL_IDENTITY_LOCK":
            active["v867_recovered_v866_nano_banana_raw"] = {
                "status": active.get("status"),
                "donor": active.get("donor"),
                "final": active.get("final"),
                "recovered_at_utc": utc_now(),
                "reason": "rebuild FINAL with source pixel authority; preserve paid RAW",
            }
            recovered_donor = Path(str(active.get("donor", "")))
            for key_name in (
                "final", "report", "final_sha256", "technical_decision",
                "failed_checks", "mandatory_failures", "diagnostic_package",
            ):
                active.pop(key_name, None)
            active["status"] = (
                "AWAITING_RAW_REVIEW"
                if recovered_donor.is_file()
                else "AWAITING_EXPLICIT_DONOR_GENERATION"
            )
            active["migration_reason"] = "v867_source_faithful_rebuild_from_v866"
            logging.info(
                "V8.6.6 ACTIVE IMAGE PRESERVED | rebuilding source-faithful FINAL"
            )

        # V8.6.7 may hold a good paid FAITHFUL DONOR but an underpowered FINAL.
        # Keep the RAW and rebuild only FINAL with the strong donor detail carrier.
        if previous_pipeline == "V867_SOURCE_FAITHFUL_UPSCALER":
            active["v868_recovered_v867_faithful_donor"] = {
                "status": active.get("status"),
                "donor": active.get("donor"),
                "final": active.get("final"),
                "recovered_at_utc": utc_now(),
                "reason": "reuse good FAITHFUL DONOR and rebuild quality-carrying FINAL",
            }
            recovered_donor = Path(str(active.get("donor", "")))
            for key_name in (
                "final", "report", "final_sha256", "technical_decision",
                "failed_checks", "mandatory_failures", "diagnostic_package",
            ):
                active.pop(key_name, None)
            active["status"] = (
                "AWAITING_RAW_REVIEW"
                if recovered_donor.is_file()
                else "AWAITING_EXPLICIT_DONOR_GENERATION"
            )
            active["migration_reason"] = "v868_rebuild_from_v867_faithful_donor"
            logging.info(
                "V8.6.7 FAITHFUL DONOR PRESERVED | rebuilding high-detail FINAL without API"
            )

        # V8.6.8 may have a technically accepted donor while its deterministic
        # FINAL ladder failed only the legacy Laplacian sharpness proxy. Keep
        # the paid RAW, discard the held FINAL and rebuild with direct donor
        # microdetail evidence. No provider request is needed.
        if previous_pipeline == "V868_FAITHFUL_DONOR_DETAIL_CARRIER":
            active["v869_recovered_v868_faithful_donor"] = {
                "status": active.get("status"),
                "donor": active.get("donor"),
                "final": active.get("final"),
                "recovered_at_utc": utc_now(),
                "reason": "replace global sharpness proxy with registered-donor detail evidence",
            }
            recovered_donor = Path(str(active.get("donor", "")))
            for key_name in (
                "final", "report", "final_sha256", "technical_decision",
                "failed_checks", "mandatory_failures", "diagnostic_package",
            ):
                active.pop(key_name, None)
            active["status"] = (
                "AWAITING_RAW_REVIEW"
                if recovered_donor.is_file()
                else "AWAITING_EXPLICIT_DONOR_GENERATION"
            )
            active["migration_reason"] = "v869_rebuild_from_v868_detail_evidence_hold"
            logging.info(
                "V8.6.8 FAITHFUL DONOR PRESERVED | rebuilding detail-evidenced FINAL without API"
            )

        # V8.6.0 used Seedream. V8.6.2 is a hard provider boundary: neither its
        # RAW nor its FINAL is evidence of a Nano Banana Pro generation.
        if previous_pipeline == "V860_NATIVE_ASPECT_REQUEST":
            active["v862_recovered_v860_seedream_state"] = {
                "paid_attempts": int(active.get("v860_verified_regeneration_attempts", 0)),
                "technical_decision": active.get("technical_decision"),
                "recovered_at_utc": utc_now(),
                "reason": "V8.6.2 requires a fresh google/gemini-3-pro-image RAW",
            }
            for key_name in (
                "donor", "provenance", "generation_receipt", "final", "report",
                "final_sha256", "technical_decision", "review_image", "error",
                "failed_checks", "mandatory_failures", "diagnostic_package",
                "v860_verified_generation_receipt", "v860_verified_generation_successes",
                "v860_verified_generation_reservation", "v860_requires_fresh_api_generation",
            ):
                active.pop(key_name, None)
            active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
            active["migration_reason"] = "v862_requires_fresh_nano_banana_pro_raw"
            logging.warning(
                "V8.6.0 SEEDREAM STATE DISCARDED | fresh Nano Banana Pro RAW required"
            )

        # V8.5.9 sent no aspect-ratio parameter. Its paid provider response may
        # therefore be square even when the immutable source is landscape.
        if previous_pipeline == "V859_ELIGIBLE_RAW_COMMIT":
            active["v862_recovered_v859_missing_aspect_request"] = {
                "paid_attempts": int(active.get("v859_paid_generation_attempts", 0)),
                "technical_decision": active.get("technical_decision"),
                "recovered_at_utc": utc_now(),
                "reason": "V8.5.9 provider request omitted aspect_ratio",
            }
            for key_name in (
                "donor", "provenance", "generation_receipt", "final", "report",
                "final_sha256", "technical_decision", "review_image", "error", "failed_checks",
                "mandatory_failures", "diagnostic_package",
            ):
                active.pop(key_name, None)
            active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
            active["migration_reason"] = "v862_requires_native_aspect_api_request"
            logging.warning(
                "V8.5.9 SQUARE-DEFAULT GENERATION RECOVERED | fresh native-aspect RAW required"
            )

        # V8.5.8 committed a provider receipt before confirming that the RAW
        # passed the whole-scene camera/geometry gate.  Such state is not
        # proof of an eligible checkpoint and must never be resumed by V8.6.2.
        stale_v858_successes = int(active.pop("v858_verified_generation_successes", 0))
        stale_v858_receipt = active.pop("v858_verified_generation_receipt", None)
        stale_v858_reservation = active.pop("v858_verified_generation_reservation", None)
        active.pop("v858_requires_fresh_api_generation", None)
        if previous_pipeline == "V858_TRANSACTIONAL_GENERATION_BUDGET":
            active["v862_recovered_unqualified_v858_commit"] = {
                "recorded_successes": stale_v858_successes,
                "receipt": stale_v858_receipt,
                "reservation": stale_v858_reservation,
                "recovered_at_utc": utc_now(),
                "reason": "V8.5.8 did not bind commit to DONOR_READY_FOR_WHOLE_SCENE_REVIEW",
            }
            for key_name in (
                "donor", "provenance", "generation_receipt", "final", "report",
                "final_sha256", "technical_decision", "review_image", "error",
                "failed_checks", "mandatory_failures", "diagnostic_package",
            ):
                active.pop(key_name, None)
            active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
            active["migration_reason"] = "v862_requires_technically_eligible_raw_commit"
            logging.warning(
                "UNQUALIFIED V8.5.8 RAW COMMIT RECOVERED | fresh V8.6.2 camera-locked RAW required"
            )

        # V8.5.7 reserved its budget before any provider response.  A state
        # containing only that counter is therefore not evidence of a paid
        # generation and must not block V8.6.2.
        stale_v857_attempts = int(active.pop("v857_verified_regeneration_attempts", 0))
        stale_v857_authorized = active.pop("v857_verified_regeneration_authorized_at_utc", None)
        active.pop("v857_requires_fresh_api_generation", None)
        if stale_v857_attempts and not active.get("v862_verified_generation_receipt"):
            active["v862_recovered_stale_v857_reservation"] = {
                "attempts": stale_v857_attempts,
                "authorized_at_utc": stale_v857_authorized,
                "recovered_at_utc": utc_now(),
                "reason": "no successful API generation receipt existed",
            }
            logging.warning("STALE V8.5.7 GENERATION RESERVATION RECOVERED | no paid RAW receipt existed")
        if previous_pipeline == "V869_DONOR_DETAIL_EVIDENCE_RECOVERY":
            active["v870_recovered_v869_faithful_donor"] = {
                "status": active.get("status"),
                "donor": active.get("donor"),
                "final": active.get("final"),
                "recovered_at_utc": utc_now(),
                "reason": "publish the accepted donor as a strict generative master without source blending",
            }
            recovered_donor = Path(str(active.get("donor", "")))
            for key_name in (
                "final", "report", "final_sha256", "technical_decision",
                "failed_checks", "mandatory_failures", "diagnostic_package",
            ):
                active.pop(key_name, None)
            active["status"] = (
                "AWAITING_RAW_REVIEW"
                if recovered_donor.is_file()
                else "AWAITING_EXPLICIT_DONOR_GENERATION"
            )
            active["migration_reason"] = "v870_direct_generative_master_from_v869_donor"
            logging.info(
                "V8.6.9 FAITHFUL DONOR PRESERVED | publishing strict generative master without API"
            )

        old_final = str(active.get("final", ""))
        if (
            old_final
            and PIPELINE_TAG not in Path(old_final).name
            and previous_pipeline not in {
                "V863_VERIFIED_DONOR_IS_FINAL_MASTER",
                "V865_VERSIONED_RETRY_BUDGET",
            }
        ):
            for key_name in ("final", "report", "final_sha256", "technical_decision"):
                active.pop(key_name, None)
            if previous_pipeline in {
                "V861_NANO_BANANA_PRO_GENERATIVE_RAW",
                "V862_DONOR_QUALITY_FINAL_LOCK",
            }:
                active["status"] = "AWAITING_RAW_REVIEW"
                active["migration_reason"] = "v863_publish_verified_nano_banana_raw_as_final"
            else:
                active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
                active["migration_reason"] = "v862_transactional_generation_rebuild"
    if (
        isinstance(active, dict)
        and previous_pipeline != PIPELINE_TAG
        and str(active.get("technical_decision")) == "REJECTED_CAMERA_VIEW"
    ):
        active["replacement_donor_eligible"] = True
        active["replacement_donor_reason"] = "previous_camera_view_rejection"
        active.setdefault("replacement_donor_generation_attempts", 0)
    state["pipeline"] = PIPELINE_TAG
    state["mode"] = "ONE_IMAGE_THEN_EXPLICIT_FINAL_APPROVAL"
    state["batch_processing_enabled"] = False
    if relocated_path_count:
        save_sequential_state(state)
    return state


def save_sequential_state(state: dict[str, Any]) -> Path:
    path = sequential_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at_utc"] = utc_now()
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def _source_relative_path(path: Path) -> str:
    return path.resolve(strict=False).relative_to(SOURCE.resolve(strict=False)).as_posix()


def _natural_source_order(path: Path) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", _source_relative_path(path))
    )


def discover_source_images() -> list[Path]:
    return discover_source_files(SOURCE, CFG["output_folder"])


def recover_excluded_service_source(state: dict[str, Any]) -> bool:
    source = recover_service_active_state(state, SOURCE, CFG["output_folder"], utc_now)
    if source is None:
        return False
    save_sequential_state(state)
    logging.warning("SERVICE SOURCE EXCLUDED | %s | resuming the real user image queue", source)
    return True


def _approved_source(state: dict[str, Any], source: Path) -> bool:
    relative = _source_relative_path(source)
    for record in state["approved"]:
        if record.get("source_relative") == relative:
            return record.get("source_sha256") == file_sha256(source)
    return False


def select_current_source(state: dict[str, Any], files: list[Path]) -> Path | None:
    active = state.get("active")
    if active:
        source = Path(str(active["source"]))
        if not source.exists():
            raise ConfigurationFailure(
                "The current image disappeared before its FINAL was approved. "
                "Restore that source; processing the next image is blocked."
            )
        if file_sha256(source) != str(active["source_sha256"]):
            raise ConfigurationFailure(
                "The current source changed after processing began. "
                "Restore its original bytes; processing the next image is blocked."
            )
        return source
    for source in files:
        if not _approved_source(state, source):
            return source
    return None


def _begin_current_source(state: dict[str, Any], source: Path) -> dict[str, Any]:
    active = state.get("active")
    if active is not None:
        return active
    active = {
        "source": str(source),
        "source_relative": _source_relative_path(source),
        "source_sha256": file_sha256(source),
        "status": "PROCESSING_CURRENT_IMAGE",
        "started_at_utc": utc_now(),
    }
    state["active"] = active
    save_sequential_state(state)
    return active


def _open_final_for_review(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
    logging.info("FINAL REVIEW | %s", path)


def approve_current_final() -> dict[str, Any]:
    state = load_sequential_state()
    active = state.get("active")
    if not active or active.get("status") != "AWAITING_FINAL_APPROVAL":
        raise ConfigurationFailure("There is no current FINAL awaiting user confirmation.")
    source = Path(str(active["source"]))
    final_path = Path(str(active["final"]))
    report_path = Path(str(active["report"]))
    if not source.exists() or file_sha256(source) != active["source_sha256"]:
        raise ConfigurationFailure("FINAL approval is blocked: the source changed after review.")
    if not final_path.exists() or file_sha256(final_path) != active.get("final_sha256"):
        raise ConfigurationFailure("FINAL approval is blocked: the reviewed FINAL is missing or changed.")
    if not report_path.exists():
        raise ConfigurationFailure("FINAL approval is blocked: the technical report is missing.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validation = report.get("validation", {})
    policy = final_release_policy(validation)
    strictly_approved = report.get("status") == "approved" and bool(validation.get("passed"))
    visually_reviewable = (
        report.get("status") == "operator_review"
        and bool(policy.get("operator_reviewable"))
        and bool(policy.get("mandatory_safety_passed"))
        and not policy.get("mandatory_failures")
    )
    if not (strictly_approved or visually_reviewable):
        raise ConfigurationFailure(
            "FINAL approval is blocked: mandatory technical safety validation did not pass."
        )
    if not validation.get("checks", {}).get("exact_camera_view"):
        raise ConfigurationFailure("FINAL approval is blocked: the original camera view is not locked.")
    if report.get("source_sha256") != active["source_sha256"]:
        raise ConfigurationFailure("FINAL approval is blocked: the report belongs to a different source.")
    master_policy = report.get("final_master_policy", {})
    if (
        not master_policy.get("verified_donor_is_final")
        or master_policy.get("pixel_processing_after_gate") is not False
        or not master_policy.get("strict_whole_scene_gate_required")
    ):
        raise ConfigurationFailure(
            "FINAL approval is blocked: report does not prove a strict verified generative master."
        )
    donor_path = Path(str(report.get("artifacts", {}).get("appearance_donor", "")))
    if not donor_path.is_file():
        raise ConfigurationFailure(
            "FINAL approval is blocked: the verified Nano Banana Pro donor is missing."
        )
    if file_sha256(donor_path) != file_sha256(final_path):
        raise ConfigurationFailure(
            "FINAL approval is blocked: the generative FINAL differs from the accepted donor master."
        )
    if not report.get("whole_scene_donor_review", {}).get("passed"):
        raise ConfigurationFailure(
            "FINAL approval is blocked: the donor did not pass camera, whole-scene and material identity gates."
        )

    approved_at = utc_now()
    operator_approval = report.setdefault("operator_final_approval", {})
    operator_approval.update({
        "required": True,
        "status": "APPROVED_BY_USER",
        "approved_at_utc": approved_at,
        "confirmation": "EXPLICIT_USER_CONFIRMATION",
        "next_source_unlocked": True,
    })
    if visually_reviewable:
        report["technical_decision_before_operator_approval"] = "operator_review"
        report["status"] = "approved"
        operator_approval["aesthetic_warnings_accepted"] = policy["aesthetic_warnings"]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    record = {
        "source": active["source"],
        "source_relative": active["source_relative"],
        "source_sha256": active["source_sha256"],
        "final": str(final_path),
        "final_sha256": active["final_sha256"],
        "report": str(report_path),
        "approved_at_utc": approved_at,
        "approval": "EXPLICIT_USER_CONFIRMATION",
        "automatic_donor_generation_attempts": int(active.get("automatic_donor_generation_attempts", 0)),
        "aesthetic_warnings_accepted": policy["aesthetic_warnings"] if visually_reviewable else [],
    }
    state["approved"] = [
        item for item in state["approved"]
        if item.get("source_relative") != record["source_relative"]
    ]
    state["approved"].append(record)
    state["active"] = None
    save_sequential_state(state)
    logging.info("FINAL APPROVED | %s | next image is now unlocked", source)
    return record


def reject_current_final() -> dict[str, Any]:
    state = load_sequential_state()
    active = state.get("active")
    if not active or active.get("status") != "AWAITING_FINAL_APPROVAL":
        raise ConfigurationFailure("There is no current FINAL awaiting user confirmation.")
    record = {
        "source": active["source"],
        "source_relative": active["source_relative"],
        "source_sha256": active["source_sha256"],
        "final": active.get("final"),
        "rejected_at_utc": utc_now(),
        "approval": "REJECTED_BY_USER",
    }
    state["rejected"].append(record)
    active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
    active["rejected_at_utc"] = record["rejected_at_utc"]
    active["replacement_donor_eligible"] = True
    active["replacement_donor_reason"] = "operator_rejected_final"
    save_sequential_state(state)
    logging.warning("FINAL REJECTED | %s | next image remains blocked", active["source"])
    return record



def approve_rejected_donor_manual_override() -> dict[str, Any]:
    state = load_sequential_state()
    active = state.get("active")
    allowed = {
        "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
        "CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED",
        "AWAITING_EXPLICIT_DONOR_GENERATION",
    }
    if not active or active.get("status") not in allowed:
        raise ConfigurationFailure("There is no rejected donor available for manual approval.")
    source = Path(str(active.get("source", "")))
    candidate = Path(str(active.get("review_image") or active.get("donor") or ""))
    if not source.is_file() or file_sha256(source) != active.get("source_sha256"):
        raise ConfigurationFailure("Manual approval is blocked: the source changed after review.")
    if not candidate.is_file():
        raise ConfigurationFailure("Manual approval is blocked: the rejected donor file is unavailable.")

    final_path = out_path(FINAL_FOLDER, source, active["source_sha256"], "MANUAL_OVERRIDE_FINAL")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate, final_path)

    approved_at = utc_now()
    report_path = out_path(REPORT_FOLDER, source, active["source_sha256"], "MANUAL_OVERRIDE_REPORT", ".json")
    report_payload = {
        "pipeline": PIPELINE_TAG,
        "status": "approved_manual_override",
        "source": str(source),
        "source_sha256": active["source_sha256"],
        "candidate": str(candidate),
        "candidate_sha256": file_sha256(candidate),
        "final": str(final_path),
        "final_sha256": file_sha256(final_path),
        "manual_override": True,
        "automatic_gate_decision": active.get("technical_decision"),
        "automatic_failed_checks": list(active.get("failed_checks", [])),
        "automatic_mandatory_failures": list(active.get("mandatory_failures", [])),
        "operator_final_approval": {
            "required": True,
            "status": "APPROVED_BY_USER_MANUAL_OVERRIDE",
            "approved_at_utc": approved_at,
            "confirmation": "EXPLICIT_USER_OVERRIDE_OF_TECHNICAL_REJECTION",
            "next_source_unlocked": True,
        },
        "warning": "This FINAL was explicitly accepted by the operator despite automatic technical rejection.",
    }
    report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    record = {
        "source": active["source"],
        "source_relative": active["source_relative"],
        "source_sha256": active["source_sha256"],
        "final": str(final_path),
        "final_sha256": file_sha256(final_path),
        "report": str(report_path),
        "approved_at_utc": approved_at,
        "approval": "EXPLICIT_USER_MANUAL_OVERRIDE",
        "manual_override": True,
        "automatic_gate_decision": active.get("technical_decision"),
        "automatic_failed_checks": list(active.get("failed_checks", [])),
    }
    state["approved"] = [
        item for item in state.get("approved", [])
        if item.get("source_relative") != record["source_relative"]
    ]
    state.setdefault("manual_overrides", []).append(record)
    state["approved"].append(record)
    state["active"] = None
    save_sequential_state(state)
    logging.warning(
        "MANUAL OVERRIDE APPROVED | %s | failed checks accepted by operator: %s | next image unlocked",
        source,
        ", ".join(record["automatic_failed_checks"]) or "unspecified",
    )
    return record


def reject_rejected_donor_candidate() -> dict[str, Any]:
    state = load_sequential_state()
    active = state.get("active")
    allowed = {
        "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
        "CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED",
        "AWAITING_EXPLICIT_DONOR_GENERATION",
    }
    if not active or active.get("status") not in allowed:
        raise ConfigurationFailure("There is no rejected donor candidate awaiting operator decision.")
    candidate = active.get("review_image") or active.get("donor")
    record = {
        "source": active["source"],
        "source_relative": active["source_relative"],
        "source_sha256": active["source_sha256"],
        "candidate": candidate,
        "candidate_sha256": file_sha256(Path(str(candidate))) if candidate and Path(str(candidate)).is_file() else None,
        "rejected_at_utc": utc_now(),
        "approval": "REJECTED_BY_USER_AFTER_TECHNICAL_HOLD",
        "automatic_gate_decision": active.get("technical_decision"),
        "automatic_failed_checks": list(active.get("failed_checks", [])),
    }
    state.setdefault("rejected", []).append(record)
    active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
    active["rejected_at_utc"] = record["rejected_at_utc"]
    active["replacement_donor_eligible"] = True
    active["replacement_donor_reason"] = "operator_rejected_technical_hold_candidate"
    save_sequential_state(state)
    logging.warning("REJECTED CANDIDATE | %s | current image remains active for regeneration", active["source"])
    return record


def _sequence_summary(
    log_path: Path,
    state: dict[str, Any],
    status: str,
    results: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "pipeline": PIPELINE_TAG,
        "model": CFG["model"],
        "target": list(TARGET),
        "mode": "ONE_IMAGE_THEN_EXPLICIT_FINAL_APPROVAL",
        "batch_processing_enabled": False,
        "operator_approval_required_before_next_image": True,
        "automatic_current_source_donor_enabled": bool(CFG.get("auto_generate_missing_raw_for_current_image", False)),
        "max_automatic_donor_generations_per_source": int(CFG.get("max_automatic_donor_generations_per_source", 1)),
        "status": status,
        "processed_this_run": sum(1 for result in results or [] if "file" in result),
        "approved_image_count": len(state.get("approved", [])),
        "active": state.get("active"),
        "results": results or [],
        "log": str(log_path),
        "sequential_state": str(sequential_state_path()),
        "completed_at_utc": utc_now(),
    }
    if error is not None:
        summary["error"] = error
    return summary


def create_failure_diagnostic_package(
    source: Path,
    result: dict[str, Any],
    state: dict[str, Any],
    report: dict[str, Any],
    stage: str = "final_validation",
) -> Path:
    digest = file_sha256(source)
    validation = report.get("validation", {})
    release = final_release_policy(validation)
    trace_payloads: list[dict[str, Any]] = []
    if TRACE_FOLDER.exists():
        matching_traces = sorted(
            (path for path in TRACE_FOLDER.glob("*.json") if digest[:10] in path.name),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:3]
        for path in matching_traces:
            try:
                trace_payloads.append({
                    "path": str(path),
                    "trace": json.loads(path.read_text(encoding="utf-8")),
                })
            except (OSError, json.JSONDecodeError) as exc:
                trace_payloads.append({"path": str(path), "error": str(exc)})
    package = DIAG_FOLDER / "diagnostic_packages" / (
        f"{source.stem}_{digest[:16]}_{PIPELINE_TAG}_DIAGNOSTIC.json"
    )
    package.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pipeline": PIPELINE_TAG,
        "created_at_utc": utc_now(),
        "stage": stage,
        "source": str(source),
        "source_sha256": digest,
        "decision": validation.get("decision", result.get("status")),
        "result_status": result.get("status"),
        "failed_checks": release["failed_checks"] or list(result.get("failed_checks", [])),
        "mandatory_failures": release["mandatory_failures"],
        "aesthetic_warnings": release["aesthetic_warnings"],
        "failed_tile_count": release["failed_tile_count"],
        "failed_tiles": release["failed_tiles"],
        "checkpoint_reused": bool(report.get("checkpoint_reused", result.get("checkpoint_reused", False))),
        "api_request_count": int(report.get("request_count", result.get("request_count", 0))),
        "candidate_attempts": [
            {
                "stage": candidate.get("stage"),
                "intensity": candidate.get("intensity"),
                "passed": candidate.get("passed"),
                "failed_checks": candidate.get("failed_checks", []),
            }
            for candidate in report.get("candidate_ladder", [])
        ],
        "active_state": state.get("active"),
        "review_image": result.get("output", result.get("donor")),
        "final_report": result.get("report", result.get("provenance")),
        "api_traces": trace_payloads,
    }
    package.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return package


def run_one_by_one(args: argparse.Namespace, log_path: Path) -> tuple[int, dict[str, Any]]:
    state = load_sequential_state()
    recover_excluded_service_source(state)
    if args.approve_final:
        approval = approve_current_final()
        return 0, _sequence_summary(log_path, load_sequential_state(), "FINAL_APPROVED", [approval])
    if args.approve_rejected_donor:
        approval = approve_rejected_donor_manual_override()
        return 0, _sequence_summary(log_path, load_sequential_state(), "MANUAL_OVERRIDE_APPROVED", [approval])
    if args.reject_rejected_donor:
        rejection = reject_rejected_donor_candidate()
        return EXIT_WAITING_FOR_DONOR, _sequence_summary(
            log_path, load_sequential_state(), "REJECTED_CANDIDATE_AWAITING_REGENERATION", [rejection]
        )
    if args.reject_final:
        rejection = reject_current_final()
        return EXIT_WAITING_FOR_FINAL_APPROVAL, _sequence_summary(
            log_path, load_sequential_state(), "FINAL_REJECTED_NEXT_IMAGE_BLOCKED", [rejection]
        )
    if args.sequence_status:
        return 0, _sequence_summary(log_path, state, "SEQUENTIAL_STATUS")
    if args.limit > 1:
        raise ConfigurationFailure("Batch processing is disabled. Exactly one image may run per invocation.")

    files = discover_source_images()
    if not files:
        logging.error("No source images found in %s", SOURCE)
        return 2, _sequence_summary(log_path, state, "NO_SOURCE_IMAGES")
    source = select_current_source(state, files)
    if source is None:
        logging.info("QUEUE COMPLETE | every available FINAL was explicitly approved")
        return EXIT_ALL_IMAGES_APPROVED, _sequence_summary(log_path, state, "ALL_IMAGES_APPROVED")

    active = _begin_current_source(state, source)
    if args.verified_regeneration:
        if not args.force_generation or not args.donor_only or not args.auto_generate_current:
            raise ConfigurationFailure(
                "Verified regeneration must authorize exactly one fresh donor for the current image only."
            )
        successes = int(active.get("v862_verified_generation_successes", 0))
        paid_attempts = int(active.get("v862_paid_generation_attempts", 0))
        allowed = int(CFG.get("max_verified_regenerations_per_source", 1))
        if successes >= allowed:
            receipt = Path(str(active.get("v862_verified_generation_receipt", "")))
            donor = Path(str(active.get("donor", "")))
            if receipt.is_file() and donor.is_file():
                active["status"] = "AWAITING_RAW_REVIEW"
                save_sequential_state(state)
                logging.info("VERIFIED GENERATION ALREADY COMMITTED | reusing paid RAW without another API request")
                return 0, _sequence_summary(
                    log_path, state, "VERIFIED_GENERATION_ALREADY_COMMITTED",
                    [{"file": str(source), "status": "paid_raw_recovered", "api_request_made": False}],
                )
            raise ConfigurationFailure(
                "A successful generation is recorded but its receipt or RAW is missing. "
                "No API request was made; inspect the diagnostic state before authorizing another charge."
            )
        if paid_attempts >= allowed:
            active["replacement_donor_eligible"] = True
            active["replacement_donor_reason"] = "v862_paid_raw_failed_technical_eligibility"
            save_sequential_state(state)
            logging.warning(
                "PAID V8.6.9 RAW WAS TECHNICALLY REJECTED | retry budget is checked before another request"
            )
            return EXIT_REPLACEMENT_DONOR_APPROVAL_REQUIRED, _sequence_summary(
                log_path,
                state,
                "REPLACEMENT_DONOR_APPROVAL_REQUIRED",
                [{"file": str(source), "status": "paid_raw_rejected", "api_request_made": False}],
            )
        for key_name in ("final", "report", "final_sha256", "technical_decision"):
            active.pop(key_name, None)
        active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
        active["v862_verified_generation_reservation"] = {
            "status": "RESERVED_NOT_SPENT",
            "reserved_at_utc": utc_now(),
            "source_sha256": active["source_sha256"],
        }
        active["v862_requires_fresh_api_generation"] = True
        save_sequential_state(state)
    if active.get("status") == "AWAITING_FINAL_APPROVAL":
        if args.open_final and active.get("final"):
            _open_final_for_review(Path(str(active["final"])))
        if args.donor_only or args.force_generation or not args.force_processing:
            logging.warning(
                "WAITING FOR FINAL APPROVAL | %s | next image and paid generation remain blocked",
                source,
            )
            return EXIT_WAITING_FOR_FINAL_APPROVAL, _sequence_summary(
                log_path, state, "AWAITING_FINAL_APPROVAL"
            )

    if (
        args.auto_generate_current
        and args.authorize_replacement_donor
        and bool(active.get("replacement_donor_eligible"))
        and active.get("status") in {
            "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
            "CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED",
        }
    ):
        active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
        save_sequential_state(state)

    if args.auto_generate_current:
        if not bool(CFG.get("auto_generate_missing_raw_for_current_image", False)):
            raise ConfigurationFailure("Automatic current-image RAW generation is disabled.")
        if not args.force_generation or not args.donor_only:
            raise ConfigurationFailure(
                "Automatic generation must create one donor only; it cannot bypass FINAL review."
            )
        if active.get("status") != "AWAITING_EXPLICIT_DONOR_GENERATION":
            raise ConfigurationFailure(
                "Automatic RAW generation requires the current image to have no approved checkpoint."
            )
        attempts = int(active.get("v869_automatic_donor_generation_attempts", 0))
        allowed_attempts = int(CFG.get("max_automatic_donor_generations_per_source", 1))
        replacement_attempts = int(active.get("replacement_donor_generation_attempts", 0))
        replacement_eligible = bool(active.get("replacement_donor_eligible"))
        replacement_authorized = bool(args.authorize_replacement_donor)
        if replacement_authorized and not replacement_eligible:
            raise ConfigurationFailure(
                "Replacement donor authorization is not applicable to this image."
            )
        if attempts >= allowed_attempts and not replacement_authorized and not args.verified_regeneration:
            raise ConfigurationFailure(
                f"The V8.6.9 automatic generation budget ({allowed_attempts} paid attempts) "
                "for this image is exhausted. The same image remains active; no later image was processed."
            )
        replacement_limit = int(CFG.get("max_replacement_donor_generations_per_source", 3))
        if replacement_authorized and replacement_attempts >= replacement_limit:
            raise ConfigurationFailure(
                "The explicitly authorized replacement-donor budget is exhausted for this image."
            )
        if not args.verified_regeneration:
            active["v869_automatic_donor_generation_attempts"] = attempts + 1
            active["automatic_donor_generation_attempts"] = int(
                active.get("automatic_donor_generation_attempts", 0)
            ) + 1
        if replacement_authorized:
            active["replacement_donor_generation_attempts"] = replacement_attempts + 1
            active["replacement_donor_authorized_at_utc"] = utc_now()
            active["replacement_donor_eligible"] = False
        active["automatic_generation_authorized_at_utc"] = utc_now()
        active["automatic_generation_source_sha256"] = active["source_sha256"]
        save_sequential_state(state)
        logging.info(
            "VERIFIED FRESH GENERATION | Exactly one paid API request authorized for current image only: %s",
            source,
        )

    logging.info("[CURRENT IMAGE ONLY] %s", source)
    force_processing = args.force_processing or (
        not args.donor_only
        and active.get("status") in {
            "AWAITING_RAW_REVIEW",
            "FINAL_REJECTED_REPROCESS_SAME_IMAGE",
            "CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED",
            "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
        }
    )
    try:
        result = process(
            source,
            key=None,
            force_generation=args.force_generation,
            force_processing=force_processing,
            allow_generation=args.allow_generation or args.force_generation,
            donor_only=args.donor_only,
        )
    except ConfigurationFailure:
        raise
    except RejectedDonorAvailable as exc:
        message = str(exc)
        active["status"] = "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED"
        active["technical_decision"] = "REJECTED_EXISTING_DONOR"
        active["review_image"] = exc.candidate
        active["failed_checks"] = list(exc.failed_checks)
        active["mandatory_failures"] = list(exc.failed_checks)
        active["next_source_blocked"] = True
        active["replacement_donor_eligible"] = True
        active["replacement_donor_reason"] = "existing_exact_source_donor_failed_technical_review"
        active["api_request_made"] = False
        save_sequential_state(state)
        logging.error("REJECTED DONOR AVAILABLE FOR REVIEW | %s | %s", exc.candidate or "candidate unavailable", message)
        return 3, _sequence_summary(
            log_path,
            state,
            "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
            [{"file": str(source), "status": "rejected_donor_review", "candidate": exc.candidate,
              "failed_checks": list(exc.failed_checks), "api_request_made": False}],
            message,
        )
    except Exception as exc:
        message = str(exc)
        if "No API request was made" in message:
            if args.verified_regeneration:
                active["v862_verified_generation_reservation"] = {
                    "status": "RELEASED_NO_API_REQUEST",
                    "released_at_utc": utc_now(),
                    "reason": message,
                }
            active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
            active["api_request_made"] = False
            save_sequential_state(state)
            logging.error("CURRENT IMAGE NEEDS A RAW | %s | %s", source, message)
            exit_code = (
                EXIT_REPLACEMENT_DONOR_APPROVAL_REQUIRED
                if bool(active.get("replacement_donor_eligible"))
                and (
                    int(active.get("v869_automatic_donor_generation_attempts", 0))
                    >= int(CFG.get("max_automatic_donor_generations_per_source", 1))
                    or int(active.get("v862_paid_generation_attempts", 0)) >= 1
                )
                else EXIT_WAITING_FOR_DONOR
            )
            return exit_code, _sequence_summary(
                log_path,
                state,
                "AWAITING_EXPLICIT_DONOR_GENERATION",
                [{"file": str(source), "status": "blocked_no_checkpoint", "api_request_made": False}],
                message,
            )
        active["status"] = "CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED"
        active["error"] = message
        if args.verified_regeneration:
            active["v862_verified_generation_reservation"] = {
                "status": "FAILED_WITHOUT_SUCCESS_RECEIPT",
                "failed_at_utc": utc_now(),
                "reason": message,
            }
        save_sequential_state(state)
        logging.exception("FAILED CURRENT IMAGE %s", source)
        return 1, _sequence_summary(
            log_path, state, "CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED",
            [{"file": str(source), "status": "failed", "error": message}], message
        )

    result_status = str(result.get("status", ""))
    if args.verified_regeneration:
        receipt_value = result.get("generation_receipt")
        receipt_path = Path(str(receipt_value or ""))
        if int(result.get("request_count", 0)) != 1 or not receipt_path.is_file():
            active["v862_verified_generation_reservation"] = {
                "status": "RELEASED_MISSING_SUCCESS_RECEIPT",
                "released_at_utc": utc_now(),
                "reported_request_count": int(result.get("request_count", 0)),
                "reported_receipt": str(receipt_value or ""),
            }
            active["status"] = "AWAITING_EXPLICIT_DONOR_GENERATION"
            save_sequential_state(state)
            raise ConfigurationFailure(
                "Generation returned without a successful API receipt. The paid budget was not consumed."
            )
        active["v862_paid_generation_attempts"] = int(
            active.get("v862_paid_generation_attempts", 0)
        ) + 1
        if result_status == "DONOR_READY_FOR_WHOLE_SCENE_REVIEW":
            active["v862_verified_generation_successes"] = int(
                active.get("v862_verified_generation_successes", 0)
            ) + 1
            active["v862_verified_generation_receipt"] = str(receipt_path)
            active["v862_verified_generation_reservation"] = {
                "status": "COMMITTED_AFTER_RECEIPT_AND_TECHNICAL_ELIGIBILITY",
                "committed_at_utc": utc_now(),
                "receipt": str(receipt_path),
                "technical_decision": result_status,
            }
            active["donor"] = result.get("donor")
            logging.info(
                "ELIGIBLE RAW COMMITTED | receipt and DONOR_READY_FOR_WHOLE_SCENE_REVIEW verified"
            )
        else:
            active["v862_verified_generation_reservation"] = {
                "status": "API_REQUEST_CONSUMED_RAW_TECHNICALLY_REJECTED",
                "recorded_at_utc": utc_now(),
                "receipt": str(receipt_path),
                "technical_decision": result_status,
            }
            active["v862_rejected_generation_receipt"] = str(receipt_path)
            logging.error(
                "RAW NOT COMMITTED | provider response exists but technical decision is %s",
                result_status or "UNKNOWN",
            )
        save_sequential_state(state)

    if result_status in {"approved", "operator_review", "skipped"}:
        final_path = Path(str(result["output"]))
        report_path = Path(str(result["report"]))
        if not final_path.exists() or not report_path.exists():
            raise ConfigurationFailure("The current FINAL or its technical report was not created.")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        release = final_release_policy(report.get("validation", {}))
        actual_status = str(report.get("status", result_status))
        if actual_status == "operator_review" and not release["operator_reviewable"]:
            raise ConfigurationFailure(
                "The current FINAL cannot be visually approved because a mandatory safety check failed."
            )
        active.update({
            "status": "AWAITING_FINAL_APPROVAL",
            "final": str(final_path),
            "final_sha256": file_sha256(final_path),
            "report": str(report_path),
            "completed_at_utc": utc_now(),
            "next_source_blocked": True,
            "technical_release_mode": actual_status,
            "technical_warnings": release["aesthetic_warnings"],
        })
        save_sequential_state(state)
        if release["aesthetic_warnings"]:
            logging.warning(
                "FINAL READY FOR VISUAL DECISION | %s | camera and architecture locked | aesthetic warnings: %s",
                final_path,
                ", ".join(release["aesthetic_warnings"]),
            )
        else:
            logging.info("FINAL READY | %s | inspect and explicitly approve before the next image", final_path)
        if args.open_final:
            _open_final_for_review(final_path)
        return 0, _sequence_summary(log_path, state, "AWAITING_FINAL_APPROVAL", [result])

    if result_status == "DONOR_READY_FOR_WHOLE_SCENE_REVIEW":
        active.update({
            "status": "AWAITING_RAW_REVIEW",
            "donor": result.get("donor"),
            "provenance": result.get("provenance"),
            "generation_receipt": result.get("generation_receipt"),
            "next_source_blocked": True,
        })
        save_sequential_state(state)
        return 0, _sequence_summary(log_path, state, "AWAITING_RAW_REVIEW", [result])

    report_path_value = result.get("report", result.get("provenance"))
    report: dict[str, Any] = {}
    if report_path_value:
        report_path = Path(str(report_path_value))
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
    validation = report.get("validation", {})
    release = final_release_policy(validation)
    whole_scene = report.get("whole_scene_quality_gate", {})
    whole_scene_failed = sorted(
        name for name, passed in whole_scene.get("checks", {}).items()
        if not bool(passed)
    )
    if not release["failed_checks"] and whole_scene_failed:
        release["failed_checks"] = whole_scene_failed
    review_value = result.get("output", result.get("donor"))
    active.update({
        "status": "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
        "technical_decision": result_status,
        "review_image": str(review_value) if review_value else None,
        "report": str(report_path_value) if report_path_value else None,
        "failed_checks": release["failed_checks"] or list(result.get("failed_checks", [])),
        "mandatory_failures": release["mandatory_failures"],
        "next_source_blocked": True,
    })
    if (
        result_status in {
            "REJECTED_CAMERA_VIEW",
            "REJECTED_WHOLE_SCENE",
            "REJECTED_MATERIAL_IDENTITY",
        }
        and int(active.get("replacement_donor_generation_attempts", 0))
        < int(CFG.get("max_replacement_donor_generations_per_source", 3))
    ):
        active["replacement_donor_eligible"] = True
        active["replacement_donor_reason"] = "whole_scene_technical_rejection"
    diagnostic = create_failure_diagnostic_package(source, result, state, report)
    active["diagnostic_package"] = str(diagnostic)
    save_sequential_state(state)
    logging.error(
        "FINAL TECHNICAL HOLD | %s | failed checks: %s",
        source,
        ", ".join(release["failed_checks"]) or result_status or "unknown",
    )
    if release["failed_tiles"]:
        logging.error("FAILED GRID TILES | %s", json.dumps(release["failed_tiles"], ensure_ascii=False))
    if review_value:
        logging.error("REVIEW IMAGE | %s", review_value)
        review_path = Path(str(review_value))
        if args.open_final and review_path.exists():
            _open_final_for_review(review_path)
    logging.error("DIAGNOSTIC PACKAGE | %s", diagnostic)
    return 3, _sequence_summary(log_path, state, active["status"], [result])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MG V8.7.4 Canvas Integrity Web Review Console / Resumable Queue"
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--offline-demo", action="store_true")
    parser.add_argument("--validator-self-test", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force-generation", action="store_true")
    parser.add_argument("--allow-generation", action="store_true")
    parser.add_argument("--force-processing", action="store_true")
    parser.add_argument("--donor-only", action="store_true")
    parser.add_argument("--auto-generate-current", action="store_true")
    parser.add_argument("--authorize-replacement-donor", action="store_true")
    parser.add_argument("--verified-regeneration", action="store_true")
    parser.add_argument("--open-final", action="store_true")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--approve-final", action="store_true")
    actions.add_argument("--approve-rejected-donor", action="store_true")
    actions.add_argument("--reject-rejected-donor", action="store_true")
    actions.add_argument("--reject-final", action="store_true")
    actions.add_argument("--sequence-status", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return offline_self_test(False, compact=args.compact)
    if args.offline_demo:
        return offline_self_test(True)
    if args.validator_self_test:
        return regression_validator_self_test()
    log_path = setup_log()
    try:
        try:
            exit_code, summary = run_one_by_one(args, log_path)
        except ConfigurationFailure as exc:
            logging.error("CONFIGURATION BLOCK | %s", exc)
            state = load_sequential_state()
            exit_code = 1
            summary = _sequence_summary(log_path, state, "CONFIGURATION_BLOCK", error=str(exc))
        (DIAG_FOLDER / "last_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return exit_code
    finally:
        close_log_handlers()


if __name__ == "__main__":
    raise SystemExit(main())
