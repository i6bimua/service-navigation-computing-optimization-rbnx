from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import numpy as np

from robonix_compute.cli.common import default_model_settings, env_int, env_path, load_json_mapping, print_json
from robonix_compute.cloud.runtime import CloudRuntime
from robonix_compute.cloud.runners import CallableS2Runner
from robonix_compute.cloud.server import CloudServer
from robonix_compute.model_adapters import InternNavS2Adapter, InternVLALatentPayload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the RoboNix Compute Optimization cloud-side S2 latent server.")
    parser.add_argument("--mode", choices=["mock", "internnav"], default="mock")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=env_int("ROBONIX_COMPUTE_CLOUD_PORT", 8765))
    parser.add_argument("--model-dir", type=Path, default=env_path("ROBONIX_COMPUTE_MODEL_DIR", "checkpoints/InternVLA-N1"))
    parser.add_argument("--model-settings-json")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _mock_s2(observation: Any, instruction: str | None = None) -> dict[str, Any]:
    del instruction
    rgb = observation.get("rgb") if isinstance(observation, dict) else observation
    latent = np.asarray(rgb, dtype=np.float32).reshape(-1)
    return InternVLALatentPayload(
        traj_latent=latent,
        memory_rgb=rgb,
        memory_depth=observation.get("depth") if isinstance(observation, dict) else None,
        metadata={"runner": "mock_s2"},
    ).to_dict()


def build_cloud_runtime(args: argparse.Namespace) -> CloudRuntime:
    if args.mode == "mock":
        return CloudRuntime(CallableS2Runner(_mock_s2))

    try:
        from internnav.edgecloud.runners import InternVLAN1S2Runner
    except ImportError as exc:
        raise RuntimeError("InternNav is required for `robonix-compute-cloud --mode internnav`.") from exc

    model_settings = default_model_settings(model_dir=args.model_dir.expanduser(), device=args.device)
    model_settings.update(load_json_mapping(args.model_settings_json))
    return CloudRuntime(InternNavS2Adapter(InternVLAN1S2Runner(model_settings=model_settings)))


def main() -> None:
    args = parse_args()
    if args.dry_run:
        print_json(
            {
                "role": "cloud",
                "mode": args.mode,
                "host": args.host,
                "port": args.port,
                "model_dir": str(args.model_dir.expanduser()),
            }
        )
        return

    runtime = build_cloud_runtime(args)
    server = CloudServer(runtime, host=args.host, port=args.port)
    server.start()
    print_json({"role": "cloud", "status": "listening", "host": args.host, "port": args.port, "mode": args.mode})
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
