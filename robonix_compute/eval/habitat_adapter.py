from __future__ import annotations

from typing import Any

from robonix_compute.edge.runtime import EdgeRuntime


class HabitatEdgeRuntimeAdapter:
    """Thin adapter for Habitat loops where the simulator remains cloud-side.

    The caller passes each observation to this adapter. Synchronization remains
    owned by EdgeRuntime, while Habitat can keep controlling environment reset,
    metrics, and rendering.
    """

    def __init__(self, runtime: EdgeRuntime):
        self.runtime = runtime

    def reset(self, *, initial_latent: Any | None = None, step_id: int = 0) -> None:
        self.runtime.reset(initial_latent=initial_latent, step_id=step_id)

    def act(self, observation: Any, *, step_id: int, instruction: str | None = None) -> Any:
        return self.runtime.step(observation, step_id=step_id, instruction=instruction)
