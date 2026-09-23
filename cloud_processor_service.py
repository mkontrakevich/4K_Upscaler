from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cloudflare_bridge


STARTED_AT = time.time()


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "MG4KCloudProcessor/8.8.1"

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return

        payload = json.dumps(
            {
                "status": "ok",
                "service": "mg4k-cloud-processor",
                "version": "8.8.1",
                "cloud_url": cloudflare_bridge.CLOUD_URL,
                "uptime_seconds": int(time.time() - STARTED_AT),
                "ephemeral_jobs": True,
                "persistent_user_database": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve_health() -> None:
    port = int(os.environ.get("MG4K_HEALTH_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever(poll_interval=0.5)


def main() -> int:
    thread = threading.Thread(target=serve_health, name="mg4k-health", daemon=True)
    thread.start()
    return cloudflare_bridge.main()


if __name__ == "__main__":
    raise SystemExit(main())
