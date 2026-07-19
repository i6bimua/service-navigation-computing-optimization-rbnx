from __future__ import annotations

import argparse
from pathlib import Path
import urllib.request

from robonix_compute.cli.common import env_path, print_json


DEFAULT_INTERNVLA_REPO = "InternRobotics/InternVLA-N1-DualVLN"
DEFAULT_DEPTH_URL = (
    "https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Hypersim-Small/"
    "resolve/main/depth_anything_v2_metric_hypersim_vits.pth"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download external RoboNix-Compute-Optimization-Skill model checkpoints.")
    parser.add_argument("--internvla-repo", default=DEFAULT_INTERNVLA_REPO)
    parser.add_argument("--depth-url", default=DEFAULT_DEPTH_URL)
    parser.add_argument(
        "--output",
        type=Path,
        default=env_path("ROBONIX_COMPUTE_CHECKPOINT_DIR", "checkpoints"),
        help="Checkpoint root. Defaults to ROBONIX_COMPUTE_CHECKPOINT_DIR or ./checkpoints.",
    )
    parser.add_argument("--skip-depth", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output.expanduser()
    model_dir = output / "InternVLA-N1"
    depth_path = output / "depth_anything_v2_metric_hypersim_vits.pth"

    plan = {
        "internvla_repo": args.internvla_repo,
        "model_dir": str(model_dir),
        "depth_url": None if args.skip_depth else args.depth_url,
        "depth_ckpt": None if args.skip_depth else str(depth_path),
    }
    if args.dry_run:
        print_json({"dry_run": True, **plan})
        return

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("Install `huggingface_hub` or run `pip install -e .[download]`.") from exc

    output.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=args.internvla_repo,
        local_dir=str(model_dir),
        local_dir_use_symlinks=False,
        resume_download=True,
    )

    if not args.skip_depth and not depth_path.exists():
        urllib.request.urlretrieve(args.depth_url, depth_path)

    print_json({"downloaded": True, **plan})


if __name__ == "__main__":
    main()
