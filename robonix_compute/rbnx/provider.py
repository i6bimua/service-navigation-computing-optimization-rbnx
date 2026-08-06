#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""robonix.service.navigation.computing_optimization — Atlas bridge.

Registers the navigation-computing-optimized dual-system VLN runtime as a Robonix Service
and exposes four MCP tools (navigate / status / cancel / telemetry).

This is the instruction-following sibling of `robonix.service.navigation`
(Nav2), whose goal is a `PoseStamped`. Both own a long-running navigation
runtime and expose the same navigate / status / cancel triple; this one takes a
natural-language route description instead of a coordinate.

Lifecycle -- note the difference from a Skill:

  * `rbnx boot` sends CMD_INIT *and* CMD_ACTIVATE. Lazy activation is a Skill
    property: both `rbnx` and the executor gate it on the namespace starting
    with `robonix/skill`, so a service is activated eagerly at boot.
  * `on_init` therefore still only parses and validates config.
  * `on_activate` does the part that is cheap and safe at boot: resolve the
    camera / chassis contracts through Atlas and subscribe. By this point every
    primitive is already ACTIVE -- rbnx blocks on soma stage 1 before it starts
    the `service:` section -- so the resolution normally succeeds.
  * The compute core is deliberately NOT built here. Checkpoints and the cloud
    link are acquired by `_ensure_compute_ready()` on the first navigate call.
    Loading a multi-gigabyte VLN model at boot would make GPU memory and a
    reachable cloud host boot-time requirements of every deployment that merely
    lists this package, and would turn a cloud outage into a boot failure.

Cloud boundary: the cloud System-2 process is part of this same package's
runtime, reached at `cloud_host:cloud_port`. It is not a separate Robonix
package -- edge and cloud are one system, and the scheduling that spans them
(when a fresh latent is worth paying for, how long to wait, what to do with a
late reply) lives here on the edge.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any, NoReturn

from robonix_api import ATLAS, Deferred, Err, Ok, Service
from robonix_api.atlas_types import Transport

from robonix_compute.rbnx.action_bridge import MotionCommand, send_move_command, strip_scheme
from robonix_compute.rbnx.config import STUB_ACTION_MODES, parse_config
from robonix_compute.rbnx.controller import RunLimits, NavigationController
from robonix_compute.rbnx.observation import ObservationBuffer

logging.basicConfig(level=logging.INFO, format="[navigation_computing_optimization] %(levelname)s %(message)s")
log = logging.getLogger("navigation_computing_optimization")

# `id` must equal the instance `name` in the deployment manifest's `service:`
# section; the namespace is what Atlas routes on.
service = Service(id="navigation_computing_optimization", namespace="robonix/service/navigation/computing_optimization")

# Codegen output from capabilities/lib/navigation_computing_optimization/srv/*.srv. robonix_api puts
# rbnx-build/codegen/{proto_gen,robonix_mcp_types} on sys.path at import time,
# so this resolves after `scripts/build.sh` has run.
from navigation_computing_optimization_mcp import (  # noqa: E402
    CancelNavigate_Request,
    CancelNavigate_Response,
    GetNavigateStatus_Request,
    GetNavigateStatus_Response,
    GetTelemetry_Request,
    GetTelemetry_Response,
    Navigate_Request,
    Navigate_Response,
)

# Observation inputs, resolved through Atlas in on_activate. Transports must be
# concrete (not UNSPECIFIED) so Atlas can return a usable endpoint.
CAMERA_INPUTS = {
    "rgb": "robonix/primitive/camera/rgb",
    "depth": "robonix/primitive/camera/depth",
    "intrinsic": "robonix/primitive/camera/intrinsics",
}
ODOM_CONTRACT = "robonix/primitive/chassis/odom"
MAP_POSE_CONTRACT = "robonix/service/map/pose"
MOVE_CONTRACT = "robonix/primitive/chassis/move"

_MSG_TYPES = {
    "rgb": "sensor_msgs/Image",
    "depth": "sensor_msgs/Image",
    "intrinsic": "sensor_msgs/CameraInfo",
    ODOM_CONTRACT: "nav_msgs/Odometry",
    MAP_POSE_CONTRACT: "geometry_msgs/PoseWithCovarianceStamped",
}


