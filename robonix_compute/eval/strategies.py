from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StrategyName(str, Enum):
    ROBONIX_COMPUTE_ONLINE = "robonix_compute_online"
    NAIVE_ASYNC = "naive_async"
    STEP_SYNC = "step_sync"
    OFFLINE_MANIFEST = "offline_manifest"


@dataclass(frozen=True)
class StrategyConfig:
    name: StrategyName
    description: str


STRATEGIES = {
    StrategyName.ROBONIX_COMPUTE_ONLINE: StrategyConfig(
        name=StrategyName.ROBONIX_COMPUTE_ONLINE,
        description="Online edge-owned switching and adaptive missing handling.",
    ),
    StrategyName.NAIVE_ASYNC: StrategyConfig(
        name=StrategyName.NAIVE_ASYNC,
        description="Reuse cached latent without key-latent switching.",
    ),
    StrategyName.STEP_SYNC: StrategyConfig(
        name=StrategyName.STEP_SYNC,
        description="Synchronize every control step.",
    ),
    StrategyName.OFFLINE_MANIFEST: StrategyConfig(
        name=StrategyName.OFFLINE_MANIFEST,
        description="Legacy reproduction path using precomputed selective-sync manifests.",
    ),
}
