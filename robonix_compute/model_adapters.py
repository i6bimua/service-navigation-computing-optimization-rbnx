from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

import numpy as np


@dataclass
class InternVLALatentPayload:
    """Serializable latent payload exchanged between RoboNix Compute Optimization cloud and edge.

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
    """RoboNix Compute Optimization adapter for InternNav/InternVLA System-1 runners.

    The wrapped runner may be:
    - `internnav.edgecloud.runners.InternVLAN1S1Runner`
    - an S1-only runner exported from InternVLA-N1
    - a mock runner implementing the same `step` contract
    """

    def __init__(self, runner: Any):
        self.runner = runner

    def reset(self) -> None:
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
        images_dp, depths_dp = self._build_s1_inputs(observation, payload)
        output = self.runner.step(
            images_dp=images_dp,
            depths_dp=depths_dp,
            latent=payload.traj_latent,
            initial_latents=payload.initial_latents,
        )
        if hasattr(output, "idx"):
            return [int(item) for item in output.idx]
        if isinstance(output, dict) and "idx" in output:
            return [int(item) for item in output["idx"]]
        return output

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
            memory_rgb=_as_numpy(payload.memory_rgb, dtype=np.float32),
            current_rgb=_as_numpy(current_rgb, dtype=np.float32),
            memory_depth=_as_numpy(payload.memory_depth, dtype=np.float32),
            current_depth=_as_numpy(current_depth, dtype=np.float32),
        )


class InternNavS2Adapter:
    """RoboNix Compute Optimization adapter for InternNav/InternVLA System-2 latent runners."""

    def __init__(self, runner: Any):
        self.runner = runner

    def reset(self) -> None:
        if hasattr(self.runner, "reset"):
            self.runner.reset()

    def generate_latent(self, observation: Any, instruction: str | None = None) -> dict[str, Any]:
        started_at = time.perf_counter()
        if hasattr(self.runner, "generate_latent"):
            raw_output = self.runner.generate_latent(observation, instruction)
        elif isinstance(observation, dict) and hasattr(self.runner, "step"):
            raw_output = self.runner.step(
                observation.get("rgb"),
                observation.get("depth"),
                observation.get("pose", observation.get("gps")),
                instruction or observation.get("instruction"),
                observation.get("intrinsic"),
                look_down=bool(observation.get("look_down", False)),
            )
        else:
            raw_output = self.runner.step(observation, instruction)

        payload = self._normalize_s2_output(raw_output, observation)
        payload.metadata.setdefault("s2_adapter_latency_s", time.perf_counter() - started_at)
        payload.metadata.setdefault("s2_adapter", type(self.runner).__name__)
        return payload.to_dict()

    def _normalize_s2_output(self, output: Any, observation: Any) -> InternVLALatentPayload:
        if isinstance(output, InternVLALatentPayload):
            return output
        if isinstance(output, dict) and "traj_latent" in output:
            return InternVLALatentPayload.from_any(output)

        output_latent = getattr(output, "output_latent", None)
        if output_latent is None:
            output_latent = getattr(output, "latent", None)
        if output_latent is None:
            output_latent = output

        memory_rgb = getattr(output, "rgb_memory", None)
        memory_depth = getattr(output, "depth_memory", None)
        if memory_rgb is None:
            memory_rgb = _get_observation_value(observation, "rgb")
        if memory_depth is None:
            memory_depth = _get_observation_value(observation, "depth")

        return InternVLALatentPayload(
            traj_latent=output_latent,
            pixel_goal=getattr(output, "output_pixel", None),
            memory_rgb=memory_rgb,
            memory_depth=memory_depth,
            metadata={"has_cloud_action": getattr(output, "output_action", None) is not None},
        )
