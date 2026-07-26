"""ROS message decoding and the latest-wins observation buffer.

Message objects are simple stand-ins: the decoders only touch the fields ROS
guarantees (`height`, `width`, `encoding`, `data`, `k`, `pose`), so no rclpy
install is needed to test them.
"""
from __future__ import annotations

import math
import threading

import numpy as np
import pytest

from robonix_compute.rbnx.observation import (
    ImageDecodeError,
    ObservationBuffer,
    decode_color_image,
    decode_depth_image,
    decode_intrinsics,
    decode_pose,
    quaternion_to_matrix,
    quaternion_to_yaw,
)


class _Image:
    def __init__(self, height, width, encoding, data):
        self.height = height
        self.width = width
        self.encoding = encoding
        self.data = data


class _CameraInfo:
    def __init__(self, k):
        self.k = k


class _Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.x, self.y, self.z, self.w = x, y, z, w


class _Pose:
    def __init__(self, position, orientation):
        self.position = position
        self.orientation = orientation


class _Odometry:
    """nav_msgs/Odometry and PoseWithCovarianceStamped nest pose identically."""

    def __init__(self, pose):
        self.pose = _Nested(pose)


class _Nested:
    def __init__(self, pose):
        self.pose = pose


def _rgb_image(width=2, height=1, encoding="rgb8"):
    pixels = bytes([10, 20, 30, 40, 50, 60])
    return _Image(height, width, encoding, pixels)


def test_decode_rgb8():
    array = decode_color_image(_rgb_image())
    assert array.shape == (1, 2, 3)
    assert array.dtype == np.uint8
    assert array[0, 0].tolist() == [10, 20, 30]


def test_decode_bgr8_swaps_channels():
    array = decode_color_image(_rgb_image(encoding="bgr8"))
    assert array[0, 0].tolist() == [30, 20, 10]


def test_decode_rgba8_drops_alpha():
    msg = _Image(1, 1, "rgba8", bytes([1, 2, 3, 255]))
    assert decode_color_image(msg)[0, 0].tolist() == [1, 2, 3]


def test_decode_mono8_broadcasts_to_three_channels():
    msg = _Image(1, 2, "mono8", bytes([7, 9]))
    array = decode_color_image(msg)
    assert array.shape == (1, 2, 3)
    assert array[0, 0].tolist() == [7, 7, 7]


def test_unsupported_colour_encoding_is_reported():
    with pytest.raises(ImageDecodeError, match="unsupported colour encoding"):
        decode_color_image(_Image(1, 1, "yuv422", b"\0\0"))


def test_truncated_colour_buffer_is_rejected():
    # A short buffer silently reshaped would put garbage in front of the model.
    with pytest.raises(ImageDecodeError, match="truncated"):
        decode_color_image(_Image(4, 4, "rgb8", bytes(6)))


def test_decode_depth_16uc1_converts_millimetres_to_metres():
    data = np.asarray([1000, 2500], dtype=np.uint16).tobytes()
    depth = decode_depth_image(_Image(1, 2, "16UC1", data))
    assert depth.dtype == np.float32
    np.testing.assert_allclose(depth[0], [1.0, 2.5], rtol=1e-6)


def test_decode_depth_32fc1_is_already_metres():
    data = np.asarray([0.5, 1.25], dtype=np.float32).tobytes()
    depth = decode_depth_image(_Image(1, 2, "32FC1", data))
    np.testing.assert_allclose(depth[0], [0.5, 1.25], rtol=1e-6)


def test_unsupported_depth_encoding_is_reported():
    with pytest.raises(ImageDecodeError, match="unsupported depth encoding"):
        decode_depth_image(_Image(1, 1, "rgb8", bytes(3)))


def test_decode_intrinsics_reshapes_row_major():
    k = [500.0, 0.0, 320.0, 0.0, 500.0, 240.0, 0.0, 0.0, 1.0]
    matrix = decode_intrinsics(_CameraInfo(k))
    assert matrix.shape == (3, 3)
    assert matrix[0, 0] == pytest.approx(500.0)  # fx
    assert matrix[0, 2] == pytest.approx(320.0)  # cx
    assert matrix[1, 2] == pytest.approx(240.0)  # cy


def test_short_intrinsics_are_rejected():
    with pytest.raises(ImageDecodeError, match="expected 9"):
        decode_intrinsics(_CameraInfo([1.0, 2.0]))


@pytest.mark.parametrize("yaw", [0.0, 0.5, -1.25, math.pi / 2])
def test_quaternion_to_yaw_roundtrip(yaw):
    q = (0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2))
    assert quaternion_to_yaw(*q) == pytest.approx(yaw, abs=1e-6)


