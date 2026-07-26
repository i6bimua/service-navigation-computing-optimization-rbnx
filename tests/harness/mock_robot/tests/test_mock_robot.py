# SPDX-License-Identifier: MulanPSL-2.0
"""The mock_robot fixture: motion mapping, dead reckoning, ROS message shapes.

The backend needs neither ROS 2 nor a simulator. The provider tests inject rclpy
message stubs, so what is under test is that the builders fill the fields the
contracts specify — not that rclpy serialises them.
"""
from __future__ import annotations

import json
import math
import sys
import types

import numpy as np
import pytest

from mock_robot.backend import (
    ACTIONS,
    MOVE_FORWARD,
    STOP,
    TURN_LEFT,
    TURN_RIGHT,
    CameraIntrinsics,
    SyntheticBody,
    UnsupportedMotionError,
    motion_to_action,
    wrap_angle,
)

KW = {"step_size_m": 0.25, "turn_angle_deg": 15.0}


# -- intrinsics --------------------------------------------------------------
def test_intrinsics_from_hfov_matches_the_pinhole_relation():
    intr = CameraIntrinsics.from_hfov(224, 224, 90.0)
    assert intr.fx == pytest.approx(112.0)  # at 90 deg HFOV, fx == width / 2
    assert intr.fy == pytest.approx(intr.fx)  # square pixels
    assert (intr.cx, intr.cy) == (112.0, 112.0)


def test_narrower_fov_gives_longer_focal_length():
    assert CameraIntrinsics.from_hfov(224, 224, 45.0).fx > CameraIntrinsics.from_hfov(224, 224, 90.0).fx


def test_to_k_is_row_major_camera_info_order():
    k = CameraIntrinsics(width=640, height=480, fx=500.0, fy=501.0, cx=320.0, cy=240.0).to_k()
    assert k == [500.0, 0.0, 320.0, 0.0, 501.0, 240.0, 0.0, 0.0, 1.0]


def test_non_square_image_keeps_its_own_centre():
    intr = CameraIntrinsics.from_hfov(640, 480, 79.0)
    assert (intr.cx, intr.cy) == (320.0, 240.0)


# -- MoveCommand -> discrete action -----------------------------------------
def test_forward_command_maps_to_move_forward():
    assert motion_to_action(0.25, 0.0, **KW) == MOVE_FORWARD


def test_turn_sign_follows_the_contract():
    # MoveCommand.rotate_deg is positive counter-clockwise.
    assert motion_to_action(0.0, 15.0, **KW) == TURN_LEFT
    assert motion_to_action(0.0, -15.0, **KW) == TURN_RIGHT


def test_forward_takes_priority_over_rotation():
    # Matches MoveCommand's documented driver-side priority.
    assert motion_to_action(0.25, 15.0, **KW) == MOVE_FORWARD


def test_backward_motion_is_rejected():
    with pytest.raises(UnsupportedMotionError, match="no backward action"):
        motion_to_action(-0.25, 0.0, **KW)


def test_mismatched_step_size_is_rejected_not_silently_substituted():
    # Executing a different distance than requested would desynchronise the
    # caller's belief about the robot's pose from the truth.
    with pytest.raises(UnsupportedMotionError, match="does not match the step size"):
        motion_to_action(1.0, 0.0, **KW)


def test_mismatched_turn_angle_is_rejected():
    with pytest.raises(UnsupportedMotionError, match="does not match the turn angle"):
        motion_to_action(0.0, 90.0, **KW)


def test_small_numeric_drift_is_tolerated():
    # A float round-trip through protobuf must not fail the command.
    assert motion_to_action(0.2500001, 0.0, **KW) == MOVE_FORWARD
    assert motion_to_action(0.0, 15.0000001, **KW) == TURN_LEFT


def test_velocity_only_command_is_rejected():
    with pytest.raises(UnsupportedMotionError, match="continuous velocity mode"):
        motion_to_action(0.0, 0.0, **KW)


def test_action_mapping_follows_configured_increments():
    assert motion_to_action(0.5, 0.0, step_size_m=0.5, turn_angle_deg=30.0) == MOVE_FORWARD
    assert motion_to_action(0.0, 30.0, step_size_m=0.5, turn_angle_deg=30.0) == TURN_LEFT


@pytest.mark.parametrize("angle", [0.5, -0.5, 3 * math.pi, -3 * math.pi, 10.0, -10.0])
def test_wrap_angle_stays_in_range_and_preserves_the_direction(angle):
    wrapped = wrap_angle(angle)
    assert -math.pi - 1e-9 <= wrapped <= math.pi + 1e-9
    turns = (angle - wrapped) / (2 * math.pi)
    assert turns == pytest.approx(round(turns), abs=1e-9)


