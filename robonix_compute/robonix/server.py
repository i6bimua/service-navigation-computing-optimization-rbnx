from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any

from robonix_compute.robonix.skill import RoboNixComputeSkill


def _json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


class RoboNixComputeHTTPServer:
    def __init__(self, *, host: str = "0.0.0.0", port: int = 8090, skill: RoboNixComputeSkill | None = None):
        self.host = host
        self.port = int(port)
        self.skill = skill or RoboNixComputeSkill()
        self._server: ThreadingHTTPServer | None = None

    def serve_forever(self) -> None:
        skill = self.skill

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/health":
                    self.send_error(404)
                    return
                self._send_json({"status": "ok", "service": "robonix-compute-skill"})

            def do_POST(self) -> None:  # noqa: N802
                try:
                    payload = self._read_json()
                    if self.path == "/setup":
                        response = skill.setup(payload.get("config", payload))
                    elif self.path == "/reset":
                        response = skill.reset(task=payload.get("task"), instruction=payload.get("instruction"))
                    elif self.path == "/step":
                        response = skill.step(payload.get("observation", payload))
                    elif self.path == "/telemetry":
                        response = skill.telemetry()
                    elif self.path == "/close":
                        response = skill.close()
                    else:
                        self.send_error(404)
                        return
                    self._send_json(response)
                except Exception as exc:  # pragma: no cover - integration behavior
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length == 0:
                    return {}
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def _send_json(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.serve_forever()

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        self.skill.close()
