#!/usr/bin/env python3
"""Post-deploy live smoke: NO HDR, no QA approval and NO paid AI generation."""
import argparse
import json
import random
import struct
import time
import urllib.error
import urllib.request
import zlib


def chunk(kind, data):
    payload=kind+data
    return struct.pack(">I",len(data))+payload+struct.pack(">I",zlib.crc32(payload)&0xffffffff)


def make_png(width=500,height=500):
    rnd=random.Random(987654321)
    rows=b"".join(b"\x00"+rnd.randbytes(width*3) for _ in range(height))
    png=b"\x89PNG\r\n\x1a\n"
    png+=chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))
    png+=chunk(b"IDAT",zlib.compress(rows,6))
    png+=chunk(b"IEND",b"")
    assert 10_000<len(png)<8*1024*1024
    return png


def request(url,method="GET",headers=None,body=None,timeout=30):
    r=urllib.request.Request(url,method=method,headers=headers or {},data=body)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as reply:
            return reply.status,dict(reply.headers),reply.read()
    except urllib.error.HTTPError as error:
        return error.code,dict(error.headers),error.read()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--url",required=True)
    p.add_argument("--timeout",type=int,default=240)
    opts=p.parse_args()
    root=opts.url.rstrip("/")
    status,_,raw=request(root+"/api/session/start","POST",{},b"{}")
    assert status==201,(status,raw[:500])
    session=json.loads(raw)
    auth={"x-session-id":session["id"],"x-session-token":session["token"]}
    boundary="mg4kEditorialTechSmoke"
    image=make_png()
    form=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"editorial_technical_smoke.png\"\r\n"
          "Content-Type: image/png\r\n\r\n").encode()+image+f"\r\n--{boundary}--\r\n".encode()
    status,_,raw=request(root+"/api/editorial/upload","POST",
                         {**auth,"content-type":"multipart/form-data; boundary="+boundary},form,timeout=60)
    assert status==201,(status,raw[:600])
    created=json.loads(raw)
    assert created.get("job_id") and created.get("technical_processing_started") is False
    assert created.get("job_token") and created.get("source_preview_ready") is True,created
    job=created["job_id"]
    auth["x-editorial-job-token"]=created["job_token"]
    print("EDITORIAL_TECH_SMOKE_CREATED",job,flush=True)
    status,_,raw=request(root+"/api/editorial/job/status?job="+job,headers=auth,timeout=30)
    assert status==200,(status,raw[:500])
    waiting=json.loads(raw)
    assert waiting.get("status")=="AWAITING_TECH_PROCESSING",waiting
    assert waiting.get("source_preview_ready") is True and waiting.get("can_start_processing") is True,waiting
    assert waiting.get("technical_master_ready") is False and waiting.get("can_approve") is False,waiting
    status,headers,pixels=request(root+"/api/editorial/job/source?job="+job,headers=auth,timeout=60)
    assert status==200 and headers.get("Content-Type","").startswith("image/png"),status
    assert len(pixels)==len(image) and pixels==image
    denied,_,_=request(root+"/api/editorial/job/handoff?job="+job,method="POST",
                       headers={**auth,"content-type":"application/json"},
                       body=b'{"target_id":"marins-mg4k"}')
    assert denied==409,denied
    print("EDITORIAL_SOURCE_PREVIEW_READY_WITHOUT_PROCESSING",job,flush=True)
    status,_,raw=request(root+"/api/editorial/job/process?job="+job,method="POST",
                         headers={**auth,"content-type":"application/json"},body=b"{}",timeout=30)
    assert status==202,(status,raw[:500])
    dispatched=json.loads(raw)
    assert dispatched.get("generation_started") is False,dispatched
    again,_,_=request(root+"/api/editorial/job/process?job="+job,method="POST",
                      headers={**auth,"content-type":"application/json"},body=b"{}",timeout=30)
    assert again==409,again
    print("EDITORIAL_TECH_SMOKE_EXPLICITLY_STARTED",job,flush=True)
    deadline=time.monotonic()+opts.timeout
    while time.monotonic()<deadline:
        status,_,raw=request(root+"/api/editorial/job/status?job="+job,headers=auth,timeout=30)
        assert status==200,(status,raw[:500])
        state=json.loads(raw)
        print("TECH",state.get("stage"),state.get("status"),state.get("progress"),flush=True)
        if state.get("status")=="AWAITING_HUMAN_QA":
            assert state.get("technical_master_ready") is True and state.get("can_approve") is True,state
            assert state.get("can_download") is False and state.get("can_handoff") is False,state
            preview,headers,pixels=request(root+"/api/editorial/job/preview?job="+job,headers=auth,timeout=60)
            assert preview==200 and headers.get("Content-Type","").startswith("image/jpeg") and len(pixels)>1000
            denied,_,_=request(root+"/api/editorial/job/download?job="+job,headers=auth)
            assert denied==409,denied
            denied,_,_=request(root+"/api/editorial/job/handoff?job="+job,method="POST",
                               headers={**auth,"content-type":"application/json"},
                               body=b'{"target_id":"marins-mg4k"}')
            assert denied==409,denied
            print("EDITORIAL_LIVE_NO_HDR_TECH_QA_SMOKE_PASS",job,flush=True)
            return
        if state.get("status")=="FAILED":
            raise AssertionError("TECH_WORKFLOW_FAILED: "+json.dumps(state,ensure_ascii=False)[:1500])
        time.sleep(5)
    raise AssertionError("TECH_WORKFLOW_TIMEOUT:"+job)


if __name__=="__main__":
    main()
