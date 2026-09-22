from __future__ import annotations

import tempfile
from pathlib import Path

from source_queue_policy import (
    discover_source_files,
    is_explicit_service_artifact,
    recover_service_active_state,
)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mg_v876_source_queue_") as temporary:
        root = Path(temporary) / "MG"
        output = "GEN_QUALITY_ARCH_LOCK_V8_4_WHOLE_SCENE"
        wanted = [root / "mg (1).jpeg", root / "base" / "mg (2).png"]
        rejected = [
            root / "MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_7_3_LOCAL_WEB_REVIEW_CONSOLE" / "UI_REFERENCE_APPROVED_V873.png",
            root / "MG_GENERATIVE_QUALITY_IMMUTABLE_ARCH_V8_7_5_SELF_HEALING_WEB_BOOTSTRAP" / "random.jpg",
            root / output / "REVIEW_REQUIRED" / "held.png",
            root / "UI_REFERENCE_APPROVED_V875.png",
            root / ".venv" / "fixture.webp",
        ]
        for path in wanted + rejected:
            touch(path)
        discovered = discover_source_files(root, output)
        assert set(discovered) == set(wanted), [str(path.relative_to(root)) for path in discovered]
        assert all(is_explicit_service_artifact(path, root, output) for path in rejected)
        state = {
            "active": {
                "source": str(rejected[0]),
                "source_sha256": "fixture",
                "status": "AWAITING_EXPLICIT_DONOR_GENERATION",
            }
        }
        recovered = recover_service_active_state(state, root, output, lambda: "fixture-time")
        assert recovered == rejected[0]
        assert state["active"] is None
        assert state["status"] == "SERVICE_SOURCE_EXCLUDED_QUEUE_RESUMED"
        assert state["excluded_service_source_recoveries"][-1]["api_request_made"] is False
    print("V8.7.6 SOURCE QUEUE ISOLATION SELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
