import time

import numpy as np
import pytest

from robonix_compute.cloud.runtime import CloudRuntime
from robonix_compute.cloud.runners import CallableS2Runner
from robonix_compute.cloud.server import CloudServer
from robonix_compute.edge.client import EdgeClient
from robonix_compute.edge.runtime import EdgeRuntime, EdgeRuntimeConfig
from robonix_compute.edge.runners import CallableS1Runner


pytest.importorskip("websockets")


def test_websocket_cloud_edge_mock_runtime():
    runtime = CloudRuntime(CallableS2Runner(lambda observation, instruction=None: observation["rgb"]))
    server = CloudServer(runtime, host="127.0.0.1", port=18765)
    server.start()
    time.sleep(0.1)
    client = EdgeClient(host="127.0.0.1", port=18765)
    edge = EdgeRuntime(
        s1_runner=CallableS1Runner(
            act_fn=lambda observation, latent: int(np.argmax(np.asarray(latent)) == 1),
        ),
        latent_sender=client.send_latent_request,
        config=EdgeRuntimeConfig(require_initial_latent=False),
    )
    try:
        assert client.reset_episode(episode_id="mock")["status"] == "reset"
        edge.reset()
        assert edge.step({"rgb": np.array([1.0, 0.0], dtype=np.float32)}, step_id=0) == 0
        assert edge.step({"rgb": np.array([0.0, 1.0], dtype=np.float32)}, step_id=1) == 1
    finally:
        client.close()
        server.stop()

    assert edge.telemetry.summary()["sync_count"] >= 1
