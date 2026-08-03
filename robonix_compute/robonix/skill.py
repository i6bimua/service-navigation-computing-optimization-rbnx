from __future__ import annotations

from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from robonix_compute.cloud.runtime import CloudRuntime
from robonix_compute.cloud.runners import CallableS2Runner
from robonix_compute.common.link import InProcessLatentLink
from robonix_compute.edge.client import EdgeClient
from robonix_compute.edge.runtime import EdgeRuntime, EdgeRuntimeConfig
from robonix_compute.edge.runners import CallableS1Runner
from robonix_compute.edge.switcher import KeyLatentSwitcherConfig
from robonix_compute.model_adapters import InternNavS1Adapter, InternVLALatentPayload


def _mock_s2(observation: Any, instruction: str | None = None) -> dict[str, Any]:
    del instruction
    rgb = observation.get("rgb") if isinstance(observation, dict) else observation
    latent = np.asarray(rgb, dtype=np.float32).reshape(-1)
    return InternVLALatentPayload(traj_latent=latent, memory_rgb=rgb, metadata={"runner": "robonix_mock_s2"}).to_dict()


def _mock_s1(_observation: Any, latent: Any) -> int:
    if isinstance(latent, dict):
        latent = latent.get("traj_latent", latent)
    return int(np.argmax(np.asarray(latent, dtype=np.float32).reshape(-1)) == 1)