# -- SyntheticBody -----------------------------------------------------------
@pytest.fixture
def body():
    sim = SyntheticBody(width=32, height=16, hfov_deg=90.0, step_size_m=0.25, turn_angle_deg=90.0)
    yield sim
    sim.close()


def test_reset_returns_a_well_formed_observation(body):
    observation = body.reset()
    assert observation.rgb.shape == (16, 32, 3) and observation.rgb.dtype == np.uint8
    assert observation.depth.shape == (16, 32) and observation.depth.dtype == np.float32
    np.testing.assert_allclose(observation.position, [0.0, 0.0, 0.0])
    assert observation.yaw == pytest.approx(0.0)
    assert observation.step_index == 0


def test_intrinsics_match_the_requested_geometry(body):
    assert (body.intrinsics.width, body.intrinsics.height) == (32, 16)


def test_forward_step_moves_along_the_current_heading(body):
    body.reset()
    observation = body.step(MOVE_FORWARD)
    np.testing.assert_allclose(observation.position, [0.25, 0.0, 0.0], atol=1e-6)
    assert observation.step_index == 1


def test_turn_then_forward_moves_along_the_new_heading(body):
    body.reset()
    body.step(TURN_LEFT)  # +90 deg for this fixture
    observation = body.step(MOVE_FORWARD)
    np.testing.assert_allclose(observation.position, [0.0, 0.25, 0.0], atol=1e-6)


def test_turns_are_opposite_in_sign(body):
    body.reset()
    left = body.step(TURN_LEFT).yaw
    body.reset()
    right = body.step(TURN_RIGHT).yaw
    assert left == pytest.approx(-right, abs=1e-6)


def test_yaw_stays_wrapped_over_a_full_turn(body):
    body.reset()
    for _ in range(8):  # 8 * 90 deg = 720 deg
        assert -math.pi - 1e-6 <= body.step(TURN_LEFT).yaw <= math.pi + 1e-6


def test_frames_change_after_a_turn(body):
    # A policy fed byte-identical frames forever is indistinguishable from a dead
    # sensor, which would hide wiring faults rather than expose them.
    first = body.reset().rgb.copy()
    assert not np.array_equal(first, body.step(TURN_LEFT).rgb)


def test_reset_returns_to_the_origin(body):
    body.reset()
    body.step(MOVE_FORWARD)
    body.step(TURN_LEFT)
    observation = body.reset()
    np.testing.assert_allclose(observation.position, [0.0, 0.0, 0.0])
    assert observation.yaw == pytest.approx(0.0) and observation.step_index == 0


def test_unknown_action_is_rejected(body):
    body.reset()
    with pytest.raises(UnsupportedMotionError, match="unknown action"):
        body.step("teleport")


def test_stop_action_marks_the_episode_done(body):
    body.reset()
    assert body.step(STOP).done is True


def test_max_steps_marks_the_episode_done():
    sim = SyntheticBody(width=8, height=8, max_steps=3)
    sim.reset()
    assert [sim.step(MOVE_FORWARD).done for _ in range(3)] == [False, False, True]


def test_body_declares_its_own_provenance(body):
    assert body.reset().info["body"] == "synthetic"


def test_every_action_name_is_accepted(body):
    body.reset()
    for action in ACTIONS:
        body.step(action)  # must not raise


def test_close_is_idempotent(body):
    body.close()
    body.close()


# ── provider: needs robonix_api + codegen output ────────────────────────────
main = pytest.importorskip(
    "mock_robot.main",
    reason="needs robonix_api plus `rbnx codegen` output on PYTHONPATH",
)


class _Header:
    def __init__(self):
        self.stamp = None
        self.frame_id = ""


class _StubImage:
    def __init__(self):
        self.header = _Header()
        self.height = self.width = self.step = self.is_bigendian = 0
        self.encoding = ""
        self.data = b""


class _StubCameraInfo:
    def __init__(self):
        self.header = _Header()
        self.height = self.width = 0
        self.distortion_model = ""
        self.d = self.k = self.r = self.p = []


class _Point:
    def __init__(self):
        self.x = self.y = self.z = 0.0


class _Quat:
    def __init__(self):
        self.x = self.y = self.z = 0.0
        self.w = 1.0


class _InnerPose:
    def __init__(self):
        self.position = _Point()
        self.orientation = _Quat()


class _PoseHolder:
    def __init__(self):
        self.pose = _InnerPose()


class _StubOdometry:
    def __init__(self):
        self.header = _Header()
        self.child_frame_id = ""
        self.pose = _PoseHolder()