class _State:
    """Module-level runtime state, guarded by `lock` for activate/deactivate."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.config: dict[str, Any] = {}
        self.controller: NavigationController | None = None
        self.compute: Any = None
        self.observations: ObservationBuffer | None = None
        self.move_channel: Any = None
        self.move_stub: Any = None
        self.chassis_pb2: Any = None
        self.subscriptions: list[Any] = []
        # `wired` is set by on_activate once the robot's contracts are bound.
        # `active` additionally means the compute core is loaded, which happens
        # on the first navigate call rather than at boot -- see the module
        # docstring for why the two are separate.
        self.wired = False
        self.active = False


_state = _State()


# ── Atlas resolution ────────────────────────────────────────────────────────
def _resolve(contract_id: str, transport: Transport, provider_id: str = "") -> Any | None:
    """Find exactly one capability for `contract_id`, or None."""
    try:
        return ATLAS.find_unique_capability(
            contract_id=contract_id, transport=transport, provider_id=provider_id
        )
    except Exception as exc:  # noqa: BLE001 - absent/ambiguous both mean "not usable yet"
        log.debug("cannot resolve %s [%s]: %s", contract_id, transport.name, exc)
        return None


def _pose_contract() -> str:
    return MAP_POSE_CONTRACT if _state.config.get("use_map_pose") else ODOM_CONTRACT


def _missing_dependencies() -> list[str]:
    """Contract ids the deployment does not currently offer."""
    camera_id = str(_state.config.get("camera_provider_id", ""))
    chassis_id = str(_state.config.get("chassis_provider_id", ""))
    map_id = str(_state.config.get("map_provider_id", ""))
    pose_contract = _pose_contract()
    pose_provider = map_id if pose_contract == MAP_POSE_CONTRACT else chassis_id

    wanted = [(cid, Transport.ROS2, camera_id) for cid in CAMERA_INPUTS.values()]
    wanted.append((pose_contract, Transport.ROS2, pose_provider))
    wanted.append((MOVE_CONTRACT, Transport.GRPC, chassis_id))
    return [cid for cid, transport, pid in wanted if _resolve(cid, transport, pid) is None]


def channel_qos(channel: Any, default: str) -> str:
    """The QoS profile the publisher declared for this channel.

    DDS refuses to deliver across a reliability mismatch, and it does so
    silently from the application's point of view: rclpy logs
    "incompatible QoS ... No messages will be received" and the subscription
    then sits empty forever. So the profile has to come from the publisher's own
    declaration rather than from a guess.

    `create_subscription_from_channel` cannot be used for this: it only honours
    an integer `qos_profile` and falls back to the reliable default for the
    string profiles publishers actually declare, which is exactly how the
    mismatch arises. `default` covers a provider that declared no profile.
    """
    params = getattr(channel, "params", None)
    profile = getattr(params, "qos_profile", None)
    if isinstance(profile, str) and profile.strip():
        return profile.strip()
    return default


def _subscribe(contract_id: str, provider_id: str, msg_type: str, callback: Any, default_qos: str) -> Any:
    """Resolve one ROS 2 input on Atlas and subscribe with the publisher's QoS."""
    cap = _resolve(contract_id, Transport.ROS2, provider_id)
    if cap is None:
        raise RuntimeError(f"{contract_id} disappeared between checks")
    channel = service.connect_capability(cap, contract_id, Transport.ROS2)
    qos = channel_qos(channel, default_qos)
    subscription = service.create_subscription(
        contract_id,
        topic=channel.endpoint,
        msg_type=msg_type,
        callback=callback,
        qos=qos,
        # We are the consumer; declaring these on atlas would advertise us as a
        # second provider of the camera's own contracts.
        declare=False,
    )
    log.info("subscribed %s -> %s (qos=%s)", contract_id, channel.endpoint, qos)
    return subscription


