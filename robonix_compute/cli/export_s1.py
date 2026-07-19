from __future__ import annotations

import argparse
from pathlib import Path

from robonix_compute.cli.common import env_path, print_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export an InternVLA-N1 S1-only checkpoint for edge deployment.")
    parser.add_argument(
        "--source",
        type=Path,
        default=env_path("ROBONIX_COMPUTE_MODEL_DIR", "checkpoints/InternVLA-N1"),
        help="Full InternVLA-N1 checkpoint directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=env_path("ROBONIX_COMPUTE_S1_MODEL_DIR", "checkpoints/InternVLA-N1-S1"),
        help="Output S1-only checkpoint directory.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.source.expanduser()
    output = args.output.expanduser()
    if args.dry_run:
        print_json({"dry_run": True, "source": str(source), "output": str(output)})
        return

    try:
        from internnav.edgecloud import export_internvla_s1_only_checkpoint
    except ImportError as exc:
        raise RuntimeError("Install InternNav before exporting an InternVLA-N1 S1-only checkpoint.") from exc

    manifest = export_internvla_s1_only_checkpoint(
        source_checkpoint_dir=source,
        output_checkpoint_dir=output,
    )
    print_json({"exported": True, "manifest": manifest, "output": str(output)})


if __name__ == "__main__":
    main()
