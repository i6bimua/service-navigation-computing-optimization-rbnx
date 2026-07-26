"""Atlas-routed RGB-D + pose input for the compute-optimization skill.

Turns the three camera contracts and the chassis odometry contract into the
observation mapping the compute runtime already consumes
(`{"rgb", "depth", "intrinsic", "pose", ...}` -- the same keys
`RoboNixComputeSkill.skill_info()` advertises and `model_adapters` read).

Image decoding is done by hand rather than through cv_bridge: the skill needs
no other OpenCV functionality, and cv_bridge is an extra system dependency
that frequently disagrees with a conda-installed numpy.

`rclpy` and `sensor_msgs` are imported lazily so this module stays importable
outside a ROS 2 environment (unit tests, `rbnx validate`, CI).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

log = logging.getLogger("compute_optimization.observation")

# sensor_msgs/Image encodings we can decode without cv_bridge.
_COLOR_ENCODINGS = {
    "rgb8": (3, np.uint8, False),
    "bgr8": (3, np.uint8, True),
    "rgba8": (4, np.uint8, False),
    "bgra8": (4, np.uint8, True),
    "mono8": (1, np.uint8, False),
}
# Depth encodings, with the factor that converts raw units to metres.
_DEPTH_ENCODINGS = {
    "16UC1": (np.uint16, 1e-3),   # millimetres
    "mono16": (np.uint16, 1e-3),
    "32FC1": (np.float32, 1.0),   # already metres
}


class ImageDecodeError(ValueError):
    """Raised for an Image message this module cannot decode."""


def decode_color_image(msg: Any) -> np.ndarray:
    """sensor_msgs/Image -> HxWx3 uint8 RGB array."""
    encoding = str(msg.encoding)
    spec = _COLOR_ENCODINGS.get(encoding)
    if spec is None:
        raise ImageDecodeError(
            f"unsupported colour encoding {encoding!r}; supported: {sorted(_COLOR_ENCODINGS)}"
        )
    channels, dtype, bgr = spec
    array = np.frombuffer(bytes(msg.data), dtype=dtype)
    expected = int(msg.height) * int(msg.width) * channels
    if array.size < expected:
        raise ImageDecodeError(
            f"colour image truncated: got {array.size} values, need {expected} "
            f"for {msg.width}x{msg.height}x{channels}"
        )
    array = array[:expected].reshape(int(msg.height), int(msg.width), channels)
    if bgr:
        array = array[:, :, ::-1]
    if channels == 4:
        array = array[:, :, :3]
    elif channels == 1:
        array = np.repeat(array, 3, axis=2)
    return np.ascontiguousarray(array)


def decode_depth_image(msg: Any) -> np.ndarray:
    """sensor_msgs/Image -> HxW float32 depth array in metres."""
    encoding = str(msg.encoding)
    spec = _DEPTH_ENCODINGS.get(encoding)
    if spec is None:
        raise ImageDecodeError(
            f"unsupported depth encoding {encoding!r}; supported: {sorted(_DEPTH_ENCODINGS)}"
        )
    dtype, scale = spec
    array = np.frombuffer(bytes(msg.data), dtype=dtype)
    expected = int(msg.height) * int(msg.width)
    if array.size < expected:
        raise ImageDecodeError(
            f"depth image truncated: got {array.size} values, need {expected} "
            f"for {msg.width}x{msg.height}"
        )
    return np.ascontiguousarray(array[:expected].reshape(int(msg.height), int(msg.width)).astype(np.float32) * scale)


def decode_intrinsics(msg: Any) -> np.ndarray:
    """sensor_msgs/CameraInfo -> 3x3 float32 intrinsic matrix.

    `k` is row-major: k[0]=fx, k[2]=cx, k[4]=fy, k[5]=cy.
    """
    k = np.asarray(list(msg.k), dtype=np.float32)
    if k.size < 9:
        raise ImageDecodeError(f"CameraInfo.k has {k.size} entries, expected 9")
    return k[:9].reshape(3, 3)


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    """Yaw (rotation about +Z) in radians from a unit quaternion."""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return float(np.arctan2(siny_cosp, cosy_cosp))


def quaternion_to_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
    """Unit quaternion -> 3x3 rotation matrix."""
    norm = float(np.sqrt(x * x + y * y + z * z + w * w)) or 1.0
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.asarray(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float32,
    )


def decode_pose(msg: Any) -> dict[str, Any]:
    """nav_msgs/Odometry or geometry_msgs/PoseWithCovarianceStamped -> pose dict.

    Both message types nest the pose the same way (`.pose.pose`), so one
    decoder covers `primitive/chassis/odom` and `service/map/pose`.

    The returned mapping carries several redundant representations on purpose:
    downstream VLN checkpoints disagree about pose conventions (4x4 transform
    vs. gps+compass vs. flat [x, y, yaw]), and which one is correct is a
    property of the checkpoint, not of Robonix. Every consumer picks the key it
    was trained on; `robonix_compute.model_adapters` reads `pose`.
    """
    inner = msg.pose.pose if hasattr(msg.pose, "pose") else msg.pose
    p, q = inner.position, inner.orientation
    position = np.asarray([p.x, p.y, p.z], dtype=np.float32)
    yaw = quaternion_to_yaw(q.x, q.y, q.z, q.w)
    transform = np.eye(4, dtype=np.float32)
    transform[:3, :3] = quaternion_to_matrix(q.x, q.y, q.z, q.w)
    transform[:3, 3] = position
    return {
        "pose": transform,
        "position": position,
        "yaw": yaw,
        "gps": position[:2].copy(),
        "compass": np.asarray([yaw], dtype=np.float32),
        "quaternion": np.asarray([q.x, q.y, q.z, q.w], dtype=np.float32),
    }


@dataclass
class _Slot:
    """Latest value for one input, with the wall-clock time it arrived."""

    value: Any = None
    stamped_at: float = 0.0

    @property
    def ready(self) -> bool:
        return self.value is not None


@dataclass
class ObservationBuffer:
    """Thread-safe latest-wins store for the skill's four Atlas inputs.

    ROS callbacks fire on the rclpy executor thread; the navigation worker
    reads from its own thread. Latest-wins (rather than queueing) is correct
    here: a VLN step must act on the freshest frame, and a backlog of stale
    frames would make the policy act on the past.
    """

    rgb: _Slot = field(default_factory=_Slot)
    depth: _Slot = field(default_factory=_Slot)
    intrinsic: _Slot = field(default_factory=_Slot)
    pose: _Slot = field(default_factory=_Slot)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _updated: threading.Condition | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._updated = threading.Condition(self._lock)

    # -- callbacks ---------------------------------------------------------
    def _set(self, slot_name: str, value: Any) -> None:
        assert self._updated is not None
        with self._updated:
            slot: _Slot = getattr(self, slot_name)
            slot.value = value
            slot.stamped_at = time.monotonic()
            self._updated.notify_all()

    def on_rgb(self, msg: Any) -> None:
        try:
            self._set("rgb", decode_color_image(msg))
        except ImageDecodeError as exc:
            log.warning("dropping RGB frame: %s", exc)

    def on_depth(self, msg: Any) -> None:
        try:
            self._set("depth", decode_depth_image(msg))
        except ImageDecodeError as exc:
            log.warning("dropping depth frame: %s", exc)

    def on_intrinsics(self, msg: Any) -> None:
        try:
            self._set("intrinsic", decode_intrinsics(msg))
        except ImageDecodeError as exc:
            log.warning("dropping CameraInfo: %s", exc)

    def on_pose(self, msg: Any) -> None:
        try:
            self._set("pose", decode_pose(msg))
        except (AttributeError, TypeError) as exc:
            log.warning("dropping pose: %s", exc)

    # -- reads -------------------------------------------------------------
    def missing(self, *, require_intrinsic: bool = True, require_pose: bool = True) -> list[str]:
        """Names of the inputs that have never produced a usable value."""
        with self._lock:
            gaps = [name for name in ("rgb", "depth") if not getattr(self, name).ready]
            if require_intrinsic and not self.intrinsic.ready:
                gaps.append("intrinsic")
            if require_pose and not self.pose.ready:
                gaps.append("pose")
            return gaps

    def wait_ready(
        self,
        timeout_s: float,
        *,
        require_intrinsic: bool = True,
        require_pose: bool = True,
        abort: threading.Event | None = None,
    ) -> list[str]:
        """Block until every required input has a value. Returns the gaps left."""
        assert self._updated is not None
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            gaps = self.missing(require_intrinsic=require_intrinsic, require_pose=require_pose)
            if not gaps:
                return []
            if abort is not None and abort.is_set():
                return gaps
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return gaps
            with self._updated:
                self._updated.wait(timeout=min(remaining, 0.25))

    def snapshot(self, *, instruction: str | None = None) -> dict[str, Any]:
        """Build one observation mapping from the latest value of each input.

        Raises RuntimeError when RGB or depth is still absent -- the compute
        runtime cannot form S1 inputs without both, and returning a partial
        observation would surface as an opaque failure deeper in the model.
        """
        with self._lock:
            rgb, depth = self.rgb.value, self.depth.value
            intrinsic, pose = self.intrinsic.value, self.pose.value
        if rgb is None or depth is None:
            absent = [n for n, v in (("rgb", rgb), ("depth", depth)) if v is None]
            raise RuntimeError(f"observation incomplete: no {' and no '.join(absent)} frame received yet")
        observation: dict[str, Any] = {"rgb": rgb, "depth": depth}
        if intrinsic is not None:
            observation["intrinsic"] = intrinsic
        if isinstance(pose, dict):
            observation.update(pose)
        if instruction:
            observation["instruction"] = instruction
        return observation

    def age_s(self) -> dict[str, float]:
        """Seconds since each input last updated; inf for never-seen inputs."""
        now = time.monotonic()
        with self._lock:
            return {
                name: (now - slot.stamped_at if slot.ready else float("inf"))
                for name, slot in (
                    ("rgb", self.rgb),
                    ("depth", self.depth),
                    ("intrinsic", self.intrinsic),
                    ("pose", self.pose),
                )
            }
