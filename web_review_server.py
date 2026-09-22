from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from PIL import Image, ImageOps
import uvicorn

from source_queue_policy import discover_source_files, is_source_candidate
from quality_gate_reason_catalog import describe_failed_checks, defect_overlays


APP_VERSION = "8.8.1"
BUILD_ID = "r4"
DEFAULT_PORT = 8742
ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"
SCENE_PROFILE_PATH = ROOT / "scene_stability_profile.json"
CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
SCENE_PROFILE = json.loads(SCENE_PROFILE_PATH.read_text(encoding="utf-8"))
SOURCE_ROOT = Path(CFG["source"])
OUTPUT_ROOT = SOURCE_ROOT / CFG["output_folder"]
DIAG_ROOT = OUTPUT_ROOT / "_diagnostics"
SAFE_STATE = DIAG_ROOT / "SAFE_LOCAL_UPSCALE_QUEUE_STATE.json"
GEN_STATE = DIAG_ROOT / "SEQUENTIAL_GENERATIVE_REVIEW_STATE.json"
WEB_SETTINGS = DIAG_ROOT / "WEB_REVIEW_CONSOLE_SETTINGS.json"
SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def activate_source_folder(path: Path) -> int:
    """Persist and activate a source root without starting any processing."""
    global CFG, SOURCE_ROOT, OUTPUT_ROOT, DIAG_ROOT, SAFE_STATE, GEN_STATE, WEB_SETTINGS
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Выбранный путь не является папкой")
    images = discover_source_files(resolved, str(CFG["output_folder"]))
    if not images:
        raise ValueError("В выбранной папке нет поддерживаемых исходных изображений")
    payload = dict(CFG)
    payload["source"] = str(resolved)
    atomic_json(CONFIG_PATH, payload)
    CFG = payload
    SOURCE_ROOT = resolved
    OUTPUT_ROOT = SOURCE_ROOT / CFG["output_folder"]
    DIAG_ROOT = OUTPUT_ROOT / "_diagnostics"
    SAFE_STATE = DIAG_ROOT / "SAFE_LOCAL_UPSCALE_QUEUE_STATE.json"
    GEN_STATE = DIAG_ROOT / "SEQUENTIAL_GENERATIVE_REVIEW_STATE.json"
    WEB_SETTINGS = DIAG_ROOT / "WEB_REVIEW_CONSOLE_SETTINGS.json"
    return len(images)


def choose_windows_folder(initial: Path) -> Path | None:
    if os.name != "nt":
        raise RuntimeError("Системный выбор папки доступен в установленной Windows-версии")
    start = str(initial if initial.is_dir() else initial.parent).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.Text='MG 4K: выбор папки';$owner.TopMost=$true;"
        "$owner.ShowInTaskbar=$true;$owner.Width=350;$owner.Height=90;"
        "$owner.StartPosition='CenterScreen';"
        "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$dialog.Description='Выберите папку с исходными изображениями';"
        f"$dialog.SelectedPath='{start}';"
        "try{$owner.Show();$owner.Activate();"
        "if($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK)"
        "{[Console]::Write($dialog.SelectedPath)}}"
        "finally{$dialog.Dispose();$owner.Dispose()}"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Не удалось открыть системный выбор папки")
    selected = completed.stdout.strip().lstrip("\ufeff")
    return Path(selected) if selected else None


def source_files() -> list[Path]:
    return discover_source_files(SOURCE_ROOT, CFG["output_folder"])


def first_unapproved(mode: str, files: list[Path], state: dict[str, Any]) -> str | None:
    if mode == "safe":
        approved = {str(key).replace("\\", "/") for key in state.get("completed", {})}
    else:
        approved = {
            str(item.get("source_relative", "")).replace("\\", "/")
            for item in state.get("approved", []) if isinstance(item, dict)
        }
    for path in files:
        try:
            key = path.relative_to(SOURCE_ROOT).as_posix()
        except ValueError:
            key = path.name
        if key not in approved:
            return str(path)
    return None


