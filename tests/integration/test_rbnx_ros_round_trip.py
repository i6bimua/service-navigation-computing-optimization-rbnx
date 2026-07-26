# SPDX-License-Identifier: MulanPSL-2.0
"""Publish-side messages built with real rclpy, decoded by the skill's decoders.

The other ROS tests inject message stubs so they can run anywhere. Stubs accept
anything, which hides a whole class of defect: rclpy validates array fields
strictly (exact length, every entry a `float`), so an int in `CameraInfo.k`
builds fine against a stub and raises at publish time on a real robot. That is
the failure this file exists to catch.

Skipped unless the interpreter can import rclpy. To run it:

    conda create -n rbnx-ros -c robostack-staging -c conda-forge python=3.11 \\
        ros-humble-rclpy ros-humble-sensor-msgs ros-humble-nav-msgs \\
        ros-humble-geometry-msgs ros-humble-std-msgs ros-humble-rmw-fastrtps-cpp
    PYTHONPATH="$PWD/tests/harness/mock_robot:$PWD" \\
        ~/miniconda3/envs/rbnx-ros/bin/python -m pytest tests/integration/test_rbnx_ros_round_trip.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rclpy", reason="needs a ROS 2 environment (see the module docstring)")

from sensor_msgs.msg import CameraInfo, Image  # noqa: E402
from nav_msgs.msg import Odometry  # noqa: E402
from builtin_interfaces.msg import Time  # noqa: E402

from robonix_compute.rbnx.observation import (  # noqa: E402
    ObservationBuffer,
    decode_color_image,
    decode_depth_image,
    decode_intrinsics,
    decode_pose,
)

HARNESS_ROOT = Path(__file__).resolve().parents[1] / "harness" / "mock_robot"
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

backend = pytest.importorskip("mock_robot.backend", reason="wiring harness not present")


@pytest.fixture
def body():
    sim = backend.SyntheticBody(width=8, height=4, hfov_deg=90.0)
    yield sim
    sim.close()


@pytest.fixture
def observation(body):
    body.reset()
    body.step(backend.MOVE_FORWARD)
    return body.step(backend.TURN_LEFT)


def _stamp(msg, frame_id: str):
    msg.header.stamp = Time(sec=1, nanosec=2)
    msg.header.frame_id = frame_id
    return msg


# -- publishers ------------------------------------------------------------
def _rgb(observation, frame_id="cam") -> Image:
    msg = Image()
    _stamp(msg, frame_id)
    msg.height, msg.width = observation.rgb.shape[:2]
    msg.encoding = "rgb8"
    msg.is_bigendian = 0
    msg.step = msg.width * 3
    msg.data = observation.rgb.astype("uint8", copy=False).tobytes()
    return msg


def _depth(observation, frame_id="cam") -> Image:
    msg = Image()
    _stamp(msg, frame_id)
    msg.height, msg.width = observation.depth.shape
    msg.encoding = "32FC1"
    msg.is_bigendian = 0
    msg.step = msg.width * 4
    msg.data = observation.depth.astype("float32", copy=False).tobytes()
    return msg


def _camera_info(intrinsics, frame_id="cam") -> CameraInfo:
    msg = CameraInfo()
    _stamp(msg, frame_id)
    msg.width, msg.height = int(intrinsics.width), int(intrinsics.height)
    msg.distortion_model = "plumb_bob"
    msg.d = [0.0] * 5
    msg.k = intrinsics.to_k()
    msg.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    msg.p = [
        float(intrinsics.fx), 0.0, float(intrinsics.cx), 0.0,
        0.0, float(intrinsics.fy), float(intrinsics.cy), 0.0,
        0.0, 0.0, 1.0, 0.0,
    ]
    return msg


def _odom(observation, frame_id="odom", child="cam") -> Odometry:
    msg = Odometry()
    _stamp(msg, frame_id)
    msg.child_frame_id = child
    msg.pose.pose.position.x = float(observation.position[0])
    msg.pose.pose.position.y = float(observation.position[1])
    msg.pose.pose.position.z = float(observation.position[2])
    msg.pose.pose.orientation.z = math.sin(observation.yaw / 2.0)
    msg.pose.pose.orientation.w = math.cos(observation.yaw / 2.0)
    return msg


# -- rclpy accepts what we build ------------------------------------------
def test_intrinsics_to_k_is_accepted_by_rclpy(body):
    """`k` must be exactly 9 floats; an int raises here but not against a stub."""
    msg = CameraInfo()
    msg.k = body.intrinsics.to_k()  # must not raise
    assert len(msg.k) == 9
    assert all(isinstance(float(v), float) for v in msg.k)


def test_camera_info_arrays_have_the_lengths_rclpy_demands(body):
    msg = _camera_info(body.intrinsics)
    assert len(msg.d) == 5 and len(msg.k) == 9 and len(msg.r) == 9 and len(msg.p) == 12


def test_image_step_matches_the_row_stride(observation):
    rgb, depth = _rgb(observation), _depth(observation)
    assert len(rgb.data) == rgb.step * rgb.height
    assert len(depth.data) == depth.step * depth.height


# -- publish -> decode round trip ------------------------------------------
def test_rgb_survives_the_round_trip_byte_for_byte(observation):
    np.testing.assert_array_equal(decode_color_image(_rgb(observation)), observation.rgb)


def test_depth_survives_the_round_trip_in_metres(observation):
    decoded = decode_depth_image(_depth(observation))
    assert decoded.dtype == np.float32
    np.testing.assert_allclose(decoded, observation.depth, rtol=1e-6)


def test_intrinsics_survive_the_round_trip(body):
    decoded = decode_intrinsics(_camera_info(body.intrinsics))
    np.testing.assert_allclose(
        decoded.reshape(-1), np.asarray(body.intrinsics.to_k(), dtype=np.float32), rtol=1e-6
    )
    assert decoded[0, 0] == pytest.approx(body.intrinsics.fx, rel=1e-6)
    assert decoded[0, 2] == pytest.approx(body.intrinsics.cx, rel=1e-6)


def test_pose_survives_the_round_trip(observation):
    decoded = decode_pose(_odom(observation))
    np.testing.assert_allclose(decoded["position"], observation.position, atol=1e-6)
    # The yaw the body moved by must be the yaw the skill reads back; a
    # quaternion-convention slip here would silently rotate the robot's belief.
    assert decoded["yaw"] == pytest.approx(observation.yaw, abs=1e-6)
    assert decoded["pose"].shape == (4, 4)


def test_observation_buffer_assembles_a_snapshot_from_real_messages(observation, body):
    buffer = ObservationBuffer()
    buffer.on_rgb(_rgb(observation))
    buffer.on_depth(_depth(observation))
    buffer.on_intrinsics(_camera_info(body.intrinsics))
    buffer.on_pose(_odom(observation))
    assert buffer.missing() == []
    snapshot = buffer.snapshot(instruction="go to the kitchen")
    assert snapshot["rgb"].shape == observation.rgb.shape
    assert snapshot["depth"].shape == observation.depth.shape
    assert snapshot["intrinsic"].shape == (3, 3)
    assert snapshot["pose"].shape == (4, 4)
    assert snapshot["instruction"] == "go to the kitchen"


def test_a_turn_changes_the_frame_that_reaches_the_skill(body):
    buffer = ObservationBuffer()
    body.reset()
    buffer.on_rgb(_rgb(body.step(backend.MOVE_FORWARD)))
    buffer.on_depth(_depth(body._observe()))
    first = buffer.rgb.value.copy()
    buffer.on_rgb(_rgb(body.step(backend.TURN_LEFT)))
    assert not np.array_equal(first, buffer.rgb.value)


# -- rclpy actually runs ---------------------------------------------------
def test_rclpy_can_create_and_destroy_a_node():
    """Guards the environment itself: message imports alone do not prove rclpy works."""
    import rclpy

    already_running = rclpy.ok()
    if not already_running:
        rclpy.init()
    try:
        node = rclpy.create_node("rbnx_round_trip_smoke")
        assert node.get_name() == "rbnx_round_trip_smoke"
        node.destroy_node()
    finally:
        if not already_running:
            rclpy.shutdown()
