from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_VERSION = "8.8.1"
BUILD_ID = "r4"
HOST = "127.0.0.1"
PREFERRED_PORT = 8742
FALLBACK_PORTS = tuple(range(8743, 8753))
STARTUP_TIMEOUT_SECONDS = 20.0
ROOT = Path(__file__).resolve().parent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config() -> dict[str, Any]:
    try:
        value = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def diagnostic_root() -> Path:
    override = os.environ.get("MG_WEB_DIAGNOSTIC_ROOT", "").strip()
    if override:
        return Path(override)
    config = load_config()
    source = config.get("source")
    output = config.get("output_folder")
    if isinstance(source, str) and source and isinstance(output, str) and output:
        return Path(source) / output / "_diagnostics" / "web_console"
    return ROOT / "_diagnostics" / "web_console"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_health(port: int, timeout: float = 0.8) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=timeout) as response:
            if response.status != 200:
                return None
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None


def is_current_server(health: dict[str, Any] | None) -> bool:
    return bool(
        health
        and health.get("status") == "ok"
        and health.get("local_only") is True
        and health.get("version") == APP_VERSION
        and health.get("build") == BUILD_ID
    )


def port_is_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((HOST, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def console_python() -> Path:
    executable = Path(sys.executable)
    if not executable.is_absolute():
        executable = executable.absolute()
    if executable.name.casefold() == "pythonw.exe":
        candidate = executable.with_name("python.exe")
        if candidate.is_file():
            return candidate
    return executable


def server_command(port: int) -> list[str]:
    return [
        str(console_python()),
        str(ROOT / "web_review_server.py"),
        "--no-browser",
        "--bootstrap-managed",
        "--port",
        str(port),
    ]


def open_local_url(url: str) -> None:
    if os.environ.get("MG_WEB_BOOTSTRAP_NO_BROWSER") == "1":
        return
    if os.name == "nt":
        os.startfile(url)
    else:
        webbrowser.open(url)


def endpoint_path() -> Path:
    return diagnostic_root() / "WEB_CONSOLE_ENDPOINT.json"


def record_endpoint(port: int, pid: int | None, reused: bool) -> None:
    atomic_json(endpoint_path(), {
        "application": "MG 4K Local Web Review Console",
        "version": APP_VERSION,
        "build": BUILD_ID,
        "created_at_utc": utc_now(),
        "host": HOST,
        "port": port,
        "url": f"http://{HOST}:{port}/",
        "pid": pid,
        "reused_existing_process": reused,
        "local_only": True,
        "paid_generation_started": False,
    })


def tail_text(path: Path, limit: int = 24000) -> str:
    try:
        data = path.read_bytes()
        return data[-limit:].decode("utf-8", errors="replace")
    except OSError:
        return ""


def write_diagnostic(
    stage: str,
    error: BaseException | str,
    attempts: list[dict[str, Any]],
    log_path: Path | None = None,
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = diagnostic_root() / f"WEB_BOOTSTRAP_DIAGNOSTIC_{stamp}.json"
    if isinstance(error, BaseException):
        exception_type = type(error).__name__
        message = str(error)
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    else:
        exception_type = "StartupError"
        message = error
        trace = ""
    atomic_json(path, {
        "application": "MG 4K Local Web Review Console",
        "version": APP_VERSION,
        "build": BUILD_ID,
        "created_at_utc": utc_now(),
        "stage": stage,
        "exception_type": exception_type,
        "exception": message,
        "traceback": trace,
        "attempts": attempts,
        "server_log": str(log_path) if log_path else None,
        "server_log_tail": tail_text(log_path) if log_path else "",
        "api_secrets_included": False,
        "paid_generation_started": False,
    })
    return path


def show_failure(message: str, diagnostic: Path) -> None:
    text = f"Не удалось запустить MG 4K Web Console.\n\n{message}\n\nДиагностика:\n{diagnostic}"
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "MG 4K", 16)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def launch_server(port: int) -> tuple[bool, dict[str, Any], Path]:
    logs = diagnostic_root()
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = logs / f"WEB_SERVER_START_{stamp}_PORT_{port}.log"
    command = server_command(port)
    started = time.monotonic()
    with log_path.open("a", encoding="utf-8", errors="replace") as output:
        output.write(f"{utc_now()} | bootstrap={APP_VERSION} build={BUILD_ID} | port={port}\n")
        output.flush()
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags(),
            close_fds=True,
        )
    while time.monotonic() - started < STARTUP_TIMEOUT_SECONDS:
        health = read_health(port)
        if is_current_server(health):
            return True, {
                "port": port,
                "result": "started",
                "pid": process.pid,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }, log_path
        return_code = process.poll()
        if return_code is not None:
            return False, {
                "port": port,
                "result": "process_exited",
                "pid": process.pid,
                "return_code": return_code,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "log_tail": tail_text(log_path),
            }, log_path
        time.sleep(0.25)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    return False, {
        "port": port,
        "result": "health_timeout",
        "pid": process.pid,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "log_tail": tail_text(log_path),
    }, log_path


def likely_port_failure(attempt: dict[str, Any]) -> bool:
    text = str(attempt.get("log_tail", "")).casefold()
    markers = (
        "address already in use",
        "only one usage of each socket address",
        "winerror 10048",
        "winerror 10013",
        "permission denied",
        "error while attempting to bind",
    )
    return any(marker in text for marker in markers)


def run() -> int:
    ports = (PREFERRED_PORT, *FALLBACK_PORTS)
    attempts: list[dict[str, Any]] = []
    for port in ports:
        health = read_health(port)
        if is_current_server(health):
            url = f"http://{HOST}:{port}/"
            record_endpoint(port, None, True)
            open_local_url(url)
            return 0
    for port in ports:
        health = read_health(port)
        if health is not None:
            attempts.append({"port": port, "result": "occupied_by_other_http_service", "health": health})
            continue
        if not port_is_free(port):
            attempts.append({"port": port, "result": "occupied_or_blocked"})
            continue
        try:
            success, attempt, log_path = launch_server(port)
        except BaseException as exc:
            diagnostic = write_diagnostic("bootstrap_spawn", exc, attempts)
            show_failure(str(exc), diagnostic)
            return 1
        attempts.append(attempt)
        if success:
            url = f"http://{HOST}:{port}/"
            record_endpoint(port, int(attempt["pid"]), False)
            open_local_url(url)
            return 0
        race_health = read_health(port)
        if is_current_server(race_health):
            url = f"http://{HOST}:{port}/"
            record_endpoint(port, None, True)
            open_local_url(url)
            return 0
        if not likely_port_failure(attempt):
            diagnostic = write_diagnostic(
                "server_start",
                "Сервер завершился до готовности. Причина сохранена в журнале запуска.",
                attempts,
                log_path,
            )
            show_failure("Сервер завершился до готовности.", diagnostic)
            return 1
    diagnostic = write_diagnostic(
        "port_selection",
        "Все локальные порты 8742-8752 заняты или заблокированы.",
        attempts,
    )
    show_failure("Все локальные порты 8742-8752 заняты или заблокированы.", diagnostic)
    return 1


def self_test() -> int:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind((HOST, 0))
    occupied_port = int(occupied.getsockname()[1])
    try:
        checks = {
            "version": APP_VERSION == "8.8.1",
            "loopback_only": HOST == "127.0.0.1",
            "preferred_port": PREFERRED_PORT == 8742,
            "fallback_ports": FALLBACK_PORTS == tuple(range(8743, 8753)),
            "occupied_port_detected": not port_is_free(occupied_port),
            "command_has_no_generation_flag": "--force-generation" not in server_command(PREFERRED_PORT),
            "command_waits_for_server_only": "--no-browser" in server_command(PREFERRED_PORT),
            "server_dialog_managed_by_bootstrap": "--bootstrap-managed" in server_command(PREFERRED_PORT),
            "headless_test_override_supported": "MG_WEB_BOOTSTRAP_NO_BROWSER" in open_local_url.__code__.co_consts,
            "diagnostic_has_no_api_secret_field": True,
        }
    finally:
        occupied.close()
    with tempfile.TemporaryDirectory(prefix="mg_v879_bootstrap_") as temporary:
        target = Path(temporary) / "endpoint.json"
        atomic_json(target, {"version": APP_VERSION, "paid_generation_started": False})
        checks["atomic_endpoint_write"] = json.loads(target.read_text(encoding="utf-8"))["version"] == APP_VERSION
    failed = [name for name, passed in checks.items() if not passed]
    print(json.dumps({
        "status": "passed" if not failed else "failed",
        **checks,
        "real_api_requests": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if not failed else 20


def main() -> int:
    parser = argparse.ArgumentParser(description="MG V8.8.1 Web Source Folder Picker Bootstrap")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    try:
        return run()
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            return 0
        diagnostic = write_diagnostic("bootstrap_unhandled", exc, [])
        show_failure(str(exc), diagnostic)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
