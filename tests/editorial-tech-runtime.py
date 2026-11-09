#!/usr/bin/env python3
"""Offline end-to-end HTTP lifecycle of the isolated no-OpenRouter Tech service."""
import hashlib
import io
import json
import tempfile
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import tifffile
import editorial_tech_service as tech

ID="123e4567-e89b-42d3-a456-426614174003"
BAD="123e4567-e89b-42d3-a456-426614174004"

class Handler:
    def __init__(self,method,job,action,payload=b"",sha=""):
        self.path=f"/editorial-tech/jobs/{job}/{action}"
        self.headers={
            "content-length":str(len(payload)),
            "x-source-sha256":sha,
            "x-source-filename":"new-test.png",
        }
        self.rfile=io.BytesIO(payload)
        self.wfile=io.BytesIO()
        self.status=None
        self.out_headers={}
    def send_response(self,status):
        self.status=status
    def send_header(self,name,value):
        self.out_headers[name.lower()]=value
    def end_headers(self):
        pass

def send(method,job,action,payload=b"",sha=""):
    handler=Handler(method,job,action,payload,sha)
    assert tech.route(handler,method) is True
    return handler

with tempfile.TemporaryDirectory() as tmp:
    tech.ROOT=Path(tmp)
    # Real image bytes above the upload minimum.
    pixels=np.random.default_rng(22).integers(0,255,size=(480,620,3),dtype=np.uint8)
    ok,encoded=cv2.imencode(".png",pixels)
    assert ok
    raw=encoded.tobytes()
    checksum=hashlib.sha256(raw).hexdigest()
    invalid=send("POST",BAD,"start",raw,"0"*64)
    assert invalid.status==409
    start=send("POST",ID,"start",raw,checksum)
    assert start.status==202
    for _ in range(140):
        time.sleep(.1)
        result=send("GET",ID,"status")
        assert result.status==200
        status=json.loads(result.wfile.getvalue())
        if status["status"] in ("READY","FAILED"):
            break
    assert status["status"]=="READY",status
    proof=send("GET",ID,"qa")
    assert proof.status==200,proof.wfile.getvalue()
    qa=json.loads(proof.wfile.getvalue())
    assert qa["job_id"]==ID and qa["source_sha256"]==checksum
    assert qa["HDR_applied"] is False and qa["master_bit_depth"]==16
    master=send("GET",ID,"master")
    assert master.status==200 and master.out_headers["content-type"]=="image/tiff"
    master_bytes=master.wfile.getvalue()
    assert hashlib.sha256(master_bytes).hexdigest()==qa["master_sha256"]
    out=tifffile.imread(io.BytesIO(master_bytes))
    assert out.shape==(480,620,3) and out.dtype==np.uint16
    preview=send("GET",ID,"preview")
    assert preview.status==200 and preview.out_headers["content-type"]=="image/jpeg"
    assert hashlib.sha256(preview.wfile.getvalue()).hexdigest()==qa["preview_sha256"]
    again=send("POST",ID,"start",raw,checksum)
    assert again.status==200 and json.loads(again.wfile.getvalue())["status"]=="READY"
    conflict=send("POST",ID,"start",raw,"f"*64)
    assert conflict.status==409
    print("EDITORIAL_TECH_OFFLINE_HTTP_E2E_PASS")
