from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

import numpy as np


@dataclass
class InternVLALatentPayload:
    """Serializable latent payload exchanged between RoboNix Navigation Computing Optimization cloud and edge.

    `traj_latent` is the semantic latent produced by S2. `memory_rgb` and
    `memory_depth` are the observation frame attached to that latent; the edge
    combines them with its current observation before S1 inference.
    """

    traj_latent: Any
    pixel_goal: Any | None = None
    memory_rgb: Any | None = None
    memory_depth: Any | None = None
    initial_latents: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "traj_latent": self.traj_latent,
            "pixel_goal": self.pixel_goal,
            "memory_rgb": self.memory_rgb,
            "memory_depth": self.memory_depth,
            "initial_latents": self.initial_latents,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_any(cls, value: Any) -> "InternVLALatentPayload":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict) and "traj_latent" in value:
            return cls(
                traj_latent=value["traj_latent"],
                pixel_goal=value.get("pixel_goal"),
                memory_rgb=value.get("memory_rgb"),
                memory_depth=value.get("memory_depth"),
                initial_latents=value.get("initial_latents"),
                metadata=dict(value.get("metadata", {})),
            )
        return cls(traj_latent=value)


def _as_numpy(value: Any, *, dtype: np.dtype | str | None = np.float32) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().to("cpu").numpy()
    array = np.asarray(value)
    if dtype is not None:
        array = array.astype(dtype, copy=False)
    return array


def _as_uint8_rgb(value: Any) -> np.ndarray:
    """Coerce an RGB frame to the 0-255 bytes `Image.fromarray` accepts.

    The S1 input builder rasterises frames through PIL and normalises by 255
    itself, so handing it floats raises `TypeError: Cannot handle this data type`.
    """
    array = _as_numpy(value, dtype=None)
    if array.dtype == np.uint8:
        return array
    array = array.astype(np.float32, copy=False)
    peak = float(array.max()) if array.size else 0.0
    if peak <= 1.0:  # already normalised by the publisher
        array = array * 255.0
    return np.clip(array, 0.0, 255.0).astype(np.uint8)


def _get_observation_value(observation: Any, *names: str, default: Any = None) -> Any:
    if not isinstance(observation, dict):
        return default
    for name in names:
        if name in observation:
            return observation[name]
    return default


def _import_s1_input_builder():
    try:
        from cloud.s1_inputs import build_s1_model_inputs_from_pair
    except ImportError as exc:  # pragma: no cover - exercised only with InternNav installed
        raise RuntimeError(
            "InternNav cloud.s1_inputs is required to build S1 inputs from RGB-D frames. "
            "Pass precomputed `images_dp` and `depths_dp` in the observation, or install InternNav."
        ) from exc
    return build_s1_model_inputs_from_pair


