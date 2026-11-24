#!/usr/bin/env python3
"""RAW thumbnail-only private container HTTP lifecycle, without any technical master."""
import hashlib
import io
import json
import sys
import tempfile
import time
import types
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editorial_tech_service as tech

# Test the *separate preview processing path* without using real production RAW
# or starting an MG4K processor. This is deliberately an isolated mock decoder.
rng = np.random.default_rng(84)
image = rng.integers(0, 255, (600, 900, 3), dtype=np.uint8)
jpeg_ok, jpeg_data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
assert jpeg_ok and len(jpeg_data) > 10000

class FakeRAW:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_thumb(self):
        return types.SimpleNamespace(format=1, data=jpeg_data.tobytes())

    def postprocess(self, **kwargs):
        raise AssertionError("full RAW development is forbidden in thumbnail smoke")

fake_rawpy = types.SimpleNamespace(
    ThumbFormat=types.SimpleNamespace(JPEG=1),
    imread=lambda _path: FakeRAW(),
    ColorSpace=types.SimpleNamespace(sRGB="sRGB")
)
previous = sys.modules.get("rawpy")
sys.modules["rawpy"] = fake_rawpy

class Handler:
    def __init__(self, job, action, data=b"", sha=""):
        self.path = "/editorial-tech/jobs/" + job + "/" + action
        self.headers = {
            "content-length": str(len(data)),
            "x-source-sha256": sha,
            "x-source-filename": "sample.arw"
        }
        self.rfile = io.BytesIO(data)
        self.wfile = io.BytesIO()
        self.code = None
        self.headers_out = {}

    def send_response(self, code):
        self.code = code

    def send_header(self, key, value):
        self.headers_out[key.lower()] = value

    def end_headers(self):
        return None

def api(method, job, action, data=b"", sha=""):
    h = Handler(job, action, data, sha)
    assert tech.route(h, method)
    return h

try:
    with tempfile.TemporaryDirectory() as temp:
        tech.ROOT = Path(temp)
        source = np.random.default_rng(985).integers(0, 255, 32000, dtype=np.uint8).tobytes()
        digest = hashlib.sha256(source).hexdigest()
        job = "123e4567-e89b-42d3-a456-426614174008"

        bad_job = "123e4567-e89b-42d3-a456-426614174009"
        bad = api("POST", bad_job, "preview-start", source, "f"*64)
        assert bad.code == 409
        assert not (Path(temp)/bad_job/"preview-source").exists()
        # A rejected upload is terminal for that job ID; use an independent
        # immutable job for the successful preview instead of reviving it.

        ready_call = api("POST", job, "preview-start", source, digest)
        assert ready_call.code == 202
        assert not (Path(temp)/job/"technical").exists()
        assert not tech._status(job), "technical process must not start from preview"

        for _ in range(60):
            st = api("GET", job, "preview-status")
            result = json.loads(st.wfile.getvalue())
            if result.get("status") in ("READY", "FAILED"):
                break
            time.sleep(.1)
        assert result.get("status") == "READY", result
        assert result["source_sha256"] == digest
        assert result["generation_started"] is False
        assert result["HDR_applied"] is False
        preview = api("GET", job, "preview-image")
        assert preview.code == 200
        assert preview.headers_out["content-type"] == "image/jpeg"
        out = preview.wfile.getvalue()
        assert hashlib.sha256(out).hexdigest() == result["preview_sha256"]
        assert len(out) == result["preview_bytes"]
        assert not (Path(temp)/job/"technical").exists()
        assert not tech._status(job)
        again = api("POST", job, "preview-start", source, digest)
        assert again.code == 200, again.wfile.getvalue()
        assert json.loads(again.wfile.getvalue())["status"] == "READY"
        print("RAW_PREVIEW_ONLY_PRIVATE_HTTP_E2E_PASS")
finally:
    if previous is None:
        del sys.modules["rawpy"]
    else:
        sys.modules["rawpy"] = previous
