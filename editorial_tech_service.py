"""Private container-side no-generation technical raster service.

The caller is the Cloudflare Worker container stub, not a browser endpoint.
New editorial jobs cannot invoke the existing paid /jobs/{id}/start pipeline.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import unquote

ROOT=Path(os.environ.get("MG4K_EDITORIAL_TECH_ROOT","/tmp/mg4k-editorial-tech")).resolve()
ROOT.mkdir(parents=True,exist_ok=True)
ID=re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$",re.I)
SHA=re.compile(r"^[0-9a-f]{64}$",re.I)
STATES={}
MUTEX=threading.RLock()
PROCESS_SLOT=threading.Lock()
MAX_SOURCE=8*1024*1024

def _path(job_id: str)->Path:
    return ROOT/job_id

def _status(job_id: str)->dict:
    with MUTEX:
        return dict(STATES.get(job_id) or {})

def _set(job_id: str,**values)->None:
    with MUTEX:
        current=STATES.setdefault(job_id,{"job_id":job_id})
        current.update(values)

def _start_worker(job_id: str,filename: str,expected_sha: str)->None:
    try:
        _set(job_id,status="PROCESSING",stage="waiting_for_processing_slot",progress=5)
        with PROCESS_SLOT:
            _set(job_id,status="PROCESSING",stage="technical_master",progress=20)
            jobdir=_path(job_id)
            source=jobdir/"source"
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=expected_sha:
                raise ValueError("source_sha256_mismatch")
            script=Path(__file__).resolve().parent/"scripts"/"editorial_raster_technical_master.py"
            cmd=[sys.executable,str(script),"--input",str(source),"--out",str(jobdir/"technical"),
                 "--job-id",job_id,"--source-sha256",expected_sha]
            correction_path=jobdir/"correction-plan.json"
            if correction_path.exists():
                cmd+=["--correction-plan",str(correction_path)]
            outcome=subprocess.run(cmd,capture_output=True,text=True,timeout=480,check=False)
            if outcome.returncode!=0:
                reason=outcome.stderr[-1200:]
                if "CORRECTION_HOLD:" in reason:
                    raise ValueError("CORRECTION_HOLD:"+reason.split("CORRECTION_HOLD:")[-1].splitlines()[0][:180])
                raise RuntimeError("technical_script_failed:"+reason)
            qa=json.loads((jobdir/"technical"/"qa.json").read_text(encoding="utf-8"))
            if qa.get("technical_status")!="TECHNICAL_QA_PASS" or qa.get("HDR_applied") is not False or qa.get("master_bit_depth")!=16:
                raise RuntimeError("technical_qa_failed")
            if qa.get("job_id")!=job_id or qa.get("source_sha256")!=expected_sha:
                raise RuntimeError("technical_lineage_failed")
            if correction_path.exists():
                p=json.loads(correction_path.read_text(encoding="utf-8"))
                proof=qa.get("correction")
                if qa.get("correction_status")!="APPLIED" or proof is None or (
                   proof.get("parent_job_id")!=p.get("parent_job_id") or
                   proof.get("parent_master_sha256")!=p.get("parent_master_sha256") or
                   proof.get("remarks_sha256")!=p.get("remarks_sha256") or
                   proof.get("HDR_applied") is not False or
                   proof.get("geometry_transform")!="IDENTITY"):
                    raise RuntimeError("corrected_master_provenance_HOLD")
            _set(job_id,status="READY",stage="technical_qa_ready",progress=100,
                 master_sha256=qa["master_sha256"],master_bytes=qa["master_bytes"])
    except Exception as error:
        msg=str(error)[-1600:]
        hold="CORRECTION_HOLD:" in msg
        _set(job_id,status="HOLD" if hold else "FAILED",
            stage="correction_hold" if hold else "technical_failed",
            progress=None,error=msg)

def _json(handler,status: int,body: dict)->None:
    data=json.dumps(body,ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type","application/json; charset=utf-8")
    handler.send_header("cache-control","no-store")
    handler.send_header("content-length",str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)

def _send_file(handler,path:Path,content_type:str)->None:
    if not path.is_file():
        return _json(handler,404,{"error":"technical_artifact_missing"})
    handler.send_response(200)
    handler.send_header("content-type",content_type)
    handler.send_header("content-length",str(path.stat().st_size))
    handler.send_header("cache-control","no-store")
    handler.end_headers()
    with path.open("rb") as fh:
        shutil.copyfileobj(fh,handler.wfile,length=1024*1024)

def route(handler,method:str)->bool:
    parts=handler.path.strip("/").split("/")
    if len(parts)!=4 or parts[:2]!=["editorial-tech","jobs"]:
        return False
    _,_,job_id,action=parts
    if not ID.fullmatch(job_id):
        _json(handler,400,{"error":"invalid_editorial_job"})
        return True
    if method=="GET":
        state=_status(job_id)
        if action=="status":
            _json(handler,200,state if state else {"status":"NOT_FOUND","job_id":job_id})
            return True
        if state.get("status")!="READY":
            _json(handler,409,{"error":"technical_master_not_ready"})
            return True
        files={"master":("master16.tif","image/tiff"),"qa":("qa.json","application/json"),
               "preview":("fullres-preview.jpg","image/jpeg")}
        if action not in files:
            _json(handler,404,{"error":"editorial_action_not_found"})
            return True
        name,mime=files[action]
        _send_file(handler,_path(job_id)/"technical"/name,mime)
        return True
    if method!="POST" or action!="start":
        _json(handler,405,{"error":"method_not_allowed"})
        return True
    plan_header=str(handler.headers.get("x-mg4k-correction-plan") or "")
    if len(plan_header)>4096:
        _json(handler,400,{"error":"correction_plan_header_too_large"})
        return True
    try:
        plan=json.loads(unquote(plan_header)) if plan_header else None
    except (ValueError, TypeError):
        _json(handler,400,{"error":"invalid_correction_plan_json"})
        return True
    if plan is not None and (not isinstance(plan,dict) or
        plan.get("type")!="DETERMINISTIC_TECH_REWORK" or
        plan.get("HDR_allowed") is not False or
        plan.get("geometry_transform")!="IDENTITY"):
        _json(handler,400,{"error":"unapproved_correction_profile"})
        return True
    length=int(handler.headers.get("content-length") or handler.headers.get("x-mg4k-source-size") or 0)
    source_sha=str(handler.headers.get("x-source-sha256") or "").lower()
    if not SHA.fullmatch(source_sha) or length<10000 or length>MAX_SOURCE:
        _json(handler,400,{"error":"invalid_source_integrity_or_length"})
        return True
    with MUTEX:
        existing=STATES.get(job_id)
        if existing:
            if existing.get("source_sha256")!=source_sha or existing.get("source_bytes")!=length:
                _json(handler,409,{"error":"job_id_source_conflict"})
            else:
                _json(handler,200,dict(existing))
            return True
        STATES[job_id]={"job_id":job_id,"source_sha256":source_sha,"source_bytes":length,
                        "status":"UPLOADING","stage":"source_receive","progress":0}
    data=handler.rfile.read(length)
    if len(data)!=length or hashlib.sha256(data).hexdigest()!=source_sha:
        _set(job_id,status="FAILED",error="source_sha256_mismatch")
        _json(handler,409,{"error":"source_sha256_mismatch"})
        return True
    folder=_path(job_id);folder.mkdir(parents=True,exist_ok=True)
    (folder/"source").write_bytes(data)
    if plan is not None:
        (folder/"correction-plan.json").write_text(json.dumps(plan,sort_keys=True),encoding="utf-8")
    name=Path(unquote(handler.headers.get("x-source-filename") or "source.jpg")).name
    _set(job_id,status="QUEUED",stage="queued",progress=0,filename=name)
    threading.Thread(target=_start_worker,args=(job_id,name,source_sha),daemon=True,
                     name="editorial-tech-"+job_id[:8]).start()
    _json(handler,202,_status(job_id))
    return True
