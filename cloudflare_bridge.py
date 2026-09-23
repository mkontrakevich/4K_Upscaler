from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import webbrowser
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
CLOUD_URL = os.environ.get(
    "MG4K_CLOUD_URL",
    "https://4k-upscaler.marinsgroup.workers.dev",
).rstrip("/")
BRIDGE_DIR = Path(
    os.environ.get(
        "MG4K_RUNTIME_DIR",
        str(ROOT / "_diagnostics" / "cloudflare_bridge"),
    )
).resolve()
BRIDGE_STATE = BRIDGE_DIR / "bridge.json"
JOBS_ROOT = Path(
    os.environ.get(
        "MG4K_JOBS_ROOT",
        str(ROOT / "_cloud_jobs"),
    )
).resolve()
POLL_SECONDS = float(os.environ.get("MG4K_POLL_SECONDS", "4"))
REQUEST_TIMEOUT = int(os.environ.get("MG4K_REQUEST_TIMEOUT", "60"))
EXIT_WHEN_IDLE_SECONDS = float(os.environ.get("MG4K_EXIT_WHEN_IDLE_SECONDS", "0"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def api(
    method: str,
    path: str,
    token: str | None = None,
    *,
    json_body: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    stream: bool = False,
    timeout: int = REQUEST_TIMEOUT,
) -> requests.Response:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.request(
        method,
        CLOUD_URL + path,
        headers=headers,
        json=json_body,
        files=files,
        data=data,
        stream=stream,
        timeout=timeout,
    )
    return response


def paired(token: str) -> bool:
    try:
        r = api("POST", "/api/processor/pair/status", token)
        return r.status_code == 200 and bool(r.json().get("paired"))
    except Exception:
        return False


def ensure_pairing() -> str:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)

    static_token = os.environ.get("MG4K_PROCESSOR_TOKEN", "").strip()
    if static_token:
        if paired(static_token):
            print("[CLOUD] Static processor token accepted. Processor is online.")
            return static_token
        raise RuntimeError(
            "MG4K_PROCESSOR_TOKEN was provided but Cloudflare rejected it. "
            "No interactive pairing was attempted."
        )

    saved = load_json(BRIDGE_STATE, {}) or {}
    existing = str(saved.get("token", "")).strip()
    if existing and paired(existing):
        print("[CLOUD] Processor already paired.")
        return existing

    while True:
        r = api("POST", "/api/processor/pair/request")
        r.raise_for_status()
        pair = r.json()
        token = str(pair["token"])
        code = str(pair["code"])
        save_json(BRIDGE_STATE, {
            "token": token,
            "pair_code": code,
            "cloud_url": CLOUD_URL,
            "paired": False,
            "created_at": time.time(),
        })

        print()
        print("=" * 62)
        print("MG 4K CLOUDFLARE PAIRING")
        print(f"CODE: {code}")
        print("Open the cloud interface and click: Подключить процессор")
        print(CLOUD_URL)
        print("=" * 62)
        if os.environ.get("MG4K_HEADLESS", "").strip().lower() not in {"1", "true", "yes", "on"}:
            try:
                webbrowser.open(CLOUD_URL)
            except Exception:
                pass

        deadline = time.time() + int(pair.get("expires_in_seconds", 600))
        while time.time() < deadline:
            if paired(token):
                save_json(BRIDGE_STATE, {
                    "token": token,
                    "cloud_url": CLOUD_URL,
                    "paired": True,
                    "paired_at": time.time(),
                })
                print("[CLOUD] Pairing approved. Processor is online.")
                return token
            time.sleep(3)
        print("[CLOUD] Pairing code expired; requesting a new code.")


def progress(token: str, job_id: str, value: int, stage: str, **extra: Any) -> None:
    payload: dict[str, Any] = {
        "status": "processing",
        "progress": int(value),
        "stage": stage,
    }
    payload.update(extra)
    try:
        api("POST", f"/api/processor/jobs/{job_id}/progress", token, json_body=payload)
    except Exception:
        pass


def download_source(token: str, job: dict[str, Any], source_dir: Path) -> Path:
    source_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(str(job.get("filename") or "source.jpg")).name
    target = source_dir / filename
    r = api("GET", str(job["source_url"]), token, stream=True, timeout=120)
    r.raise_for_status()
    with target.open("wb") as fh:
        for chunk in r.iter_content(1024 * 1024):
            if chunk:
                fh.write(chunk)
    return target


def pipeline_env(source_dir: Path, job: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env["MG4K_SOURCE"] = str(source_dir)
    env["MG4K_LOCK_PROFILE_JSON"] = json.dumps(job.get("locks") or {}, ensure_ascii=False)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_pipeline(args: list[str], env: dict[str, str], log_file: Path) -> int:
    cmd = [sys.executable, str(ROOT / "v8_safe_appearance.py"), *args]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(cmd) + "\n")
        process = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log.write(f"\n[exit={process.returncode}]\n")
        return int(process.returncode)


def state_path(source_dir: Path) -> Path:
    return (
        source_dir
        / str(CONFIG["output_folder"])
        / "_diagnostics"
        / "SEQUENTIAL_GENERATIVE_REVIEW_STATE.json"
    )


def active_state(source_dir: Path) -> dict[str, Any]:
    state = load_json(state_path(source_dir), {}) or {}
    return dict(state.get("active") or {})


def report_validation(active: dict[str, Any]) -> dict[str, Any] | None:
    report = active.get("report")
    if not report:
        return None
    payload = load_json(Path(str(report)), {}) or {}
    return payload.get("validation")


def candidate_path(active: dict[str, Any]) -> Path | None:
    for key in ("final", "review_image", "donor"):
        value = active.get(key)
        if value:
            path = Path(str(value))
            if path.is_file():
                return path
    return None


def upload_candidate(
    token: str,
    job_id: str,
    path: Path,
    validation: dict[str, Any] | None,
    status: str,
) -> None:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    with path.open("rb") as fh:
        r = api(
            "POST",
            f"/api/processor/jobs/{job_id}/result",
            token,
            files={"file": (path.name, fh, mime)},
            data={
                "status": status,
                "validation": json.dumps(validation, ensure_ascii=False) if validation else "null",
            },
            timeout=180,
        )
    r.raise_for_status()


def post_failure(token: str, job_id: str, error: str, stage: str = "failed") -> None:
    try:
        r = api(
            "POST",
            f"/api/processor/jobs/{job_id}/result",
            token,
            json_body={
                "status": "failed",
                "stage": stage,
                "error": error[:1800],
            },
        )
        r.raise_for_status()
    except Exception:
        pass


def wait_for_decision(token: str, job_id: str) -> str:
    print(f"[CLOUD] Job {job_id[:8]} waiting for visual approval.")
    while True:
        r = api("GET", f"/api/processor/jobs/{job_id}/decision", token)
        if r.status_code == 200:
            decision = str(r.json().get("decision") or "")
            if decision in {"approve", "reject"}:
                return decision
        elif r.status_code in {401, 403}:
            raise RuntimeError("Cloud processor authorization was lost.")
        time.sleep(2.5)


def process_job(token: str, job: dict[str, Any]) -> None:
    job_id = str(job["id"])
    job_dir = JOBS_ROOT / job_id
    source_dir = job_dir / "source"
    log_file = job_dir / "bridge_pipeline.log"
    job_dir.mkdir(parents=True, exist_ok=True)
    save_json(job_dir / "job.json", job)

    try:
        progress(token, job_id, 15, "download_source")
        source = download_source(token, job, source_dir)
        print(f"[CLOUD] Job {job_id[:8]} source: {source}")

        env = pipeline_env(source_dir, job)
        progress(token, job_id, 24, "nano_banana_generation")
        run_pipeline(
            ["--limit", "1", "--force-generation", "--donor-only", "--auto-generate-current"],
            env,
            log_file,
        )

        active = active_state(source_dir)
        status = str(active.get("status") or "")

        if status == "AWAITING_RAW_REVIEW":
            progress(token, job_id, 55, "arch_lock_validation")
            run_pipeline(["--limit", "1", "--force-processing"], env, log_file)
            active = active_state(source_dir)
            status = str(active.get("status") or "")

        if status == "AWAITING_EXPLICIT_DONOR_GENERATION":
            progress(token, job_id, 34, "replacement_generation")
            run_pipeline(
                [
                    "--limit", "1", "--force-generation", "--donor-only",
                    "--auto-generate-current", "--authorize-replacement-donor",
                ],
                env,
                log_file,
            )
            active = active_state(source_dir)
            status = str(active.get("status") or "")
            if status == "AWAITING_RAW_REVIEW":
                run_pipeline(["--limit", "1", "--force-processing"], env, log_file)
                active = active_state(source_dir)
                status = str(active.get("status") or "")

        candidate = candidate_path(active)
        if not candidate:
            raise RuntimeError(
                "Pipeline did not produce a reviewable candidate. "
                f"Current state: {status or 'unknown'}. See {log_file}"
            )

        validation = report_validation(active)
        progress(token, job_id, 82, "review_candidate_ready", validation=validation)
        upload_candidate(token, job_id, candidate, validation, "review")

        decision = wait_for_decision(token, job_id)
        if decision == "reject":
            progress(token, job_id, 96, "operator_reject")
            run_pipeline(["--reject-final"], env, log_file)
            post_failure(token, job_id, "rejected_by_user", "rejected")
            print(f"[CLOUD] Job {job_id[:8]} rejected by user.")
            return

        progress(token, job_id, 96, "operator_approve")
        run_pipeline(["--approve-final"], env, log_file)
        active = active_state(source_dir)
        final = candidate_path(active) or candidate
        upload_candidate(token, job_id, final, validation, "done")
        print(f"[CLOUD] Job {job_id[:8]} approved and completed.")

    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        post_failure(token, job_id, detail)
        diagnostic = {
            "job_id": job_id,
            "stage": "cloud_bridge",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
            "api_secrets_included": False,
            "user_image_bytes_included": False,
        }
        save_json(BRIDGE_DIR / "failures" / f"{job_id}.json", diagnostic)
        print(f"[CLOUD] Job {job_id[:8]} failed: {detail}")
    finally:
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass


def main() -> int:
    print("MG 4K Cloudflare Bridge")
    print(f"Cloud: {CLOUD_URL}")
    token = ensure_pairing()
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    last_activity = time.monotonic()

    while True:
        try:
            r = api("POST", "/api/processor/claim", token)
            if r.status_code in {401, 403, 428}:
                print("[CLOUD] Pairing is no longer valid; re-pairing.")
                try:
                    BRIDGE_STATE.unlink(missing_ok=True)
                except Exception:
                    pass
                token = ensure_pairing()
                continue
            r.raise_for_status()
            job = r.json().get("job")
            if not job:
                if EXIT_WHEN_IDLE_SECONDS > 0 and (time.monotonic() - last_activity) >= EXIT_WHEN_IDLE_SECONDS:
                    print(f"[CLOUD] Idle for {EXIT_WHEN_IDLE_SECONDS:.0f}s. Cloud processor exiting for scale-to-zero.")
                    return 0
                time.sleep(POLL_SECONDS)
                continue
            process_job(token, job)
            last_activity = time.monotonic()
        except KeyboardInterrupt:
            print("\n[CLOUD] Bridge stopped.")
            return 0
        except Exception as exc:
            print(f"[CLOUD] Poll error: {type(exc).__name__}: {exc}")
            time.sleep(8)


if __name__ == "__main__":
    raise SystemExit(main())
