from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np

from robonix_compute.cloud.runtime import CloudRuntime
from robonix_compute.cloud.runners import CallableS2Runner
from robonix_compute.common.link import InProcessLatentLink
from robonix_compute.edge.runtime import EdgeRuntime, EdgeRuntimeConfig
from robonix_compute.edge.runners import CallableS1Runner
from robonix_compute.edge.switcher import KeyLatentSwitcherConfig


def build_observation(step: int) -> dict[str, np.ndarray]:
    if step < 2:
        feature = np.array([1.0, 0.0], dtype=np.float32)
    elif step == 2:
        feature = np.array([0.0, 1.0], dtype=np.float32)
    else:
        feature = np.array([0.0, 1.0 + step * 0.01], dtype=np.float32)
    return {"rgb": feature}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a RoboNix Navigation Computing Optimization mock rollout.")
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--delay-s", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=Path("outputs/mock_compute/telemetry.json"))
    args = parser.parse_args()

    def generate_latent(observation, instruction=None):
        del instruction
        return np.asarray(observation["rgb"], dtype=np.float32)

    def act(observation, latent):
        del observation
        return int(np.argmax(np.asarray(latent)) == 1)

    cloud = CloudRuntime(CallableS2Runner(generate_latent))
    link = InProcessLatentLink(cloud, delay_s=args.delay_s)
    edge = EdgeRuntime(
        s1_runner=CallableS1Runner(act_fn=act),
        latent_sender=link.send,
        config=EdgeRuntimeConfig(
            require_initial_latent=False,
            initial_timeout_s=max(0.05, args.delay_s / 2.0 if args.delay_s else 0.05),
            switcher=KeyLatentSwitcherConfig(tau_lower=0.5, tau_upper=0.99, tau_initial=0.99),
        ),
    )
    edge.reset()
    actions = []
    for step in range(args.steps):
        actions.append(edge.step(build_observation(step), step_id=step, instruction="mock instruction"))
        time.sleep(0.01)
    edge.wait_for_late_responses(timeout_s=2.0)
    edge.telemetry.write_json(args.output)
    print(json.dumps({"actions": actions, "telemetry": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
