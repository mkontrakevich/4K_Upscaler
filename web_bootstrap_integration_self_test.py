from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"


def wait_for_file(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.1)
    return False


def terminate_pid(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass


def main() -> int:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        blocker.bind((HOST, 8742))
        blocker.listen(1)
    except OSError as exc:
        print(json.dumps({
            "status": "failed",
            "reason": f"test could not reserve preferred port 8742: {exc}",
            "real_api_requests": 0,
        }, ensure_ascii=False, indent=2))
        return 20

    pid: int | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="mg_v879_live_bootstrap_") as temporary:
            diagnostic = Path(temporary)
            environment = dict(os.environ)
            environment["MG_WEB_DIAGNOSTIC_ROOT"] = str(diagnostic)
            environment["MG_WEB_BOOTSTRAP_NO_BROWSER"] = "1"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "web_console_bootstrap.py")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            endpoint_path = diagnostic / "WEB_CONSOLE_ENDPOINT.json"
            if completed.returncode != 0 or not wait_for_file(endpoint_path, 2):
                raise AssertionError(
                    f"bootstrap failed: code={completed.returncode}; stdout={completed.stdout}; stderr={completed.stderr}"
                )
            endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
            port = int(endpoint["port"])
            pid_value = endpoint.get("pid")
            pid = int(pid_value) if isinstance(pid_value, int) else None
            if port == 8742 or port < 8743 or port > 8752:
                raise AssertionError(f"fallback port was not selected: {port}")
            with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=2) as response:
                health = json.loads(response.read().decode("utf-8"))
            second = subprocess.run(
                [sys.executable, str(ROOT / "web_console_bootstrap.py")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            reused_endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
            checks = {
                "preferred_port_was_occupied": True,
                "fallback_port_selected": 8743 <= port <= 8752,
                "health_verified": health == {"status": "ok", "version": "8.8.1", "build": "r4", "local_only": True},
                "endpoint_persisted": endpoint.get("version") == "8.8.1" and endpoint.get("build") == "r4",
                "browser_suppressed_for_test": True,
                "paid_generation_not_started": endpoint.get("paid_generation_started") is False,
                "second_launch_succeeded": second.returncode == 0,
                "existing_server_reused": (
                    reused_endpoint.get("reused_existing_process") is True
                    and int(reused_endpoint.get("port", 0)) == port
                ),
                "real_api_requests": 0,
            }
            failed = [name for name, passed in checks.items() if passed is not True and name != "real_api_requests"]
            print(json.dumps({
                "status": "passed" if not failed else "failed",
                "selected_port": port,
                **checks,
            }, ensure_ascii=False, indent=2))
            return 0 if not failed else 21
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "reason": str(exc),
            "real_api_requests": 0,
        }, ensure_ascii=False, indent=2))
        return 22
    finally:
        blocker.close()
        if pid is not None:
            terminate_pid(pid)


if __name__ == "__main__":
    raise SystemExit(main())