def test_quaternion_to_matrix_is_orthonormal():
    matrix = quaternion_to_matrix(0.0, 0.0, math.sin(0.3), math.cos(0.3))
    np.testing.assert_allclose(matrix @ matrix.T, np.eye(3), atol=1e-5)


def test_decode_pose_exposes_every_convention():
    yaw = 0.75
    msg = _Odometry(
        _Pose(
            _Vec(1.0, 2.0, 3.0),
            _Vec(0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)),
        )
    )
    pose = decode_pose(msg)
    assert pose["pose"].shape == (4, 4)
    np.testing.assert_allclose(pose["pose"][:3, 3], [1.0, 2.0, 3.0], atol=1e-6)
    np.testing.assert_allclose(pose["position"], [1.0, 2.0, 3.0], atol=1e-6)
    assert pose["yaw"] == pytest.approx(yaw, abs=1e-6)
    np.testing.assert_allclose(pose["gps"], [1.0, 2.0], atol=1e-6)
    assert pose["compass"][0] == pytest.approx(yaw, abs=1e-6)


# -- ObservationBuffer ------------------------------------------------------
def _fill(buffer: ObservationBuffer) -> None:
    buffer.on_rgb(_rgb_image())
    buffer.on_depth(_Image(1, 2, "32FC1", np.asarray([1.0, 2.0], dtype=np.float32).tobytes()))
    buffer.on_intrinsics(_CameraInfo([1.0] * 9))
    buffer.on_pose(_Odometry(_Pose(_Vec(), _Vec())))


def test_missing_lists_every_absent_input():
    buffer = ObservationBuffer()
    assert set(buffer.missing()) == {"rgb", "depth", "intrinsic", "pose"}
    _fill(buffer)
    assert buffer.missing() == []


def test_missing_can_ignore_optional_inputs():
    buffer = ObservationBuffer()
    buffer.on_rgb(_rgb_image())
    buffer.on_depth(_Image(1, 2, "32FC1", np.asarray([1.0, 2.0], dtype=np.float32).tobytes()))
    assert buffer.missing(require_intrinsic=False, require_pose=False) == []


def test_snapshot_raises_until_rgb_and_depth_arrive():
    buffer = ObservationBuffer()
    with pytest.raises(RuntimeError, match="observation incomplete"):
        buffer.snapshot()


def test_snapshot_merges_pose_keys_and_instruction():
    buffer = ObservationBuffer()
    _fill(buffer)
    observation = buffer.snapshot(instruction="go to the kitchen")
    assert observation["rgb"].shape == (1, 2, 3)
    assert observation["depth"].shape == (1, 2)
    assert observation["intrinsic"].shape == (3, 3)
    # decode_pose's keys are merged in flat so model_adapters can read `pose`.
    assert observation["pose"].shape == (4, 4)
    assert observation["instruction"] == "go to the kitchen"


def test_latest_frame_wins():
    buffer = ObservationBuffer()
    _fill(buffer)
    newer = _Image(1, 1, "rgb8", bytes([99, 98, 97]))
    buffer.on_rgb(newer)
    assert buffer.snapshot()["rgb"][0, 0].tolist() == [99, 98, 97]


def test_undecodable_frame_is_dropped_not_stored():
    buffer = ObservationBuffer()
    _fill(buffer)
    good = buffer.snapshot()["rgb"].copy()
    buffer.on_rgb(_Image(1, 1, "yuv422", b"\0\0"))
    np.testing.assert_array_equal(buffer.snapshot()["rgb"], good)


def test_wait_ready_returns_gaps_on_timeout():
    buffer = ObservationBuffer()
    gaps = buffer.wait_ready(0.05)
    assert set(gaps) == {"rgb", "depth", "intrinsic", "pose"}


def test_wait_ready_returns_empty_once_filled_from_another_thread():
    buffer = ObservationBuffer()
    threading.Timer(0.05, lambda: _fill(buffer)).start()
    assert buffer.wait_ready(2.0) == []


def test_wait_ready_honours_abort():
    buffer = ObservationBuffer()
    abort = threading.Event()
    abort.set()
    assert buffer.wait_ready(5.0, abort=abort)  # returns immediately with gaps


def test_age_reports_inf_for_never_seen_inputs():
    buffer = ObservationBuffer()
    ages = buffer.age_s()
    assert ages["rgb"] == float("inf")
    buffer.on_rgb(_rgb_image())
    assert buffer.age_s()["rgb"] < 1.0