def queue_snapshot(mode: str) -> dict[str, Any]:
    files = source_files()
    if mode == "safe":
        state = read_json(SAFE_STATE)
        pending = state.get("pending_review") if isinstance(state.get("pending_review"), dict) else {}
        source = pending.get("source") or state.get("active_source")
        if source and not is_source_candidate(Path(str(source)), SOURCE_ROOT, CFG["output_folder"]):
            source = None
            pending = {}
        source = source or first_unapproved(mode, files, state)
        return {
            "mode": mode,
            "state": state,
            "source": source,
            "result": pending.get("output"),
            "report": pending.get("report"),
            "status": state.get("status", "READY"),
            "approved": len(state.get("completed", {})),
            "total": len(files),
            "awaiting_approval": bool(pending),
            "needs_generation": False,
            "technical_hold": False,
            "replacement_eligible": False,
        }

    state = read_json(GEN_STATE)
    active = state.get("active") if isinstance(state.get("active"), dict) else {}
    active_source = active.get("source")
    active_is_service_asset = bool(
        active_source
        and not is_source_candidate(Path(str(active_source)), SOURCE_ROOT, CFG["output_folder"])
    )
    if active_is_service_asset:
        active = {}
    status = (
        "SERVICE_SOURCE_EXCLUDED_QUEUE_RESUME_READY"
        if active_is_service_asset
        else str(active.get("status") or state.get("status") or "READY")
    )
    result = active.get("final") if status == "AWAITING_FINAL_APPROVAL" else (
        active.get("review_image") or active.get("donor")
    )
    return {
        "mode": mode,
        "state": state,
        "source": active.get("source") or first_unapproved(mode, files, state),
        "result": result,
        "report": active.get("report") or active.get("diagnostic_package"),
        "status": status,
        "approved": len(state.get("approved", [])),
        "total": len(files),
        "awaiting_approval": status == "AWAITING_FINAL_APPROVAL",
        "needs_generation": status == "AWAITING_EXPLICIT_DONOR_GENERATION",
        "technical_hold": status in {
            "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
            "CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED",
        },
        "replacement_eligible": bool(active.get("replacement_donor_eligible")),
        "failed_checks": list(active.get("failed_checks", [])),
        "technical_decision": active.get("technical_decision"),
        "manual_override_allowed": bool(
            (active.get("review_image") or active.get("donor"))
            and status in {
                "CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED",
                "CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED",
                "AWAITING_EXPLICIT_DONOR_GENERATION",
            }
        ),
    }


def command_for(mode: str, action: str, snapshot: dict[str, Any]) -> list[str]:
    if mode == "safe":
        mapping = {
            "process": [sys.executable, str(ROOT / "safe_local_upscale.py"), "--limit", "1"],
            "approve": [sys.executable, str(ROOT / "safe_local_upscale.py"), "--approve"],
            "reject": [sys.executable, str(ROOT / "safe_local_upscale.py"), "--reject"],
        }
        if action not in mapping:
            raise ValueError("SAFE mode does not support this action")
        return mapping[action]

    base = [sys.executable, str(ROOT / "v8_safe_appearance.py")]
    if action == "process":
        if snapshot.get("status") == "AWAITING_RAW_REVIEW":
            return base + ["--limit", "1", "--force-processing"]
        return base + ["--limit", "1"]
    if action == "approve":
        return base + ["--approve-final"]
    if action == "manual_approve":
        return base + ["--approve-rejected-donor"]
    if action == "reject_candidate":
        return base + ["--reject-rejected-donor"]
    if action == "reject":
        return base + ["--reject-final"]
    if action == "generate":
        command = base + [
            "--limit", "1", "--force-generation", "--donor-only", "--auto-generate-current"
        ]
        if snapshot.get("replacement_eligible") or snapshot.get("technical_hold"):
            command.append("--authorize-replacement-donor")
        return command
    raise ValueError(action)


def image_info(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_file():
        return None
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened)
            width, height = image.size
        return {
            "name": path.name,
            "width": width,
            "height": height,
            "modified_ns": path.stat().st_mtime_ns,
        }
    except OSError:
        return None