class _StubTime:
    def __init__(self, sec=0, nanosec=0):
        self.sec, self.nanosec = sec, nanosec


@pytest.fixture(autouse=True)
def stub_ros_messages(monkeypatch):
    modules = {
        "sensor_msgs": types.ModuleType("sensor_msgs"),
        "sensor_msgs.msg": types.ModuleType("sensor_msgs.msg"),
        "nav_msgs": types.ModuleType("nav_msgs"),
        "nav_msgs.msg": types.ModuleType("nav_msgs.msg"),
        "builtin_interfaces": types.ModuleType("builtin_interfaces"),
        "builtin_interfaces.msg": types.ModuleType("builtin_interfaces.msg"),
    }
    modules["sensor_msgs.msg"].Image = _StubImage
    modules["sensor_msgs.msg"].CameraInfo = _StubCameraInfo
    modules["nav_msgs.msg"].Odometry = _StubOdometry
    modules["builtin_interfaces.msg"].Time = _StubTime
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    yield


@pytest.fixture
def observation():
    from mock_robot.backend import Observation

    return Observation(
        rgb=np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3),
        depth=np.full((2, 3), 1.5, dtype=np.float32),
        position=np.asarray([1.0, 2.0, 0.0], dtype=np.float32),
        yaw=0.5,
        step_index=7,
    )


# -- config ------------------------------------------------------------------
def test_empty_config_is_valid():
    config, error = main.parse_config({})
    assert error is None and config["step_size_m"] == pytest.approx(0.25)


def test_none_config_is_valid():
    assert main.parse_config(None)[1] is None


@pytest.mark.parametrize("key", ["step_size_m", "turn_angle_deg", "sensor_hz", "hfov_deg"])
def test_non_positive_floats_are_rejected(key):
    _, error = main.parse_config({key: 0})
    assert error is not None and f"config.{key}" in error


@pytest.mark.parametrize("key", ["image_width", "image_height", "max_steps"])
def test_non_positive_ints_are_rejected(key):
    _, error = main.parse_config({key: -1})
    assert error is not None and f"config.{key}" in error


@pytest.mark.parametrize("key", ["rgb_topic", "depth_topic", "intrinsics_topic", "odom_topic"])
def test_relative_topic_names_are_rejected(key):
    # A relative name resolves against the node namespace, so a consumer handed
    # the absolute name from atlas would subscribe to nothing.
    _, error = main.parse_config({key: "camera/image"})
    assert error is not None and "absolute topic name" in error


def test_numeric_strings_are_coerced():
    config, error = main.parse_config({"sensor_hz": "5", "image_width": "64"})
    assert error is None
    assert config["sensor_hz"] == pytest.approx(5.0) and config["image_width"] == 64


def test_build_body_honours_geometry():
    config = main.parse_config({"image_width": 64, "image_height": 48, "hfov_deg": 60.0})[0]
    body = main.build_body(config)
    assert (body.intrinsics.width, body.intrinsics.height) == (64, 48)
    body.close()


# -- ROS message builders ----------------------------------------------------
def test_rgb_message_fields_and_round_trip(observation):
    msg = main.build_rgb_message(observation, "cam")
    assert (msg.height, msg.width) == (2, 3)
    assert msg.encoding == "rgb8" and msg.step == 3 * 3
    assert msg.header.frame_id == "cam"
    np.testing.assert_array_equal(
        np.frombuffer(msg.data, dtype=np.uint8).reshape(2, 3, 3), observation.rgb
    )


def test_depth_message_uses_metres_not_millimetres(observation):
    msg = main.build_depth_message(observation, "cam")
    assert msg.encoding == "32FC1" and msg.step == 3 * 4
    np.testing.assert_allclose(np.frombuffer(msg.data, dtype=np.float32).reshape(2, 3), 1.5)


def test_camera_info_k_and_p_agree():
    intrinsics = SyntheticBody(width=64, height=32, hfov_deg=90.0).intrinsics
    msg = main.build_camera_info_message(intrinsics, "cam")
    assert (msg.width, msg.height) == (64, 32)
    assert msg.k[0] == pytest.approx(32.0)  # fx == width / 2 at 90 deg
    assert msg.k[2] == pytest.approx(32.0) and msg.k[8] == 1.0
    assert msg.p[0] == pytest.approx(msg.k[0]) and msg.p[2] == pytest.approx(msg.k[2])
    assert msg.distortion_model == "plumb_bob" and msg.d == [0.0] * 5


