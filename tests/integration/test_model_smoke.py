import os
from pathlib import Path

import pytest


@pytest.mark.gpu
def test_internnav_s2_runner_loads_when_model_dir_is_available():
    model_dir = Path(os.environ.get("ROBONIX_COMPUTE_MODEL_DIR", ""))
    if not model_dir.is_dir():
        pytest.skip("ROBONIX_COMPUTE_MODEL_DIR is not set to an existing checkpoint")
    pytest.importorskip("internnav")
    from internnav.edgecloud.runners import InternVLAN1S2Runner

    runner = InternVLAN1S2Runner(
        model_settings={
            "policy_name": "InternVLAN1_Policy",
            "model_path": str(model_dir),
            "device": os.environ.get("ROBONIX_COMPUTE_TEST_DEVICE", "cuda:0"),
            "mode": "dual_system",
            "state_encoder": None,
        }
    )
    assert runner is not None


@pytest.mark.gpu
def test_internnav_s1_only_runner_loads_when_s1_model_dir_is_available():
    s1_model_dir = Path(os.environ.get("ROBONIX_COMPUTE_S1_MODEL_DIR", ""))
    if not s1_model_dir.is_dir():
        pytest.skip("ROBONIX_COMPUTE_S1_MODEL_DIR is not set to an existing S1-only checkpoint")
    pytest.importorskip("internnav")
    from internnav.edgecloud.runners import InternVLAN1S1Runner

    runner = InternVLAN1S1Runner(
        model_settings={
            "policy_name": "InternVLAN1_Policy",
            "model_path": os.environ.get("ROBONIX_COMPUTE_MODEL_DIR", str(s1_model_dir)),
            "s1_model_path": str(s1_model_dir),
            "device": os.environ.get("ROBONIX_COMPUTE_TEST_DEVICE", "cuda:0"),
            "mode": "dual_system",
            "state_encoder": None,
        }
    )
    assert runner is not None