class ConsoleRuntime:
    def __init__(self) -> None:
        settings = read_json(WEB_SETTINGS)
        mode = str(settings.get("mode", "generative"))
        self.mode = mode if mode in {"safe", "generative"} else "generative"
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.busy = False
        self.action: str | None = None
        self.command: list[str] | None = None
        self.cancel_requested = False
        self.folder_dialog_open = False
        self.sequence = 0
        self.logs: deque[dict[str, Any]] = deque(maxlen=500)
        self.add_log("INFO", "MG 4K Web Review Console готова к работе")
        self.add_log("SAFE", "Запуск web-интерфейса не выполняет платную генерацию")

    def add_log(self, level: str, message: str) -> None:
        with self.lock:
            self.sequence += 1
            self.logs.append({
                "id": self.sequence,
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "message": message.strip(),
            })

    def set_mode(self, mode: str) -> None:
        if mode not in {"safe", "generative"}:
            raise ValueError("Unknown mode")
        with self.lock:
            if self.busy or self.folder_dialog_open:
                raise RuntimeError("Mode cannot be changed while processing")
            self.mode = mode
            atomic_json(WEB_SETTINGS, {"mode": mode, "updated_at_utc": utc_now()})
            self.add_log("INFO", "Выбран режим: " + ("SAFE · без генерации" if mode == "safe" else "GENERATIVE · Nano Banana Pro"))

    def public_status(self, token: str) -> dict[str, Any]:
        with self.lock:
            snapshot = queue_snapshot(self.mode)
            source = image_info(snapshot.get("source"))
            result = image_info(snapshot.get("result"))
            revision = max(
                int(source.get("modified_ns", 0)) if source else 0,
                int(result.get("modified_ns", 0)) if result else 0,
                self.sequence,
            )
            if source:
                source["url"] = f"/api/image/source?token={token}&v={revision}"
            if result:
                result["url"] = f"/api/image/result?token={token}&v={revision}"
            return {
                "version": APP_VERSION,
                "mode": self.mode,
                "status": snapshot.get("status", "READY"),
                "approved": int(snapshot.get("approved", 0)),
                "total": int(snapshot.get("total", 0)),
                "source": source,
                "result": result,
                "current_file": Path(str(snapshot.get("source") or "")).name or None,
                "source_folder": str(SOURCE_ROOT),
                "report_available": bool(snapshot.get("report") and Path(str(snapshot["report"])).is_file()),
                "busy": self.busy,
                "action": self.action,
                "can": {
                    "process": not self.busy and not self.folder_dialog_open and not snapshot.get("awaiting_approval"),
                    "approve": not self.busy and not self.folder_dialog_open and bool(snapshot.get("awaiting_approval")),
                    "manual_approve": (
                        not self.busy and not self.folder_dialog_open and self.mode == "generative"
                        and bool(snapshot.get("manual_override_allowed"))
                    ),
                    "reject": not self.busy and not self.folder_dialog_open and bool(snapshot.get("awaiting_approval")),
                    "reject_candidate": (
                        not self.busy and not self.folder_dialog_open and self.mode == "generative"
                        and bool(snapshot.get("manual_override_allowed"))
                    ),
                    "generate": (
                        not self.busy and not self.folder_dialog_open and self.mode == "generative"
                        and bool(snapshot.get("needs_generation") or snapshot.get("technical_hold"))
                    ),
                    "pause": self.busy and not self.folder_dialog_open,
                    "folder": not self.busy and not self.folder_dialog_open,
                    "select_folder": not self.busy and not self.folder_dialog_open,
                },
                "logs": list(self.logs)[-160:],
                "local_only": True,
                "api_generation_on_start": False,
                "scene_profile": SCENE_PROFILE,
                "technical_hold": bool(snapshot.get("technical_hold")),
                "failed_checks": list(snapshot.get("failed_checks", [])),
                "failed_checks_detailed": describe_failed_checks(list(snapshot.get("failed_checks", []))),
                "defect_overlays": defect_overlays(list(snapshot.get("failed_checks", []))),
                "technical_decision": snapshot.get("technical_decision"),
                "manual_override_allowed": bool(snapshot.get("manual_override_allowed")),
                "viewer_capabilities": {
                    "zoom": True,
                    "pan": True,
                    "split": True,
                    "diff": True,
                    "blink": True,
                    "sync_pan_zoom": True,
                    "defect_overlay": True,
                },
                "build": BUILD_ID,
            }

    def select_source_folder(self) -> dict[str, Any]:
        with self.lock:
            if self.busy:
                raise RuntimeError("Нельзя менять папку во время обработки")
            if self.folder_dialog_open:
                raise RuntimeError("Окно выбора папки уже открыто")
            self.folder_dialog_open = True
        try:
            selected = choose_windows_folder(SOURCE_ROOT)
            if selected is None:
                self.add_log("INFO", "Выбор папки отменён")
                return {"accepted": False, "cancelled": True}
            with self.lock:
                image_count = activate_source_folder(selected)
                atomic_json(WEB_SETTINGS, {
                    "mode": self.mode,
                    "source_folder": str(SOURCE_ROOT),
                    "updated_at_utc": utc_now(),
                })
                self.add_log("SAFE", f"Выбрана папка исходников: {SOURCE_ROOT} · файлов: {image_count}")
                self.add_log("SAFE", "Смена папки не запускает обработку или платную генерацию")
            return {
                "accepted": True,
                "cancelled": False,
                "source_folder": str(SOURCE_ROOT),
                "image_count": image_count,
                "api_request_made": False,
            }
        finally:
            with self.lock:
                self.folder_dialog_open = False

    def set_source_folder_path(self, path: str) -> dict[str, Any]:
        if not path.strip():
            raise ValueError("Укажите путь к папке с исходниками")
        with self.lock:
            if self.busy:
                raise RuntimeError("Нельзя менять папку во время обработки")
            if self.folder_dialog_open:
                raise RuntimeError("Дождитесь закрытия окна выбора папки")
            count = activate_source_folder(Path(path.strip().strip('"')))
            atomic_json(WEB_SETTINGS, {
                "mode": self.mode,
                "source_folder": str(SOURCE_ROOT),
                "updated_at_utc": utc_now(),
            })
            self.add_log("SAFE", f"Выбрана папка исходников: {SOURCE_ROOT} · файлов: {count}")
            return {"accepted": True, "cancelled": False, "source_folder": str(SOURCE_ROOT),
                    "image_count": count, "api_request_made": False}

    def image_path(self, kind: str) -> Path:
        with self.lock:
            snapshot = queue_snapshot(self.mode)
            value = snapshot.get("source" if kind == "source" else "result")
            if not value:
                raise FileNotFoundError(kind)
            path = Path(str(value))
            if not path.is_file():
                raise FileNotFoundError(path)
            return path

    def validate_action(self, action: str, paid_confirmed: bool) -> dict[str, Any]:
        snapshot = queue_snapshot(self.mode)
        if action == "process" and snapshot.get("awaiting_approval"):
            raise RuntimeError("Сначала утвердите или отклоните текущий результат")
        if action in {"approve", "reject"} and not snapshot.get("awaiting_approval"):
            raise RuntimeError("Нет результата, ожидающего решения")
        if action == "generate":
            if self.mode != "generative":
                raise RuntimeError("Генерация недоступна в SAFE-режиме")
            if not paid_confirmed:
                raise RuntimeError("Платная генерация требует отдельного подтверждения")
            if not (snapshot.get("needs_generation") or snapshot.get("technical_hold")):
                raise RuntimeError("Новая генерация сейчас не требуется")
        return snapshot

    def launch(self, action: str, paid_confirmed: bool = False, follow: str | None = None) -> None:
        with self.lock:
            if self.busy or self.folder_dialog_open:
                raise RuntimeError("Другое действие уже выполняется")
            snapshot = self.validate_action(action, paid_confirmed)
            command = command_for(self.mode, action, snapshot)
            self.busy = True
            self.action = action
            self.command = command
            self.cancel_requested = False
            self.add_log("COMMAND", f"Запущено действие: {action}")
        threading.Thread(target=self._worker, args=(command, action, follow), daemon=True).start()

    def _worker(self, command: list[str], action: str, follow: str | None) -> None:
        code = 1
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(
                command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags,
            )
            with self.lock:
                self.process = process
                if self.cancel_requested:
                    process.terminate()
            assert process.stdout is not None
            for line in process.stdout:
                text = line.strip()
                if text:
                    level = "ERROR" if "ERROR" in text else (
                        "SAFE" if "READY" in text or "APPROVED" in text else "INFO"
                    )
                    self.add_log(level, text)
            code = process.wait()
        except Exception as exc:
            diagnostic = self.write_diagnostic("subprocess", exc, command)
            self.add_log("ERROR", f"Ошибка приложения: {exc} · диагностика: {diagnostic or 'не создана'}")
        finally:
            with self.lock:
                self.process = None
                self.busy = False
                self.action = None
                self.command = None
            level = "SAFE" if code in {0, 4, 5, 6} else "ERROR"
            self.add_log(level, f"Действие {action} завершено · код {code}")

        if code == 0 and follow:
            time.sleep(0.35)
            try:
                self.launch("process", paid_confirmed=False)
            except Exception as exc:
                self.add_log("ERROR", f"Автопродолжение остановлено: {exc}")

    def pause(self) -> None:
        with self.lock:
            if not self.busy:
                raise RuntimeError("Нет активного процесса")
            self.cancel_requested = True
            if self.process is not None:
                self.process.terminate()
            self.add_log("INFO", "Запрошена пауза. Текущий кадр остаётся активным")

    def open_folder(self) -> None:
        with self.lock:
            snapshot = queue_snapshot(self.mode)
            value = snapshot.get("result")
            path = Path(str(value)).parent if value and Path(str(value)).is_file() else OUTPUT_ROOT
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(path))
            self.add_log("INFO", "Открыта папка результата")

    def write_diagnostic(self, stage: str, error: BaseException, command: list[str] | None = None) -> Path | None:
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = DIAG_ROOT / "web_console" / f"WEB_DIAGNOSTIC_{stamp}.json"
            atomic_json(path, {
                "application": "MG 4K Local Web Review Console",
                "version": APP_VERSION,
                "created_at_utc": utc_now(),
                "stage": stage,
                "exception_type": type(error).__name__,
                "exception": str(error),
                "traceback": traceback.format_exc(),
                "command_without_secrets": command or [],
                "safe_state": read_json(SAFE_STATE),
                "generative_state": read_json(GEN_STATE),
                "recent_logs": list(self.logs)[-80:],
                "api_secrets_included": False,
            })
            return path
        except Exception:
            return None


