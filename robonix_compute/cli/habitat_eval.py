from __future__ import annotations

from robonix_compute.cli.habitat_smoke import _run_real_edge_cloud_habitat, parse_args


def main() -> None:
    args = parse_args()
    args.real_edge_cloud = True
    if str(args.output_dir) == "outputs/habitat_smoke":
        args.output_dir = args.output_dir.parent / "habitat_eval"
    _run_real_edge_cloud_habitat(args)


if __name__ == "__main__":
    main()
