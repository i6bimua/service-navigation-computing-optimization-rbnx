from __future__ import annotations

import time

from robonix_compute.cloud.runners import S2Runner
from robonix_compute.common.protocol import LatentRequest, LatentResponse


class CloudRuntime:
    """Cloud-side S2 latent service."""

    def __init__(self, runner: S2Runner):
        self.runner = runner

    def reset(self) -> None:
        self.runner.reset()

    def handle_latent_request(self, request: LatentRequest) -> LatentResponse:
        started_at = time.perf_counter()
        latent = self.runner.generate_latent(request.observation, request.instruction)
        metadata = {
            "s2_latency_s": time.perf_counter() - started_at,
            "cloud_role": "s2_latent_server",
        }
        if isinstance(latent, dict) and isinstance(latent.get("metadata"), dict):
            metadata.update(latent["metadata"])
        return LatentResponse(
            request_id=request.request_id,
            step_id=request.step_id,
            latent=latent,
            produced_at=time.time(),
            metadata=metadata,
        )
