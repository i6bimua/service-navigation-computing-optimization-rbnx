import time

import numpy as np

from robonix_compute.cloud.runtime import CloudRuntime
from robonix_compute.cloud.runners import CallableS2Runner
from robonix_compute.common.link import InProcessLatentLink
from robonix_compute.edge.runtime import EdgeRuntime, EdgeRuntimeConfig
from robonix_compute.edge.runners import CallableS1Runner
from robonix_compute.edge.switcher import KeyLatentSwitcherConfig


def make_observation(value: float):
    if value > 0.5:
        return {"rgb": np.array([0.0, 1.0], dtype=np.float32)}
    return {"rgb": np.array([1.0, 0.0], dtype=np.float32)}


def build_runtime(*, delay_s: float = 0.0, initial_timeout_s: float = 0.05):
    cloud = CloudRuntime(
        CallableS2Runner(
            lambda observation, instruction=None: np.asarray(observation["rgb"], dtype=np.float32)
        )
    )
    link = InProcessLatentLink(cloud, delay_s=delay_s)
    edge = EdgeRuntime(
        s1_runner=CallableS1Runner(
            act_fn=lambda observation, latent: int(np.argmax(np.asarray(latent)) == 1),
        ),
        latent_sender=link.send,
        config=EdgeRuntimeConfig(
            require_initial_latent=False,
            initial_timeout_s=initial_timeout_s,
            min_timeout_s=0.01,
            max_timeout_s=0.05,
            switcher=KeyLatentSwitcherConfig(tau_lower=0.5, tau_upper=0.99, tau_initial=0.99),
        ),
    )
    edge.reset()
    return edge, link


def test_mock_runtime_fresh_sync_and_reuse():
    edge, link = build_runtime()
    try:
        first_action = edge.step(make_observation(0.1), step_id=0)
        second_action = edge.step(make_observation(1.0), step_id=1)
    finally:
        link.close()

    assert first_action == 0
    assert second_action == 1
    summary = edge.telemetry.summary()
    assert summary["step_count"] == 2
    assert any(item.fresh_latent for item in edge.telemetry.steps)


def test_mock_runtime_timeout_reuses_and_absorbs_late_latent():
    edge, link = build_runtime(delay_s=0.08, initial_timeout_s=0.01)
    try:
        edge.reset(initial_latent=np.array([0.1], dtype=np.float32), step_id=0)
        edge.switcher.observe(np.array([1.0, 0.0], dtype=np.float32))
        action = edge.step(make_observation(1.0), step_id=1)
        edge.wait_for_late_responses(timeout_s=1.0)
        time.sleep(0.01)
    finally:
        link.close()

    assert action == 0
    assert edge.telemetry.steps[0].timeout is True
    assert edge.telemetry.steps[0].latent_reuse is True
    assert edge.telemetry.steps[0].late_absorbed is True
    assert edge.buffer.active().request_id == edge.telemetry.steps[0].request_id


def test_signal_lock_blocks_overlapping_trigger():
    edge, link = build_runtime(delay_s=0.08, initial_timeout_s=0.01)
    try:
        edge.reset(initial_latent=np.array([1.0, 0.0], dtype=np.float32), step_id=0)
        edge.switcher.observe(np.array([1.0, 0.0], dtype=np.float32))
        assert edge.signal_lock.acquire("existing")
        action = edge.step(make_observation(1.0), step_id=1)
        assert edge.signal_lock.request_id == "existing"
        edge.signal_lock.release("existing")
    finally:
        link.close()

    assert action in {0, 1}
    assert edge.telemetry.steps[0].metadata.get("sync_skipped") in {None, "inflight_request"}