def _build_observation_inputs() -> None:
    """Subscribe to RGB / depth / intrinsics / pose through Atlas-given topics."""
    buffer = ObservationBuffer()
    camera_id = str(_state.config.get("camera_provider_id", ""))
    callbacks = {
        "rgb": buffer.on_rgb,
        "depth": buffer.on_depth,
        "intrinsic": buffer.on_intrinsics,
    }
    # Sensor streams are best_effort by convention; intrinsics are static and
    # published reliably so a late subscriber still learns K.
    defaults = {"rgb": "best_effort", "depth": "best_effort", "intrinsic": "reliable"}
    for key, contract_id in CAMERA_INPUTS.items():
        _state.subscriptions.append(
            _subscribe(contract_id, camera_id, _MSG_TYPES[key], callbacks[key], defaults[key])
        )

    pose_contract = _pose_contract()
    pose_provider = (
        str(_state.config.get("map_provider_id", ""))
        if pose_contract == MAP_POSE_CONTRACT
        else str(_state.config.get("chassis_provider_id", ""))
    )
    _state.subscriptions.append(
        _subscribe(
            pose_contract, pose_provider, _MSG_TYPES[pose_contract], buffer.on_pose, "best_effort"
        )
    )
    _state.observations = buffer


def _build_move_sink() -> None:
    """Open the chassis/move gRPC channel and build its typed stub."""
    import grpc  # local: only needed once the service binds its chassis
    import chassis_pb2  # type: ignore[import-not-found]
    import robonix_contracts_pb2_grpc as contracts_grpc  # type: ignore[import-not-found]

    cap = _resolve(MOVE_CONTRACT, Transport.GRPC, str(_state.config.get("chassis_provider_id", "")))
    if cap is None:
        raise RuntimeError(f"{MOVE_CONTRACT} disappeared between checks")
    channel_view = service.connect_capability(cap, MOVE_CONTRACT, Transport.GRPC)
    endpoint = strip_scheme(channel_view.endpoint)
    _state.move_channel = grpc.insecure_channel(endpoint)
    _state.move_stub = contracts_grpc.RobonixPrimitiveChassisMoveStub(_state.move_channel)
    _state.chassis_pb2 = chassis_pb2
    log.info("connected %s -> %s", MOVE_CONTRACT, endpoint)


def _motion_sink(motion: MotionCommand) -> str:
    if _state.move_stub is None or _state.chassis_pb2 is None:
        raise RuntimeError("chassis/move is not connected")
    return send_move_command(
        _state.move_stub,
        motion,
        _state.chassis_pb2,
        timeout_s=float(_state.config["move_timeout_s"]),
    )


def _discarded_motion_sink(motion: MotionCommand) -> str:
    """The motion sink a stub backend gets: counts the command, sends nothing.

    `mock` and `websocket` read their action off the latent and ignore the
    instruction, so the commands are arbitrary with respect to the task. This
    sink has no reference to the chassis stub, which is what keeps an arbitrary
    command from reaching a real robot; the run still steps and reports so the
    contract surface stays testable without hardware.
    """
    log.info("stub backend: %s not sent to %s", motion.action_name, MOVE_CONTRACT)
    return "discarded: a stub backend does not drive a chassis"


def _pick_motion_sink(mode: str) -> Callable[[MotionCommand], str]:
    """Which sink the controller gets, decided by the backend alone.

    Named rather than inlined so the choice is assertable without building a
    runtime: sending stub actions to a real chassis is the failure this guards.
    """
    return _discarded_motion_sink if mode in STUB_ACTION_MODES else _motion_sink


def _build_compute_core() -> Any:
    """Construct the compute runtime from this package's existing wrapper.

    `RoboNixComputeSkill.setup()` already owns the mock / websocket / internnav
    branch, the EdgeRuntime construction and the telemetry recorder, and is
    covered by tests/integration/test_robonix_skill.py. Reusing it keeps one
    code path behind both the HTTP and the Robonix boundary.
    """
    from robonix_compute.robonix.skill import RoboNixComputeSkill

    core = RoboNixComputeSkill()
    core.setup(_state.config)
    return core


