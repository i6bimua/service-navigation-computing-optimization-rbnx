from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from robonix_compute.cli.common import env_int, env_path, print_json
from robonix_compute.robonix.skill import RoboNixComputeSkill


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or inspect a RoboNix-Compute-Optimization-Skill Habitat smoke job.")
    parser.add_argument("--internnav-root", type=Path, default=env_path("INTERNNAV_ROOT", "../internnav-thor-codeonly/workspace/InternNav"))
    parser.add_argument("--config", default="scripts/eval/configs/habitat_dual_system_edge_cloud_cfg.py")
    parser.add_argument("--real-edge-cloud", action="store_true", help="Run real Habitat cloud-S2 plus edge-S1 split inference.")
    parser.add_argument("--analysis-config", default="scripts/eval/configs/analysis_cfg.py")
    parser.add_argument("--edge-config", default="scripts/eval/configs/h1_internvla_n1_async_cfg.py")
    parser.add_argument("--episodes", default=os.environ.get("ROBONIX_COMPUTE_HABITAT_EPISODES", "1"))
    parser.add_argument("--checkpoint-path", type=Path, default=env_path("ROBONIX_COMPUTE_MODEL_DIR", "checkpoints/InternVLA-N1"))
    parser.add_argument("--s1-model-path", type=Path, default=env_path("ROBONIX_COMPUTE_S1_MODEL_DIR", "checkpoints/InternVLA-N1-S1"))
    parser.add_argument("--data-root", type=Path, default=env_path("ROBONIX_COMPUTE_DATA_ROOT", "data"))
    parser.add_argument("--cloud-gpu-id", type=int, default=env_int("ROBONIX_COMPUTE_CLOUD_GPU_ID", 1))
    parser.add_argument("--edge-gpu-id", type=int, default=env_int("ROBONIX_COMPUTE_EDGE_GPU_ID", 0))
    parser.add_argument("--port", type=int, default=env_int("ROBONIX_COMPUTE_CLOUD_PORT", 8765))
    parser.add_argument("--cloud-bind-host", default=os.environ.get("ROBONIX_COMPUTE_CLOUD_BIND_HOST", "0.0.0.0"))
    parser.add_argument("--edge-connect-host", default=os.environ.get("ROBONIX_COMPUTE_EDGE_CONNECT_HOST", "127.0.0.1"))
    parser.add_argument("--startup-timeout", type=float, default=float(os.environ.get("ROBONIX_COMPUTE_STARTUP_TIMEOUT", "900")))
    parser.add_argument("--shutdown-timeout", type=float, default=float(os.environ.get("ROBONIX_COMPUTE_SHUTDOWN_TIMEOUT", "30")))
    parser.add_argument("--sample-interval", type=float, default=float(os.environ.get("ROBONIX_COMPUTE_GPU_SAMPLE_INTERVAL", "0.5")))
    parser.add_argument("--edge-cloud-send-delay-ms", type=float, default=float(os.environ.get("ROBONIX_COMPUTE_SEND_DELAY_MS", "0")))
    parser.add_argument("--edge-cloud-reply-delay-ms", type=float, default=float(os.environ.get("ROBONIX_COMPUTE_REPLY_DELAY_MS", "0")))
    parser.add_argument("--edge-cloud-rtt-delay-ms", type=float, default=float(os.environ.get("ROBONIX_COMPUTE_RTT_DELAY_MS", "0")))
    parser.add_argument("--output-dir", type=Path, default=env_path("ROBONIX_COMPUTE_OUTPUT_DIR", "outputs/habitat_smoke"))
    parser.add_argument("--local-edge-smoke", action="store_true", default=os.environ.get("LOCAL_EDGE_SMOKE") == "1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail if Habitat/InternNav cannot be launched.")
    return parser.parse_args()


def _prepend_pythonpath(env: dict[str, str], *paths: Path) -> None:
    entries = [str(path) for path in paths]
    current = env.get("PYTHONPATH")
    if current:
        entries.append(current)
    env["PYTHONPATH"] = os.pathsep.join(entries)


