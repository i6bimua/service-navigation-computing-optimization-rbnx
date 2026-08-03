"""Navigation run state machine for the navigation computing optimization service.

Owns the observe -> infer -> act loop and the run bookkeeping the
status / cancel / telemetry contracts read. Deliberately knows nothing about
Atlas, gRPC or rclpy: the compute core, the observation source and the motion
sink are all injected, so the loop is unit-testable with plain stubs.

State words are the canonical async-task names the executor's poller
understands. The executor treats anything it does not recognise as RUNNING, so
a run that ends in a non-canonical word would be polled forever.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from robonix_compute.rbnx.action_bridge import (
    ACTION_STOP,
    MotionCommand,
    UnknownActionError,
    action_to_motion,
    normalize_action_chunk,
)

log = logging.getLogger("compute_optimization.controller")

PENDING = "PENDING"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCELED = "CANCELED"
TIMEOUT = "TIMEOUT"

TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, CANCELED, TIMEOUT})


class ComputeCore(Protocol):
    """The subset of `RoboNixComputeSkill` this controller drives."""

    def reset(self, task: str | None = ..., instruction: str | None = ...) -> dict[str, Any]: ...
    def step(self, observation: Any, *, force_sync: bool = ...) -> dict[str, Any]: ...
    def telemetry(self) -> dict[str, Any]: ...


class ObservationSource(Protocol):
    def snapshot(self, *, instruction: str | None = ...) -> dict[str, Any]: ...


MotionSink = Callable[[MotionCommand], str]


@dataclass
class RunLimits:
    timeout_s: float = 300.0
    max_steps: int = 500
    # 0 = execute every action of an inference chunk before re-observing.
    action_steps_to_execute: int = 0


@dataclass
class NavigationRun:
    run_id: str
    instruction: str
    limits: RunLimits
    state: str = PENDING
    detail: str = "queued"
    steps_executed: int = 0
    actions_issued: int = 0
    stop_predicted: bool = False
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    # Half-open slice [telemetry_offset, telemetry_end) into the compute core's
    # cumulative telemetry log. EdgeRuntime.reset() keeps the telemetry recorder
    # intact across episodes, so per-run numbers need both bounds: without an
    # end, a finished run would absorb the next run's steps.
    telemetry_offset: int = 0
    telemetry_end: int | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def summarize_step_logs(step_logs: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute TelemetryRecorder.summary() over an arbitrary slice of steps.

    Mirrors `robonix_compute.common.telemetry.TelemetryRecorder.summary()`
    field for field; needed because that method always summarises the whole
    recorder while the telemetry contract is per-run.
    """
    latencies = [s["latency_s"] for s in step_logs if s.get("latency_s") is not None]
    return {
        "step_count": len(step_logs),
        "sync_count": sum(1 for s in step_logs if s.get("triggered_sync")),
        "timeout_count": sum(1 for s in step_logs if s.get("timeout")),
        "late_absorbed_count": sum(1 for s in step_logs if s.get("late_absorbed")),
        "latent_reuse_count": sum(1 for s in step_logs if s.get("latent_reuse")),
        "mean_step_latency_s": None if not latencies else sum(latencies) / len(latencies),
    }


