from __future__ import annotations

import threading
from typing import Any

from robonix_compute.cloud.runtime import CloudRuntime
from robonix_compute.common.protocol import ACK, CLOSE, EPISODE_RESET, ERROR, LATENT_REQUEST, RESET, LatentRequest, make_message
from robonix_compute.common.serialization import packb, unpackb


class CloudServer:
    """Minimal websocket latent server.

    The dependency is optional so unit tests and library imports work without
    installing websockets.
    """

    def __init__(self, runtime: CloudRuntime, *, host: str = "0.0.0.0", port: int = 8765):
        self.runtime = runtime
        self.host = host
        self.port = int(port)
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return

        try:
            from websockets.sync.server import serve
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("CloudServer requires `websockets` to be installed.") from exc

        def _serve() -> None:
            with serve(self._handle_client, self.host, self.port, max_size=None, compression=None) as server:
                self._server = server
                server.serve_forever()

        self._thread = threading.Thread(target=_serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _handle_client(self, websocket: Any) -> None:
        while True:
            raw_message = websocket.recv()
            message = unpackb(raw_message)
            message_type = message.get("type")
            if message_type in {RESET, EPISODE_RESET}:
                self.runtime.reset()
                websocket.send(packb(make_message(ACK, request_id=message.get("request_id"), status="reset")))
                continue
            if message_type == CLOSE:
                websocket.send(packb(make_message(ACK, request_id=message.get("request_id"), status="closed")))
                return
            if message_type != LATENT_REQUEST:
                websocket.send(packb(make_message(ERROR, message=f"unsupported message type: {message.get('type')}")))
                continue
            request = LatentRequest(
                request_id=str(message["request_id"]),
                step_id=int(message["step_id"]),
                observation=message.get("observation"),
                instruction=message.get("instruction"),
                sent_at=float(message.get("sent_at", 0.0)),
                metadata=dict(message.get("metadata", {})),
            )
            response = self.runtime.handle_latent_request(request)
            websocket.send(packb(response.to_message()))
