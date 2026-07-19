from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import time

from robonix_compute.cloud.runtime import CloudRuntime
from robonix_compute.common.protocol import LatentRequest


class InProcessLatentLink:
    """Future-returning in-process link for tests and local smoke runs."""

    def __init__(self, cloud_runtime: CloudRuntime, *, delay_s: float = 0.0, max_workers: int = 4):
        self.cloud_runtime = cloud_runtime
        self.delay_s = float(delay_s)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def send(self, request: LatentRequest) -> Future:
        def _run():
            if self.delay_s > 0:
                time.sleep(self.delay_s)
            return self.cloud_runtime.handle_latent_request(request)

        return self.executor.submit(_run)

    def close(self) -> None:
        self.executor.shutdown(wait=True)