CONTROL_TOKEN = secrets.token_urlsafe(32)
RUNTIME = ConsoleRuntime()
app = FastAPI(title="MG 4K Web Review Console", version=APP_VERSION, docs_url=None, redoc_url=None)


def require_control(value: str | None) -> None:
    if not value or not secrets.compare_digest(value, CONTROL_TOKEN):
        raise HTTPException(status_code=403, detail="Local control token is invalid")


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    template = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    return (template.replace("__CONTROL_TOKEN__", CONTROL_TOKEN)
            .replace("__APP_VERSION__", APP_VERSION)
            .replace("__BUILD_ID__", BUILD_ID))


@app.get("/assets/app.css")
def app_css() -> FileResponse:
    return FileResponse(WEB_ROOT / "app.css", media_type="text/css")


@app.get("/assets/app.js")
def app_js() -> FileResponse:
    return FileResponse(WEB_ROOT / "app.js", media_type="application/javascript")


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    return FileResponse(WEB_ROOT / "favicon.svg", media_type="image/svg+xml")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": APP_VERSION, "build": BUILD_ID, "local_only": True}


@app.get("/api/status")
def status(x_mg_control: str | None = Header(default=None)) -> dict[str, Any]:
    require_control(x_mg_control)
    return RUNTIME.public_status(CONTROL_TOKEN)