def _run_real_edge_cloud_habitat(args: argparse.Namespace) -> None:
    internnav_root = args.internnav_root.expanduser().resolve()
    measure_script = internnav_root / "scripts/eval/measure_edge_cloud_s2_runtime.py"
    analysis_config = internnav_root / args.analysis_config
    edge_config = internnav_root / args.edge_config
    checkpoint_path = args.checkpoint_path.expanduser().resolve()
    s1_model_path = args.s1_model_path.expanduser().resolve()
    data_root = args.data_root.expanduser().resolve()
    output_root = args.output_dir.expanduser().resolve()
    command = [
        sys.executable,
        str(measure_script),
        "--root",
        str(internnav_root),
        "--config",
        args.analysis_config,
        "--edge-config",
        args.edge_config,
        "--checkpoint-path",
        str(checkpoint_path),
        "--episodes",
        str(args.episodes),
        "--cloud-gpu-id",
        str(args.cloud_gpu_id),
        "--edge-gpu-id",
        str(args.edge_gpu_id),
        "--cloud-bind-host",
        args.cloud_bind_host,
        "--edge-connect-host",
        args.edge_connect_host,
        "--port",
        str(args.port),
        "--edge-cloud-send-delay-ms",
        str(args.edge_cloud_send_delay_ms),
        "--edge-cloud-reply-delay-ms",
        str(args.edge_cloud_reply_delay_ms),
        "--edge-cloud-rtt-delay-ms",
        str(args.edge_cloud_rtt_delay_ms),
        "--sample-interval",
        str(args.sample_interval),
        "--startup-timeout",
        str(args.startup_timeout),
        "--shutdown-timeout",
        str(args.shutdown_timeout),
        "--output-root",
        str(output_root),
    ]
    payload = {
        "mode": "real_edge_cloud_habitat",
        "internnav_root": str(internnav_root),
        "analysis_config": str(analysis_config),
        "edge_config": str(edge_config),
        "checkpoint_path": str(checkpoint_path),
        "s1_model_path": str(s1_model_path),
        "data_root": str(data_root),
        "output_root": str(output_root),
        "episodes": args.episodes,
        "cloud_gpu_id": args.cloud_gpu_id,
        "edge_gpu_id": args.edge_gpu_id,
        "cloud_bind_host": args.cloud_bind_host,
        "edge_connect_host": args.edge_connect_host,
        "port": args.port,
        "edge_cloud_send_delay_ms": args.edge_cloud_send_delay_ms,
        "edge_cloud_reply_delay_ms": args.edge_cloud_reply_delay_ms,
        "edge_cloud_rtt_delay_ms": args.edge_cloud_rtt_delay_ms,
        "sample_interval": args.sample_interval,
        "startup_timeout": args.startup_timeout,
        "shutdown_timeout": args.shutdown_timeout,
        "command": command,
    }
    if args.dry_run:
        print_json(payload)
        return

    missing = [
        str(path)
        for path in (measure_script, analysis_config, edge_config, checkpoint_path, s1_model_path, data_root)
        if not path.exists()
    ]
    if missing:
        message = "Real Habitat compute evaluation prerequisites are missing: " + ", ".join(missing)
        if args.strict or args.real_edge_cloud:
            raise FileNotFoundError(message)
        print_json({"skipped": True, "reason": message, **payload})
        return

    env = os.environ.copy()
    env["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    env["PYTHONUNBUFFERED"] = "1"
    env["INTERNNAV_HABITAT_DATA_ROOT"] = str(data_root)
    env["INTERNVLA_N1_MODEL_PATH"] = str(checkpoint_path)
    env["INTERNVLA_N1_S1_MODEL_PATH"] = str(s1_model_path)
    _prepend_pythonpath(env, internnav_root, internnav_root / "third_party/diffusion-policy")
    output_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, check=True, cwd=str(internnav_root), env=env)


def main() -> None:
    args = parse_args()
    if args.real_edge_cloud:
        _run_real_edge_cloud_habitat(args)
        return

    if args.local_edge_smoke:
        output_path = args.output_dir / "telemetry.json"
        if args.dry_run:
            print_json({"mode": "local_edge_smoke", "output": str(output_path)})
            return
        skill = RoboNixComputeSkill()
        skill.setup({"mode": "mock", "require_initial_latent": False})
        skill.reset(instruction="local edge smoke")
        for step_id in range(5):
            rgb = [1.0, 0.0] if step_id < 2 else [0.0, 1.0]
            skill.step({"rgb": rgb, "depth": [0.0]})
        assert skill.edge_runtime is not None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(skill.edge_runtime.telemetry.to_dict(), indent=2, default=str), encoding="utf-8")
        skill.close()
        print_json({"mode": "local_edge_smoke", "telemetry": str(output_path)})
        return

    internnav_root = args.internnav_root.expanduser().resolve()
    eval_script = internnav_root / "scripts/eval/eval.py"
    config_path = internnav_root / args.config
    command = [
        sys.executable,
        str(eval_script),
        "--config",
        str(config_path),
    ]
    payload = {
        "mode": "habitat",
        "internnav_root": str(internnav_root),
        "config": str(config_path),
        "output_dir": str(args.output_dir.expanduser()),
        "required_env": [
            "ROBONIX_COMPUTE_MODEL_DIR",
            "ROBONIX_COMPUTE_S1_MODEL_DIR",
            "ROBONIX_COMPUTE_DATA_ROOT",
            "ROBONIX_COMPUTE_CLOUD_HOST",
            "ROBONIX_COMPUTE_CLOUD_PORT",
        ],
        "command": command,
    }
    if args.dry_run:
        print_json(payload)
        return
    if not eval_script.is_file() or not config_path.is_file():
        message = "InternNav Habitat evaluator is not available; rerun with --dry-run or set INTERNNAV_ROOT."
        if args.strict:
            raise FileNotFoundError(message)
        print_json({"skipped": True, "reason": message, **payload})
        return
    subprocess.run(command, check=True, cwd=str(internnav_root))


if __name__ == "__main__":
    main()
