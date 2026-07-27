"""End-to-end navigation loop over the real compute runtime.

Wires the actual `RoboNixComputeSkill` (mock backend: in-process S1/S2, no
checkpoints, no GPU, no network) to the actual `NavigationController`, with only the
observation source and the chassis sink faked. This exercises the whole
observe -> synchronize -> infer -> act path including the cloud round-trip,
the context buffer and the telemetry recorder.

The mock backend's action is deterministic:
`_mock_s1` returns `int(argmax(latent) == 1)` and `_mock_s2` sets
`latent = rgb.flatten()`, so a frame whose maximum sits at index 1 yields
MOVE_FORWARD and any other frame yields STOP. Tests use that to script runs.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from robonix_compute.rbnx.action_bridge import ACTION_MOVE_FORWARD
from robonix_compute.rbnx.config import parse_config
from robonix_compute.rbnx.controller import (
    SUCCEEDED,
    TERMINAL_STATES,
    RunLimits,
    NavigationController,
)
from robonix_compute.robonix.skill import RoboNixComputeSkill

pytestmark = pytest.mark.integration


def _frame(peak_index: int, size: int = 8) -> np.ndarray:
    """RGB frame whose flattened argmax lands on `peak_index`."""
    flat = np.zeros(size, dtype=np.float32)
    flat[peak_index] = 1.0
    return flat


FORWARD_FRAME = _frame(1)  # argmax == 1 -> MOVE_FORWARD
STOP_FRAME = _frame(0)     # argmax == 0 -> STOP


class ScriptedObservations:
    """Serves one frame per step, holding the last frame once exhausted."""

    def __init__(self, frames: list[np.ndarray]):
        self.frames = list(frames)
        self.index = 0
        self.instructions: list[str | None] = []

    def snapshot(self, *, instruction=None):
        self.instructions.append(instruction)
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return {
            "rgb": frame,
            "depth": np.zeros_like(frame),
            "intrinsic": np.eye(3, dtype=np.float32),
            "pose": np.eye(4, dtype=np.float32),
            "instruction": instruction,
        }


class RecordingChassis:
    def __init__(self):
        self.motions = []

    def __call__(self, motion):
        self.motions.append(motion)
        return "ok"


@pytest.fixture
def compute():
    """Real compute runtime on the mock backend, torn down after each test."""
    core = RoboNixComputeSkill()
    core.setup(parse_config({"mode": "mock", "allow_stub_actions": True})[0])
    yield core
    core.close()


def _controller(compute, observations, chassis, **limit_kwargs):
    config, error = parse_config({"mode": "mock", "allow_stub_actions": True})
    assert error is None
    limits = RunLimits(timeout_s=10.0, max_steps=20)
    for key, value in limit_kwargs.items():
        setattr(limits, key, value)
    return NavigationController(
        compute=compute,
        observations=observations,
        motion_sink=chassis,
        step_size_m=config["step_size_m"],
        turn_angle_deg=config["turn_angle_deg"],
        defaults=limits,
    )


def _await(controller, run_id, timeout_s=15.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snapshot = controller.status(run_id)
        if snapshot and snapshot["state"] in TERMINAL_STATES:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"run did not finish: {controller.status(run_id)}")


def test_setup_reports_the_mock_backend(compute):
    info = compute.skill_info()
    assert info["name"] == "compute_optimization_adapter"
    assert "rgb" in info["inputs"] and "action" in info["outputs"]


def test_first_step_synchronizes_without_a_pre_seeded_buffer(compute):
    """require_initial_latent defaults to False, so step 0 must not raise.

    A Robonix deployment never seeds the context buffer -- only the offline eval
    harness does -- so the online path relies on the initial synchronization.
    """
    observations = ScriptedObservations([STOP_FRAME])
    chassis = RecordingChassis()
    controller = _controller(compute, observations, chassis)
    run = controller.start(instruction="stop immediately")
    snapshot = _await(controller, run.run_id)
    assert snapshot["state"] == SUCCEEDED, snapshot["detail"]
    assert snapshot["stop_predicted"] is True
    assert snapshot["steps_executed"] == 1
    assert chassis.motions == []


def test_forward_steps_then_stop_drives_the_chassis(compute):
    observations = ScriptedObservations([FORWARD_FRAME] * 3 + [STOP_FRAME])
    chassis = RecordingChassis()
    controller = _controller(compute, observations, chassis)
    run = controller.start(instruction="walk forward then stop")
    snapshot = _await(controller, run.run_id)

    assert snapshot["state"] == SUCCEEDED, snapshot["detail"]
    assert snapshot["steps_executed"] == 4
    assert [m.action_index for m in chassis.motions] == [ACTION_MOVE_FORWARD] * 3
    assert all(m.forward_m == pytest.approx(0.25) for m in chassis.motions)
    assert all(m.rotate_deg == 0.0 for m in chassis.motions)


def test_instruction_reaches_every_observation(compute):
    observations = ScriptedObservations([FORWARD_FRAME, STOP_FRAME])
    controller = _controller(compute, observations, RecordingChassis())
    run = controller.start(instruction="follow the corridor")
    _await(controller, run.run_id)
    assert set(observations.instructions) == {"follow the corridor"}


def test_telemetry_records_real_synchronizations(compute):
    observations = ScriptedObservations([FORWARD_FRAME] * 4 + [STOP_FRAME])
    controller = _controller(compute, observations, RecordingChassis())
    run = controller.start(instruction="measure me")
    snapshot = _await(controller, run.run_id)
    assert snapshot["state"] == SUCCEEDED, snapshot["detail"]

    report = controller.telemetry(run.run_id)
    summary = report["summary"]
    assert summary["step_count"] == snapshot["steps_executed"]
    # Step 0 always synchronizes: the buffer starts empty.
    assert summary["sync_count"] >= 1
    assert summary["sync_count"] + summary["latent_reuse_count"] >= summary["step_count"]
    assert summary["mean_step_latency_s"] is not None
    assert len(report["step_logs"]) == summary["step_count"]
    # Every recorded step carries the switcher's decision inputs.
    assert all("visual_similarity" in entry for entry in report["step_logs"])


def test_latent_is_reused_across_steps(compute):
    """The point of the skill: not every step pays for a cloud round-trip."""
    observations = ScriptedObservations([FORWARD_FRAME] * 8 + [STOP_FRAME])
    controller = _controller(compute, observations, RecordingChassis(), max_steps=30)
    run = controller.start(instruction="reuse the latent")
    snapshot = _await(controller, run.run_id)
    assert snapshot["state"] == SUCCEEDED, snapshot["detail"]
    summary = controller.telemetry(run.run_id)["summary"]
    # Identical consecutive frames keep visual similarity high, so the switcher
    # should reuse rather than synchronize on at least some steps.
    assert summary["latent_reuse_count"] >= 1
    assert summary["sync_count"] < summary["step_count"]


def test_step_limit_stops_a_policy_that_never_stops(compute):
    observations = ScriptedObservations([FORWARD_FRAME])  # holds forward forever
    chassis = RecordingChassis()
    controller = _controller(compute, observations, chassis, max_steps=5)
    run = controller.start(instruction="never stops")
    snapshot = _await(controller, run.run_id)
    assert snapshot["state"] == "FAILED"
    assert "step limit of 5" in snapshot["detail"]
    assert len(chassis.motions) == 5


def test_cancel_halts_the_real_loop(compute):
    observations = ScriptedObservations([FORWARD_FRAME])

    slow = RecordingChassis()
    original = slow.__call__

    def slow_call(motion):
        time.sleep(0.02)
        return original(motion)

    controller = _controller(compute, observations, slow_call, max_steps=10_000, timeout_s=30.0)
    run = controller.start(instruction="cancel me")
    time.sleep(0.1)
    assert controller.cancel(run.run_id)[0] is True
    snapshot = _await(controller, run.run_id)
    assert snapshot["state"] == "CANCELED"
    # It stopped early rather than running to the step ceiling.
    assert snapshot["steps_executed"] < 10_000


def test_second_run_reuses_the_same_compute_core(compute):
    """Each run resets the episode; telemetry stays scoped per run."""
    observations = ScriptedObservations([FORWARD_FRAME, STOP_FRAME])
    controller = _controller(compute, observations, RecordingChassis())
    first = controller.start(instruction="first")
    _await(controller, first.run_id)

    observations.index = 0  # replay the same script
    second = controller.start(instruction="second")
    snapshot = _await(controller, second.run_id)
    assert snapshot["state"] == SUCCEEDED, snapshot["detail"]

    first_steps = controller.telemetry(first.run_id)["summary"]["step_count"]
    second_steps = controller.telemetry(second.run_id)["summary"]["step_count"]
    assert first_steps == 2 and second_steps == 2


def test_stop_runtime_leaves_no_straggler(compute):
    observations = ScriptedObservations([FORWARD_FRAME])
    controller = _controller(compute, observations, RecordingChassis(), max_steps=10_000, timeout_s=30.0)
    controller.start(instruction="long run")
    time.sleep(0.05)
    assert controller.stop_runtime() == []