def _reject(message: str) -> NoReturn:
    """Refuse to start a run, as a failed tool call rather than a return value.

    The executor treats a completed MCP round-trip as a successful dispatch: it
    reads `run_id` out of the reply and then polls `navigate/status` with it
    until that run reports a terminal state. A rejection expressed as
    `accepted=false` with an empty `run_id` satisfies that first step, so the
    executor would start polling a run that never existed and never terminates.
    Raising makes the tool call itself fail, which the executor turns into a
    failed RTDL node carrying this message.
    """
    log.warning("navigate rejected: %s", message)
    raise RuntimeError(message)


# ── MCP tools ───────────────────────────────────────────────────────────────
@service.mcp("robonix/service/navigation/computing_optimization/navigate")
def navigate(req: Navigate_Request) -> Navigate_Response:
    """Navigate by following a natural-language instruction, e.g. "walk down
    the hallway, turn left at the painting and stop by the kitchen door".
    Call this when the user describes a route in words rather than naming a
    known map goal. Returns immediately with a run_id; poll status with that
    run_id to follow progress, and cancel to abort. Only one navigation run
    can be active at a time.

    A start that cannot be accepted raises instead of answering
    `accepted=false`: see `_reject` for why the distinction matters to the
    executor. A returned response therefore always carries a live `run_id`.
    """
    # First call pays for the checkpoints and the cloud link; later calls are a
    # flag check. Deliberately not done in on_activate — see the module
    # docstring.
    unavailable = _ensure_compute_ready()
    if unavailable is not None:
        _reject(unavailable)
    controller = _state.controller
    if controller is None:
        _reject("controller unavailable after setup reported success")

    buffer = _state.observations
    if buffer is not None:
        gaps = buffer.wait_ready(
            float(_state.config["observation_timeout_s"]),
            require_pose=True,
            require_intrinsic=True,
        )
        if gaps:
            _reject(f"observation not ready: no {', '.join(gaps)} received from the deployment")

    try:
        run = controller.start(
            instruction=req.instruction,
            timeout_s=float(req.timeout_s),
            max_steps=int(req.max_steps),
        )
    except RuntimeError as exc:
        _reject(str(exc))
    return Navigate_Response(accepted=True, run_id=run.run_id, message="navigation started")


@service.mcp("robonix/service/navigation/computing_optimization/navigate/status")
def navigate_status(req: GetNavigateStatus_Request) -> GetNavigateStatus_Response:
    """Poll a navigation run. Empty run_id means the run that is still active.
    `state` is one of PENDING, RUNNING, SUCCEEDED, FAILED, CANCELED, TIMEOUT."""
    # No _ensure_compute_ready() here: polling must answer "unknown run" without
    # loading a model.
    controller = _state.controller
    snapshot = controller.status(req.run_id or None) if controller is not None else None
    if snapshot is None:
        # FAILED, not PENDING. A poller that is told PENDING keeps polling,
        # because PENDING is not a terminal state, and an id that is unknown now
        # will stay unknown — so PENDING here is a request to loop forever.
        return GetNavigateStatus_Response(
            known=False,
            state="FAILED",
            steps_executed=0,
            actions_issued=0,
            elapsed_s=0.0,
            mean_step_latency_ms=0.0,
            stop_predicted=False,
            detail=(
                f"no navigation run with id {req.run_id!r}"
                if req.run_id
                else "no navigation run is active"
            ),
        )
    return GetNavigateStatus_Response(
        known=True,
        state=snapshot["state"],
        steps_executed=int(snapshot["steps_executed"]),
        actions_issued=int(snapshot["actions_issued"]),
        elapsed_s=float(snapshot["elapsed_s"]),
        mean_step_latency_ms=float(snapshot["mean_step_latency_ms"]),
        stop_predicted=bool(snapshot["stop_predicted"]),
        detail=str(snapshot["detail"]),
    )


@service.mcp("robonix/service/navigation/computing_optimization/navigate/cancel")
def navigate_cancel(req: CancelNavigate_Request) -> CancelNavigate_Response:
    """Abort the active navigation run. Empty run_id cancels whatever is
    running. Idempotent."""
    # Like status, this must not build the runtime: cancelling a run that never
    # started is the normal way the executor unwinds a rejected navigate.
    controller = _state.controller
    if controller is None:
        return CancelNavigate_Response(
            ok=False, message="no navigation runtime yet — nothing has been started to cancel"
        )
    ok, message = controller.cancel(req.run_id or None)
    return CancelNavigate_Response(ok=ok, message=message)


