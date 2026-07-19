from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


def cosine_similarity(left: Any, right: Any) -> float:
    left_array = np.asarray(left, dtype=np.float32).reshape(-1)
    right_array = np.asarray(right, dtype=np.float32).reshape(-1)
    denominator = float(np.linalg.norm(left_array) * np.linalg.norm(right_array))
    if denominator <= 1e-12:
        return 1.0
    return float(np.dot(left_array, right_array) / denominator)


@dataclass
class KeyLatentSwitcherConfig:
    tau_lower: float = 0.50
    tau_upper: float = 0.80
    tau_initial: float = 0.80
    delta: float = 0.015
    k_max: int = 8
    eta: float = 0.05


class KeyLatentSwitcher:
    """Edge-local visual-similarity switcher with configurable dynamics."""

    def __init__(self, config: KeyLatentSwitcherConfig | None = None) -> None:
        self.config = config or KeyLatentSwitcherConfig()
        self.tau_sim = float(self.config.tau_initial)
        self.previous_feature: np.ndarray | None = None

    def reset(self) -> None:
        self.tau_sim = float(self.config.tau_initial)
        self.previous_feature = None

    def observe(self, feature: Any) -> float:
        feature_array = np.asarray(feature, dtype=np.float32).reshape(-1)
        if self.previous_feature is None:
            similarity = 1.0
        else:
            similarity = cosine_similarity(feature_array, self.previous_feature)
        self.previous_feature = feature_array
        return similarity

    def should_trigger(self, similarity: float, *, lock_free: bool) -> bool:
        return bool(lock_free and float(similarity) < self.tau_sim)

    def update_step(self, *, misalignment_k: int, triggered: bool) -> float:
        cfg = self.config
        k_ratio = min(max(int(misalignment_k), 0), max(int(cfg.k_max), 1)) / max(float(cfg.k_max), 1.0)
        next_tau = self.tau_sim + cfg.delta * (1.0 + k_ratio)
        if triggered:
            span = max(cfg.tau_upper - cfg.tau_lower, 1e-6)
            next_tau -= (self.tau_sim - cfg.tau_lower) / span
        self.tau_sim = self._clip(next_tau)
        return self.tau_sim

    def apply_network_feedback(
        self,
        *,
        success: bool,
        rtt_s: float | None,
        timeout_s: float,
        timeout_count: int,
        ok_count: int,
        misalignment_k: int,
    ) -> float:
        cfg = self.config
        total = max(int(timeout_count) + int(ok_count), 1)
        if success:
            rtt = 0.0 if rtt_s is None else max(float(rtt_s), 0.0)
            margin = max(0.0, 1.0 - rtt / max(float(timeout_s), 1e-6))
            span = max(cfg.tau_upper - cfg.tau_lower, 1e-6)
            correction = -cfg.eta * margin * ((self.tau_sim - cfg.tau_lower) / span)
        else:
            timeout_ratio = float(timeout_count) / float(total)
            k_ratio = min(max(int(misalignment_k), 0), max(int(cfg.k_max), 1)) / max(float(cfg.k_max), 1.0)
            correction = cfg.eta * (1.0 + timeout_ratio) * (1.0 + k_ratio)
        self.tau_sim = self._clip(self.tau_sim + correction)
        return self.tau_sim

    def _clip(self, value: float) -> float:
        return float(min(max(value, self.config.tau_lower), self.config.tau_upper))