@app.post("/api/mode")
def set_mode(
    payload: dict[str, Any] = Body(...), x_mg_control: str | None = Header(default=None)
) -> dict[str, Any]:
    require_control(x_mg_control)
    try:
        RUNTIME.set_mode(str(payload.get("mode", "")))
        return {"accepted": True}
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/source-folder/select")
def select_source_folder(x_mg_control: str | None = Header(default=None)) -> dict[str, Any]:
    require_control(x_mg_control)
    try:
        return RUNTIME.select_source_folder()
    except Exception as exc:
        diagnostic = RUNTIME.write_diagnostic("source_folder_selection", exc)
        RUNTIME.add_log("ERROR", f"Не удалось выбрать папку: {exc} · диагностика: {diagnostic or 'не создана'}")
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/source-folder/path")
def set_source_folder_path(
    payload: dict[str, Any] = Body(...), x_mg_control: str | None = Header(default=None)
) -> dict[str, Any]:
    require_control(x_mg_control)
    try:
        return RUNTIME.set_source_folder_path(str(payload.get("path", "")))
    except (ValueError, OSError, RuntimeError) as exc:
        RUNTIME.add_log("ERROR", f"Не удалось открыть папку: {exc}")
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/action/{action}")
def action(
    action: str,
    payload: dict[str, Any] = Body(default={}),
    x_mg_control: str | None = Header(default=None),
) -> dict[str, Any]:
    require_control(x_mg_control)
    if action not in {"process", "approve", "manual_approve", "reject", "reject_candidate", "generate", "pause", "folder"}:
        raise HTTPException(status_code=404, detail="Unknown action")
    try:
        if action == "pause":
            RUNTIME.pause()
        elif action == "folder":
            RUNTIME.open_folder()
        else:
            follow = "process" if action in {"approve", "manual_approve", "generate"} else None
            RUNTIME.launch(action, bool(payload.get("paid_confirmed", False)), follow)
        return {"accepted": True, "action": action}
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/image/{kind}")
def current_image(kind: str, token: str = Query(...), v: str | None = Query(default=None)) -> FileResponse:
    require_control(token)
    if kind not in {"source", "result"}:
        raise HTTPException(status_code=404, detail="Unknown image kind")
    try:
        path = RUNTIME.image_path(kind)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Image is not available") from exc
    with Image.open(path) as opened:
        image_format = opened.format or ""
    media = Image.MIME.get(image_format, "application/octet-stream")
    return FileResponse(path, media_type=media, filename=None)


