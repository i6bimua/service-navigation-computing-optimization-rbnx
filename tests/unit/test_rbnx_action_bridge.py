"""Discrete VLN action -> chassis/move mapping."""
from __future__ import annotations

import numpy as np
import pytest

from robonix_compute.rbnx.action_bridge import (
    ACTION_MOVE_FORWARD,
    ACTION_STOP,
    ACTION_TURN_LEFT,
    ACTION_TURN_RIGHT,
    MotionCommand,
    UnknownActionError,
    action_to_motion,
    build_move_request,
    normalize_action_chunk,
    send_move_command,
    strip_scheme,
)

KW = {"step_size_m": 0.25, "turn_angle_deg": 15.0}


def test_stop_maps_to_no_motion():
    # STOP terminates the episode; emitting a zero MoveCommand would make the
    # driver fall through to velocity mode and publish an all-zero twist.
    assert action_to_motion(ACTION_STOP, **KW) is None


def test_forward_uses_forward_m_only():
    motion = action_to_motion(ACTION_MOVE_FORWARD, **KW)
    assert motion == MotionCommand(forward_m=0.25, rotate_deg=0.0, action_index=ACTION_MOVE_FORWARD)
    assert motion.action_name == "MOVE_FORWARD"


def test_turns_are_signed_and_opposite():
    left = action_to_motion(ACTION_TURN_LEFT, **KW)
    right = action_to_motion(ACTION_TURN_RIGHT, **KW)
    # MoveCommand.rotate_deg is positive counter-clockwise.
    assert left.rotate_deg == pytest.approx(15.0)
    assert right.rotate_deg == pytest.approx(-15.0)
    assert left.forward_m == right.forward_m == 0.0


def test_custom_step_and_turn_are_honoured():
    motion = action_to_motion(ACTION_MOVE_FORWARD, step_size_m=0.5, turn_angle_deg=30.0)
    assert motion.forward_m == pytest.approx(0.5)


def test_unknown_action_raises_rather_than_being_dropped():
    # Silently dropping an action would desynchronise the policy's belief about
    # the robot's pose from reality.
    with pytest.raises(UnknownActionError, match="outside the R2R-CE discrete"):
        action_to_motion(7, **KW)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        (1, [1]),
        (2.0, [2]),
        ([1, 2, 3], [1, 2, 3]),
        ([[1, 2], [3]], [1, 2, 3]),
        (np.int64(3), [3]),
        (np.asarray([0, 1, 2]), [0, 1, 2]),
    ],
)
def test_normalize_action_chunk(raw, expected):
    assert normalize_action_chunk(raw) == expected


def test_normalize_rejects_unusable_types():
    with pytest.raises(UnknownActionError):
        normalize_action_chunk({"idx": 1})


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("127.0.0.1:50055", "127.0.0.1:50055"),
        ("http://127.0.0.1:50055", "127.0.0.1:50055"),
        ("https://host.local:9", "host.local:9"),
    ],
)
def test_strip_scheme(endpoint, expected):
    assert strip_scheme(endpoint) == expected


# -- gRPC surface, exercised against fakes so no deployment is needed --------
class _FakeMoveCommand:
    def __init__(self, forward_m=0.0, rotate_deg=0.0):
        self.forward_m = forward_m
        self.rotate_deg = rotate_deg


class _FakeRequest:
    def __init__(self, command):
        self.command = command


class _FakeChassisPb2:
    MoveCommand = _FakeMoveCommand
    ExecuteMoveCommand_Request = _FakeRequest


class _FakeStatus:
    def __init__(self, data):
        self.data = data


class _FakeResponse:
    def __init__(self, data):
        self.status = _FakeStatus(data)


class _FakeStub:
    def __init__(self, data="ok"):
        self._data = data
        self.calls = []

    def ExecuteMoveCommand(self, request, timeout=None):  # noqa: N802
        self.calls.append((request, timeout))
        return _FakeResponse(self._data)


def test_build_move_request_wraps_the_command():
    motion = action_to_motion(ACTION_TURN_RIGHT, **KW)
    request = build_move_request(motion, _FakeChassisPb2)
    assert request.command.rotate_deg == pytest.approx(-15.0)
    assert request.command.forward_m == 0.0


def test_send_move_command_returns_status_and_passes_timeout():
    stub = _FakeStub("moved")
    motion = action_to_motion(ACTION_MOVE_FORWARD, **KW)
    assert send_move_command(stub, motion, _FakeChassisPb2, timeout_s=1.5) == "moved"
    assert stub.calls[0][1] == pytest.approx(1.5)


def test_send_move_command_tolerates_an_empty_status():
    class _NoStatus:
        def ExecuteMoveCommand(self, request, timeout=None):  # noqa: N802
            return object()

    motion = action_to_motion(ACTION_MOVE_FORWARD, **KW)
    assert send_move_command(_NoStatus(), motion, _FakeChassisPb2) == ""