@service.mcp("robonix/service/navigation/computing_optimization/telemetry")
def navigate_telemetry(req: GetTelemetry_Request) -> GetTelemetry_Response:
    """Read cloud-edge navigation computing optimization measurements for a navigation run:
    how many steps requested a fresh cloud latent, how many cloud responses
    missed their deadline, how many steps reused a latent, and the mean
    per-step edge latency. Empty run_id means the most recent run."""
    controller = _state.controller
    report = controller.telemetry(req.run_id or None) if controller is not None else None
    if report is None:
        return GetTelemetry_Response(
            known=False,
            steps=0,
            sync_count=0,
            timeout_count=0,
            late_absorbed_count=0,
            latent_reuse_count=0,
            mean_step_latency_ms=0.0,
            summary_json="{}",
        )
    summary = report["summary"]
    mean_latency_s = summary.get("mean_step_latency_s") or 0.0
    return GetTelemetry_Response(
        known=True,
        steps=int(summary["step_count"]),
        sync_count=int(summary["sync_count"]),
        timeout_count=int(summary["timeout_count"]),
        late_absorbed_count=int(summary["late_absorbed_count"]),
        latent_reuse_count=int(summary["latent_reuse_count"]),
        mean_step_latency_ms=float(mean_latency_s) * 1000.0,
        summary_json=json.dumps(report, default=str),
    )


# ── lifecycle ───────────────────────────────────────────────────────────────
@service.on_init
def init(cfg: dict):
    """REGISTERED -> INACTIVE. Light: validate config only.

    Deliberately does not touch Atlas, the cloud, the GPU or the filesystem:
    every package in a deployment reaches INITIALIZED at boot, including ones
    the operator never intends to call in this session.
    """
    config, error = parse_config(cfg)
    if error is not None:
        log.error("CMD_INIT rejected: %s", error)
        return Err(error)
    with _state.lock:
        _state.config = config
    log.info(
        "CMD_INIT ok: mode=%s cloud=%s:%s step=%.3fm turn=%.1fdeg pose=%s",
        config["mode"], config["cloud_host"], config["cloud_port"],
        config["step_size_m"], config["turn_angle_deg"], _pose_contract(),
    )
    return Ok()


@service.on_activate
def activate():
    """INACTIVE -> ACTIVE. Bind the robot's contracts; do not load the model.

    Fired by `rbnx boot` (services are activated eagerly), after soma stage 1
    has brought every primitive to ACTIVE. Returns `Deferred` when the camera or
    chassis provider still is not on Atlas, and `Err` only for failures that
    retrying will not fix.

    The compute core is left to `_ensure_compute_ready()`: see the module
    docstring. Subscribing here rather than on first call is deliberate — the
    observation buffer needs time to fill, and binding at boot means `rbnx caps`
    reports whether the wiring is sound without anyone having to navigate first.
    """
    with _state.lock:
        if _state.wired:
            log.info("CMD_ACTIVATE — already wired, no-op")
            return Ok()

        if not _state.config:
            return Err("CMD_ACTIVATE before a successful CMD_INIT")

        missing = _missing_dependencies()
        if missing:
            return Deferred(
                "waiting for deployment providers: "
                + ", ".join(missing)
                + ". This service needs a camera primitive (rgb + depth + intrinsics), a pose "
                f"source ({_pose_contract()}) and a chassis primitive offering {MOVE_CONTRACT}."
            )

        try:
            _build_observation_inputs()
            _build_move_sink()
        except Exception as exc:  # noqa: BLE001
            log.exception("CMD_ACTIVATE failed")
            _teardown_locked()
            return Err(f"failed to bind deployment contracts: {type(exc).__name__}: {exc}")

        _state.wired = True
    log.info(
        "CMD_ACTIVATE ok — bound to camera/chassis; %s runtime loads on the first navigate call",
        _state.config["mode"],
    )
    return Ok()


