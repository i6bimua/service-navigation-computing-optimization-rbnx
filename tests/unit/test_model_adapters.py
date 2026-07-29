import numpy as np
import pytest

from robonix_compute.cloud.runtime import CloudRuntime
from robonix_compute.cloud.runners import CallableS2Runner
from robonix_compute.common.protocol import LatentRequest
from robonix_compute.model_adapters import InternNavS1Adapter, InternNavS2Adapter


class FakeS2Output:
    output_latent = np.array([[1.0, 2.0]], dtype=np.float32)
    output_pixel = np.array([12, 34], dtype=np.int32)
    output_action = None

    def __init__(self):
        self.rgb_memory = np.array([1.0, 0.0], dtype=np.float32)
        self.depth_memory = np.array([0.5], dtype=np.float32)


class FakeS2Runner:
    def __init__(self):
        self.calls = []

    def step(self, rgb, depth, pose, instruction, intrinsic, look_down=False):
        self.calls.append((rgb, depth, pose, instruction, intrinsic, look_down))
        return FakeS2Output()


class FakeS1Runner:
    def __init__(self):
        self.last_call = None

    def step(self, images_dp, depths_dp, latent, initial_latents=None):
        self.last_call = {
            "images_dp": images_dp,
            "depths_dp": depths_dp,
            "latent": latent,
            "initial_latents": initial_latents,
        }
        return {"idx": [1, 2, 3]}


def test_internnav_s2_adapter_normalizes_payload():
    runner = FakeS2Runner()
    observation = {
        "rgb": np.array([0.0, 1.0], dtype=np.float32),
        "depth": np.array([0.1], dtype=np.float32),
        "pose": [0.0, 0.0, 0.0],
        "intrinsic": np.eye(3).tolist(),
    }

    payload = InternNavS2Adapter(runner).generate_latent(observation, "go")

    np.testing.assert_array_equal(payload["traj_latent"], FakeS2Output.output_latent)
    np.testing.assert_array_equal(payload["pixel_goal"], FakeS2Output.output_pixel)
    np.testing.assert_array_equal(payload["memory_rgb"], np.array([1.0, 0.0], dtype=np.float32))
    assert payload["metadata"]["s2_adapter"] == "FakeS2Runner"
    assert runner.calls[0][3] == "go"


def test_internnav_s2_adapter_handles_look_down_inside_cloud():
    class ActionOutput:
        output_latent = None
        output_pixel = None
        output_action = [InternNavS2Adapter.LOOK_DOWN_ACTION]
        rgb_memory = None
        depth_memory = None

    class LookDownRunner(FakeS2Runner):
        def step(self, rgb, depth, pose, instruction, intrinsic, look_down=False):
            self.calls.append((rgb, depth, pose, instruction, intrinsic, look_down))
            return FakeS2Output() if look_down else ActionOutput()

    runner = LookDownRunner()
    observation = {
        "rgb": np.zeros((2, 2, 3), dtype=np.uint8),
        "depth": np.ones((2, 2), dtype=np.float32),
        "pose": np.eye(4, dtype=np.float32),
        "intrinsic": np.eye(3, dtype=np.float32),
    }

    payload = InternNavS2Adapter(runner).generate_latent(observation, "look near the floor")

    assert [call[-1] for call in runner.calls] == [False, True]
    np.testing.assert_array_equal(payload["traj_latent"], FakeS2Output.output_latent)
    assert payload["metadata"]["s2_look_down"] is True
    assert "cloud_action" not in payload["metadata"]


def test_internnav_s2_adapter_preserves_action_only_stop():
    class StopOutput:
        output_latent = None
        output_pixel = None
        output_action = [0]
        rgb_memory = None
        depth_memory = None

    class StopRunner(FakeS2Runner):
        def step(self, rgb, depth, pose, instruction, intrinsic, look_down=False):
            self.calls.append((rgb, depth, pose, instruction, intrinsic, look_down))
            return StopOutput()

    payload = InternNavS2Adapter(StopRunner()).generate_latent(
        {
            "rgb": np.zeros((2, 2, 3), dtype=np.uint8),
            "depth": np.ones((2, 2), dtype=np.float32),
            "pose": np.eye(4, dtype=np.float32),
            "intrinsic": np.eye(3, dtype=np.float32),
        },
        "stop at the door",
    )

    assert payload["traj_latent"] is None
    assert payload["metadata"]["cloud_action"] == [0]


