from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable

from robonix_compute.common.context_buffer import ContextBuffer, LatentSlot
from robonix_compute.common.protocol import LatentRequest, LatentResponse, new_request_id
from robonix_compute.common.telemetry import StepTelemetry, TelemetryRecorder
from robonix_compute.common.timeout import AdaptiveTimeoutController
from robonix_compute.edge.runners import S1Runner
from robonix_compute.edge.switcher import KeyLatentSwitcher, KeyLatentSwitcherConfig

LatentRequestSender = Callable[[LatentRequest], Future]


@dataclass
class EdgeRuntimeConfig:
    switcher: KeyLatentSwitcherConfig = field(default_factory=KeyLatentSwitcherConfig)
    initial_timeout_s: float = 0.4
    min_timeout_s: float = 0.05
    max_timeout_s: float = 10.0
    timeout_window_size: int = 32
    require_initial_latent: bool = True


class SignalLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight_request_id: str | None = None

    @property
    def request_id(self) -> str | None:
        with self._lock:
            return self._inflight_request_id

    def is_free(self) -> bool:
        return self.request_id is None

    def acquire(self, request_id: str) -> bool:
        with self._lock:
            if self._inflight_request_id is not None:
                return False
            self._inflight_request_id = request_id
            return True

    def release(self, request_id: str | None = None) -> bool:
        with self._lock:
            if self._inflight_request_id is None:
                return False
            if request_id is not None and self._inflight_request_id != request_id:
                return False
            self._inflight_request_id = None
            return True

    def reset(self) -> None:
        with self._lock:
            self._inflight_request_id = None


