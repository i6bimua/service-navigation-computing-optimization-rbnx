import numpy as np
import pytest

from robonix_compute.common.serialization import packb, unpackb
from robonix_compute.common.timeout import AdaptiveTimeoutController
from robonix_compute.edge.switcher import KeyLatentSwitcher, KeyLatentSwitcherConfig, cosine_similarity


def test_timeout_estimator_records_success_and_timeout():
    controller = AdaptiveTimeoutController(initial_rtt_s=0.2, window_size=4)
    initial_timeout = controller.current_timeout_s()

    controller.record_success(0.1)
    assert controller.ok_count == 1
    assert controller.current_timeout_s() <= initial_timeout

    controller.record_timeout()
    assert controller.timeout_count == 1
    assert controller.timeout_ratio == 0.5
    assert controller.current_timeout_s() > 0


def test_timeout_estimator_jacobson_karels_formula_exact():
    controller = AdaptiveTimeoutController(initial_rtt_s=0.2, min_timeout_s=0.0, max_timeout_s=10.0)

    timeout = controller.record_success(0.1)

    assert controller.mean_rtt_s == pytest.approx(0.1875)
    assert controller.deviation_s == pytest.approx(0.1)
    assert timeout == pytest.approx(0.5875)


def test_switcher_triggers_and_updates_threshold():
    switcher = KeyLatentSwitcher(
        KeyLatentSwitcherConfig(
            tau_lower=0.5,
            tau_upper=0.8,
            tau_initial=0.8,
            delta=0.01,
            k_max=4,
            eta=0.05,
        )
    )

    assert switcher.observe(np.array([1.0, 0.0])) == 1.0
    similarity = switcher.observe(np.array([0.0, 1.0]))
    assert similarity < 0.01
    assert switcher.should_trigger(similarity, lock_free=True)

    switcher.update_step(misalignment_k=4, triggered=True)
    assert switcher.tau_sim == 0.5
    switcher.apply_network_feedback(
        success=False,
        rtt_s=None,
        timeout_s=0.1,
        timeout_count=1,
        ok_count=0,
        misalignment_k=4,
    )
    assert switcher.tau_sim > 0.5


def test_switcher_threshold_formula_exact_non_trigger_step():
    switcher = KeyLatentSwitcher(
        KeyLatentSwitcherConfig(
            tau_lower=0.5,
            tau_upper=0.8,
            tau_initial=0.6,
            delta=0.01,
            k_max=4,
        )
    )

    updated = switcher.update_step(misalignment_k=2, triggered=False)

    assert updated == pytest.approx(0.615)


def test_network_feedback_success_formula_exact():
    switcher = KeyLatentSwitcher(
        KeyLatentSwitcherConfig(
            tau_lower=0.5,
            tau_upper=0.8,
            tau_initial=0.7,
            eta=0.06,
        )
    )

    updated = switcher.apply_network_feedback(
        success=True,
        rtt_s=0.05,
        timeout_s=0.1,
        timeout_count=0,
        ok_count=1,
        misalignment_k=0,
    )

    assert updated == pytest.approx(0.68)


def test_cosine_similarity_handles_zero_vectors():
    assert cosine_similarity(np.zeros(4), np.ones(4)) == 1.0


def test_msgpack_numpy_roundtrip():
    message = {"type": "latent_response", "latent": np.arange(6, dtype=np.float32).reshape(2, 3)}
    decoded = unpackb(packb(message))
    assert decoded["type"] == "latent_response"
    np.testing.assert_array_equal(decoded["latent"], message["latent"])