def _ensure_compute_ready() -> str | None:
    """Load the compute core and build the controller. Idempotent.

    Called from `navigate`, not from `on_activate`, so that a deployment can
    boot without a GPU or a reachable cloud host. Returns None on success, or a
    message explaining why navigation is not possible.
    """
    with _state.lock:
        if _state.active and _state.controller is not None:
            return None
        if not _state.wired:
            return (
                "not bound to the deployment yet: CMD_ACTIVATE has not succeeded. "
                f"Check `rbnx caps -v` for {service.id} — a Deferred state names the "
                "missing camera, pose or chassis contract."
            )
        try:
            _state.compute = _build_compute_core()
        except Exception as exc:  # noqa: BLE001 - any setup failure is reportable, not fatal
            log.exception("compute core setup failed")
            _state.compute = None
            return (
                f"compute runtime unavailable ({type(exc).__name__}: {exc}). "
                f"mode={_state.config.get('mode')}; for mode=internnav check the checkpoints and "
                f"the cloud System-2 host at {_state.config.get('cloud_host')}:"
                f"{_state.config.get('cloud_port')}."
            )

        assert _state.observations is not None
        mode = str(_state.config["mode"])
        stub_backend = mode in STUB_ACTION_MODES
        _state.controller = NavigationController(
            compute=_state.compute,
            observations=_state.observations,
            motion_sink=_pick_motion_sink(mode),
            step_size_m=float(_state.config["step_size_m"]),
            turn_angle_deg=float(_state.config["turn_angle_deg"]),
            defaults=RunLimits(
                timeout_s=float(_state.config["timeout_s"]),
                max_steps=int(_state.config["max_steps"]),
                action_steps_to_execute=int(_state.config["action_steps_to_execute"]),
            ),
        )
        _state.active = True
    log.info(
        "compute runtime ready — controller running (mode=%s, chassis/move %s)",
        _state.config["mode"],
        "disabled: stub backend" if stub_backend else "live",
    )
    return None


def _teardown_locked() -> list[str]:
    """Release everything on_activate and _ensure_compute_ready acquired.

    Caller holds _state.lock. Clears both flags: leaving `wired` set after the
    subscriptions are destroyed would let a later `_ensure_compute_ready()` past
    its wiring check and then trip on a None observation buffer.
    """
    stragglers: list[str] = []
    if _state.controller is not None:
        stragglers = _state.controller.stop_runtime()
        _state.controller = None

    if _state.compute is not None:
        try:
            _state.compute.close()
        except Exception:  # noqa: BLE001 - closing must not mask the real error
            log.warning("compute core close() raised", exc_info=True)
        _state.compute = None

    if _state.move_channel is not None:
        try:
            _state.move_channel.close()
        except Exception:  # noqa: BLE001
            log.warning("chassis/move channel close() raised", exc_info=True)
    _state.move_channel = None
    _state.move_stub = None
    _state.chassis_pb2 = None

    for subscription in _state.subscriptions:
        destroy = getattr(subscription, "destroy", None)
        if callable(destroy):
            try:
                destroy()
            except Exception:  # noqa: BLE001
                log.debug("subscription destroy() raised", exc_info=True)
    _state.subscriptions.clear()
    _state.observations = None
    _state.active = False
    _state.wired = False
    return stragglers


@service.on_deactivate
def deactivate():
    """ACTIVE -> INACTIVE. Drop the runtime and the wiring but stay registered.

    A later CMD_ACTIVATE re-binds. Idempotent — an explicit deactivate and a
    shutdown can both land here.

    The guard tests `wired` as well as `active`: after boot the normal state is
    wired-but-not-active (subscriptions and the chassis channel are open, the
    model is not loaded), and testing `active` alone would return early and leak
    both.
    """
    with _state.lock:
        if not _state.wired and not _state.active and _state.controller is None:
            return Ok()
        stragglers = _teardown_locked()
    if stragglers:
        return Err(f"navigation worker(s) did not stop within 2 seconds: {', '.join(stragglers)}")
    log.info("CMD_DEACTIVATE ok — runtime released")
    return Ok()


@service.on_shutdown
def shutdown():
    """any -> TERMINATED. Last-chance cleanup."""
    return deactivate()


def main() -> int:
    service.run()
    with _state.lock:
        _teardown_locked()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