class RoboNixComputeSkill:
    """RoboNix-facing navigation computing optimization skill wrapper.

    The wrapper owns the edge runtime. A caller uses `setup`, `reset`, and
    `step`; the runtime decides whether each step needs a fresh cloud latent.
    """

    def __init__(self, edge_runtime: EdgeRuntime | None = None):
        self.edge_runtime = edge_runtime
        self._transport: Any | None = None
        self._config: dict[str, Any] = {}
        self._external_runtime = edge_runtime is not None
        self._mode = "custom" if edge_runtime is not None else "mock"
        self._closed = edge_runtime is None
        self._step_id = 0
        self._instruction: str | None = None

    def setup(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = dict(config or {})
        if not self._external_runtime:
            self.close()
            self.edge_runtime, self._transport = self._build_edge_runtime(config)
            self._config = dict(config)
            self._mode = str(config.get("mode", "mock"))
            self._closed = False
        return {
            "status": "ready",
            "mode": self._mode,
            "role": "compute_optimization_skill",
            "skill": self.skill_info(),
        }

    def skill_info(self) -> dict[str, Any]:
        return {
            "name": "compute_optimization_adapter",
            "type": "service_backed_navigation",
            "runtime": "http_skill_wrapper",
            "edge_device": "Orin/Thor",
            "cloud_dependency": "robonix-compute-cloud",
            "inputs": ["rgb", "depth", "pose_or_gps", "intrinsic", "instruction"],
            "outputs": ["action", "telemetry"],
            "features": [
                "edge-side fast action generation",
                "cloud-side semantic latent generation",
                "adaptive cloud-edge synchronization",
                "timeout-safe latent reuse",
                "edge utilization and latency telemetry",
            ],
        }

    def reset(self, task: str | None = None, instruction: str | None = None) -> dict[str, Any]:
        if self.edge_runtime is None:
            self.setup({})
        if self._closed:
            raise RuntimeError("RoboNixComputeSkill is closed; call setup before reset.")
        assert self.edge_runtime is not None
        self._step_id = 0
        self._instruction = instruction or task
        self.edge_runtime.reset()
        if self._transport is not None and hasattr(self._transport, "reset_episode"):
            self._transport.reset_episode(instruction=self._instruction)
        return {"status": "reset", "instruction": self._instruction}

    def step(self, observation: Any, *, force_sync: bool = False) -> dict[str, Any]:
        if self.edge_runtime is None:
            self.setup({})
        if self._closed:
            raise RuntimeError("RoboNixComputeSkill is closed; call setup before step.")
        assert self.edge_runtime is not None
        action = self.edge_runtime.step(
            observation,
            step_id=self._step_id,
            instruction=self._instruction,
            force_sync=force_sync,
        )
        telemetry = asdict(self.edge_runtime.telemetry.steps[-1])
        result = {
            "step_id": self._step_id,
            "action": action,
            "telemetry": telemetry,
            "summary": self.edge_runtime.telemetry.summary(),
        }
        self._step_id += 1
        return result

    def close(self) -> dict[str, Any]:
        if self._transport is not None and hasattr(self._transport, "close"):
            if hasattr(self._transport, "close_cloud_connection"):
                with suppress(Exception):
                    self._transport.close_cloud_connection()
            self._transport.close()
        self._transport = None
        if not self._external_runtime:
            self._mode = "closed"
            self._closed = True
        return {"status": "closed"}

    def telemetry(self) -> dict[str, Any]:
        if self.edge_runtime is None:
            return {"status": "not_ready", "summary": {}, "step_logs": []}
        return {"status": "ok", **self.edge_runtime.telemetry.to_dict()}

    def _build_edge_runtime(self, config: dict[str, Any]) -> tuple[EdgeRuntime, Any]:
        mode = str(config.get("mode", "mock"))
        runtime_config = EdgeRuntimeConfig(
            require_initial_latent=bool(config.get("require_initial_latent", False)),
            initial_timeout_s=float(config.get("initial_timeout_s", 0.4)),
            min_timeout_s=float(config.get("min_timeout_s", 0.05)),
            max_timeout_s=float(config.get("max_timeout_s", 10.0)),
            timeout_window_size=int(config.get("timeout_window_size", 32)),
            switcher=KeyLatentSwitcherConfig(
                tau_lower=float(config.get("tau_lower", 0.50)),
                tau_upper=float(config.get("tau_upper", 0.80)),
                tau_initial=float(config.get("tau_initial", 0.80)),
                delta=float(config.get("delta", 0.015)),
                k_max=int(config.get("k_max", 8)),
                eta=float(config.get("eta", 0.05)),
            ),
        )
        if mode == "mock":
            cloud = CloudRuntime(CallableS2Runner(_mock_s2))
            link = InProcessLatentLink(cloud, delay_s=float(config.get("mock_cloud_delay_s", 0.0)))
            return (
                EdgeRuntime(
                    s1_runner=CallableS1Runner(act_fn=_mock_s1),
                    latent_sender=link.send,
                    config=runtime_config,
                ),
                link,
            )

        cloud_host = str(config.get("cloud_host", "127.0.0.1"))
        cloud_port = int(config.get("cloud_port", 8765))
        client = EdgeClient(host=cloud_host, port=cloud_port)
        if mode == "websocket":
            s1_runner = CallableS1Runner(act_fn=_mock_s1)
        elif mode == "internnav":
            try:
                from internnav.edgecloud.runners import InternVLAN1S1Runner
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("InternNav is required for compute backend mode `internnav`.") from exc
            model_settings = {
                "policy_name": "InternVLAN1_Policy",
                "model_path": str(Path(config.get("model_dir", "checkpoints/InternVLA-N1")).expanduser()),
                "s1_model_path": str(Path(config.get("s1_model_dir", "checkpoints/InternVLA-N1-S1")).expanduser()),
                "device": str(config.get("device", "cuda:0")),
                "mode": "dual_system",
                "state_encoder": None,
                # InternVLA-N1's trajectory head emits continuous waypoints that
                # the runner discretises. `ModelCfg` allows extra fields but
                # supplies no defaults, so leaving this out makes the S1 runner
                # raise AttributeError on its first action conversion.
                "continuous_traj": True,
            }
            model_settings.update(dict(config.get("model_settings", {})))
            s1_runner = InternNavS1Adapter(InternVLAN1S1Runner(model_settings=model_settings))
        else:
            raise ValueError(f"unsupported compute backend mode: {mode}")

        return EdgeRuntime(s1_runner=s1_runner, latent_sender=client.send_latent_request, config=runtime_config), client