class NavigationController:
    """Runs one navigation episode at a time."""

    def __init__(
        self,
        *,
        compute: ComputeCore,
        observations: ObservationSource,
        motion_sink: MotionSink,
        step_size_m: float,
        turn_angle_deg: float,
        defaults: RunLimits | None = None,
    ) -> None:
        self._compute = compute
        self._observations = observations
        self._motion_sink = motion_sink
        self._step_size_m = float(step_size_m)
        self._turn_angle_deg = float(turn_angle_deg)
        self._defaults = defaults or RunLimits()
        self._lock = threading.RLock()
        self._runs: dict[str, NavigationRun] = {}
        self._latest_run_id: str | None = None
        self._shutdown = threading.Event()

    # -- public surface ----------------------------------------------------
    def start(self, *, instruction: str, timeout_s: float = 0.0, max_steps: int = 0) -> NavigationRun:
        """Begin a navigation run. Raises RuntimeError if one is already active."""
        instruction = (instruction or "").strip()
        if not instruction:
            raise RuntimeError("instruction is required")
        if self._shutdown.is_set():
            raise RuntimeError("controller is shutting down")

        limits = RunLimits(
            timeout_s=float(timeout_s) if timeout_s and timeout_s > 0 else self._defaults.timeout_s,
            max_steps=int(max_steps) if max_steps and max_steps > 0 else self._defaults.max_steps,
            action_steps_to_execute=self._defaults.action_steps_to_execute,
        )

        with self._lock:
            active = [r for r in self._runs.values() if not r.is_terminal]
            if active:
                raise RuntimeError(
                    f"a navigation run is already active ({active[0].run_id}); "
                    f"cancel it before starting another"
                )
            run = NavigationRun(
                run_id=f"copt-{uuid.uuid4().hex[:12]}",
                instruction=instruction,
                limits=limits,
                telemetry_offset=self._telemetry_length(),
            )
            self._runs[run.run_id] = run
            self._latest_run_id = run.run_id
            run.thread = threading.Thread(
                target=self._run_episode, args=(run,), name=run.run_id, daemon=True
            )
        run.thread.start()
        log.info("run %s started: %r (timeout=%.1fs max_steps=%d)",
                 run.run_id, instruction, limits.timeout_s, limits.max_steps)
        return run

    def status(self, run_id: str | None = None) -> dict[str, Any] | None:
        run = self._lookup(run_id, active_only=True)
        if run is None:
            return None
        with self._lock:
            step_logs = self._run_step_logs(run)
            summary = summarize_step_logs(step_logs)
            mean_latency_s = summary["mean_step_latency_s"] or 0.0
            return {
                "run_id": run.run_id,
                "state": run.state,
                "steps_executed": run.steps_executed,
                "actions_issued": run.actions_issued,
                "elapsed_s": run.elapsed_s,
                "mean_step_latency_ms": mean_latency_s * 1000.0,
                "stop_predicted": run.stop_predicted,
                "detail": run.detail,
            }

    def cancel(self, run_id: str | None = None) -> tuple[bool, str]:
        run = self._lookup(run_id, active_only=True)
        if run is None:
            # Aborting is idempotent, so "there is nothing running" is a
            # successful no-op rather than an error — an unwind path that cancels
            # twice, or cancels after the run finished on its own, should not
            # look like a failure. A wrong id is still an error.
            if not run_id:
                return True, "no-op: no navigation run is active"
            return False, "no navigation run with that id"
        with self._lock:
            if run.is_terminal:
                return True, f"no-op: run already finished in state {run.state}"
            run.cancel_event.set()
        log.info("run %s cancel requested", run.run_id)
        return True, "cancel requested"

    def telemetry(self, run_id: str | None = None) -> dict[str, Any] | None:
        run = self._lookup(run_id)
        if run is None:
            return None
        with self._lock:
            step_logs = self._run_step_logs(run)
            return {
                "run_id": run.run_id,
                "state": run.state,
                "summary": summarize_step_logs(step_logs),
                "step_logs": step_logs,
            }

    def stop_runtime(self, *, join_timeout_s: float = 2.0) -> list[str]:
        """Cancel every live run and join its worker. Returns stragglers.

        Idempotent: safe to call from on_deactivate and again from
        on_shutdown.
        """
        self._shutdown.set()
        with self._lock:
            live = [r for r in self._runs.values() if not r.is_terminal]
            for run in live:
                run.cancel_event.set()
        threads = [(r.run_id, r.thread) for r in live if r.thread is not None]
        for _, thread in threads:
            thread.join(timeout=join_timeout_s)
        stragglers = [run_id for run_id, thread in threads if thread.is_alive()]
        self._shutdown.clear()
        return stragglers

    # -- internals ---------------------------------------------------------
    def _lookup(self, run_id: str | None, *, active_only: bool = False) -> NavigationRun | None:
        """Resolve a run id. An empty id means "the obvious run", which for
        `active_only` callers is the one still running and nothing else.

        Status and cancel pass `active_only` so that an empty id can never
        resolve to an unrelated run that has already finished: reporting a
        previous run's state as if it belonged to this caller is worse than
        reporting that nothing is active.
        """
        with self._lock:
            if run_id:
                return self._runs.get(run_id)
            if active_only:
                live = [r for r in self._runs.values() if not r.is_terminal]
                return live[0] if len(live) == 1 else None
            if self._latest_run_id is None:
                return None
            return self._runs.get(self._latest_run_id)

    def _telemetry_length(self) -> int:
        """Current length of the compute core's cumulative step log."""
        try:
            return len(self._compute.telemetry().get("step_logs") or [])
        except Exception:  # noqa: BLE001 - telemetry is diagnostics, never fatal
            return 0

    def _run_step_logs(self, run: NavigationRun) -> list[dict[str, Any]]:
        try:
            logs = self._compute.telemetry().get("step_logs") or []
        except Exception:  # noqa: BLE001
            return []
        return list(logs[run.telemetry_offset:run.telemetry_end])

    def _finish(self, run: NavigationRun, state: str, detail: str) -> None:
        # Read the log length before taking the lock: it calls into the compute
        # core, which is not ours to hold a lock across.
        end = self._telemetry_length()
        with self._lock:
            run.state = state
            run.detail = detail
            run.finished_at = time.monotonic()
            run.telemetry_end = end
        log.info("run %s -> %s (%s) after %d steps / %d actions in %.1fs",
                 run.run_id, state, detail, run.steps_executed, run.actions_issued, run.elapsed_s)

    def _aborted(self, run: NavigationRun) -> bool:
        return run.cancel_event.is_set() or self._shutdown.is_set()

    def _run_episode(self, run: NavigationRun) -> None:
        with self._lock:
            run.state = RUNNING
            run.detail = "navigating"
        try:
            self._compute.reset(instruction=run.instruction)
        except Exception as exc:  # noqa: BLE001
            self._finish(run, FAILED, f"compute reset failed: {exc}")
            return

        deadline = run.started_at + run.limits.timeout_s
        try:
            while True:
                if self._aborted(run):
                    self._finish(run, CANCELED, "canceled before the next step")
                    return
                if time.monotonic() >= deadline:
                    self._finish(run, TIMEOUT, f"wall-clock budget of {run.limits.timeout_s:.1f}s exhausted")
                    return
                if run.steps_executed >= run.limits.max_steps:
                    # The policy never predicted STOP, so the instruction was
                    # not completed -- reporting SUCCEEDED here would be wrong.
                    self._finish(
                        run,
                        FAILED,
                        f"step limit of {run.limits.max_steps} reached without a STOP prediction",
                    )
                    return

                observation = self._observations.snapshot(instruction=run.instruction)
                result = self._compute.step(observation)
                run.steps_executed += 1

                actions = normalize_action_chunk(result.get("action"))
                if not actions:
                    # The active context is spent: S1 had nothing left to follow.
                    # Ask the cloud for fresh context once before giving up, which
                    # is how the upstream dual-system loop treats this state.
                    result = self._compute.step(observation, force_sync=True)
                    run.steps_executed += 1
                    actions = normalize_action_chunk(result.get("action"))
                if not actions:
                    self._finish(run, FAILED, "policy returned no action even after a forced cloud sync")
                    return

                budget = run.limits.action_steps_to_execute
                if budget > 0:
                    actions = actions[:budget]

                for action_index in actions:
                    if self._aborted(run):
                        self._finish(run, CANCELED, "canceled mid-chunk")
                        return
                    if int(action_index) == ACTION_STOP:
                        run.stop_predicted = True
                        self._finish(run, SUCCEEDED, "policy predicted STOP")
                        return
                    motion = action_to_motion(
                        action_index,
                        step_size_m=self._step_size_m,
                        turn_angle_deg=self._turn_angle_deg,
                    )
                    if motion is None:  # unreachable: STOP handled above
                        continue
                    status = self._motion_sink(motion)
                    run.actions_issued += 1
                    if status:
                        log.debug("run %s %s -> chassis status %s", run.run_id, motion.action_name, status)
        except UnknownActionError as exc:
            self._finish(run, FAILED, str(exc))
        except Exception as exc:  # noqa: BLE001 - a worker crash must reach status()
            log.exception("run %s crashed", run.run_id)
            self._finish(run, FAILED, f"{type(exc).__name__}: {exc}")
