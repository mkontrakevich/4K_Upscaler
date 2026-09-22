from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PIL import Image
import web_review_server as server


def main() -> int:
    original = {
        "CONFIG_PATH": server.CONFIG_PATH,
        "CFG": server.CFG,
        "SOURCE_ROOT": server.SOURCE_ROOT,
        "OUTPUT_ROOT": server.OUTPUT_ROOT,
        "DIAG_ROOT": server.DIAG_ROOT,
        "SAFE_STATE": server.SAFE_STATE,
        "GEN_STATE": server.GEN_STATE,
        "WEB_SETTINGS": server.WEB_SETTINGS,
        "runtime_mode": server.RUNTIME.mode,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="mg_v879_folder_picker_") as temporary:
            folder = Path(temporary)
            selected = folder / "selected sources"
            selected.mkdir()
            original_image = selected / "mg (1).png"
            Image.new("RGB", (32, 24), "#447788").save(original_image)
            config_path = folder / "config.json"
            server.CONFIG_PATH = config_path
            server.CFG = dict(server.CFG)
            count = server.activate_source_folder(selected)
            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            assert count == 1
            assert Path(persisted["source"]) == selected.resolve()
            assert server.SOURCE_ROOT == selected.resolve()
            assert server.OUTPUT_ROOT == selected.resolve() / server.CFG["output_folder"]
            assert not server.OUTPUT_ROOT.exists(), "folder selection must not start processing"
            result_image = server.OUTPUT_ROOT / "4K_SAFE_LOCAL_UPSCALE" / "mg (1).png"
            result_image.parent.mkdir(parents=True)
            Image.new("RGB", (64, 48), "#337799").save(result_image)
            server.atomic_json(server.SAFE_STATE, {"pending_review": {
                "source": str(original_image), "output": str(result_image)
            }})
            server.RUNTIME.mode = "safe"
            status = server.RUNTIME.public_status("test-token")
            assert status["source"]["url"].startswith("/api/image/source?")
            assert status["result"]["url"].startswith("/api/image/result?")
            assert (status["source"]["width"], status["result"]["width"]) == (32, 64)
            css = (server.WEB_ROOT / "app.css").read_text(encoding="utf-8")
            assert ".empty[hidden]{display:none}" in css, "placeholder must not cover the images"
            second = folder / "another selected source"
            second.mkdir()
            Image.new("RGB", (16, 16)).save(second / "mg (2).png")
            response = server.RUNTIME.set_source_folder_path(str(second))
            assert response["image_count"] == 1 and response["api_request_made"] is False
            assert server.RUNTIME.public_status("test-token")["source"]["name"] == "mg (2).png"
    finally:
        for name, value in original.items():
            if name == "runtime_mode":
                server.RUNTIME.mode = value
            else:
                setattr(server, name, value)
    print("V8.7.9 VISIBLE PREVIEW AND SOURCE FOLDER SELF-TEST PASSED")
    print('{"folder_selection_persisted":true,"preview_urls_visible":true,"path_selection_without_generation":true,"api_requests":0}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
