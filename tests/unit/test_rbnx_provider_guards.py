# SPDX-License-Identifier: MulanPSL-2.0
"""Two guards on the provider that a wrong answer turns into a hang or a move.

Both were found by reading the executor's async dispatch rather than this
package: `system/executor/src/dispatch/async_poll.rs` treats a completed MCP
round-trip as a successful dispatch, so what a refused `navigate` returns
decides whether a plan terminates.

`tests/integration/test_executor_rejection.py` proves the same thing through a
real executor. These stay here because they run without one.
"""
from __future__ import annotations

import pytest

pytest.importorskip(
    "robonix_api",
    reason="needs a robonix source tree plus `rbnx codegen` output on PYTHONPATH",
)

from robonix_compute.rbnx import provider  # noqa: E402
from robonix_compute.rbnx.config import PRODUCTION_MODES, STUB_ACTION_MODES  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    """Each test drives the module-level provider state directly."""
    state = provider._state
    before = (state.wired, state.active, state.controller, state.observations, dict(state.config))
    yield state
    (state.wired, state.active, state.controller, state.observations, config) = before
    state.config = config


def test_a_refused_start_raises_instead_of_answering_accepted_false(_clean_state):
    """The executor reads `run_id` out of a successful reply and polls it. A
    refusal returned as `accepted=false, run_id=""` is a successful reply, so the
    executor would poll a run that never existed — and `PENDING` is not terminal,
    so that plan never finishes. Raising fails the tool call instead, which the
    executor reports as a failed RTDL node."""
    state = _clean_state
    state.wired = False
    state.active = False
    state.controller = None

    with pytest.raises(RuntimeError) as caught:
        provider.navigate(provider.Navigate_Request(instruction="go", timeout_s=0.0, max_steps=0))

    # The message is what an operator sees on the failed node, so it has to name
    # the cause rather than just the failure.
    assert "CMD_ACTIVATE" in str(caught.value)


def test_status_of_an_unknown_run_is_terminal(_clean_state):
    """A poller told `PENDING` keeps polling, and an id that is unknown now stays
    unknown, so `PENDING` here is a request to loop forever."""
    state = _clean_state
    state.controller = None

    reply = provider.navigate_status(provider.GetNavigateStatus_Request(run_id="copt-nope"))

    assert reply.known is False
    assert reply.state == "FAILED"
    assert "copt-nope" in reply.detail


def test_status_without_a_run_id_says_nothing_is_active(_clean_state):
    state = _clean_state
    state.controller = None

    reply = provider.navigate_status(provider.GetNavigateStatus_Request(run_id=""))

    assert reply.known is False and reply.state == "FAILED"
    assert "active" in reply.detail


@pytest.mark.parametrize("mode", STUB_ACTION_MODES)
def test_a_stub_backend_is_wired_to_a_sink_that_cannot_reach_the_chassis(mode):
    """`mock` and `websocket` read their action off the latent and ignore the
    instruction. Whatever they emit must not become chassis motion, so the
    controller is built with a sink that holds no reference to the chassis stub.
    """
    assert provider._pick_motion_sink(mode) is provider._discarded_motion_sink


@pytest.mark.parametrize("mode", PRODUCTION_MODES)
def test_a_production_backend_drives_the_chassis(mode):
    assert provider._pick_motion_sink(mode) is provider._motion_sink


def test_the_discarding_sink_does_not_touch_the_chassis_stub(_clean_state):
    """Called with no chassis connected at all: the live sink raises, the
    discarding one answers. That difference is the safety property."""
    state = _clean_state
    state.move_stub = None
    state.chassis_pb2 = None
    motion = provider.MotionCommand(forward_m=0.25, action_index=1)

    assert "discarded" in provider._discarded_motion_sink(motion)
    with pytest.raises(RuntimeError, match="not connected"):
        provider._motion_sink(motion)