class InternNavS1Adapter:
    """RoboNix Navigation Computing Optimization adapter for InternNav/InternVLA System-1 runners.

    The wrapped runner may be:
    - `internnav.edgecloud.runners.InternVLAN1S1Runner`
    - an S1-only runner exported from InternVLA-N1
    - a mock runner implementing the same `step` contract
    """

    def __init__(self, runner: Any):
        self.runner = runner
        self._pending_actions: list[int] = []
        self._pending_latent_id: int | None = None
        settings = getattr(runner, "model_settings", None)
        self._max_action_chunk = max(1, int(getattr(settings, "num_future_steps", 4)))

    def reset(self) -> None:
        self._pending_actions.clear()
        self._pending_latent_id = None
        if hasattr(self.runner, "reset"):
            self.runner.reset()

    def extract_visual_feature(self, observation: Any) -> Any:
        if hasattr(self.runner, "extract_visual_feature"):
            return self.runner.extract_visual_feature(observation)
        feature = _get_observation_value(observation, "visual_feature", "s1_visual_feature")
        if feature is not None:
            return feature
        image = _get_observation_value(observation, "rgb", "image", default=observation)
        return _as_numpy(image, dtype=np.float32)

    def act(self, observation: Any, latent: Any) -> Any:
        if hasattr(self.runner, "act"):
            return self.runner.act(observation, latent)
        payload = InternVLALatentPayload.from_any(latent)
        latent_id = id(payload.traj_latent)

        # InternNav executes exactly one primitive action per environment frame.
        # S1 may predict a short trajectory, but the upstream agent keeps its
        # tail and emits one item on each later `step()` call (see
        # internvla_n1_agent.py:314-320). Returning the whole trajectory here
        # makes the chassis move several times without a new observation; merely
        # truncating it in the controller is also wrong because that discards the
        # tail. Preserve it here, and invalidate it when a fresh cloud latent
        # replaces the one it was planned from.
        if self._pending_actions and self._pending_latent_id == latent_id:
            return [self._pending_actions.pop(0)]
        self._pending_actions.clear()
        self._pending_latent_id = None

        images_dp, depths_dp = self._build_s1_inputs(observation, payload)
        output = self.runner.step(
            images_dp=images_dp,
            depths_dp=depths_dp,
            latent=payload.traj_latent,
            initial_latents=payload.initial_latents,
        )
        if hasattr(output, "idx"):
            actions = [int(item) for item in output.idx]
        elif isinstance(output, dict) and "idx" in output:
            actions = [int(item) for item in output["idx"]]
        else:
            return output
        # The full S1-only runner exposes every discretized waypoint, whereas
        # the policy used by InternNav's evaluator deliberately returns only
        # `action_list[:num_future_steps]` (four for N1). Keeping more changes
        # the closed-loop policy into a long open-loop rollout and overshoots
        # turns even when we drain the list one frame at a time.
        actions = actions[: self._max_action_chunk]
        if not actions:
            return []
        self._pending_actions = actions[1:]
        self._pending_latent_id = latent_id if self._pending_actions else None
        return actions[:1]

    def _build_s1_inputs(self, observation: Any, payload: InternVLALatentPayload) -> tuple[Any, Any]:
        images_dp = _get_observation_value(observation, "images_dp")
        depths_dp = _get_observation_value(observation, "depths_dp")
        if images_dp is not None and depths_dp is not None:
            return images_dp, depths_dp

        current_rgb = _get_observation_value(observation, "rgb", "image")
        current_depth = _get_observation_value(observation, "depth")
        if current_rgb is None or current_depth is None or payload.memory_rgb is None or payload.memory_depth is None:
            raise ValueError(
                "S1 input packaging requires either observation.images_dp/depths_dp "
                "or current rgb/depth plus latent payload memory_rgb/memory_depth."
            )

        builder = _import_s1_input_builder()
        return builder(
            memory_rgb=_as_uint8_rgb(payload.memory_rgb),
            current_rgb=_as_uint8_rgb(current_rgb),
            memory_depth=_as_numpy(payload.memory_depth, dtype=np.float32),
            current_depth=_as_numpy(current_depth, dtype=np.float32),
        )


