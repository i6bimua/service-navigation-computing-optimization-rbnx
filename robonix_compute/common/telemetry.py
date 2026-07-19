from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import time
from pathlib import Path
from typing import Any


@dataclass
class StepTelemetry:
    step_id: int
    action: Any = None
    visual_similarity: float | None = None
    tau_sim: float | None = None
    triggered_sync: bool = False
    request_id: str | None = None
    fresh_latent: bool = False
    timeout: bool = False
    late_absorbed: bool = False
    latent_reuse: bool = False
    latent_misalignment_k: int | None = None
    rtt_s: float | None = None
    timeout_s: float | None = None
    latency_s: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TelemetryRecorder:
    def __init__(self) -> None:
        self.steps: list[StepTelemetry] = []

    def record(self, item: StepTelemetry) -> None:
        self.steps.append(item)

    def summary(self) -> dict[str, Any]:
        latencies = [item.latency_s for item in self.steps if item.latency_s is not None]
        return {
            "step_count": len(self.steps),
            "sync_count": sum(1 for item in self.steps if item.triggered_sync),
            "timeout_count": sum(1 for item in self.steps if item.timeout),
            "late_absorbed_count": sum(1 for item in self.steps if item.late_absorbed),
            "latent_reuse_count": sum(1 for item in self.steps if item.latent_reuse),
            "mean_step_latency_s": None if not latencies else sum(latencies) / len(latencies),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": time.time(),
            "summary": self.summary(),
            "step_logs": [asdict(item) for item in self.steps],
        }

    def write_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
