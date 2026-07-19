from __future__ import annotations

import argparse
from concurrent.futures import Future
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

from robonix_compute.cli.common import default_model_settings, env_int, env_path, load_json_mapping, print_json
from robonix_compute.common.protocol import LatentRequest
from robonix_compute.edge.client import EdgeClient
from robonix_compute.edge.runtime import EdgeRuntime, EdgeRuntimeConfig
from robonix_compute.edge.runners import CallableS1Runner
from robonix_compute.edge.switcher import KeyLatentSwitcherConfig
from robonix_compute.model_adapters import InternNavS1Adapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the RoboNix-Compute-Optimization-Skill edge-side online runtime.")
    parser.add_argument("--mode", choices=["mock", "internnav"], default="mock")
    parser.add_argument("--cloud-host", default="127.0.0.1")
    parser.add_argument("--cloud-port", type=int, default=env_int("ROBONIX_COMPUTE_CLOUD_PORT", 8765))
    parser.add_argument("--model-dir", type=Path, default=env_path("ROBONIX_COMPUTE_MODEL_DIR", "checkpoints/InternVLA-N1"))
    parser.add_argument("--s1-model-dir", type=Path, default=env_path("ROBONIX_COMPUTE_S1_MODEL_DIR", "checkpoints/InternVLA-N1-S1"))
    parser.add_argument("--model-settings-json")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--stdin-jsonl", action="store_true", help="Read one JSON observation per line from stdin.")
    parser.add_argument("--require-initial-latent", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _mock_observation(step_id: int) -> dict[str, Any]:
    if step_id < 2:
        return {"rgb": [1.0, 0.0], "depth": [0.0]}
    return {"rgb": [0.0, 1.0], "depth": [0.0]}


def _mock_s1_action(_observation: Any, latent: Any) -> int:
    if isinstance(latent, dict):
        latent = latent.get("traj_latent", latent)
    return int(np.argmax(np.asarray(latent, dtype=np.float32).reshape(-1)) == 1)


def _build_edge_runtime(args: argparse.Namespace, client: EdgeClient) -> EdgeRuntime:
    if args.mode == "mock":
        s1_runner = CallableS1Runner(act_fn=_mock_s1_action)
    else:
        try:
            from internnav.edgecloud.runners import InternVLAN1S1Runner
        except ImportError as exc:
            raise RuntimeError("InternNav is required for `robonix-compute-edge --mode internnav`.") from exc

        model_settings = default_model_settings(model_dir=args.model_dir.expanduser(), device=args.device)
        model_settings["s1_model_path"] = str(args.s1_model_dir.expanduser())
        model_settings.update(load_json_mapping(args.model_settings_json))
        s1_runner = InternNavS1Adapter(InternVLAN1S1Runner(model_settings=model_settings))

    config = EdgeRuntimeConfig(
        require_initial_latent=bool(args.require_initial_latent),
        switcher=KeyLatentSwitcherConfig(),
    )
    return EdgeRuntime(s1_runner=s1_runner, latent_sender=client.send_latent_request, config=config)


def _run_jsonl(edge: EdgeRuntime) -> None:
    edge.reset()
    for line_index, line in enumerate(sys.stdin):
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        step_id = int(payload.get("step_id", line_index))
        observation = payload.get("observation", payload)
        instruction = payload.get("instruction")
        action = edge.step(observation, step_id=step_id, instruction=instruction)
        print_json(
            {
                "step_id": step_id,
                "action": action,
                "telemetry": edge.telemetry.to_dict()["step_logs"][-1],
            }
        )


def _run_mock_steps(edge: EdgeRuntime, *, steps: int) -> None:
    edge.reset()
    actions = []
    for step_id in range(steps):
        actions.append(edge.step(_mock_observation(step_id), step_id=step_id, instruction="mock navigation"))
    print_json({"actions": actions, "summary": edge.telemetry.summary()})


def main() -> None:
    args = parse_args()
    if args.dry_run:
        print_json(
            {
                "role": "edge",
                "mode": args.mode,
                "cloud_host": args.cloud_host,
                "cloud_port": args.cloud_port,
                "model_dir": str(args.model_dir.expanduser()),
                "s1_model_dir": str(args.s1_model_dir.expanduser()),
            }
        )
        return

    client = EdgeClient(host=args.cloud_host, port=args.cloud_port)
    try:
        edge = _build_edge_runtime(args, client)
        if args.stdin_jsonl:
            _run_jsonl(edge)
        else:
            _run_mock_steps(edge, steps=args.steps)
    finally:
        client.close()


def make_future_sender(latent: Any) -> Any:
    """Test helper: build a Future-returning sender for a fixed latent."""

    def _send(request: LatentRequest) -> Future:
        future: Future = Future()
        future.set_result({"request_id": request.request_id, "step_id": request.step_id, "latent": latent})
        return future

    return _send


if __name__ == "__main__":
    main()
