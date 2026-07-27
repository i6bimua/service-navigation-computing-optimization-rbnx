"""Navigation run state machine.

The compute core, observation source and motion sink are all injected, so the
whole observe -> infer -> act loop runs here without Atlas, ROS or a GPU.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from robonix_compute.rbnx.action_bridge import (
    ACTION_MOVE_FORWARD,
    ACTION_STOP,
    ACTION_TURN_LEFT,
)
from robonix_compute.rbnx.controller import (
    CANCELED,
    FAILED,
    SUCCEEDED,
    TERMINAL_STATES,
    TIMEOUT,
    RunLimits,
    NavigationController,
    summarize_step_logs,
)


class FakeCompute:
    """Stands in for RoboNixComputeSkill: yields a scripted action per step."""

    def __init__(self, script: list[Any], *, step_hook=None, latency_s: float = 0.0):
        self.script = list(script)
        self.step_hook = step_hook
        self.latency_s = latency_s
        self.resets: list[str | None] = []
        self.step_logs: list[dict[str, Any]] = []
        self.observations: list[Any] = []

    def reset(self, task=None, instruction=None):
        self.resets.append(instruction or task)
        return {"status": "reset"}

    def step(self, observation):
        self.observations.append(observation)
        if self.step_hook is not None:
            self.step_hook(len(self.observations))
        action = self.script.pop(0) if self.script else ACTION_STOP
        self.step_logs.append(
            {
                "step_id": len(self.step_logs),
                "triggered_sync": len(self.step_logs) % 2 == 0,
                "timeout": False,
                "late_absorbed": False,
                "latent_reuse": len(self.step_logs) % 2 == 1,
                "latency_s": self.latency_s,
            }
        )
        return {"step_id": len(self.step_logs) - 1, "action": action}

    def telemetry(self):
        return {"status": "ok", "summary": {}, "step_logs": list(self.step_logs)}


class FakeObservations:
    def snapshot(self, *, instruction=None):
        return {"rgb": "frame", "instruction": instruction}


def make_controller(compute, *, sink=None, defaults=None):
    calls: list[Any] = []

    def default_sink(motion):
        calls.append(motion)
        return "ok"

    controller = NavigationController(
        compute=compute,
        observations=FakeObservations(),
        motion_sink=sink or default_sink,
        step_size_m=0.25,
        turn_angle_deg=15.0,
        defaults=defaults or RunLimits(timeout_s=10.0, max_steps=50),
    )
    return controller, calls


def await_terminal(controller, run_id=None, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snapshot = controller.status(run_id)
        if snapshot and snapshot["state"] in TERMINAL_STATES:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"run did not reach a terminal state: {controller.status(run_id)}")


def test_empty_instruction_is_rejected():
    controller, _ = make_controller(FakeCompute([]))
    with pytest.raises(RuntimeError, match="instruction is required"):
        controller.start(instruction="   ")


def test_stop_prediction_succeeds_and_issues_no_motion():
    controller, calls = make_controller(FakeCompute([ACTION_STOP]))
    run = controller.start(instruction="stop right here")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == SUCCEEDED
    assert snapshot["stop_predicted"] is True
    assert calls == []


def test_actions_are_issued_in_order_then_stop():
    compute = FakeCompute([ACTION_MOVE_FORWARD, ACTION_TURN_LEFT, ACTION_STOP])
    controller, calls = make_controller(compute)
    run = controller.start(instruction="forward then left")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == SUCCEEDED
    assert [m.action_index for m in calls] == [ACTION_MOVE_FORWARD, ACTION_TURN_LEFT]
    assert calls[0].forward_m == pytest.approx(0.25)
    assert calls[1].rotate_deg == pytest.approx(15.0)
    assert snapshot["actions_issued"] == 2
    assert snapshot["steps_executed"] == 3
    assert compute.resets == ["forward then left"]


def test_instruction_is_forwarded_into_each_observation():
    compute = FakeCompute([ACTION_MOVE_FORWARD, ACTION_STOP])
    controller, _ = make_controller(compute)
    run = controller.start(instruction="walk to the door")
    await_terminal(controller, run.run_id)
    assert {o["instruction"] for o in compute.observations} == {"walk to the door"}


def test_action_chunk_is_expanded_and_truncated_by_budget():
    # One inference returns a chunk; action_steps_to_execute=1 keeps only the
    # first action of each chunk so the loop re-observes as tightly as possible.
    compute = FakeCompute([[ACTION_MOVE_FORWARD, ACTION_TURN_LEFT], [ACTION_STOP]])
    controller, calls = make_controller(
        compute, defaults=RunLimits(timeout_s=10.0, max_steps=50, action_steps_to_execute=1)
    )
    run = controller.start(instruction="chunked")
    await_terminal(controller, run.run_id)
    assert [m.action_index for m in calls] == [ACTION_MOVE_FORWARD]


def test_whole_chunk_executes_when_budget_is_zero():
    compute = FakeCompute([[ACTION_MOVE_FORWARD, ACTION_TURN_LEFT], [ACTION_STOP]])
    controller, calls = make_controller(compute)
    run = controller.start(instruction="chunked")
    await_terminal(controller, run.run_id)
    assert [m.action_index for m in calls] == [ACTION_MOVE_FORWARD, ACTION_TURN_LEFT]


def test_step_limit_fails_rather_than_claiming_success():
    # The policy never predicted STOP, so the instruction was not completed.
    compute = FakeCompute([ACTION_MOVE_FORWARD] * 10)
    controller, _ = make_controller(compute, defaults=RunLimits(timeout_s=10.0, max_steps=3))
    run = controller.start(instruction="never stops")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == FAILED
    assert "step limit of 3" in snapshot["detail"]
    assert snapshot["steps_executed"] == 3


def test_wall_clock_timeout():
    compute = FakeCompute([ACTION_MOVE_FORWARD] * 1000, step_hook=lambda _: time.sleep(0.02))
    controller, _ = make_controller(compute, defaults=RunLimits(timeout_s=0.1, max_steps=10_000))
    run = controller.start(instruction="slow")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == TIMEOUT
    assert "wall-clock budget" in snapshot["detail"]


def test_cancel_stops_the_loop():
    compute = FakeCompute([ACTION_MOVE_FORWARD] * 1000, step_hook=lambda _: time.sleep(0.01))
    controller, _ = make_controller(compute, defaults=RunLimits(timeout_s=30.0, max_steps=10_000))
    run = controller.start(instruction="cancel me")
    time.sleep(0.05)
    ok, message = controller.cancel(run.run_id)
    assert ok and "cancel requested" in message
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == CANCELED


def test_cancel_is_idempotent_after_completion():
    controller, _ = make_controller(FakeCompute([ACTION_STOP]))
    run = controller.start(instruction="done fast")
    await_terminal(controller, run.run_id)
    ok, message = controller.cancel(run.run_id)
    assert ok and "no-op" in message


def test_cancel_unknown_run():
    controller, _ = make_controller(FakeCompute([]))
    assert controller.cancel("copt-nope") == (False, "no navigation run with that id")


def test_an_empty_run_id_never_reports_a_finished_run_as_the_current_one():
    """Status and cancel resolve an empty id to the run that is still active,
    and to nothing once it has finished.

    Falling back to the most recent run let an empty id — which is what a caller
    holds when a start was refused — silently pick up an unrelated earlier run
    and report its state as this caller's. Telemetry keeps the convenience,
    since reading counters after a run is its whole purpose and it drives no
    control flow.
    """
    controller, _ = make_controller(FakeCompute([ACTION_STOP]))
    run = controller.start(instruction="latest")
    await_terminal(controller, run.run_id)

    assert controller.status(None) is None
    assert controller.cancel(None) == (True, "no-op: no navigation run is active")
    assert controller.status(run.run_id)["run_id"] == run.run_id
    assert controller.telemetry(None)["run_id"] == run.run_id


def test_an_empty_run_id_addresses_the_active_run_while_it_runs():
    compute = FakeCompute([ACTION_MOVE_FORWARD] * 1000, step_hook=lambda _: time.sleep(0.01))
    controller, _ = make_controller(compute, defaults=RunLimits(timeout_s=30.0, max_steps=10_000))
    run = controller.start(instruction="still going")
    try:
        assert controller.status(None)["run_id"] == run.run_id
    finally:
        controller.cancel(run.run_id)
        await_terminal(controller, run.run_id)


def test_status_of_unknown_run_is_none():
    controller, _ = make_controller(FakeCompute([]))
    assert controller.status("copt-nope") is None
    assert controller.telemetry("copt-nope") is None


def test_only_one_run_at_a_time():
    compute = FakeCompute([ACTION_MOVE_FORWARD] * 1000, step_hook=lambda _: time.sleep(0.01))
    controller, _ = make_controller(compute, defaults=RunLimits(timeout_s=30.0, max_steps=10_000))
    first = controller.start(instruction="first")
    with pytest.raises(RuntimeError, match="already active"):
        controller.start(instruction="second")
    controller.cancel(first.run_id)
    await_terminal(controller, first.run_id)
    # Once terminal, a new run is allowed again.
    controller.start(instruction="third")


def test_unknown_action_from_the_policy_fails_the_run():
    controller, _ = make_controller(FakeCompute([99]))
    run = controller.start(instruction="bad action")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == FAILED
    assert "outside the R2R-CE discrete" in snapshot["detail"]


def test_compute_reset_failure_is_reported():
    class Broken(FakeCompute):
        def reset(self, task=None, instruction=None):
            raise RuntimeError("no checkpoint")

    controller, _ = make_controller(Broken([]))
    run = controller.start(instruction="broken")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == FAILED
    assert "compute reset failed: no checkpoint" in snapshot["detail"]


def test_worker_crash_surfaces_in_status():
    def exploding_sink(motion):
        raise RuntimeError("chassis unreachable")

    controller, _ = make_controller(FakeCompute([ACTION_MOVE_FORWARD]), sink=exploding_sink)
    run = controller.start(instruction="crash")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == FAILED
    assert "chassis unreachable" in snapshot["detail"]


def test_empty_action_list_fails_the_run():
    controller, _ = make_controller(FakeCompute([[]]))
    run = controller.start(instruction="no action")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["state"] == FAILED
    assert "no action" in snapshot["detail"]


def test_stop_runtime_cancels_and_joins():
    compute = FakeCompute([ACTION_MOVE_FORWARD] * 1000, step_hook=lambda _: time.sleep(0.01))
    controller, _ = make_controller(compute, defaults=RunLimits(timeout_s=30.0, max_steps=10_000))
    run = controller.start(instruction="long")
    time.sleep(0.05)
    assert controller.stop_runtime() == []
    assert controller.status(run.run_id)["state"] == CANCELED


def test_stop_runtime_is_idempotent():
    controller, _ = make_controller(FakeCompute([ACTION_STOP]))
    controller.start(instruction="quick")
    assert controller.stop_runtime() == []
    assert controller.stop_runtime() == []


def test_start_after_stop_runtime_is_allowed():
    # stop_runtime clears its own shutdown flag so on_deactivate followed by a
    # re-activation leaves a usable controller.
    controller, _ = make_controller(FakeCompute([ACTION_STOP, ACTION_STOP]))
    controller.start(instruction="one")
    controller.stop_runtime()
    controller.start(instruction="two")


# -- per-run telemetry slicing ----------------------------------------------
def test_telemetry_is_sliced_per_run():
    # EdgeRuntime.reset() keeps the telemetry recorder intact across episodes,
    # so a second run must not report the first run's steps.
    compute = FakeCompute([ACTION_MOVE_FORWARD, ACTION_STOP, ACTION_STOP])
    controller, _ = make_controller(compute)
    first = controller.start(instruction="one")
    await_terminal(controller, first.run_id)
    second = controller.start(instruction="two")
    await_terminal(controller, second.run_id)

    assert controller.telemetry(first.run_id)["summary"]["step_count"] == 2
    assert controller.telemetry(second.run_id)["summary"]["step_count"] == 1
    assert len(compute.step_logs) == 3


def test_summarize_step_logs_matches_recorder_fields():
    logs = [
        {"triggered_sync": True, "timeout": False, "late_absorbed": False, "latent_reuse": False, "latency_s": 0.2},
        {"triggered_sync": False, "timeout": True, "late_absorbed": True, "latent_reuse": True, "latency_s": 0.4},
    ]
    summary = summarize_step_logs(logs)
    assert summary == {
        "step_count": 2,
        "sync_count": 1,
        "timeout_count": 1,
        "late_absorbed_count": 1,
        "latent_reuse_count": 1,
        "mean_step_latency_s": pytest.approx(0.3),
    }


def test_summarize_handles_missing_latency():
    assert summarize_step_logs([{"latency_s": None}])["mean_step_latency_s"] is None
    assert summarize_step_logs([])["step_count"] == 0


def test_mean_step_latency_is_reported_in_milliseconds():
    compute = FakeCompute([ACTION_STOP], latency_s=0.25)
    controller, _ = make_controller(compute)
    run = controller.start(instruction="latency")
    snapshot = await_terminal(controller, run.run_id)
    assert snapshot["mean_step_latency_ms"] == pytest.approx(250.0)


def test_telemetry_survives_a_broken_compute_core():
    class NoTelemetry(FakeCompute):
        def telemetry(self):
            raise RuntimeError("recorder gone")

    controller, _ = make_controller(NoTelemetry([ACTION_STOP]))
    run = controller.start(instruction="broken telemetry")
    snapshot = await_terminal(controller, run.run_id)
    # Diagnostics failing must not change the run's outcome.
    assert snapshot["state"] == SUCCEEDED
    assert controller.telemetry(run.run_id)["summary"]["step_count"] == 0


def test_concurrent_status_reads_are_safe():
    compute = FakeCompute([ACTION_MOVE_FORWARD] * 200, step_hook=lambda _: time.sleep(0.001))
    controller, _ = make_controller(compute, defaults=RunLimits(timeout_s=30.0, max_steps=200))
    run = controller.start(instruction="poll me")
    errors: list[BaseException] = []

    def poll():
        try:
            for _ in range(50):
                controller.status(run.run_id)
                controller.telemetry(run.run_id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=poll) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    controller.stop_runtime()
    assert errors == []
