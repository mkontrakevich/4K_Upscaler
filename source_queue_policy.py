from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterable


SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

EXCLUDED_DIRECTORY_NAMES = {
    ".venv",
    ".qa_venv",
    "__pycache__",
    "4k_output",
    "dci_4k_output",
    "rerender_dci",
    "seedream_dci",
    "seedream_dci_v4",
    "seedream_dci_v5",
    "immutable_uhd4k_v6",
    "gen_quality_arch_lock_v7",
    "gen_quality_arch_lock_v8",
    "gen_quality_arch_lock_v8_1_fresh",
    "gen_quality_arch_lock_v8_2_verified_fresh",
    "gen_quality_arch_lock_v8_3_water_gate",
    "gen_quality_arch_lock_v8_4_whole_scene",
    "nano_banana_pro",
    "_diagnostics",
}

EXCLUDED_DIRECTORY_PREFIXES = (
    "mg_generative_quality_immutable_arch_",
)

EXCLUDED_FILE_PREFIXES = (
    "ui_reference_",
)


def _relative(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return None


def is_source_candidate(path: Path, root: Path, output_folder: str) -> bool:
    """Admit user imagery and reject application, output, UI and diagnostic assets."""
    if not path.is_file() or path.suffix.casefold() not in SUPPORTED_IMAGES:
        return False
    relative = _relative(path, root)
    if relative is None:
        return False
    output_name = output_folder.casefold()
    for part in relative.parts[:-1]:
        folded = part.casefold()
        if folded == output_name or folded in EXCLUDED_DIRECTORY_NAMES:
            return False
        if any(folded.startswith(prefix) for prefix in EXCLUDED_DIRECTORY_PREFIXES):
            return False
    filename = relative.name.casefold()
    if any(filename.startswith(prefix) for prefix in EXCLUDED_FILE_PREFIXES):
        return False
    return True


def natural_key(path: Path, root: Path) -> tuple[tuple[int, Any], ...]:
    relative = _relative(path, root)
    value = relative.as_posix() if relative is not None else path.name
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    )


def discover_source_files(root: Path, output_folder: str) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.rglob("*") if is_source_candidate(path, root, output_folder)),
        key=lambda path: natural_key(path, root),
    )


def is_explicit_service_artifact(path: Path, root: Path, output_folder: str) -> bool:
    """True only for an existing image rejected by the explicit service-asset policy."""
    relative = _relative(path, root)
    return bool(
        path.is_file()
        and path.suffix.casefold() in SUPPORTED_IMAGES
        and relative is not None
        and not is_source_candidate(path, root, output_folder)
    )


def normalized_paths(paths: Iterable[Path]) -> set[str]:
    return {str(path.resolve(strict=False)).casefold() for path in paths}


def recover_service_active_state(
    state: dict[str, Any],
    root: Path,
    output_folder: str,
    utc_now: Callable[[], str],
) -> Path | None:
    """Clear only an explicitly identified service asset from GENERATIVE state."""
    active = state.get("active")
    if not isinstance(active, dict):
        return None
    source = Path(str(active.get("source", "")))
    if not is_explicit_service_artifact(source, root, output_folder):
        return None
    state.setdefault("excluded_service_source_recoveries", []).append({
        "source": str(source),
        "source_sha256": active.get("source_sha256"),
        "previous_status": active.get("status"),
        "recovered_at_utc": utc_now(),
        "reason": "V8.7.6 excluded an application or UI asset from the user image queue",
        "api_request_made": False,
    })
    state["active"] = None
    state["status"] = "SERVICE_SOURCE_EXCLUDED_QUEUE_RESUMED"
    return source
