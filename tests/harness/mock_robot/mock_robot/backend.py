# SPDX-License-Identifier: MulanPSL-2.0
"""Synthetic robot body for the wiring harness.

Produces RGB-D frames and a dead-reckoned pose. There is no simulator behind
this: frames are heading-dependent gradients with no semantic content, so any
navigation "quality" measured against it is meaningless by construction. Its
only job is to make the skill's Atlas wiring exercisable — contract resolution,
image decode, `chassis/move` dispatch, lifecycle transitions.

Deliberately free of rclpy so the motion mapping and dead reckoning can be
unit-tested without a ROS 2 install.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Discrete action names, matching the vocabulary the benchmark harness uses so
# the mapping reads the same on both sides.
MOVE_FORWARD = "move_forward"
TURN_LEFT = "turn_left"
TURN_RIGHT = "turn_right"
STOP = "stop"
ACTIONS = (MOVE_FORWARD, TURN_LEFT, TURN_RIGHT, STOP)


class UnsupportedMotionError(ValueError):
    """Raised when a MoveCommand cannot be expressed as a discrete action."""


@dataclass
class CameraIntrinsics:
    """Pinhole intrinsics for the synthetic RGB-D sensor."""

    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float

    @classmethod
    def from_hfov(cls, width: int, height: int, hfov_deg: float) -> "CameraIntrinsics":
        """Derive fx/fy from horizontal FOV, the way simulators configure sensors."""
        half = math.radians(float(hfov_deg)) / 2.0
        fx = (width / 2.0) / math.tan(half)
        # Square pixels, so fy follows fx.
        return cls(width=width, height=height, fx=fx, fy=fx, cx=width / 2.0, cy=height / 2.0)

    def to_k(self) -> list[float]:
        """Row-major 3x3 matrix in sensor_msgs/CameraInfo `k` order.

        Every entry is coerced to float: real rclpy rejects a `k` containing an
        int outright, and an integer creeping in from a hand-built
        CameraIntrinsics would only surface at publish time.
        """
        return [float(self.fx), 0.0, float(self.cx),
                0.0, float(self.fy), float(self.cy),
                0.0, 0.0, 1.0]


@dataclass
class Observation:
    """One RGB-D frame plus the pose that produced it, in the ROS frame."""

    rgb: np.ndarray          # HxWx3 uint8
    depth: np.ndarray        # HxW float32, metres
    position: np.ndarray     # [x, y, z] float32, ROS REP-103 (x forward, z up)
    yaw: float               # radians about +Z, counter-clockwise
    step_index: int = 0
    done: bool = False
    info: dict[str, Any] = field(default_factory=dict)


def motion_to_action(
    forward_m: float,
    rotate_deg: float,
    *,
    step_size_m: float,
    turn_angle_deg: float,
    tolerance: float = 0.5,
) -> str:
    """Map a `chassis/msg/MoveCommand` onto one discrete action.

    Reproduces MoveCommand's driver-side priority (`forward_m` over
    `rotate_deg` over the twist fields). `tolerance` is the fraction of the
    configured increment a command may differ by and still be accepted; a
    command far off the increment is rejected rather than silently executed as
    a different distance, because the policy's belief about where the robot is
    depends on the increment it asked for.
    """
    if abs(forward_m) > 1e-9:
        if forward_m < 0:
            raise UnsupportedMotionError(
                f"this body has no backward action; got forward_m={forward_m}"
            )
        if abs(forward_m - step_size_m) > tolerance * step_size_m:
            raise UnsupportedMotionError(
                f"forward_m={forward_m} does not match the step size {step_size_m}; "
                f"align the skill's step_size_m with this body's"
            )
        return MOVE_FORWARD
    if abs(rotate_deg) > 1e-9:
        if abs(abs(rotate_deg) - turn_angle_deg) > tolerance * turn_angle_deg:
            raise UnsupportedMotionError(
                f"rotate_deg={rotate_deg} does not match the turn angle {turn_angle_deg}; "
                f"align the skill's turn_angle_deg with this body's"
            )
        return TURN_LEFT if rotate_deg > 0 else TURN_RIGHT
    raise UnsupportedMotionError(
        "MoveCommand carried neither forward_m nor rotate_deg; this body is "
        "step-discrete and does not implement continuous velocity mode"
    )


def wrap_angle(angle: float) -> float:
    """Normalise to [-pi, pi]. Either sign is returned at the boundary."""
    return math.atan2(math.sin(angle), math.cos(angle))


class SyntheticBody:
    """Dead-reckoned pose plus heading-dependent frames. No simulator."""

    def __init__(
        self,
        *,
        width: int = 224,
        height: int = 224,
        hfov_deg: float = 79.0,
        step_size_m: float = 0.25,
        turn_angle_deg: float = 15.0,
        max_steps: int = 500,
    ) -> None:
        self._intrinsics = CameraIntrinsics.from_hfov(width, height, hfov_deg)
        self._step_size_m = float(step_size_m)
        self._turn_angle_rad = math.radians(float(turn_angle_deg))
        self._max_steps = int(max_steps)
        self._position = np.zeros(3, dtype=np.float32)
        self._yaw = 0.0
        self._step_index = 0

    @property
    def intrinsics(self) -> CameraIntrinsics:
        return self._intrinsics

    def reset(self) -> Observation:
        self._position = np.zeros(3, dtype=np.float32)
        self._yaw = 0.0
        self._step_index = 0
        return self._observe()

    def step(self, action: str) -> Observation:
        if action not in ACTIONS:
            raise UnsupportedMotionError(f"unknown action {action!r}; expected one of {ACTIONS}")
        if action == MOVE_FORWARD:
            self._position = self._position + np.asarray(
                [
                    math.cos(self._yaw) * self._step_size_m,
                    math.sin(self._yaw) * self._step_size_m,
                    0.0,
                ],
                dtype=np.float32,
            )
        elif action == TURN_LEFT:
            self._yaw = wrap_angle(self._yaw + self._turn_angle_rad)
        elif action == TURN_RIGHT:
            self._yaw = wrap_angle(self._yaw - self._turn_angle_rad)
        self._step_index += 1
        return self._observe(done=action == STOP or self._step_index >= self._max_steps)

    def close(self) -> None:
        return None

    def _observe(self, *, done: bool = False) -> Observation:
        h, w = self._intrinsics.height, self._intrinsics.width
        # Heading-dependent gradient so consecutive frames differ after a turn.
        # A policy fed byte-identical frames forever is indistinguishable from a
        # dead sensor, which would hide wiring faults instead of exposing them.
        column = np.linspace(0.0, 1.0, w, dtype=np.float32)
        phase = (self._yaw / (2 * math.pi)) % 1.0
        band = ((column + phase) % 1.0) * 255.0
        rgb = np.repeat(np.repeat(band[None, :, None], h, axis=0), 3, axis=2).astype(np.uint8)
        depth = np.full((h, w), 2.0, dtype=np.float32)
        return Observation(
            rgb=rgb,
            depth=depth,
            position=self._position.copy(),
            yaw=self._yaw,
            step_index=self._step_index,
            done=done,
            info={"body": "synthetic"},
        )


class HabitatBridgeBody:
    """Real Habitat imagery, fetched from the bridge server over TCP.

    Same interface as `SyntheticBody`, so `mock_robot` publishes real MP3D-CE
    frames on the camera and chassis contracts without knowing Habitat exists.
    Habitat itself runs in its own Python 3.9 environment (see
    ../habitat_bridge/serve.py) because neither rclpy nor robonix-api installs
    there.

    Still a test harness: it makes no metric claims. Benchmark numbers come from
    InternNav's evaluator, which owns the real episode loop.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8799,
        timeout_s: float = 120.0,
        step_size_m: float = 0.25,
        turn_angle_deg: float = 15.0,
    ) -> None:
        self._host, self._port, self._timeout_s = host, int(port), float(timeout_s)
        self._step_size_m = float(step_size_m)
        self._turn_angle_deg = float(turn_angle_deg)
        self._sock: Any = None
        self._intrinsics: CameraIntrinsics | None = None
        self._instruction = ""
        self._connect()

    # -- wire ---------------------------------------------------------------
    def _connect(self) -> None:
        import socket

        sock = socket.create_connection((self._host, self._port), timeout=self._timeout_s)
        sock.settimeout(self._timeout_s)
        self._sock = sock
        info = self._request({"op": "info"})
        i = info["intrinsics"]
        self._intrinsics = CameraIntrinsics(
            width=int(i["width"]), height=int(i["height"]),
            fx=float(i["fx"]), fy=float(i["fy"]), cx=float(i["cx"]), cy=float(i["cy"]),
        )
        self._instruction = str(info.get("instruction", ""))

    def _request(self, payload: dict) -> dict:
        import struct

        import msgpack

        if self._sock is None:
            raise UnsupportedMotionError("habitat bridge is closed")
        blob = msgpack.packb(payload, use_bin_type=True)
        self._sock.sendall(struct.pack(">I", len(blob)) + blob)

        def _exact(count: int) -> bytes:
            chunks = []
            while count:
                chunk = self._sock.recv(count)
                if not chunk:
                    raise ConnectionError("habitat bridge closed the connection")
                chunks.append(chunk)
                count -= len(chunk)
            return b"".join(chunks)

        reply = msgpack.unpackb(_exact(struct.unpack(">I", _exact(4))[0]), raw=False)
        if not reply.get("ok", False):
            raise UnsupportedMotionError(str(reply.get("error", "habitat bridge returned ok=false")))
        return reply

    # -- SyntheticBody-compatible surface -----------------------------------
    @property
    def intrinsics(self) -> CameraIntrinsics:
        if self._intrinsics is None:
            raise UnsupportedMotionError("habitat bridge did not report intrinsics")
        return self._intrinsics

    @property
    def instruction(self) -> str:
        """The episode's own instruction, so a caller can drive the real task."""
        return self._instruction

    def reset(self) -> Observation:
        reply = self._request({"op": "reset"})
        return self._observation(reply)

    def step(self, action: str) -> Observation:
        if action not in ACTIONS:
            raise UnsupportedMotionError(f"unknown action {action!r}; expected one of {ACTIONS}")
        return self._observation(self._request({"op": "step", "action": action}))

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            import struct

            import msgpack

            blob = msgpack.packb({"op": "close"}, use_bin_type=True)
            sock.sendall(struct.pack(">I", len(blob)) + blob)
        except Exception:  # noqa: BLE001 - closing must not raise
            pass
        finally:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass

    def _observation(self, reply: dict) -> Observation:
        rgb = np.frombuffer(reply["rgb"], dtype=np.uint8).reshape(tuple(reply["rgb_shape"]))
        depth = np.frombuffer(reply["depth"], dtype=np.float32).reshape(tuple(reply["depth_shape"]))
        return Observation(
            rgb=np.ascontiguousarray(rgb),
            depth=np.ascontiguousarray(depth),
            position=np.asarray(reply["position"], dtype=np.float32),
            yaw=float(reply["yaw"]),
            step_index=int(reply["step_index"]),
            done=bool(reply["done"]),
            info={"body": "habitat_bridge", "instruction": self._instruction},
        )
