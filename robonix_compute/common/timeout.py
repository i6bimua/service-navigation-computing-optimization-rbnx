from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class AdaptiveTimeoutController:
    alpha: float = 0.125
    beta: float = 0.25
    kappa: float = 4.0
    initial_rtt_s: float = 0.4
    min_timeout_s: float = 0.05
    max_timeout_s: float = 10.0
    window_size: int = 32
    mean_rtt_s: float = field(init=False)
    deviation_s: float = field(init=False)
    outcomes: deque[bool] = field(init=False)

    def __post_init__(self) -> None:
        self.mean_rtt_s = float(self.initial_rtt_s)
        self.deviation_s = max(float(self.initial_rtt_s) / 2.0, 1e-6)
        self.outcomes = deque(maxlen=max(1, int(self.window_size)))

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.outcomes if item)

    @property
    def timeout_count(self) -> int:
        return sum(1 for item in self.outcomes if not item)

    @property
    def timeout_ratio(self) -> float:
        total = len(self.outcomes)
        if total == 0:
            return 0.0
        return float(self.timeout_count) / float(total)

    def current_timeout_s(self) -> float:
        multiplier = 1.0 + self.timeout_ratio
        timeout = self.mean_rtt_s + self.kappa * self.deviation_s * multiplier
        return min(max(timeout, self.min_timeout_s), self.max_timeout_s)

    def record_success(self, rtt_s: float) -> float:
        rtt_s = max(float(rtt_s), 0.0)
        previous_mean = self.mean_rtt_s
        self.mean_rtt_s = (1.0 - self.alpha) * self.mean_rtt_s + self.alpha * rtt_s
        self.deviation_s = (1.0 - self.beta) * self.deviation_s + self.beta * abs(rtt_s - previous_mean)
        self.outcomes.append(True)
        return self.current_timeout_s()

    def record_timeout(self) -> float:
        self.outcomes.append(False)
        return self.current_timeout_s()

    def state_dict(self) -> dict[str, float | int]:
        return {
            "mean_rtt_s": self.mean_rtt_s,
            "deviation_s": self.deviation_s,
            "timeout_s": self.current_timeout_s(),
            "ok_count": self.ok_count,
            "timeout_count": self.timeout_count,
            "timeout_ratio": self.timeout_ratio,
        }
