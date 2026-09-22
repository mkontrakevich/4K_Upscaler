from __future__ import annotations

import json
from pathlib import Path

import v8_safe_appearance as app


def main() -> int:
    expected_project_root = r"D:\Work\ИИ\MG"
    cfg = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
    configured_source = str(cfg.get("source", "")).rstrip("\\/")

    # The web console may intentionally persist either the MG root or MG\base as
    # the active queue. Both are valid. Relocation itself must always target MG.
    valid_sources = {expected_project_root.casefold(), (expected_project_root + r"\base").casefold()}
    if configured_source.replace("/", "\\").casefold() not in valid_sources:
        raise AssertionError(f"Unexpected selected source queue: {configured_source!r}")

    actual_project_root = str(app._project_data_root()).rstrip("\\/")
    if actual_project_root.replace("/", "\\").casefold() != expected_project_root.casefold():
        raise AssertionError(
            f"Unexpected relocation root: {actual_project_root!r}; expected {expected_project_root!r}"
        )

    fixture = {
        "active": {
            "source": r"C:\Users\kontrakevichMU\Desktop\MG\base\mg (4).jpeg",
            "donor": r"C:\Users\kontrakevichMU\Desktop\MG\GEN_QUALITY_ARCH_LOCK_V8_4_WHOLE_SCENE\02_SEEDREAM_APPEARANCE_DONOR\donor.png",
        },
        "approved": [],
    }
    migrated, count = app._remap_moved_project_paths(fixture)
    if count != 2:
        raise AssertionError(f"Expected 2 migrated paths, got {count}")
    expected_source = expected_project_root + r"\base\mg (4).jpeg"
    if migrated["active"]["source"].casefold() != expected_source.casefold():
        raise AssertionError(
            f"Active source was remapped incorrectly: {migrated['active']['source']!r}"
        )
    if not migrated["active"]["donor"].casefold().startswith((expected_project_root + "\\").casefold()):
        raise AssertionError("Donor path was not remapped to the stable MG project root")
    if "\\base\\base\\" in migrated["active"]["source"].casefold():
        raise AssertionError("Regression: selected MG\\base queue was incorrectly used as relocation root")

    print("V8.8.1 PROJECT-ROOT / SOURCE-QUEUE RELOCATION SELF-TEST PASSED")
    print(json.dumps({
        "configured_source_queue": str(configured_source),
        "project_relocation_root": str(actual_project_root),
        "persisted_paths_remapped": count,
        "double_base_regression_blocked": True,
        "old_c_drive_dependency": False,
        "real_api_requests": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