def test_internnav_s2_adapter_replays_edge_only_frames_without_inference():
    class ReplayRunner(FakeS2Runner):
        def __init__(self):
            super().__init__()
            self.replayed = []

        def step_no_infer(self, rgb, depth, pose):
            self.replayed.append((rgb, depth, pose))

    runner = ReplayRunner()
    skipped = [
        {
            "rgb": np.full((2, 2, 3), value, dtype=np.uint8),
            "depth": np.full((2, 2), value, dtype=np.float32),
            "pose": np.eye(4, dtype=np.float32),
        }
        for value in (1, 2)
    ]
    observation = {
        "rgb": np.zeros((2, 2, 3), dtype=np.uint8),
        "depth": np.ones((2, 2), dtype=np.float32),
        "pose": np.eye(4, dtype=np.float32),
        "intrinsic": np.eye(3, dtype=np.float32),
        "skipped_frames": skipped,
    }

    payload = InternNavS2Adapter(runner).generate_latent(observation, "go")

    assert len(runner.replayed) == 2
    np.testing.assert_array_equal(runner.replayed[1][0], skipped[1]["rgb"])
    assert payload["metadata"]["s2_replayed_frames"] == 2


def test_internnav_s1_adapter_uses_precomputed_s1_inputs():
    runner = FakeS1Runner()
    adapter = InternNavS1Adapter(runner)
    observation = {
        "images_dp": np.ones((1, 2), dtype=np.float32),
        "depths_dp": np.zeros((1, 2), dtype=np.float32),
        "visual_feature": np.array([1.0, 0.0], dtype=np.float32),
    }
    latent = {"traj_latent": np.array([4.0], dtype=np.float32), "initial_latents": np.array([0.1])}

    action = adapter.act(observation, latent)

    assert action == [1]
    np.testing.assert_array_equal(runner.last_call["images_dp"], observation["images_dp"])
    np.testing.assert_array_equal(runner.last_call["depths_dp"], observation["depths_dp"])
    np.testing.assert_array_equal(runner.last_call["latent"], latent["traj_latent"])


def test_internnav_s1_adapter_drains_one_action_per_observed_frame():
    runner = FakeS1Runner()
    adapter = InternNavS1Adapter(runner)
    observation = {
        "images_dp": np.ones((1, 2), dtype=np.float32),
        "depths_dp": np.zeros((1, 2), dtype=np.float32),
    }
    latent = {"traj_latent": np.array([4.0], dtype=np.float32)}

    assert adapter.act(observation, latent) == [1]
    first_call = runner.last_call
    assert adapter.act(observation, latent) == [2]
    assert adapter.act(observation, latent) == [3]
    assert runner.last_call is first_call

    # Once the trajectory is spent, S1 replans from the new frame.
    assert adapter.act(observation, latent) == [1]
    assert runner.last_call is not first_call


def test_internnav_s1_adapter_discards_tail_when_latent_changes():
    runner = FakeS1Runner()
    adapter = InternNavS1Adapter(runner)
    observation = {
        "images_dp": np.ones((1, 2), dtype=np.float32),
        "depths_dp": np.zeros((1, 2), dtype=np.float32),
    }

    assert adapter.act(observation, {"traj_latent": np.array([1.0])}) == [1]
    assert adapter.act(observation, {"traj_latent": np.array([2.0])}) == [1]


def test_internnav_s1_adapter_caps_s1_only_trajectory_to_num_future_steps():
    class LongTrajectoryRunner(FakeS1Runner):
        def step(self, images_dp, depths_dp, latent, initial_latents=None):
            super().step(images_dp, depths_dp, latent, initial_latents)
            return {"idx": [1, 2, 3, 1, 2, 3]}

    adapter = InternNavS1Adapter(LongTrajectoryRunner())
    observation = {
        "images_dp": np.ones((1, 2), dtype=np.float32),
        "depths_dp": np.zeros((1, 2), dtype=np.float32),
    }
    latent = {"traj_latent": np.array([4.0], dtype=np.float32)}

    assert [adapter.act(observation, latent)[0] for _ in range(4)] == [1, 2, 3, 1]
    # The fifth call replans; actions 5 and 6 from the old open-loop trajectory
    # were discarded, exactly like InternVLA-N1's `action_list[:4]`.
    assert adapter.act(observation, latent) == [1]


def test_internnav_s1_adapter_requires_packaging_inputs():
    adapter = InternNavS1Adapter(FakeS1Runner())
    with pytest.raises(ValueError, match="S1 input packaging requires"):
        adapter.act({"rgb": np.array([1.0])}, {"traj_latent": np.array([1.0])})


def test_cloud_runtime_adds_s2_metadata():
    runtime = CloudRuntime(CallableS2Runner(lambda observation, instruction=None: {"traj_latent": observation["rgb"]}))
    response = runtime.handle_latent_request(
        LatentRequest(request_id="req", step_id=2, observation={"rgb": np.array([1.0])})
    )

    assert response.metadata["cloud_role"] == "s2_latent_server"
    assert response.metadata["s2_latency_s"] >= 0.0
