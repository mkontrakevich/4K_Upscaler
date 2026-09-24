from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
from typing import Any

import cloudflare_bridge as bridge


STARTED_AT = time.time()
JOBS_ROOT = Path(os.environ.get("MG4K_JOBS_ROOT", "/tmp/mg4k-jobs")).resolve()
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

_STATE_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_DECISION_EVENTS: dict[str, threading.Event] = {}
_RUNNERS: dict[str, threading.Thread] = {}
_PROCESSING_LOCK = threading.Lock()


def _state_file(job_id: str) -> Path:
    return JOBS_ROOT / job_id / "service_state.json"


def _save_state(job_id: str) -> None:
    with _STATE_LOCK:
        state = dict(_JOBS.get(job_id) or {})
    path = _state_file(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.partial")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _set_state(job_id: str, **updates: Any) -> dict[str, Any]:
    with _STATE_LOCK:
        state = _JOBS.setdefault(job_id, {"id": job_id})
        state.update(updates)
        state["updated_at"] = time.time()
        result = dict(state)
    _save_state(job_id)
    return result


def _public_state(job_id: str) -> dict[str, Any] | None:
    with _STATE_LOCK:
        state = _JOBS.get(job_id)
        if not state:
            return None
        return {
            "id": job_id,
            "status": state.get("status", "queued"),
            "stage": state.get("stage", "queued"),
            "progress": int(state.get("progress", 0) or 0),
            "error": state.get("error"),
            "diagnostic_excerpt": state.get("diagnostic_excerpt"),
            "validation": state.get("validation"),
            "decision": state.get("decision"),
            "result_available": bool(state.get("result_path") and Path(str(state.get("result_path"))).is_file()),
        }


def _safe_pipeline_tail(path: Path, max_chars: int = 5000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    # Never expose credentials or embedded image payloads in cloud diagnostics.
    text = re.sub(r"sk-or-v1-[A-Za-z0-9_-]+", "<OPENROUTER_KEY_REDACTED>", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <REDACTED>", text, flags=re.I)
    text = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "<EMBEDDED_IMAGE_REDACTED>", text)
    return text[-max_chars:].strip()


def _wait_for_decision(job_id: str) -> str:
    event = _DECISION_EVENTS.setdefault(job_id, threading.Event())
    while True:
        event.wait(timeout=2.0)
        with _STATE_LOCK:
            decision = str((_JOBS.get(job_id) or {}).get("decision") or "")
        if decision in {"approve", "reject", "skip"}:
            return decision


def _process_job(job_id: str) -> None:
    job_dir = JOBS_ROOT / job_id
    source_dir = job_dir / "source"
    log_file = job_dir / "bridge_pipeline.log"

    try:
        with _STATE_LOCK:
            job = dict(_JOBS[job_id])

        source_path = Path(str(job.get("source_path") or ""))
        if not source_path.is_file():
            raise RuntimeError(f"Container source is missing: {source_path}")

        env = bridge.pipeline_env(source_dir, job)

        # Only compute-heavy pipeline phases are serialized. Human review must
        # never hold the processing slot, otherwise one image waiting for an
        # operator decision blocks every later image at runner_started.
        _set_state(
            job_id,
            status="processing",
            stage="waiting_for_processing_slot",
            progress=13,
            error=None,
        )
        with _PROCESSING_LOCK:
            _set_state(job_id, status="processing", stage="source_received", progress=15, error=None)

            _set_state(job_id, status="processing", stage="prepare_generation_state", progress=20)
            init_rc = bridge.run_pipeline(["--limit", "1"], env, log_file)
            active = bridge.active_state(source_dir)
            status = str(active.get("status") or "")
            if status != "AWAITING_EXPLICIT_DONOR_GENERATION":
                raise RuntimeError(
                    "Pipeline preparation did not reach AWAITING_EXPLICIT_DONOR_GENERATION. "
                    f"exit={init_rc}; state={status or 'unknown'}"
                )

            _set_state(job_id, status="processing", stage="nano_banana_generation", progress=24)
            generation_rc = bridge.run_pipeline(
                ["--limit", "1", "--force-generation", "--donor-only", "--auto-generate-current"],
                env,
                log_file,
            )
            active = bridge.active_state(source_dir)
            status = str(active.get("status") or "")
            if generation_rc not in {0, 3, 4, 5, 7}:
                tail = _safe_pipeline_tail(log_file)
                _set_state(job_id, diagnostic_excerpt=tail)
                raise RuntimeError(
                    "Nano Banana generation subprocess failed. "
                    f"exit={generation_rc}; state={status or 'unknown'}"
                    + (f" | pipeline_tail: {tail[-1800:]}" if tail else "")
                )

            if status == "AWAITING_RAW_REVIEW":
                _set_state(job_id, status="processing", stage="arch_lock_validation", progress=55)
                bridge.run_pipeline(["--limit", "1", "--force-processing"], env, log_file)
                active = bridge.active_state(source_dir)
                status = str(active.get("status") or "")

            if status == "AWAITING_EXPLICIT_DONOR_GENERATION":
                _set_state(job_id, status="processing", stage="replacement_generation", progress=34)
                bridge.run_pipeline(
                    [
                        "--limit", "1", "--force-generation", "--donor-only",
                        "--auto-generate-current", "--authorize-replacement-donor",
                    ],
                    env,
                    log_file,
                )
                active = bridge.active_state(source_dir)
                status = str(active.get("status") or "")
                if status == "AWAITING_RAW_REVIEW":
                    _set_state(job_id, status="processing", stage="arch_lock_validation", progress=55)
                    bridge.run_pipeline(["--limit", "1", "--force-processing"], env, log_file)
                    active = bridge.active_state(source_dir)
                    status = str(active.get("status") or "")

            candidate = bridge.candidate_path(active)
            if not candidate:
                raise RuntimeError(
                    "Pipeline did not produce a reviewable candidate. "
                    f"Current state: {status or 'unknown'}. See {log_file}"
                )

            validation = bridge.report_validation(active)
            _set_state(
                job_id,
                status="review",
                stage="awaiting_approval",
                progress=90,
                validation=validation,
                result_path=str(candidate),
                error=None,
            )

        # IMPORTANT: no processing lock is held while a person reviews RESULT.
        decision = _wait_for_decision(job_id)

        if decision == "skip":
            _set_state(job_id, status="skipped", stage="skipped_by_user", progress=100)
            return

        if decision == "reject":
            _set_state(
                job_id,
                status="processing",
                stage="operator_reject_waiting_slot",
                progress=95,
            )
            with _PROCESSING_LOCK:
                _set_state(job_id, status="processing", stage="operator_reject", progress=96)
                bridge.run_pipeline(["--reject-final"], env, log_file)
            _set_state(
                job_id,
                status="failed",
                stage="rejected",
                progress=100,
                error="rejected_by_user",
            )
            return

        _set_state(
            job_id,
            status="processing",
            stage="operator_approve_waiting_slot",
            progress=95,
        )
        with _PROCESSING_LOCK:
            _set_state(job_id, status="processing", stage="operator_approve", progress=96)
            approve_rc = bridge.run_pipeline(["--approve-final"], env, log_file)
            active = bridge.active_state(source_dir)
            final = bridge.candidate_path(active) or candidate
            if approve_rc not in {0, 6} or not final.is_file():
                raise RuntimeError(
                    f"FINAL approval failed. exit={approve_rc}; candidate={final}"
                )

        _set_state(
            job_id,
            status="done",
            stage="final",
            progress=100,
            validation=validation,
            result_path=str(final),
            error=None,
        )

    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        diagnostic = {
            "job_id": job_id,
            "stage": "container_push_processor",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
            "api_secrets_included": False,
            "user_image_bytes_included": False,
            "pipeline_log": str(log_file),
        }
        diag_path = job_dir / "failure.json"
        diag_path.parent.mkdir(parents=True, exist_ok=True)
        diag_path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
        tail = _safe_pipeline_tail(log_file)
        _set_state(
            job_id,
            status="failed",
            stage="container_processing_failed",
            error=detail[:1800],
            diagnostic_excerpt=tail,
        )


def _runner_entry(job_id: str) -> None:
    try:
        _set_state(job_id, stage="runner_started", progress=12, runner_started_at=time.time())
        _process_job(job_id)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        try:
            _set_state(
                job_id,
                status="failed",
                stage="runner_failed",
                error=detail[:1800],
            )
        except Exception:
            pass
    finally:
        with _STATE_LOCK:
            _RUNNERS.pop(job_id, None)


def _ensure_job_runner(job_id: str) -> bool:
    with _STATE_LOCK:
        state = _JOBS.get(job_id)
        if not state:
            return False
        if state.get("status") in {"review", "done", "failed", "skipped", "decision_pending"}:
            return False
        current = _RUNNERS.get(job_id)
        if current and current.is_alive():
            return False
        thread = threading.Thread(
            target=_runner_entry,
            args=(job_id,),
            name=f"mg4k-job-{job_id[:8]}",
            daemon=False,
        )
        _RUNNERS[job_id] = thread
        thread.start()
        return True


class ProcessorHandler(BaseHTTPRequestHandler):
    server_version = "MG4KCloudProcessor/8.8.1-push-watchdog"

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _job_id(self, suffix: str) -> str | None:
        parts = [part for part in self.path.split("/") if part]
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == suffix:
            return parts[1]
        return None

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._json(200, {
                "status": "ok",
                "service": "mg4k-cloud-processor",
                "version": "8.8.1-push-watchdog",
                "uptime_seconds": int(time.time() - STARTED_AT),
                "ephemeral_jobs": True,
                "persistent_user_database": False,
                "transport": "worker-push-runner-watchdog",
                "active_runners": sum(1 for thread in _RUNNERS.values() if thread.is_alive()),
                "processing_slot_locked": _PROCESSING_LOCK.locked(),
            })
            return

        job_id = self._job_id("status")
        if job_id:
            state = _public_state(job_id)
            if not state:
                self._json(404, {"error": "job_not_found"})
                return
            if state.get("status") == "queued":
                _ensure_job_runner(job_id)
                state = _public_state(job_id) or state
            self._json(200, state)
            return

        job_id = self._job_id("result")
        if job_id:
            with _STATE_LOCK:
                state = dict(_JOBS.get(job_id) or {})
            path = Path(str(state.get("result_path") or ""))
            if not path.is_file():
                self._json(404, {"error": "result_not_ready"})
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(size))
            self.send_header("cache-control", "no-store")
            self.send_header("x-mg4k-filename", path.name)
            self.end_headers()
            with path.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile, length=1024 * 1024)
            return

        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        job_id = self._job_id("start")
        if job_id:
            length = int(self.headers.get("content-length") or self.headers.get("x-mg4k-source-size") or "0")
            if length <= 0 or length > 50 * 1024 * 1024:
                self._json(413 if length > 50 * 1024 * 1024 else 400, {"error": "invalid_source_size"})
                return

            with _STATE_LOCK:
                existing = _JOBS.get(job_id)
                if existing and existing.get("status") not in {"failed", "skipped"}:
                    self._json(200, _public_state(job_id) or {"id": job_id})
                    return

            filename = unquote(self.headers.get("x-mg4k-filename") or "source.jpg")
            try:
                locks = json.loads(self.headers.get("x-mg4k-locks") or "{}")
            except Exception:
                self._json(400, {"error": "invalid_lock_profile"})
                return

            safe_name = Path(filename).name or "source.jpg"
            job_dir = JOBS_ROOT / job_id
            source_dir = job_dir / "source"
            source_dir.mkdir(parents=True, exist_ok=True)
            source_path = source_dir / safe_name
            remaining = length
            with source_path.open("wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            if remaining != 0:
                self._json(400, {"error": "incomplete_source_upload"})
                return

            with _STATE_LOCK:
                _JOBS[job_id] = {
                    "id": job_id,
                    "filename": safe_name,
                    "locks": locks,
                    "mode": self.headers.get("x-mg4k-mode") or "generative",
                    "source_path": str(source_path),
                    "status": "queued",
                    "stage": "container_job_received",
                    "progress": 10,
                    "error": None,
                    "validation": None,
                    "decision": None,
                    "result_path": None,
                    "created_at": time.time(),
                }
                _DECISION_EVENTS[job_id] = threading.Event()
            _save_state(job_id)
            _ensure_job_runner(job_id)
            self._json(202, _public_state(job_id) or {"id": job_id})
            return

        job_id = self._job_id("decision")
        if job_id:
            length = int(self.headers.get("content-length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                self._json(400, {"error": "invalid_json"})
                return
            action = str(payload.get("action") or "").lower()
            if action not in {"approve", "reject", "skip"}:
                self._json(400, {"error": "invalid_decision"})
                return
            with _STATE_LOCK:
                if job_id not in _JOBS:
                    self._json(404, {"error": "job_not_found"})
                    return
                _JOBS[job_id]["decision"] = action
                _JOBS[job_id]["updated_at"] = time.time()
                event = _DECISION_EVENTS.setdefault(job_id, threading.Event())
                event.set()
            _save_state(job_id)
            self._json(200, {"ok": True, "decision": action})
            return

        self._json(404, {"error": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    port = int(os.environ.get("MG4K_HEALTH_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), ProcessorHandler)
    print(f"MG4K push processor listening on :{port}")
    server.serve_forever(poll_interval=0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
