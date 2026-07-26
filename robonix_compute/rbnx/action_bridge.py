"""Discrete VLN action -> robonix/primitive/chassis/move translation.

The dual-system policy emits R2R-CE discrete action indices; edge S1 returns
them as a chunk (`InternNavS1Adapter.act` -> `[int(i) for i in output.idx]`).
Robonix's chassis primitive accepts bounded single-shot motion commands whose
`forward_m` / `rotate_deg` fields express exactly the same "move a fixed
increment, then re-observe" semantics, so the mapping is 1:1 and needs no
velocity-times-duration arithmetic on our side.

This module is deliberately free of `robonix_api` and rclpy imports so the
mapping can be unit-tested without a Robonix deployment; only
`send_move_command` touches gRPC, and it takes an already-built stub.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

# R2R-CE / Habitat discrete action space. Any index outside this set is a
# policy/checkpoint mismatch and must fail loudly rather than be silently
# dropped -- a dropped action desynchronises the policy's belief about where
# the robot is from where it actually is.
ACTION_STOP = 0
ACTION_MOVE_FORWARD = 1
ACTION_TURN_LEFT = 2
ACTION_TURN_RIGHT = 3

ACTION_NAMES = {
    ACTION_STOP: "STOP",
    ACTION_MOVE_FORWARD: "MOVE_FORWARD",
    ACTION_TURN_LEFT: "TURN_LEFT",
    ACTION_TURN_RIGHT: "TURN_RIGHT",
}


class UnknownActionError(ValueError):
    """Raised when the policy emits an index outside the discrete action space."""


@dataclass(frozen=True)
class MotionCommand:
    """Transport-independent description of one bounded chassis motion.

    Mirrors the non-zero subset of `chassis/msg/MoveCommand.msg`. The driver
    picks the mode by priority (`forward_m` over `rotate_deg` over the twist
    fields), so exactly one of the two is ever non-zero here.
    """

    forward_m: float = 0.0
    rotate_deg: float = 0.0
    action_index: int = -1

    @property
    def action_name(self) -> str:
        return ACTION_NAMES.get(self.action_index, f"UNKNOWN({self.action_index})")


def normalize_action_chunk(action: Any) -> list[int]:
    """Flatten whatever S1 returned into a list of discrete action indices.

    Edge S1 may return a single int (mock / single-step runners) or a chunk
    (`output.idx`); numpy scalars and arrays are accepted too.
    """
    if action is None:
        return []
    # numpy arrays and torch tensors both expose tolist().
    if hasattr(action, "tolist") and not isinstance(action, (str, bytes)):
        action = action.tolist()
    if isinstance(action, (int, float)):
        return [int(action)]
    if isinstance(action, Iterable) and not isinstance(action, (str, bytes, dict)):
        out: list[int] = []
        for item in action:
            out.extend(normalize_action_chunk(item))
        return out
    raise UnknownActionError(f"cannot interpret policy action of type {type(action).__name__}: {action!r}")


def action_to_motion(action_index: int, *, step_size_m: float, turn_angle_deg: float) -> MotionCommand | None:
    """Map one discrete action index onto a bounded chassis motion.

    Returns None for STOP: STOP is an episode-termination signal, not a motion,
    and issuing a zero-magnitude MoveCommand would make the driver fall through
    to velocity mode and publish an all-zero twist for its default duration.
    """
    index = int(action_index)
    if index == ACTION_STOP:
        return None
    if index == ACTION_MOVE_FORWARD:
        return MotionCommand(forward_m=float(step_size_m), action_index=index)
    if index == ACTION_TURN_LEFT:
        # MoveCommand.rotate_deg is positive counter-clockwise.
        return MotionCommand(rotate_deg=float(turn_angle_deg), action_index=index)
    if index == ACTION_TURN_RIGHT:
        return MotionCommand(rotate_deg=-float(turn_angle_deg), action_index=index)
    raise UnknownActionError(
        f"policy emitted action index {index}, which is outside the R2R-CE discrete "
        f"action space {sorted(ACTION_NAMES)}. Check that step_size_m / turn_angle_deg "
        f"and the checkpoint's action space agree."
    )


class MoveStub(Protocol):
    """The generated RobonixPrimitiveChassisMoveStub surface we depend on."""

    def ExecuteMoveCommand(self, request: Any, timeout: float | None = ...) -> Any:  # noqa: N802
        ...


def strip_scheme(endpoint: str) -> str:
    """gRPC's insecure_channel wants host:port, atlas may hand back a URL."""
    for scheme in ("http://", "https://"):
        if endpoint.startswith(scheme):
            return endpoint[len(scheme):]
    return endpoint


def build_move_request(motion: MotionCommand, chassis_pb2: Any):
    """Wrap a MotionCommand in chassis/srv/ExecuteMoveCommand's Request.

    `chassis_pb2` is injected rather than imported so this module stays
    importable outside a deployment (the module only exists after codegen).
    """
    command = chassis_pb2.MoveCommand(
        forward_m=motion.forward_m,
        rotate_deg=motion.rotate_deg,
    )
    return chassis_pb2.ExecuteMoveCommand_Request(command=command)


def send_move_command(
    stub: MoveStub,
    motion: MotionCommand,
    chassis_pb2: Any,
    *,
    timeout_s: float = 5.0,
) -> str:
    """Issue one bounded motion and return the driver's status string.

    The call blocks for the motion's duration: chassis/move is deliberately
    burst-style, which is what makes the observe -> infer -> move -> observe
    loop well-defined without us timing anything.
    """
    response = stub.ExecuteMoveCommand(build_move_request(motion, chassis_pb2), timeout=timeout_s)
    status = getattr(response, "status", None)
    return str(getattr(status, "data", "") or "")
