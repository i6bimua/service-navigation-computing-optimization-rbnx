import json
import socket
import threading
import time
import urllib.request

from robonix_compute.robonix.server import RoboNixComputeHTTPServer


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_robonix_http_skill_health_step_telemetry():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = RoboNixComputeHTTPServer(host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    try:
        health = _get_json(f"{base_url}/health")
        setup = _post_json(f"{base_url}/setup", {"mode": "mock", "require_initial_latent": False})
        reset = _post_json(f"{base_url}/reset", {"instruction": "go"})
        step = _post_json(f"{base_url}/step", {"observation": {"rgb": [1.0, 0.0]}})
        telemetry = _post_json(f"{base_url}/telemetry", {})
        closed = _post_json(f"{base_url}/close", {})
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert health["service"] == "robonix-compute-skill"
    assert setup["skill"]["name"] == "compute_optimization_adapter"
    assert reset["status"] == "reset"
    assert step["action"] == 0
    assert telemetry["summary"]["step_count"] == 1
    assert closed["status"] == "closed"
