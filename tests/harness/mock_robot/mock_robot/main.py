#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
# pyright: reportArgumentType=false
"""mock_robot — synthetic robot body for verifying this skill's Atlas wiring.

NOT A PUBLISHED PACKAGE. This is a test fixture: it exists so `rbnx boot` can
bring up a deployment and the navigation computing optimization service can be driven end to
end (contract resolution, image decode, chassis/move dispatch, lifecycle) on a
machine with no simulator and no checkpoints.

The frames it publishes carry no semantic content, so nothing measured against
it says anything about navigation quality. Benchmark numbers come from the
InternNav Habitat harness, which owns its own episode loop and metrics — see
the repository README.

Capability surface (all global contracts; no new interface is introduced):

  primitive/camera/driver      rpc        gRPC lifecycle
  primitive/chassis/driver     rpc        gRPC lifecycle
  primitive/camera/rgb         topic_out  sensor_msgs/Image  (rgb8)
  primitive/camera/depth       topic_out  sensor_msgs/Image  (32FC1, metres)
  primitive/camera/intrinsics  topic_out  sensor_msgs/CameraInfo
  primitive/chassis/odom       topic_out  nav_msgs/Odometry
  primitive/chassis/move       rpc        gRPC ExecuteMoveCommand — one discrete
                                          step per call. Not MCP, matching every
                                          real chassis primitive: raw motion
                                          stays off the LLM tool list.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from typing import Any

from robonix_api import Err, Ok, Primitive

from mock_robot.backend import (
    HabitatBridgeBody,
    Observation,
    SyntheticBody,
    UnsupportedMotionError,
    motion_to_action,
)

logging.basicConfig(level=logging.INFO, format="[mock_robot] %(levelname)s %(message)s")
log = logging.getLogger("mock_robot")

# ONE provider, in the chassis namespace.
#
# `<namespace>/driver` is what robonix-api binds the lifecycle servicer to, and
# there is no `robonix/primitive/driver`, so the namespace has to name a concrete
# family. Without a Driver servicer CMD_INIT never arrives and on_init never
# runs, so this choice is load-bearing rather than cosmetic.
#
# A second `Primitive` in the camera namespace does NOT give the camera contracts
# their own provider: `RBNX_INSTANCE_NAME` overrides the id of *every* provider
# constructed in the process (robonix_api._resolve_provider_id), so under
# `rbnx boot` both collapse onto the deployment's single instance name. One
# package is one provider identity; the official multi-family primitives behave
# the same way.
#
# Consequence: `rbnx caps` marks the camera contracts `[namespace mismatch]`.
# That is advisory — atlas's own source says "a mismatch never rejects
# registration or calling" — and the skill resolves them by contract id, which
# is namespace-independent.
provider = Primitive(
    id=os.environ.get("RBNX_INSTANCE_NAME", "mock_robot"),
    namespace="robonix/primitive/chassis",
)

RGB_CONTRACT = "robonix/primitive/camera/rgb"
DEPTH_CONTRACT = "robonix/primitive/camera/depth"
INTRINSICS_CONTRACT = "robonix/primitive/camera/intrinsics"
ODOM_CONTRACT = "robonix/primitive/chassis/odom"
MOVE_CONTRACT = "robonix/primitive/chassis/move"

VALID_BACKENDS = ("synthetic", "habitat_bridge")

DEFAULTS: dict[str, Any] = {
    # "synthetic" needs nothing but ROS 2 and produces meaningless gradients.
    # "habitat_bridge" pulls real MP3D-CE frames from ../habitat_bridge/serve.py,
    # which runs in the Habitat env (py3.9) because rclpy cannot go there.
    "backend": "synthetic",
    "bridge_host": "127.0.0.1",
    "bridge_port": 8799,
    "bridge_timeout_s": 120.0,
    # Must agree with the consuming skill's step_size_m / turn_angle_deg.
    "step_size_m": 0.25,
    "turn_angle_deg": 15.0,
    "sensor_hz": 10.0,
    "rgb_topic": "/mock_robot/camera/color/image_raw",
    "depth_topic": "/mock_robot/camera/depth/image_raw",
    "intrinsics_topic": "/mock_robot/camera/camera_info",
    "odom_topic": "/mock_robot/chassis/odom",
    "frame_id": "mock_robot_camera",
    "odom_frame_id": "odom",
    "image_width": 224,
    "image_height": 224,
    "hfov_deg": 79.0,
    "max_steps": 500,
}


class _State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        # Serialises body access: a move RPC can land while the publisher thread
        # is reading the latest frame.
        self.body_lock = threading.RLock()
        self.config: dict[str, Any] = {}
        self.body: Any = None
        self.latest: Observation | None = None
        self.active = False
        self.ros_bound = False
        self.publish_stop = threading.Event()
        self.publish_thread: threading.Thread | None = None


_state = _State()


def parse_config(cfg: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    """Merge over DEFAULTS and validate. Returns (config, error)."""
    merged = dict(DEFAULTS)
    merged.update(cfg or {})

    backend = str(merged["backend"]).strip().lower()
    if backend not in VALID_BACKENDS:
        return merged, f"config.backend must be one of {VALID_BACKENDS}, got {merged['backend']!r}"
    merged["backend"] = backend

    for key in ("step_size_m", "turn_angle_deg", "sensor_hz", "hfov_deg", "bridge_timeout_s"):
        try:
            value = float(merged[key])
        except (TypeError, ValueError):
            return merged, f"config.{key} must be a number, got {merged[key]!r}"
        if not value > 0:
            return merged, f"config.{key} must be > 0, got {value}"
        merged[key] = value

    for key in ("image_width", "image_height", "max_steps", "bridge_port"):
        try:
            value = int(merged[key])
        except (TypeError, ValueError):
            return merged, f"config.{key} must be an integer, got {merged[key]!r}"
        if not value > 0:
            return merged, f"config.{key} must be > 0, got {value}"
        merged[key] = value

    for key in ("rgb_topic", "depth_topic", "intrinsics_topic", "odom_topic"):
        merged[key] = str(merged[key]).strip()
        if not merged[key].startswith("/"):
            return merged, f"config.{key} must be an absolute topic name, got {merged[key]!r}"
    return merged, None


def build_body(config: dict[str, Any]) -> Any:
    if config["backend"] == "habitat_bridge":
        # Geometry comes from the episode's own sensor config, not from
        # image_width/hfov_deg here — those describe the synthetic body only.
        return HabitatBridgeBody(
            host=str(config["bridge_host"]),
            port=int(config["bridge_port"]),
            timeout_s=float(config["bridge_timeout_s"]),
            step_size_m=float(config["step_size_m"]),
            turn_angle_deg=float(config["turn_angle_deg"]),
        )
    return SyntheticBody(
        width=int(config["image_width"]),
        height=int(config["image_height"]),
        hfov_deg=float(config["hfov_deg"]),
        step_size_m=float(config["step_size_m"]),
        turn_angle_deg=float(config["turn_angle_deg"]),
        max_steps=int(config["max_steps"]),
    )


# ── ROS message builders (rclpy types imported lazily) ──────────────────────
def _stamp(msg: Any, frame_id: str) -> Any:
    from builtin_interfaces.msg import Time  # type: ignore

    now = time.time()
    msg.header.stamp = Time(sec=int(now), nanosec=int((now % 1.0) * 1e9))
    msg.header.frame_id = frame_id
    return msg


def build_rgb_message(observation: Observation, frame_id: str) -> Any:
    from sensor_msgs.msg import Image  # type: ignore

    height, width = observation.rgb.shape[:2]
    msg = Image()
    _stamp(msg, frame_id)
    msg.height, msg.width = int(height), int(width)
    msg.encoding = "rgb8"
    msg.is_bigendian = 0
    msg.step = int(width) * 3
    msg.data = observation.rgb.astype("uint8", copy=False).tobytes()
    return msg


def build_depth_message(observation: Observation, frame_id: str) -> Any:
    from sensor_msgs.msg import Image  # type: ignore

    height, width = observation.depth.shape[:2]
    msg = Image()
    _stamp(msg, frame_id)
    msg.height, msg.width = int(height), int(width)
    # 32FC1 in metres: no millimetre quantisation, and one of the encodings the
    # consuming skill decodes without cv_bridge.
    msg.encoding = "32FC1"
    msg.is_bigendian = 0
    msg.step = int(width) * 4
    msg.data = observation.depth.astype("float32", copy=False).tobytes()
    return msg


def build_camera_info_message(intrinsics: Any, frame_id: str) -> Any:
    from sensor_msgs.msg import CameraInfo  # type: ignore

    msg = CameraInfo()
    _stamp(msg, frame_id)
    msg.width, msg.height = int(intrinsics.width), int(intrinsics.height)
    msg.distortion_model = "plumb_bob"
    # rclpy validates these arrays strictly: exact length, every entry a float.
    # An int here is rejected at publish time, not at build time.
    msg.d = [0.0] * 5
    msg.k = intrinsics.to_k()
    msg.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    msg.p = [
        float(intrinsics.fx), 0.0, float(intrinsics.cx), 0.0,
        0.0, float(intrinsics.fy), float(intrinsics.cy), 0.0,
        0.0, 0.0, 1.0, 0.0,
    ]
    return msg


def build_odom_message(observation: Observation, frame_id: str, child_frame_id: str) -> Any:
    from nav_msgs.msg import Odometry  # type: ignore

    msg = Odometry()
    _stamp(msg, frame_id)
    msg.child_frame_id = child_frame_id
    msg.pose.pose.position.x = float(observation.position[0])
    msg.pose.pose.position.y = float(observation.position[1])
    msg.pose.pose.position.z = float(observation.position[2])
    # Yaw-only rotation about +Z: this body stays upright.
    msg.pose.pose.orientation.x = 0.0
    msg.pose.pose.orientation.y = 0.0
    msg.pose.pose.orientation.z = math.sin(observation.yaw / 2.0)
    msg.pose.pose.orientation.w = math.cos(observation.yaw / 2.0)
    return msg


# ── publishing ──────────────────────────────────────────────────────────────
def publish_observation(observation: Observation) -> None:
    config = _state.config
    frame_id = str(config["frame_id"])
    provider.emit(RGB_CONTRACT, build_rgb_message(observation, frame_id))
    provider.emit(DEPTH_CONTRACT, build_depth_message(observation, frame_id))
    provider.emit(ODOM_CONTRACT, build_odom_message(observation, str(config["odom_frame_id"]), frame_id))


def publish_intrinsics() -> None:
    """Republished each cycle so a subscriber that starts late still gets it."""
    body = _state.body
    if body is None:
        return
    provider.emit(
        INTRINSICS_CONTRACT,
        build_camera_info_message(body.intrinsics, str(_state.config["frame_id"])),
    )


def _publish_loop() -> None:
    period = 1.0 / float(_state.config["sensor_hz"])
    while not _state.publish_stop.wait(period):
        with _state.lock:
            observation = _state.latest
            active = _state.active
        if not active or observation is None:
            continue
        try:
            publish_observation(observation)
            publish_intrinsics()
        except Exception:  # noqa: BLE001 - one bad publish must not kill the thread
            log.warning("failed to publish an observation", exc_info=True)


# ── chassis/move ────────────────────────────────────────────────────────────
def execute_move(forward_m: float, rotate_deg: float) -> dict[str, Any]:
    """Advance the body one discrete step. Returns the status mapping."""
    with _state.lock:
        if not _state.active or _state.body is None:
            raise RuntimeError("mock_robot is not active")
        config = dict(_state.config)

    action = motion_to_action(
        forward_m,
        rotate_deg,
        step_size_m=float(config["step_size_m"]),
        turn_angle_deg=float(config["turn_angle_deg"]),
    )
    with _state.body_lock:
        body = _state.body
        if body is None:  # deactivated while waiting for the lock
            raise RuntimeError("mock_robot is not active")
        observation = body.step(action)

    with _state.lock:
        _state.latest = observation
    # Publish before returning so a caller that acts on the response is not a
    # frame behind the motion it just commanded.
    try:
        publish_observation(observation)
    except Exception:  # noqa: BLE001
        log.warning("failed to publish after %s", action, exc_info=True)
    return {"action": action, "step": observation.step_index, "episode_over": observation.done}


import chassis_pb2  # noqa: E402  (codegen; on PYTHONPATH via rbnx-build/codegen/proto_gen)
import std_msgs_pb2  # noqa: E402


@provider.grpc(MOVE_CONTRACT)
def move(req: "chassis_pb2.ExecuteMoveCommand_Request") -> "chassis_pb2.ExecuteMoveCommand_Response":
    """Advance the body by one discrete action.

    `command.forward_m` drives one forward step and `command.rotate_deg` turns
    in place; positive rotate_deg is counter-clockwise, per the contract. This
    body is step-discrete, so the magnitudes must match its configured
    `step_size_m` / `turn_angle_deg`, and the continuous velocity fields are not
    implemented.
    """
    command = req.command
    try:
        status = execute_move(float(command.forward_m), float(command.rotate_deg))
    except (UnsupportedMotionError, RuntimeError) as exc:
        status = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - never let a fault kill the RPC
        log.exception("chassis/move failed")
        status = {"error": f"{type(exc).__name__}: {exc}"}
    return chassis_pb2.ExecuteMoveCommand_Response(
        status=std_msgs_pb2.String(data=json.dumps(status)),
    )


def _bind_ros() -> None:
    config = _state.config
    provider.create_publisher(
        RGB_CONTRACT, topic=str(config["rgb_topic"]), msg_type="sensor_msgs/Image", qos="best_effort"
    )
    provider.create_publisher(
        DEPTH_CONTRACT, topic=str(config["depth_topic"]), msg_type="sensor_msgs/Image", qos="best_effort"
    )
    # Intrinsics are static; reliable so a consumer is never left guessing K.
    provider.create_publisher(
        INTRINSICS_CONTRACT,
        topic=str(config["intrinsics_topic"]),
        msg_type="sensor_msgs/CameraInfo",
        qos="reliable",
    )
    provider.create_publisher(
        ODOM_CONTRACT, topic=str(config["odom_topic"]), msg_type="nav_msgs/Odometry", qos="best_effort"
    )
    _state.ros_bound = True


# ── lifecycle ───────────────────────────────────────────────────────────────
@provider.on_init
def init(cfg: dict):
    """REGISTERED -> INACTIVE. Validate config only."""
    config, error = parse_config(cfg)
    if error is not None:
        log.error("CMD_INIT rejected: %s", error)
        return Err(error)
    with _state.lock:
        _state.config = config
    log.info(
        "CMD_INIT ok: backend=%s step=%.3fm turn=%.1fdeg %.1fHz",
        config["backend"], config["step_size_m"], config["turn_angle_deg"], config["sensor_hz"],
    )
    return Ok()


@provider.on_activate
def activate():
    """INACTIVE -> ACTIVE. Start the body and the publishing thread."""
    with _state.lock:
        if _state.active:
            log.info("CMD_ACTIVATE — already active, no-op")
            return Ok()
        if not _state.config:
            return Err("CMD_ACTIVATE before a successful CMD_INIT")
        config = dict(_state.config)

    body: Any = None
    try:
        body = build_body(config)
        with _state.body_lock:
            observation = body.reset()
        if not _state.ros_bound:
            _bind_ros()
        with _state.lock:
            _state.body = body
            _state.latest = observation
            _state.active = True
        publish_intrinsics()
        publish_observation(observation)
        _state.publish_stop.clear()
        _state.publish_thread = threading.Thread(
            target=_publish_loop, name="mock-robot-sensors", daemon=True
        )
        _state.publish_thread.start()
    except Exception as exc:  # noqa: BLE001
        log.exception("CMD_ACTIVATE failed")
        if body is not None:
            try:
                body.close()
            except Exception:  # noqa: BLE001
                pass
        with _state.lock:
            _state.body = None
            _state.latest = None
            _state.active = False
        return Err(f"failed to activate: {type(exc).__name__}: {exc}")

    instruction = getattr(body, "instruction", "")
    log.info("CMD_ACTIVATE ok — backend=%s publishing at %.1f Hz%s",
             config["backend"], config["sensor_hz"],
             f" | episode: {instruction[:80]}" if instruction else "")
    return Ok()


@provider.on_deactivate
def deactivate():
    """ACTIVE -> INACTIVE. Stop publishing and drop the body. Idempotent."""
    with _state.lock:
        if not _state.active and _state.body is None:
            return Ok()
        _state.active = False

    _state.publish_stop.set()
    thread, _state.publish_thread = _state.publish_thread, None
    if thread is not None:
        thread.join(timeout=2.0)
        if thread.is_alive():
            return Err("sensor publishing thread did not stop within 2 seconds")

    with _state.body_lock:
        with _state.lock:
            body, _state.body = _state.body, None
            _state.latest = None
        if body is not None:
            try:
                body.close()
            except Exception:  # noqa: BLE001
                log.warning("body close() raised", exc_info=True)
    log.info("CMD_DEACTIVATE ok")
    return Ok()


@provider.on_shutdown
def shutdown():
    """any -> TERMINATED."""
    return deactivate()


def main() -> int:
    provider.run()
    deactivate()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
