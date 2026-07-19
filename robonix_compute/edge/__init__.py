"""Edge-side RoboNix-Compute-Optimization-Skill runtime."""

from robonix_compute.edge.runtime import EdgeRuntime, EdgeRuntimeConfig
from robonix_compute.edge.switcher import KeyLatentSwitcher, KeyLatentSwitcherConfig

__all__ = [
    "EdgeRuntime",
    "EdgeRuntimeConfig",
    "KeyLatentSwitcher",
    "KeyLatentSwitcherConfig",
]
