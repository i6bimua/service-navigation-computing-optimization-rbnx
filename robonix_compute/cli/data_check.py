from __future__ import annotations

import argparse
import json
from pathlib import Path

from robonix_compute.benchmark_data import DATASET_SPECS, PROFILES
from robonix_compute.benchmark_data import format_data_validation_table, validate_benchmark_data
from robonix_compute.cli.common import env_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate licensed VLN benchmark annotations and MP3D-CE scene assets."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=env_path("ROBONIX_COMPUTE_DATA_ROOT", "data"),
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="core")
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=sorted(DATASET_SPECS),
        help="Override the profile with an explicit dataset list.",
    )
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate_benchmark_data(
        args.data_root,
        profile=args.profile,
        datasets=args.datasets,
    )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_data_validation_table(report))
    if args.strict and not report.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
