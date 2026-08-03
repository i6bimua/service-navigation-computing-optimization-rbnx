from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable

import numpy as np

from robonix_compute.common.context_buffer import ContextBuffer, LatentSlot
from robonix_compute.common.protocol import LatentRequest, LatentResponse, new_request_id
from robonix_compute.common.telemetry import StepTelemetry, TelemetryRecorder
from robonix_compute.common.timeout import AdaptiveTimeoutController
from robonix_compute.edge.runners import S1Runner
from robonix_compute.edge.switcher import KeyLatentSwitcher, KeyLatentSwitcherConfig

LatentRequestSender = Callable[[LatentRequest], Future]


def _payload_latent(payload: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get("traj_latent")
    return getattr(payload, "traj_latent", payload)


def _payload_cloud_action(payload: Any) -> list[int] | None:
    metadata = payload.get("metadata") if isinstance(payload, dict) else getattr(payload, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    action = metadata.get("cloud_action")
    if not action:
        return None
    return [int(item) for item in action]


@dataclass
class EdgeRuntimeConfig:
    switcher: KeyLatentSwitcherConfig = field(default_factory=KeyLatentSwitcherConfig)
    initial_timeout_s: float = 0.4
    min_timeout_s: float = 0.05
    max_timeout_s: float = 10.0
    timeout_window_size: int = 32
    require_initial_latent: bool = True
    # Steps the edge handles alone are invisible to S2, which keeps its own frame
    # history and its own step counter. Left unfed, S2 plans every request as if
    # the episode had just started and never concludes it has arrived. The edge
    # therefore keeps the frames it acted on locally and hands them to the cloud
    # with the next request, where they advance the history without triggering
    # inference. Bounded so a long run cannot grow the buffer without limit.
    replay_skipped_frames: bool = True
    max_replay_frames: int = 64


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
    """Online RoboNix Navigation Computing Optimization edge runtime.

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
        self._cloud_action: list[int] | None = None
        self._cloud_action_lock = threading.Lock()
        self._skipped_frames: list[Any] = []

    def _take_cloud_action(self) -> list[int] | None:
        with self._cloud_action_lock:
            if not self._cloud_action:
                self._cloud_action = None
                return None
            action = int(self._cloud_action.pop(0))
            if not self._cloud_action:
                self._cloud_action = None
        return [action]

    def _has_cloud_action(self) -> bool:
        with self._cloud_action_lock:
            return bool(self._cloud_action)

    def _clear_cloud_actions(self) -> None:
        with self._cloud_action_lock:
            self._cloud_action = None

    def _record_skipped_frame(self, observation: Any) -> None:
        """Keep a frame S1 acted on alone so the next sync can advance S2 history.

        InternVLA-N1's S2 owns its own `rgb_list` / `episode_idx`. Without this
        replay it only ever sees the frames that triggered a cloud request, so a
        100-step edge run looks like a 17-step episode to S2 and it never emits
        STOP even when the body is already at the goal.
        """
        if not self.config.replay_skipped_frames or not isinstance(observation, dict):
            return
        rgb = observation.get("rgb")
        if rgb is None:
            return
        frame: dict[str, Any] = {"rgb": np.asarray(rgb)}
        depth = observation.get("depth")
        if depth is not None:
            frame["depth"] = np.asarray(depth)
        pose = observation.get("pose")
        if pose is not None:
            frame["pose"] = np.asarray(pose)
        self._skipped_frames.append(frame)
        overflow = len(self._skipped_frames) - int(self.config.max_replay_frames)
        if overflow > 0:
            del self._skipped_frames[:overflow]

    def _drain_skipped_frames(self) -> list[dict[str, Any]]:
        frames, self._skipped_frames = self._skipped_frames, []
        return frames

    def reset(self, *, initial_latent: Any | None = None, step_id: int = 0) -> None:
        self.buffer.reset()
        self.signal_lock.reset()
        self.switcher.reset()
        self.s1_runner.reset()
        self._clear_cloud_actions()
        self._skipped_frames.clear()
        if initial_latent is not None:
            self.buffer.seed(initial_latent, step_id=step_id, request_id="initial")

    def step(
        self,
        observation: Any,
        *,
        step_id: int,
        instruction: str | None = None,
        force_sync: bool = False,
    ) -> Any:
        """Advance one control step.

        `force_sync` requests fresh cloud context regardless of what the switcher
        decides. The caller uses it after a step yielded no action, which means
        the active context is spent — the same condition upstream calls
        `no_output_flag` and answers with an unconditional S2 call.
        """
        started_at = time.perf_counter()
        feature = self.s1_runner.extract_visual_feature(observation)
        similarity = self.switcher.observe(feature)
        misalignment = self.buffer.misalignment(current_step_id=step_id)
        if misalignment is None:
            misalignment = 0
        # A discrete S2 answer is a short action program. InternNav drains that
        # program one primitive per observed frame before asking S2 again; doing
        # another synchronization midway can replace it and alter the route.
        trigger = force_sync or (
            not self._has_cloud_action()
            and self.switcher.should_trigger(similarity, lock_free=self.signal_lock.is_free())
        )
        telemetry = StepTelemetry(
            step_id=int(step_id),
            visual_similarity=similarity,
            tau_sim=self.switcher.tau_sim,
            triggered_sync=trigger,
            latent_misalignment_k=misalignment,
            timeout_s=self.timeout.current_timeout_s(),
        )

        if trigger:
            telemetry.metadata["sync_reason"] = "forced" if force_sync else "key_latent"
            self._synchronize(
                observation=observation,
                step_id=step_id,
                instruction=instruction,
                telemetry=telemetry,
            )
            active_slot = self.buffer.active()
        else:
            active_slot = self.buffer.active()
            self._record_skipped_frame(observation)
            self.switcher.update_step(misalignment_k=misalignment, triggered=False)

        if active_slot is None and not self._has_cloud_action():
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
            if active_slot is None and not self._has_cloud_action():
                raise RuntimeError("Cloud did not provide an initial latent.")

        cloud_action = self._take_cloud_action()
        if cloud_action is not None:
            # S2 asked for a specific motion rather than handing down new
            # context, so it owns this step and S1 sits it out. The active latent
            # is left alone: the next step reuses it, or triggers a fresh sync.
            telemetry.metadata["cloud_action"] = True
            telemetry.action = cloud_action
            telemetry.tau_sim = self.switcher.tau_sim
            telemetry.latency_s = time.perf_counter() - started_at
            self.telemetry.record(telemetry)
            return cloud_action

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

        sync_observation: Any = observation
        skipped = self._drain_skipped_frames()
        if skipped and isinstance(observation, dict):
            sync_observation = dict(observation)
            sync_observation["skipped_frames"] = skipped
            telemetry.metadata["replayed_frames"] = len(skipped)
        request = LatentRequest(
            request_id=request_id,
            step_id=int(step_id),
            observation=sync_observation,
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
        telemetry.fresh_latent = slot is not None and slot.request_id == request_id
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

    def _absorb_response(self, response: LatentResponse | dict[str, Any], *, late: bool) -> LatentSlot | None:
        if isinstance(response, dict):
            response = LatentResponse.from_message(response)
        if _payload_latent(response.latent) is None:
            # An action-only answer carries no context to absorb. Overwriting the
            # active slot with an empty latent would strand S1 on the next step,
            # so keep the buffer as it is and hand the action to `step`.
            action = _payload_cloud_action(response.latent)
            if action is not None:
                with self._cloud_action_lock:
                    self._cloud_action = action
            return None
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
