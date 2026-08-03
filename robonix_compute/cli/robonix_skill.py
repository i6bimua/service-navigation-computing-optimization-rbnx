from __future__ import annotations

import argparse
import json

from robonix_compute.cli.common import load_json_mapping, print_json
from robonix_compute.robonix.server import RoboNixComputeHTTPServer
from robonix_compute.robonix.skill import RoboNixComputeSkill


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the RoboNix Navigation Computing Optimization service.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--config-json")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_json_mapping(args.config_json)
    if args.dry_run:
        print_json({"service": "robonix-compute-skill", "host": args.host, "port": args.port, "config": config})
        return

    skill = RoboNixComputeSkill()
    skill.setup(config)
    server = RoboNixComputeHTTPServer(host=args.host, port=args.port, skill=skill)
    print(json.dumps({"service": "robonix-compute-skill", "status": "listening", "host": args.host, "port": args.port}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
