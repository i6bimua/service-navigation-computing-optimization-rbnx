from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from robonix_compute.cli.common import env_int, env_path
from robonix_compute.preflight import format_preflight_table, run_preflight


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check compute optimization evaluation prerequisites.")
    parser.add_argument("--mode", choices=["habitat_eval"], default="habitat_eval")
    parser.add_argument("--internnav-root", type=Path, default=env_path("INTERNNAV_ROOT", "../internnav-thor-codeonly/workspace/InternNav"))
    parser.add_argument("--analysis-config", default="scripts/eval/configs/analysis_cfg.py")
    parser.add_argument("--edge-config", default="scripts/eval/configs/h1_internvla_n1_async_cfg.py")
    parser.add_argument("--checkpoint-path", type=Path, default=env_path("ROBONIX_COMPUTE_MODEL_DIR", "checkpoints/InternVLA-N1"))
    parser.add_argument("--s1-model-path", type=Path, default=env_path("ROBONIX_COMPUTE_S1_MODEL_DIR", "checkpoints/InternVLA-N1-S1"))
    parser.add_argument(
        "--depth-checkpoint-path",
        type=Path,
        default=env_path(
            "ROBONIX_COMPUTE_DEPTH_CKPT",
            "checkpoints/depth_anything_v2_metric_hypersim_vits.pth",
        ),
    )
    parser.add_argument("--data-root", type=Path, default=env_path("ROBONIX_COMPUTE_DATA_ROOT", "data"))
    parser.add_argument("--output-dir", type=Path, default=env_path("ROBONIX_COMPUTE_OUTPUT_DIR", "outputs/habitat_eval"))
    parser.add_argument("--cloud-bind-host", default=os.environ.get("ROBONIX_COMPUTE_CLOUD_BIND_HOST", "0.0.0.0"))
    parser.add_argument("--cloud-port", type=int, default=env_int("ROBONIX_COMPUTE_CLOUD_PORT", 8765))
    parser.add_argument("--cloud-gpu-id", type=int, default=env_int("ROBONIX_COMPUTE_CLOUD_GPU_ID", 1))
    parser.add_argument("--edge-gpu-id", type=int, default=env_int("ROBONIX_COMPUTE_EDGE_GPU_ID", 0))
    parser.add_argument("--require-gpu", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_preflight(
        mode=args.mode,
        internnav_root=args.internnav_root,
        data_root=args.data_root,
        checkpoint_path=args.checkpoint_path,
        s1_model_path=args.s1_model_path,
        depth_checkpoint_path=args.depth_checkpoint_path,
        output_dir=args.output_dir,
        analysis_config=args.analysis_config,
        edge_config=args.edge_config,
        cloud_bind_host=args.cloud_bind_host,
        cloud_port=args.cloud_port,
        cloud_gpu_id=args.cloud_gpu_id,
        edge_gpu_id=args.edge_gpu_id,
        require_gpu=args.require_gpu,
    )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_preflight_table(report))
    if args.strict and not report.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