class EdgeRuntime:
    """Online RoboNix-Compute-Optimization-Skill edge runtime.

    The edge owns synchronization decisions. Cloud calls are represented by a
    Future-returning sender so the same runtime can run over websockets, RPC, or
    an in-process mock.
    """

    def __init__(
        self,
        *,
        s1_runner: S1Runner,
        latent_sender: LatentRequestSender,
        config: EdgeRuntimeConfig | None = None,
        telemetry: TelemetryRecorder | None = None,
    ) -> None:
        self.s1_runner = s1_runner
        self.latent_sender = latent_sender
        self.config = config or EdgeRuntimeConfig()
        self.buffer = ContextBuffer()
        self.signal_lock = SignalLock()
        self.switcher = KeyLatentSwitcher(self.config.switcher)
        self.timeout = AdaptiveTimeoutController(
            initial_rtt_s=self.config.initial_timeout_s,
            min_timeout_s=self.config.min_timeout_s,
            max_timeout_s=self.config.max_timeout_s,
            window_size=self.config.timeout_window_size,
        )
        self.telemetry = telemetry or TelemetryRecorder()
        self._late_threads: list[threading.Thread] = []

    def reset(self, *, initial_latent: Any | None = None, step_id: int = 0) -> None:
        self.buffer.reset()
        self.signal_lock.reset()
        self.switcher.reset()
        self.s1_runner.reset()
        if initial_latent is not None:
            self.buffer.seed(initial_latent, step_id=step_id, request_id="initial")

    def step(self, observation: Any, *, step_id: int, instruction: str | None = None) -> Any:
        started_at = time.perf_counter()
        feature = self.s1_runner.extract_visual_feature(observation)
        similarity = self.switcher.observe(feature)
        misalignment = self.buffer.misalignment(current_step_id=step_id)
        if misalignment is None:
            misalignment = 0
        trigger = self.switcher.should_trigger(similarity, lock_free=self.signal_lock.is_free())
        telemetry = StepTelemetry(
            step_id=int(step_id),
            visual_similarity=similarity,
            tau_sim=self.switcher.tau_sim,
            triggered_sync=trigger,
            latent_misalignment_k=misalignment,
            timeout_s=self.timeout.current_timeout_s(),
        )

        if trigger:
            telemetry.metadata["sync_reason"] = "key_latent"
            self._synchronize(
                observation=observation,
                step_id=step_id,
                instruction=instruction,
                telemetry=telemetry,
            )
            active_slot = self.buffer.active()
        else:
            active_slot = self.buffer.active()
            self.switcher.update_step(misalignment_k=misalignment, triggered=False)

        if active_slot is None:
            if self.config.require_initial_latent:
                raise RuntimeError("No active latent available; seed the buffer or allow initial synchronization.")
            telemetry.triggered_sync = True
            telemetry.metadata["sync_reason"] = "initial_latent_missing"
            self._synchronize(
                observation=observation,
                step_id=step_id,
                instruction=instruction,
                telemetry=telemetry,
            )
            active_slot = self.buffer.active()
            if active_slot is None:
                raise RuntimeError("Cloud did not provide an initial latent.")

        telemetry.latent_reuse = not telemetry.fresh_latent
        telemetry.latent_misalignment_k = self.buffer.misalignment(current_step_id=step_id)
        action = self.s1_runner.act(observation, active_slot.latent)
        telemetry.action = action
        telemetry.tau_sim = self.switcher.tau_sim
        telemetry.latency_s = time.perf_counter() - started_at
        self.telemetry.record(telemetry)
        return action

    def _synchronize(
        self,
        *,
        observation: Any,
        step_id: int,
        instruction: str | None,
        telemetry: StepTelemetry,
    ) -> None:
        request_id = new_request_id()
        if not self.signal_lock.acquire(request_id):
            telemetry.metadata["sync_skipped"] = "inflight_request"
            return

        request = LatentRequest(
            request_id=request_id,
            step_id=int(step_id),
            observation=observation,
            instruction=instruction,
        )
        telemetry.request_id = request_id
        sent_at = time.perf_counter()
        future = self.latent_sender(request)
        timeout_s = self.timeout.current_timeout_s()
        telemetry.timeout_s = timeout_s
        try:
            response = future.result(timeout=timeout_s)
        except FutureTimeoutError:
            telemetry.timeout = True
            self.timeout.record_timeout()
            self.signal_lock.release(request_id)
            self.switcher.apply_network_feedback(
                success=False,
                rtt_s=None,
                timeout_s=timeout_s,
                timeout_count=self.timeout.timeout_count,
                ok_count=self.timeout.ok_count,
                misalignment_k=self.buffer.misalignment(current_step_id=step_id) or 0,
            )
            self._watch_late_response(future, request_id=request_id, telemetry=telemetry)
            self.switcher.update_step(
                misalignment_k=self.buffer.misalignment(current_step_id=step_id) or 0,
                triggered=True,
            )
            return
        except Exception as exc:
            self.signal_lock.release(request_id)
            telemetry.metadata["sync_error"] = str(exc)
            raise

        rtt_s = time.perf_counter() - sent_at
        telemetry.rtt_s = rtt_s
        slot = self._absorb_response(response, late=False)
        telemetry.fresh_latent = slot.request_id == request_id
        self.timeout.record_success(rtt_s)
        self.signal_lock.release(request_id)
        self.switcher.apply_network_feedback(
            success=True,
            rtt_s=rtt_s,
            timeout_s=timeout_s,
            timeout_count=self.timeout.timeout_count,
            ok_count=self.timeout.ok_count,
            misalignment_k=self.buffer.misalignment(current_step_id=step_id) or 0,
        )
        self.switcher.update_step(misalignment_k=0, triggered=True)

    def _watch_late_response(self, future: Future, *, request_id: str, telemetry: StepTelemetry) -> None:
        def _worker() -> None:
            try:
                response = future.result()
            except Exception as exc:  # pragma: no cover
                telemetry.metadata["late_response_error"] = str(exc)
                return
            self._absorb_response(response, late=True)
            telemetry.late_absorbed = True
            telemetry.metadata["late_request_id"] = request_id

        thread = threading.Thread(target=_worker, daemon=True)
        self._late_threads.append(thread)
        thread.start()

    def _absorb_response(self, response: LatentResponse | dict[str, Any], *, late: bool) -> LatentSlot:
        if isinstance(response, dict):
            response = LatentResponse.from_message(response)
        return self.buffer.absorb(
            response.latent,
            step_id=response.step_id,
            request_id=response.request_id,
            metadata={**response.metadata, "late_absorbed": late},
        )

    def wait_for_late_responses(self, timeout_s: float | None = None) -> None:
        deadline = None if timeout_s is None else time.time() + float(timeout_s)
        for thread in list(self._late_threads):
            remaining = None if deadline is None else max(0.0, deadline - time.time())
            thread.join(timeout=remaining)