def self_test() -> int:
    waiting = {"status": "AWAITING_EXPLICIT_DONOR_GENERATION", "replacement_eligible": True}
    normal = command_for("generative", "process", waiting)
    paid = command_for("generative", "generate", waiting)
    checks = {
        "version": APP_VERSION == "8.8.1",
        "build_revision": BUILD_ID == "r4",
        "failure_reason_catalog": callable(describe_failed_checks) and callable(defect_overlays),
        "loopback_default": DEFAULT_PORT == 8742,
        "startup_never_generates": "--force-generation" not in normal,
        "paid_generation_separate": "--force-generation" in paid,
        "replacement_explicit": "--authorize-replacement-donor" in paid,
        "one_image_per_action": normal[-2:] == ["--limit", "1"],
        "safe_has_no_generator": "v8_safe_appearance.py" not in " ".join(command_for("safe", "process", {})),
        "csrf_token_present": len(CONTROL_TOKEN) >= 32,
        "web_assets_present": all((WEB_ROOT / name).is_file() for name in ("index.html", "app.css", "app.js", "favicon.svg")),
        "diagnostics_present": callable(RUNTIME.write_diagnostic),
        "source_folder_endpoint": callable(RUNTIME.select_source_folder),
        "folder_switch_never_generates": all(
            "--force-generation" not in str(value)
            for value in choose_windows_folder.__code__.co_consts
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    print(json.dumps({"status": "passed" if not failed else "failed", **checks, "real_api_requests": 0}, ensure_ascii=False, indent=2))
    return 0 if not failed else 20


def server_is_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.2) as response:
            return response.status == 200
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="MG V8.8.1-r3 Inspection Review Console")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--bootstrap-managed", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    port = max(1024, min(65535, args.port))
    url = f"http://127.0.0.1:{port}/"
    if server_is_running(port):
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if not args.no_browser:
        threading.Timer(1.1, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")
        return 0
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            return 0
        diagnostic = RUNTIME.write_diagnostic("server_start", exc)
        if os.name == "nt" and not args.bootstrap_managed:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, f"Не удалось запустить MG 4K Web Console.\n{diagnostic or exc}", "MG 4K", 16
                )
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