def test_odom_message_carries_position_and_yaw_only(observation):
    msg = main.build_odom_message(observation, "odom", "base")
    assert msg.header.frame_id == "odom" and msg.child_frame_id == "base"
    assert (msg.pose.pose.position.x, msg.pose.pose.position.y) == (1.0, 2.0)
    assert msg.pose.pose.orientation.x == 0.0 and msg.pose.pose.orientation.y == 0.0
    assert msg.pose.pose.orientation.z == pytest.approx(math.sin(0.25))
    assert msg.pose.pose.orientation.w == pytest.approx(math.cos(0.25))


def test_odom_quaternion_is_normalised(observation):
    q = main.build_odom_message(observation, "odom", "base").pose.pose.orientation
    assert q.x**2 + q.y**2 + q.z**2 + q.w**2 == pytest.approx(1.0)


# -- chassis/move ------------------------------------------------------------
@pytest.fixture
def active_provider(monkeypatch):
    published: list[tuple[str, object]] = []
    monkeypatch.setattr(main.provider, "emit", lambda contract, msg: published.append((contract, msg)))
    monkeypatch.setattr(main, "_bind_ros", lambda: None)
    assert main.init({"image_width": 8, "image_height": 8}).__class__.__name__ == "Ok"
    result = main.activate()
    assert result.__class__.__name__ == "Ok", result
    yield published
    main.deactivate()


def test_activate_publishes_every_contract(active_provider):
    contracts = {contract for contract, _ in active_provider}
    assert {main.RGB_CONTRACT, main.DEPTH_CONTRACT, main.ODOM_CONTRACT, main.INTRINSICS_CONTRACT} <= contracts


def test_execute_move_advances_the_body(active_provider):
    status = main.execute_move(0.25, 0.0)
    assert status == {"action": "move_forward", "step": 1, "episode_over": False}


def test_execute_move_turns(active_provider):
    assert main.execute_move(0.0, 15.0)["action"] == "turn_left"
    assert main.execute_move(0.0, -15.0)["action"] == "turn_right"


def test_execute_move_publishes_before_returning(active_provider):
    before = len(active_provider)
    main.execute_move(0.25, 0.0)
    # A caller acting on the response must not be a frame behind.
    assert len(active_provider) > before


def test_execute_move_rejects_a_mismatched_increment(active_provider):
    with pytest.raises(UnsupportedMotionError):
        main.execute_move(5.0, 0.0)


def test_move_rpc_wraps_errors_in_the_status_payload(active_provider):
    """The contract returns std_msgs/String; a bad command must not abort."""
    import chassis_pb2

    request = chassis_pb2.ExecuteMoveCommand_Request(command=chassis_pb2.MoveCommand(forward_m=5.0))
    payload = json.loads(main.move(request).status.data)
    assert "error" in payload and "step size" in payload["error"]


def test_move_rpc_success_payload(active_provider):
    import chassis_pb2

    request = chassis_pb2.ExecuteMoveCommand_Request(command=chassis_pb2.MoveCommand(forward_m=0.25))
    payload = json.loads(main.move(request).status.data)
    assert payload["action"] == "move_forward" and payload["episode_over"] is False


def test_execute_move_before_activation_is_refused(monkeypatch):
    monkeypatch.setattr(main._state, "active", False)
    monkeypatch.setattr(main._state, "body", None)
    with pytest.raises(RuntimeError, match="not active"):
        main.execute_move(0.25, 0.0)


def test_init_rejects_bad_config():
    assert main.init({"sensor_hz": 0}).__class__.__name__ == "Err"


def test_activate_before_init_is_an_error(monkeypatch):
    monkeypatch.setattr(main._state, "config", {})
    monkeypatch.setattr(main._state, "active", False)
    result = main.activate()
    assert result.__class__.__name__ == "Err" and "CMD_INIT" in str(result)


def test_deactivate_is_idempotent(monkeypatch):
    monkeypatch.setattr(main.provider, "emit", lambda *a, **k: None)
    monkeypatch.setattr(main, "_bind_ros", lambda: None)
    main.init({})
    main.activate()
    assert main.deactivate().__class__.__name__ == "Ok"
    assert main.deactivate().__class__.__name__ == "Ok"
    assert main.shutdown().__class__.__name__ == "Ok"


def test_activate_failure_leaves_no_partial_state(monkeypatch):
    monkeypatch.setattr(main.provider, "emit", lambda *a, **k: None)
    monkeypatch.setattr(main, "_bind_ros", lambda: None)

    class Exploding(SyntheticBody):
        def reset(self):
            raise RuntimeError("body unavailable")

    monkeypatch.setattr(main, "build_body", lambda cfg: Exploding(width=8, height=8))
    main.init({})
    result = main.activate()
    assert result.__class__.__name__ == "Err" and "body unavailable" in str(result)
    assert main._state.active is False and main._state.body is None
