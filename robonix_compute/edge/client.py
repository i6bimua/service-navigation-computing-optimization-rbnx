from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading

from robonix_compute.common.protocol import (
    ACK,
    CLOSE,
    ERROR,
    LATENT_RESPONSE,
    EpisodeReset,
    LatentRequest,
    LatentResponse,
    make_message,
)
from robonix_compute.common.serialization import packb, unpackb


class EdgeClient:
    """Synchronous websocket client exposed as a Future-returning sender."""

    def __init__(self, *, host: str, port: int, max_workers: int = 1):
        self.host = host
        self.port = int(port)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._send_lock = threading.Lock()

    def send_latent_request(self, request: LatentRequest) -> Future:
        return self.executor.submit(self._send_sync, request)

    def reset_episode(self, *, episode_id: str | None = None, instruction: str | None = None) -> dict:
        response = self._send_message(EpisodeReset(episode_id=episode_id, instruction=instruction).to_message())
        if response.get("type") != ACK:
            raise RuntimeError(f"unexpected reset response: {response.get('type')}")
        return response

    def close_cloud_connection(self) -> dict:
        response = self._send_message(make_message(CLOSE))
        if response.get("type") != ACK:
            raise RuntimeError(f"unexpected close response: {response.get('type')}")
        return response

    def _send_sync(self, request: LatentRequest) -> LatentResponse:
        response = self._send_message(request.to_message())
        if response.get("type") != LATENT_RESPONSE:
            raise RuntimeError(f"unexpected cloud response: {response.get('type')}")
        return LatentResponse.from_message(response)

    def _send_message(self, message: dict) -> dict:
        try:
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("EdgeClient requires `websockets` to be installed.") from exc

        uri = f"ws://{self.host}:{self.port}"
        with self._send_lock:
            with connect(uri, max_size=None, compression=None) as websocket:
                websocket.send(packb(message))
                response = unpackb(websocket.recv())
        if response.get("type") == ERROR:
            raise RuntimeError(response.get("message", "cloud returned an error"))
        return response

    def close(self) -> None:
        self.executor.shutdown(wait=True)