class InternNavS2Adapter:
    """RoboNix Navigation Computing Optimization adapter for InternNav/InternVLA System-2 latent runners."""

    # InternVLA-N1 borrows Habitat's discrete indices, where 5 is LOOK_DOWN. S2
    # emits it to ask for a lower camera view before committing to a route.
    LOOK_DOWN_ACTION = 5

    def __init__(self, runner: Any):
        self.runner = runner

    def reset(self) -> None:
        if hasattr(self.runner, "reset"):
            self.runner.reset()

    def generate_latent(self, observation: Any, instruction: str | None = None) -> dict[str, Any]:
        started_at = time.perf_counter()
        looked_down = bool(_get_observation_value(observation, "look_down", default=False))
        replayed = 0
        if isinstance(observation, dict):
            frames = observation.pop("skipped_frames", None) or []
            for frame in frames:
                self._replay_history_frame(frame)
            replayed = len(frames)

        raw_output = self._invoke_runner(observation, instruction, look_down=looked_down)

        if not looked_down and self._is_look_down_request(raw_output):
            # A chassis-only body cannot tilt its camera, so the cloud answers
            # the request itself: one more S2 pass over the same frame with the
            # look-down prompt, exactly as the upstream real-robot server does.
            # The edge never sees an action it has no actuator for.
            raw_output = self._invoke_runner(observation, instruction, look_down=True)
            looked_down = True

        payload = self._normalize_s2_output(raw_output, observation)
        payload.metadata.setdefault("s2_adapter_latency_s", time.perf_counter() - started_at)
        payload.metadata.setdefault("s2_adapter", type(self.runner).__name__)
        if looked_down:
            payload.metadata.setdefault("s2_look_down", True)
        if replayed:
            payload.metadata.setdefault("s2_replayed_frames", replayed)
        return payload.to_dict()

    def _replay_history_frame(self, frame: Any) -> None:
        """Advance S2's frame history without running inference (edge-local steps)."""
        if not hasattr(self.runner, "step_no_infer") or not isinstance(frame, dict):
            return
        rgb = frame.get("rgb")
        if rgb is None:
            return
        depth = frame.get("depth")
        pose = frame.get("pose")
        if depth is None:
            depth = np.zeros((1, 1), dtype=np.float32)
        if pose is None:
            pose = np.eye(4, dtype=np.float32)
        self.runner.step_no_infer(np.asarray(rgb), np.asarray(depth), np.asarray(pose))

    def _invoke_runner(self, observation: Any, instruction: str | None, *, look_down: bool) -> Any:
        if hasattr(self.runner, "generate_latent"):
            return self.runner.generate_latent(observation, instruction)
        if isinstance(observation, dict) and hasattr(self.runner, "step"):
            return self.runner.step(
                observation.get("rgb"),
                observation.get("depth"),
                observation.get("pose", observation.get("gps")),
                instruction or observation.get("instruction"),
                observation.get("intrinsic"),
                look_down=look_down,
            )
        return self.runner.step(observation, instruction)

    def _is_look_down_request(self, output: Any) -> bool:
        if getattr(output, "output_latent", None) is not None:
            return False
        action = getattr(output, "output_action", None)
        if action is None:
            return False
        return [int(item) for item in action] == [self.LOOK_DOWN_ACTION]

    def _normalize_s2_output(self, output: Any, observation: Any) -> InternVLALatentPayload:
        if isinstance(output, InternVLALatentPayload):
            return output
        if isinstance(output, dict) and "traj_latent" in output:
            return InternVLALatentPayload.from_any(output)

        output_latent = getattr(output, "output_latent", None)
        if output_latent is None:
            output_latent = getattr(output, "latent", None)

        cloud_action = getattr(output, "output_action", None)
        # A runner result always carries these attributes, even when the field is
        # None. Anything else is a bare latent (a tensor from a `generate_latent`
        # runner, say) and stands in for the payload itself.
        is_runner_result = hasattr(output, "output_latent") or hasattr(output, "output_action")
        if output_latent is None and not is_runner_result:
            output_latent = output

        memory_rgb = getattr(output, "rgb_memory", None)
        memory_depth = getattr(output, "depth_memory", None)
        if memory_rgb is None:
            memory_rgb = _get_observation_value(observation, "rgb")
        if memory_depth is None:
            memory_depth = _get_observation_value(observation, "depth")

        metadata: dict[str, Any] = {}
        if cloud_action is not None:
            # S2 can answer with a discrete action instead of a new latent —
            # InternVLA-N1 does this to look down before committing to a route,
            # and to stop. The action is authoritative for that step, so it
            # travels as its own field rather than as a latent the edge would
            # try to condition S1 on.
            metadata["cloud_action"] = [int(item) for item in cloud_action]

        return InternVLALatentPayload(
            traj_latent=output_latent,
            pixel_goal=getattr(output, "output_pixel", None),
            memory_rgb=memory_rgb,
            memory_depth=memory_depth,
            metadata=metadata,
        )
